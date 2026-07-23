"""
src/options_fetcher.py

OptionsFetcher — fetches options chains and price history from the Schwab API.

Usage
-----
    fetcher = OptionsFetcher(client, symbol="$SPX")
    chain   = fetcher.fetch_monthly_chain()   # monthly expiries, ±5 strikes ATM
    prices  = fetcher.fetch_price_history()
"""

from __future__ import annotations

import calendar
import math
from datetime import datetime, date, timedelta, UTC
from typing import Optional, Union

import pandas as pd
import schwab

_KEEP_COLUMNS = [
    "symbol", "optionType", "expiration", "dte", "strikePrice",
    "bid", "ask", "mark", "last", "totalVolume", "openInterest",
    "volatility", "delta", "gamma", "theta", "vega", "rho",
    "inTheMoney", "theoreticalOptionValue", "underlyingPrice", "fetchTime",
]
_RENAME = {"volatility": "impliedVolatility", "totalVolume": "volume"}
_NUMERIC_COLS = [
    "strikePrice", "bid", "ask", "mark", "last", "volume", "openInterest",
    "impliedVolatility", "delta", "gamma", "theta", "vega", "rho",
    "theoreticalOptionValue", "underlyingPrice", "dte",
]

# US market holidays (add/update annually)
_US_HOLIDAYS: set[date] = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day (observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 11, 27), # Black Friday (early close but listed as holiday)
    date(2026, 12, 25), # Christmas
    date(2027, 1, 1),   # New Year's Day
    date(2027, 1, 18),  # MLK Day
    date(2027, 2, 15),  # Presidents Day
    date(2027, 3, 26),  # Good Friday
    date(2027, 5, 31),  # Memorial Day
    date(2027, 6, 18),  # Juneteenth (observed)
    date(2027, 7, 5),   # Independence Day (observed)
    date(2027, 9, 6),   # Labor Day
    date(2027, 11, 25), # Thanksgiving
    date(2027, 12, 24), # Christmas (observed)
}


def _third_friday(year: int, month: int) -> date:
    """Return the 3rd Friday of the given month."""
    # calendar.weekday: 0=Mon … 4=Fri
    first_day = date(year, month, 1)
    # Day of week of the 1st (0=Mon, 4=Fri)
    first_weekday = first_day.weekday()
    # Days until first Friday
    days_to_first_friday = (4 - first_weekday) % 7
    first_friday = first_day + timedelta(days=days_to_first_friday)
    third_friday = first_friday + timedelta(weeks=2)
    return third_friday


def _monthly_expiry(year: int, month: int) -> date:
    """
    Standard monthly SPX expiry: 3rd Friday.
    If that Friday is a market holiday, fall back to the preceding Thursday.
    """
    d = _third_friday(year, month)
    if d in _US_HOLIDAYS:
        d -= timedelta(days=1)  # Thursday
    return d


def monthly_expiry_dates(
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
) -> list[date]:
    """
    Return all monthly SPX expiry dates (3rd Fri, holiday-adjusted)
    between from_date and to_date inclusive.
    """
    start = from_date or date.today()
    end = to_date or (start + timedelta(days=730))

    expiries: list[date] = []
    year, month = start.year, start.month

    while date(year, month, 1) <= end:
        exp = _monthly_expiry(year, month)
        if start <= exp <= end:
            expiries.append(exp)
        # Advance to next month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    return expiries


class OptionsFetcher:
    """Fetches and parses SPX (or any symbol) options chains from Schwab."""

    _CONTRACT_TYPE_MAP = {
        "ALL": schwab.client.Client.Options.ContractType.ALL,
        "CALL": schwab.client.Client.Options.ContractType.CALL,
        "PUT": schwab.client.Client.Options.ContractType.PUT,
    }

    def __init__(self, client: schwab.client.Client, symbol: str = "$SPX") -> None:
        self._client = client
        self.symbol = symbol

    def fetch_monthly_chain(
        self,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        contract_type: str = "ALL",
        strikes_each_side: int = 5,
        strike_increment: Optional[int] = 100,
        scale_strikes_with_dte: bool = True,
        dte_scaling_reference: float = 30.0,
        max_strikes_each_side: int = 40,
    ) -> pd.DataFrame:
        """
        Fetch monthly expiries only (3rd Friday, holiday-adjusted).

        For each expiry, requests a wide strike window then post-filters to
        only keep strikes at `strike_increment` intervals (default $100) within
        N steps of ATM, where N defaults to `strikes_each_side` but grows with
        DTE (see `scale_strikes_with_dte`).

        Example: SPX at 7500, strikes_each_side=5, strike_increment=100, dte=30
          → keeps 7000, 7100, 7200, 7300, 7400, 7500, 7600, 7700, 7800, 7900, 8000

        Why scale with DTE
        -------------------
        Downstream skew/curvature metrics need strikes bracketing ~25-delta on
        both the call and put side per expiry. Since implied-vol-driven strike
        dispersion scales roughly with sigma * sqrt(T), the dollar distance from
        ATM to the 25-delta strike grows with DTE — a fixed $100 x 5-strike
        window (±$500) brackets 25-delta for near-dated SPX monthlies (~90 DTE
        and under) but falls short for longer-dated ones, leaving too few
        in-range strikes and NaN skew/curvature for anything further out.
        `scale_strikes_with_dte` (on by default) widens the per-expiry strike
        count by roughly sqrt(dte / dte_scaling_reference), so the fixed
        `strike_increment` filter still applies but pulls more strikes for
        longer-dated expiries. This was validated (see _strikes_each_side_for_dte)
        against a static SPX chain snapshot by estimating the 25-delta strike
        from each expiry's own ATM implied vol via Black-Scholes and checking
        the scaled window comfortably covers it with margin, at every DTE from
        34 to 699 days.

        Parameters
        ----------
        from_date              : first expiry to include (default: today)
        to_date                : last expiry to include (default: 2 years out)
        contract_type           : 'ALL', 'CALL', or 'PUT'
        strikes_each_side       : baseline steps on each side of ATM at
                                   strike_increment spacing, used as-is for
                                   expiries at/under dte_scaling_reference days
        strike_increment        : spacing between selected strikes in $ (default 100).
                                   None skips the increment filter entirely and keeps
                                   whatever real strikes Schwab's own strike_count
                                   parameter already returned around ATM -- use this
                                   for individual equities, where a single fixed $
                                   increment doesn't fit every underlying's price
                                   level/tick size the way $100 fits SPX
        scale_strikes_with_dte  : if True (default), widen strikes_each_side for
                                   longer-dated expiries so they still bracket
                                   ~25-delta; if False, reproduces the old fixed
                                   window behavior for every expiry
        dte_scaling_reference   : DTE (days) at/under which strikes_each_side is
                                   used unchanged; the scaling anchor point
        max_strikes_each_side   : safety cap on how wide the per-expiry strike
                                   count is allowed to grow
        """
        ct = self._CONTRACT_TYPE_MAP.get(
            contract_type.upper(),
            schwab.client.Client.Options.ContractType.ALL,
        )

        expiries = monthly_expiry_dates(from_date, to_date)
        reference_date = from_date or date.today()

        chunks: list[pd.DataFrame] = []
        strikes_each_side_by_expiry: dict[pd.Timestamp, int] = {}

        for exp in expiries:
            if scale_strikes_with_dte:
                dte_est = max((exp - reference_date).days, 1)
                n_side = self._strikes_each_side_for_dte(
                    dte_est, strikes_each_side, dte_scaling_reference, max_strikes_each_side
                )
            else:
                n_side = strikes_each_side

            if strike_increment:
                # Fetch wide enough to guarantee we capture increment-aligned strikes.
                # Worst case: SPX near-term has $1 increments → need 2 × n_side
                # × increment strikes. Using 300 covers ±$500 at $5 increments safely.
                wide_strike_count = max(300, n_side * strike_increment * 2 // 5)
            else:
                # No increment filter to compensate for -- Schwab's strike_count
                # already centers on ATM, so just ask for enough margin above
                # n_side for _trim_to_n_strikes to have real strikes to pick from.
                wide_strike_count = max(100, n_side * 8)

            chunk = self._fetch_single_expiry(ct, exp, wide_strike_count)
            if not chunk.empty:
                strikes_each_side_by_expiry[pd.Timestamp(exp)] = n_side
                chunks.append(chunk)

        if not chunks:
            return pd.DataFrame()

        df = pd.concat(chunks, ignore_index=True)

        # Post-filter: keep only strikes at the desired increment (skipped
        # entirely when strike_increment is None -- see docstring).
        if strike_increment:
            df = df[df["strikePrice"] % strike_increment == 0].copy()

        # Keep only ±n_side strikes from ATM per expiration, where n_side is
        # per-expiry when scale_strikes_with_dte is on, else the flat baseline.
        n_arg: Union[int, dict[pd.Timestamp, int]] = (
            strikes_each_side_by_expiry if scale_strikes_with_dte else strikes_each_side
        )
        df = self._trim_to_n_strikes(df, n_arg)

        df.sort_values(["optionType", "expiration", "strikePrice"], inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    @staticmethod
    def _strikes_each_side_for_dte(
        dte: float,
        base_strikes_each_side: int,
        reference_dte: float,
        max_strikes_each_side: int,
    ) -> int:
        """
        Scale strikes_each_side by sqrt(dte / reference_dte).

        Rationale: for a roughly constant-vol lognormal model, the strike
        offset corresponding to a fixed delta (e.g. 25-delta) scales with
        sigma * sqrt(T). Anchoring at `reference_dte` (default 30 days, ~the
        nearest monthly expiry) keeps short-dated expiries at the original
        `base_strikes_each_side`, while longer-dated expiries get a wider
        window so they keep bracketing 25-delta in dollar-strike terms.
        Capped at `max_strikes_each_side` as a sanity limit on request size.
        """
        if dte <= reference_dte:
            return base_strikes_each_side
        scale = math.sqrt(dte / reference_dte)
        n = math.ceil(base_strikes_each_side * scale)
        return min(max(n, base_strikes_each_side), max_strikes_each_side)

    @staticmethod
    def _trim_to_n_strikes(
        df: pd.DataFrame, n: Union[int, dict[pd.Timestamp, int]]
    ) -> pd.DataFrame:
        """
        For each (optionType, expiration), keep only the strikes closest to
        ATM (underlyingPrice) on each side, i.e. ≤ n below and ≤ n above.

        `n` may be a single int applied to every expiration (legacy/flat
        behavior), or a dict mapping expiration (pd.Timestamp) to a per-expiry
        strike count (used when strike coverage is scaled by DTE).
        """
        if df.empty or "underlyingPrice" not in df.columns:
            return df

        result_parts: list[pd.DataFrame] = []
        for (opt_type, expiration), grp in df.groupby(["optionType", "expiration"]):
            # `n` dict is populated for every expiration that produced a
            # non-empty chunk, i.e. every expiration present in `df`.
            this_n = n[expiration] if isinstance(n, dict) else n
            underlying = grp["underlyingPrice"].iloc[0]
            strikes = grp["strikePrice"].drop_duplicates().sort_values()

            below = strikes[strikes <= underlying].nlargest(this_n + 1)   # include ATM
            above = strikes[strikes > underlying].nsmallest(this_n)
            allowed = set(below) | set(above)

            result_parts.append(grp[grp["strikePrice"].isin(allowed)])

        return pd.concat(result_parts, ignore_index=True) if result_parts else df

    def _fetch_single_expiry(
        self,
        ct: schwab.client.Client.Options.ContractType,
        expiry: date,
        strike_count: int,
    ) -> pd.DataFrame:
        """Fetch one expiry date. strike_count is centred around ATM."""
        response = self._client.get_option_chain(
            symbol=self.symbol,
            contract_type=ct,
            include_underlying_quote=True,
            from_date=expiry,
            to_date=expiry,
            strike_count=strike_count,
        )
        if response.status_code == 200:
            return self._parse_chain(response.json())
        if response.status_code in (404, 400):
            # No options listed for this date
            return pd.DataFrame()
        raise RuntimeError(
            f"Schwab API error {response.status_code} (expiry {expiry}): {response.text}"
        )

    # ------------------------------------------------------------------
    # Legacy full-chain fetch (kept for flexibility)
    # ------------------------------------------------------------------

    def fetch_chain(
        self,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        contract_type: str = "ALL",
        chunk_days: int = 30,
    ) -> pd.DataFrame:
        """
        Fetch all expirations in date-range chunks (avoids 502 TooBigBody).
        Use fetch_monthly_chain() for the standard targeted pull.
        """
        ct = self._CONTRACT_TYPE_MAP.get(
            contract_type.upper(),
            schwab.client.Client.Options.ContractType.ALL,
        )
        start = from_date or date.today()
        end = to_date or (start + timedelta(days=730))

        chunks: list[pd.DataFrame] = []
        window_start = start
        while window_start <= end:
            window_end = min(window_start + timedelta(days=chunk_days - 1), end)
            chunk = self._fetch_single_window(ct, window_start, window_end)
            if not chunk.empty:
                chunks.append(chunk)
            window_start = window_end + timedelta(days=1)

        if not chunks:
            return pd.DataFrame()

        df = pd.concat(chunks, ignore_index=True)
        df.sort_values(["optionType", "expiration", "strikePrice"], inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def _fetch_single_window(
        self,
        ct: schwab.client.Client.Options.ContractType,
        from_date: date,
        to_date: date,
    ) -> pd.DataFrame:
        """Fetch one date-range window. Returns empty DataFrame on non-200."""
        response = self._client.get_option_chain(
            symbol=self.symbol,
            contract_type=ct,
            include_underlying_quote=True,
            from_date=from_date,
            to_date=to_date,
        )
        if response.status_code == 200:
            return self._parse_chain(response.json())
        if response.status_code == 404:
            return pd.DataFrame()
        raise RuntimeError(
            f"Schwab API error {response.status_code} "
            f"({from_date} → {to_date}): {response.text}"
        )

    def fetch_price_history(self, lookback_years: int = 1) -> pd.DataFrame:
        """
        Fetch daily OHLCV price history for the symbol.
        Returns a DataFrame with columns: datetime, open, high, low, close, volume.
        """
        period_map = {
            1: schwab.client.Client.PriceHistory.Period.ONE_YEAR,
            2: schwab.client.Client.PriceHistory.Period.TWO_YEARS,
            3: schwab.client.Client.PriceHistory.Period.THREE_YEARS,
        }
        period = period_map.get(lookback_years, schwab.client.Client.PriceHistory.Period.ONE_YEAR)
        response = self._client.get_price_history(
            symbol=self.symbol,
            period_type=schwab.client.Client.PriceHistory.PeriodType.YEAR,
            period=period,
            frequency_type=schwab.client.Client.PriceHistory.FrequencyType.DAILY,
            frequency=schwab.client.Client.PriceHistory.Frequency.DAILY,
            need_extended_hours_data=False,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Schwab API error {response.status_code}: {response.text}"
            )
        candles = response.json().get("candles", [])
        df = pd.DataFrame(candles)
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        df.sort_values("datetime", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    @staticmethod
    def _parse_chain(data: dict) -> pd.DataFrame:
        """Convert raw Schwab chain JSON into a flat, typed DataFrame."""
        underlying_price: float = data.get("underlyingPrice", float("nan"))
        fetch_time: str = datetime.now(UTC).isoformat(timespec="seconds")
        records: list[dict] = []

        for side, option_type in (("callExpDateMap", "CALL"), ("putExpDateMap", "PUT")):
            for exp_key, strikes in data.get(side, {}).items():
                parts = exp_key.split(":")
                expiration = parts[0]
                dte = int(parts[1]) if len(parts) > 1 else None
                for _strike, contracts in strikes.items():
                    for contract in contracts:
                        contract["optionType"] = option_type
                        contract["expiration"] = expiration
                        contract["dte"] = dte
                        contract["underlyingPrice"] = underlying_price
                        contract["fetchTime"] = fetch_time
                        records.append(contract)

        if not records:
            return pd.DataFrame(columns=_KEEP_COLUMNS)

        df = pd.DataFrame(records)
        present = [c for c in _KEEP_COLUMNS if c in df.columns]
        df = df[present].copy()
        df.rename(columns=_RENAME, inplace=True)

        for col in _NUMERIC_COLS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["expiration"] = pd.to_datetime(df["expiration"], errors="coerce")
        df["fetchTime"] = pd.to_datetime(df["fetchTime"], utc=True, errors="coerce")
        df.sort_values(["optionType", "expiration", "strikePrice"], inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df
