"""
src/synthetic_market.py

A simulated options market, for the public demo.

Why this exists
---------------
The dashboard is worth showing to people, and the data it runs on is not
ours to publish. Those two facts used to be in direct conflict: the public
demo served a database of real Schwab chains, and the repository carried
1.6 million real contracts in Git LFS where anyone could clone them.

Generating the market removes the conflict rather than managing it. There
is no redistribution question about numbers that came out of a random
number generator, so the demo can be fully interactive, the repository can
stay public, and nobody has to reason about what a broker agreement permits
on a given Tuesday.

What it is not
--------------
Not a pricing model anybody should trade against, and not a forecast. It is
a *plausible-looking* market: the stylised facts are reproduced because a
dashboard rendering a flat, symmetric, perfectly-quoted surface would
demonstrate nothing about whether the dashboard works.

The stylised facts it reproduces, and why each one earns its place:

  * **Downward skew, flattening with maturity.** Puts bid over calls, and
    the slope decaying roughly as 1/sqrt(T). Without it the skew panel,
    the 25-delta columns and half the Trade Ideas logic have nothing to
    chew on.

  * **A term structure that is not flat**, with the front end pulled up or
    down toward a long-run level. The whole calendar-spread engine is a
    statement about the shape of this curve.

  * **Event bumps.** One expiration per symbol carries extra variance, the
    way a real chain does ahead of earnings. This is what makes the
    forward-variance decomposition produce anything interesting, and it
    exercises the earnings-aware paths.

  * **Quotes that get unreliable in the wings.** Market makers quote a vol
    band, not a price, and tick size and gap risk put a floor under how
    tight that band can be. Far out and near expiry, the floor dominates
    and the spread spans many vol points -- which is exactly the condition
    vol_surface's quote filter exists to detect. A synthetic market with
    uniformly perfect quotes would make that filter look like dead code.

How close it gets
-----------------
Measured against the real 2026-10-26 snapshot, over the same OTM window
the surface uses (+/-25% moneyness), where "band" is the vol points the
bid-ask spans (spread / vega):

                      synthetic    real
    band median            2.1      1.9
    band p90               3.3      7.5
    band > 5 vol pts       1.8%    17.6%
    no bid                 1.9%     1.2%
    GOOGL IV range    29 - 84   27 - 90

Per contract it is close. The real GOOGL wing that motivated the quote
filter -- 21% out, 4 days, bid 0.02 / ask 0.06, vega 0.004, band 10.0 --
is reproduced almost exactly at 19% out.

In aggregate the simulated market is *cleaner* than the real one: the
tail of badly-quoted contracts is thinner, so the quote filter withholds
proportionally less here than it does on live data. That is a known
limitation rather than a target to tune away. Reproducing the real tail
would mean modelling why particular contracts go stale, which is a
property of participants rather than of prices, and the demo does not
need it -- the filter still fires on the wings it exists for.

Determinism
-----------
Seeded per symbol, so the same inputs rebuild the same market. A demo that
reshuffles itself on every publish would make screenshots unreproducible
and any bug in it unrepeatable.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

RISK_FREE = 0.04
TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Black-Scholes
# ---------------------------------------------------------------------------


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass
class Greeks:
    price: float
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 vol POINT, per share -- the convention Schwab uses
    rho: float


def black_scholes(
    spot: float, strike: float, years: float, vol: float, is_call: bool, rate: float = RISK_FREE
) -> Greeks:
    """Price and greeks. `vol` is a decimal (0.45), not vol points.

    Degenerate inputs return intrinsic value with flat greeks rather than
    raising: an expiring or zero-vol contract is a legitimate thing to ask
    for here, and the caller should not have to special-case it.
    """
    if years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        intrinsic = max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
        delta = (1.0 if spot > strike else 0.0) if is_call else (-1.0 if spot < strike else 0.0)
        return Greeks(intrinsic, delta, 0.0, 0.0, 0.0, 0.0)

    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    discount = math.exp(-rate * years)

    if is_call:
        price = spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta_annual = -(spot * _norm_pdf(d1) * vol) / (2 * sqrt_t) - rate * strike * discount * _norm_cdf(d2)
        rho = strike * years * discount * _norm_cdf(d2) / 100.0
    else:
        price = strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta_annual = -(spot * _norm_pdf(d1) * vol) / (2 * sqrt_t) + rate * strike * discount * _norm_cdf(-d2)
        rho = -strike * years * discount * _norm_cdf(-d2) / 100.0

    gamma = _norm_pdf(d1) / (spot * vol * sqrt_t)
    # /100 converts "per unit of vol" to "per vol point", which is what a
    # broker reports and what the rest of this project assumes.
    vega = spot * sqrt_t * _norm_pdf(d1) / 100.0

    return Greeks(max(price, 0.0), delta, gamma, theta_annual / 365.0, vega, rho)


# ---------------------------------------------------------------------------
# The volatility surface
# ---------------------------------------------------------------------------


@dataclass
class SurfaceParams:
    """One symbol's vol character."""

    vol_long: float  # vol points the curve tends to far out
    vol_short: float  # vol points at the very front
    term_tau: float = 0.25  # years over which the front decays to vol_long
    skew: float = 4.0  # vol points per unit standardised moneyness
    curvature: float = 7.0  # vol points of wing lift
    event_bump: float = 0.0  # extra vol points of event variance
    event_date: date | None = None


def implied_vol(log_moneyness: float, years: float, p: SurfaceParams, as_of: date) -> float:
    """Vol points for one (strike, maturity). Not a model anyone should fit.

    Expressed in *standardised* moneyness, z = k / (sigma_atm * sqrt(T)),
    which is the coordinate a real smile is roughly stable in. A one-week
    smile and a one-year smile have similar shapes in z, and converting back
    to strike space is what makes the front month steep and the back month
    nearly flat -- the flattening falls out instead of being imposed.

    The wing term is sqrt(1 + z^2) - 1: quadratic near the money, linear far
    from it. A plain quadratic in z was the first attempt and it diverged,
    putting GOOGL's four-day wings at 219 vol points and pinning the whole
    surface against its cap. Real wings are linear in log-strike, which is
    the same reason SVI is shaped this way.
    """
    t = max(years, 1.0 / 365.0)

    atm = p.vol_long + (p.vol_short - p.vol_long) * math.exp(-t / p.term_tau)

    # An event sits at a point in time: every expiration after it carries
    # the same absolute extra variance, which is diluted by maturity. That
    # dilution is precisely what the forward-variance decomposition undoes.
    if p.event_bump and p.event_date is not None:
        years_to_event = (p.event_date - as_of).days / 365.0
        if 0 <= years_to_event <= t:
            event_variance = (p.event_bump**2) * (1.0 / 365.0)
            atm = math.sqrt(atm * atm + event_variance / t)

    z = log_moneyness / max(1e-6, (atm / 100.0) * math.sqrt(t))
    vol = atm - p.skew * z + p.curvature * (math.sqrt(1.0 + z * z) - 1.0)

    # The cap is relative to the symbol's own level, not absolute. A flat
    # ceiling let SPX -- whose ATM vol is 13 -- print 164 in its wings,
    # because low ATM vol inflates standardised moneyness. Index vol of 164
    # is the kind of detail that tells a reader the market is fake.
    ceiling = max(atm * 3.0, atm + 35.0)

    # Saturate smoothly rather than clipping. A hard min() put five adjacent
    # strikes on exactly 113.545, and a row of identical IVs across a wing
    # is the sort of artefact that tells a reader the market is generated.
    # tanh keeps the curve monotonic in strike, so the smile still reads as
    # a smile at the edges.
    return max(5.0, ceiling * math.tanh(vol / ceiling))


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------


def _tick(mark: float) -> float:
    """US option tick: a penny under $3, a nickel above."""
    return 0.01 if mark < 3.0 else 0.05


def quote(
    mark: float, vega: float, log_moneyness: float, years: float, rng: random.Random
) -> tuple[float, float]:
    """(bid, ask) around a theoretical mark.

    A market maker quotes a band of volatility, not a price, so the natural
    spread is `band x vega`. Two things stop that going to zero in the
    wings, and both are what make far-OTM IVs untrustworthy in real chains:

      * **Tick size.** You cannot quote inside a penny, so for a contract
        with vega 0.004 the minimum possible spread is already 2.5 vol
        points.
      * **Gap risk.** Nobody quotes a tail tightly, because the thing that
        moves it is a jump. The floor widens with distance from the money.

    The result reproduces the behaviour the surface's quote filter looks
    for: cheap, far, short-dated contracts end up with spreads spanning
    many vol points, and their IV stops being a measurement.
    """
    distance = abs(log_moneyness)

    # Full width of the vol band, in vol points. Widens with distance from
    # the money and with maturity: a two-year contract is quoted in far
    # looser vol terms than a front-month one, because nobody is warehousing
    # that risk cheaply. Leaving maturity out of this was an early mistake
    # -- it gave every LEAP a one-vol-point market and put the surface's
    # overall spread distribution well inside the real one.
    liquidity_penalty = 1.0 + 1.6 * math.sqrt(max(years, 0.0))
    band = (0.7 + 4.0 * distance) * liquidity_penalty + rng.uniform(-0.15, 0.35)
    spread = max(band, 0.2) * vega

    tick = _tick(mark)
    gap_floor = tick + 0.30 * max(0.0, distance - 0.05)
    spread = max(spread, gap_floor)

    # Quantise to the tick, which is where the wings lose their precision.
    spread = max(tick, round(spread / tick) * tick)

    bid = mark - spread / 2.0
    bid = math.floor(bid / tick) * tick

    # A contract worth less than half a tick has no bid. Real chains are
    # full of these and the surface is supposed to discard them.
    if bid < tick:
        bid = 0.0
        ask = max(tick, round(max(mark, tick / 2) / tick) * tick)
        return 0.0, round(ask, 2)

    return round(bid, 2), round(bid + spread, 2)


# ---------------------------------------------------------------------------
# Symbol universe
# ---------------------------------------------------------------------------


@dataclass
class SymbolSpec:
    symbol: str
    spot: float
    drift: float
    realized_vol: float  # decimal, drives the simulated price path
    surface: SurfaceParams
    strike_increment: float
    # Fraction of spot the listed ladder spans each way. Matched to what
    # the real collector requests per symbol: SPX is pulled with a handful
    # of wide strikes, single names with a deep ladder. Getting this wrong
    # changes the width of the surface heatmap, which is the first thing
    # anyone looks at.
    coverage: float = 0.26
    is_index: bool = False
    seed: int = field(default=0)


def _spec(symbol, spot, rv, vol_long, vol_short, skew, increment, bump=0.0, index=False, tau=0.25,
          coverage=0.26):
    return SymbolSpec(
        symbol=symbol,
        spot=spot,
        drift=0.06,
        realized_vol=rv,
        surface=SurfaceParams(
            vol_long=vol_long,
            vol_short=vol_short,
            term_tau=tau,
            skew=skew,
            curvature=7.0 if not index else 10.0,
            event_bump=bump,
        ),
        strike_increment=increment,
        coverage=coverage,
        is_index=index,
        seed=abs(hash(symbol)) % (2**31),
    )


# Spots and vol levels are in the right neighbourhood for each name without
# being anyone's actual quote. Index names get a steeper skew and a lower
# vol floor, which is the real stylised difference between index and single
# stock surfaces -- crash protection is structurally bid in the index.
UNIVERSE: list[SymbolSpec] = [
    _spec("SPX", 6900.0, 0.11, 16.0, 13.0, 6.0, 25.0, index=True, tau=0.45, coverage=0.09),
    _spec("AAPL", 262.0, 0.24, 27.0, 31.0, 3.7, 2.5, bump=3.2),
    _spec("MSFT", 515.0, 0.23, 26.0, 29.0, 3.3, 5.0, bump=3.0),
    _spec("GOOGL", 318.0, 0.29, 31.0, 38.0, 4.3, 2.5, bump=4.5),
    _spec("AMZN", 248.0, 0.30, 32.0, 37.0, 4.0, 2.5, bump=4.2),
    _spec("NVDA", 198.0, 0.42, 42.0, 50.0, 4.7, 2.5, bump=6.0),
    _spec("META", 735.0, 0.33, 34.0, 41.0, 4.3, 5.0, bump=5.0),
    _spec("TSLA", 445.0, 0.52, 52.0, 61.0, 5.0, 5.0, bump=7.5),
    _spec("MU", 215.0, 0.46, 46.0, 54.0, 4.7, 2.5, bump=6.5),
    _spec("AMD", 252.0, 0.44, 44.0, 51.0, 4.7, 2.5, bump=6.0),
    _spec("MRVL", 95.0, 0.50, 49.0, 57.0, 4.7, 1.0, bump=7.0),
    _spec("JPM", 312.0, 0.20, 21.0, 23.0, 3.3, 2.5, bump=2.4),
    _spec("NFLX", 1120.0, 0.34, 35.0, 42.0, 4.3, 10.0, bump=5.5),
    _spec("PLTR", 185.0, 0.62, 61.0, 72.0, 5.3, 2.5, bump=9.0),
    _spec("COHR", 135.0, 0.55, 54.0, 63.0, 5.0, 1.0, bump=7.5),
    _spec("LITE", 190.0, 0.64, 63.0, 74.0, 5.3, 2.5, bump=8.5),
    _spec("APLD", 30.0, 0.92, 90.0, 104.0, 6.0, 0.5, bump=12.0),
    _spec("BE", 95.0, 0.76, 75.0, 87.0, 5.7, 1.0, bump=10.0),
    _spec("NBIS", 105.0, 0.86, 85.0, 99.0, 5.8, 1.0, bump=11.0),
    _spec("RKLB", 52.0, 0.78, 77.0, 90.0, 5.7, 1.0, bump=10.5),
    _spec("SPCX", 28.0, 0.60, 59.0, 68.0, 5.0, 0.5, bump=7.0),
    _spec("IREN", 58.0, 0.95, 95.0, 110.0, 6.0, 1.0, bump=12.0),
    _spec("APP", 620.0, 0.58, 58.0, 68.0, 5.3, 5.0, bump=8.5),
    _spec("SNDK", 92.0, 0.70, 69.0, 80.0, 5.3, 1.0, bump=9.0),
]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def price_path(spec: SymbolSpec, days: int, end: date) -> list[tuple[date, float, float, float, float]]:
    """(date, open, high, low, close) back from `end`, newest last.

    Geometric Brownian motion. Realised vol is what the VRP panel measures
    against implied, so the two are set independently per symbol on purpose
    -- a market where implied always equals realised would make the whole
    variance-risk-premium view read zero.
    """
    rng = random.Random(spec.seed)
    dt = 1.0 / TRADING_DAYS
    mu = (spec.drift - 0.5 * spec.realized_vol**2) * dt
    sd = spec.realized_vol * math.sqrt(dt)

    # The session dates are built backwards from `end` so the series
    # *finishes* on it. Walking forwards while skipping weekends overshoots
    # -- 400 steps of one day that skip Saturdays span about 560 calendar
    # days -- which put the tail of the path in the future and left the
    # chain reading a spot from the middle of the walk. SPX was generated
    # at 6900 and the dashboard rendered 6298.
    sessions: list[date] = []
    day = end
    while len(sessions) < days + 1:
        if day.weekday() < 5:
            sessions.append(day)
        day -= timedelta(days=1)
    sessions.reverse()

    # Walk backwards from today's spot, so closes[-1] is exactly spec.spot.
    closes = [spec.spot]
    for _ in range(days):
        closes.append(closes[-1] / math.exp(mu + sd * rng.gauss(0, 1)))
    closes.reverse()

    out = []
    for session, close in zip(sessions, closes):
        intraday = abs(rng.gauss(0, sd)) * close
        open_ = close * (1 + rng.gauss(0, sd * 0.4))
        out.append(
            (
                session,
                round(open_, 2),
                round(max(open_, close) + intraday, 2),
                round(max(0.01, min(open_, close) - intraday), 2),
                round(close, 2),
            )
        )
    return out


def expirations_for(as_of: date, count: int = 14) -> list[date]:
    """Weeklies out to two months, then monthlies -- the real listing shape.

    A uniform ladder would make the term-structure chart a straight line of
    evenly spaced points and hide the fact that the front end is where all
    the resolution is.
    """
    out: list[date] = []

    friday = as_of + timedelta(days=(4 - as_of.weekday()) % 7)
    if friday == as_of:
        friday += timedelta(days=7)
    for i in range(8):
        out.append(friday + timedelta(days=7 * i))

    month_anchor = out[-1]
    while len(out) < count:
        month_anchor += timedelta(days=28)
        third_friday = month_anchor.replace(day=15)
        third_friday += timedelta(days=(4 - third_friday.weekday()) % 7)
        if third_friday > out[-1]:
            out.append(third_friday)
        else:
            out.append(out[-1] + timedelta(days=30))

    return out[:count]


def strikes_for(spot: float, increment: float, coverage: float = 0.26) -> list[float]:
    """The listed ladder: `coverage` of spot either side, on the increment."""
    each_side = max(1, int(round(spot * coverage / increment)))
    atm = round(spot / increment) * increment
    lo = atm - increment * each_side
    return [round(lo + i * increment, 2) for i in range(each_side * 2 + 1) if lo + i * increment > 0]


def occ_symbol(symbol: str, expiration: date, is_call: bool, strike: float) -> str:
    return (
        f"{symbol:<6}{expiration:%y%m%d}{'C' if is_call else 'P'}{int(round(strike * 1000)):08d}"
    )


def generate_chain(spec: SymbolSpec, as_of: date, spot: float) -> list[dict]:
    """One symbol's full chain for one session."""
    rng = random.Random(spec.seed ^ as_of.toordinal())
    surface = spec.surface

    # The event sits a few weeks out and moves with the session, so every
    # snapshot has something in front of it rather than drifting into the
    # past as the demo ages.
    surface = SurfaceParams(
        vol_long=surface.vol_long,
        vol_short=surface.vol_short,
        term_tau=surface.term_tau,
        skew=surface.skew,
        curvature=surface.curvature,
        event_bump=surface.event_bump,
        event_date=as_of + timedelta(days=18) if surface.event_bump else None,
    )

    rows: list[dict] = []
    for expiration in expirations_for(as_of):
        dte = (expiration - as_of).days
        if dte <= 0:
            continue
        years = dte / 365.0

        for strike in strikes_for(spot, spec.strike_increment, spec.coverage):
            k = math.log(strike / spot)

            vol_points = implied_vol(k, years, surface, as_of)
            vol = vol_points / 100.0

            for is_call in (True, False):
                g = black_scholes(spot, strike, years, vol, is_call)
                if g.price < 0.005:
                    continue  # not listed as a tradeable quote

                bid, ask = quote(g.price, g.vega, k, years, rng)
                mark = round((bid + ask) / 2.0, 3) if bid > 0 else round(ask / 2.0, 3)

                otm_distance = abs(k) + years * 0.5
                liquidity = math.exp(-3.0 * otm_distance)
                volume = int(max(0, rng.gauss(2200 * liquidity, 600 * liquidity)))
                open_interest = int(max(0, rng.gauss(9000 * liquidity, 2500 * liquidity)) + 40)

                rows.append(
                    {
                        "symbol": spec.symbol,
                        "snapshot_date": as_of.isoformat(),
                        "contract_symbol": occ_symbol(spec.symbol, expiration, is_call, strike),
                        "option_type": "CALL" if is_call else "PUT",
                        "expiration": expiration.isoformat(),
                        "dte": dte,
                        "strike_price": strike,
                        "bid": bid,
                        "ask": ask,
                        "mark": mark,
                        "last": mark,
                        "volume": volume,
                        "open_interest": open_interest,
                        "implied_volatility": round(vol_points, 3),
                        "delta": round(g.delta, 5),
                        "gamma": round(g.gamma, 6),
                        "theta": round(g.theta, 5),
                        "vega": round(g.vega, 5),
                        "rho": round(g.rho, 5),
                        "in_the_money": int((is_call and strike < spot) or (not is_call and strike > spot)),
                        "theoretical_value": round(g.price, 4),
                        "underlying_price": round(spot, 2),
                        "fetch_time": f"{as_of.isoformat()}T21:05:00+00:00",
                    }
                )
    return rows
