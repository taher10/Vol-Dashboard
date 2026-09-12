# Architecture patterns

Concrete, copy-this-not-that patterns established across real features on this project. If you're about to write something that doesn't match one of these shapes, stop and ask whether it should.

## Cross-symbol leaderboard endpoint

Four of these exist (`_scanner_row`, `_calendar_edge_row`, `_delta_neutral_row`, `_term_structure_row`, all in `src/api/routes.py`) and every one follows the identical shape:

```python
def _xxx_row(symbol: str, ...) -> dict | None:
    """Never raises. Returns None for a symbol with no snapshot yet, or
    whatever other precondition can't be met -- the caller skips it."""
    try:
        bundle = data_loader.load_latest_snapshot(symbol)
    except FileNotFoundError:
        return None
    # ... real computation, reusing whatever's already in bundle.metrics ...
    return {"symbol": symbol, "color": SYMBOL_REGISTRY[symbol].color, ...}


@router.get("/scanner/xxx")
def scanner_xxx(...) -> dict:
    rows = [
        row
        for symbol in SYMBOL_REGISTRY
        if (row := _xxx_row(symbol, ...)) is not None
    ]
    return {"rows": rows}
```

Why this shape specifically: a leaderboard spans every tracked symbol, and symbols legitimately differ in how much history/data they have (a symbol added yesterday has none). The walrus-comprehension-with-`is not None` pattern means one thin symbol never breaks the other 23, and the frontend never has to distinguish "this symbol errored" from "this symbol has no signal yet" — both just don't appear in `rows`. Don't add a `try/except` around the whole loop, and don't let one row's `raise` propagate — the `_xxx_row` helper absorbing every expected failure mode is what keeps this reliable at 24 symbols and growing.

When you build the frontend for a new leaderboard, reuse `ChartCard` + `VolBarChart` (bar chart, `referenceValue={0}` for any signed metric where positive/negative both mean something — e.g. IV slope, skew slope, calendar edge) plus a sortable `Table` below using the exact sort-toggle (`ArrowUp`/`ArrowDown` icon swap) pattern already in `frontend/src/app/calendar-math/page.tsx`, `term-structure/page.tsx`, and `delta-neutral/page.tsx`. All three are equally good references; pick whichever is closest in shape to what you're building.

## Option structure builders

Every structure (`build_vertical`, `build_cash_secured_put`, `build_covered_call`, `build_calendar_call`, `build_straddle_strangle`, all in `src/dashboard/strategy_engine.py`) produces the same `Candidate`/`Leg` dataclass. There are two genuinely different sub-patterns for the payoff math, and picking the wrong one is how the two real bugs below happened:

**Pattern A — `_summarize()`'s curve-sampling.** Correct only when every leg expires together *and* both max profit and max loss are genuinely bounded (verticals: the wing widths cap both sides). `_summarize()` samples the piecewise-linear expiration payoff across a price range and reads max/min/breakevens straight off the sampled curve.

**Pattern B — hand-derived closed-form math.** Required whenever a side is genuinely open-ended: a naked short leg (`build_cash_secured_put`, `build_covered_call`), a structure whose legs expire on different dates so the back leg still has real time value (`build_calendar_call`), or a straddle/strangle where one side is capped and the other isn't (`build_straddle_strangle`). In every one of these, the uncapped side must be set to `None`, never a number pulled from `_summarize()`'s bounded-price-range sampling — that sampling will happily return a finite-looking number for something that isn't actually finite, which is exactly the bug class below.

### Real bug #1: calendar spread max loss

`build_calendar_call()` initially left `max_loss = None` (correct — no pricing model for the back leg's value at front expiration), but a *downstream* leaderboard endpoint (`_calendar_edge_row` on the Calendar Math page) approximated it anyway as `abs(net_debit_credit)` — "you can't lose more than you paid." That's only true when both legs share a strike. Checked against a real OptionStrat quote for a real diagonal (front/back legs at *different* strikes, which is normal here since `build_calendar_call` matches legs by delta, not strike), the true max loss was ~15x the net debit — because if the stock rallies hard, the short leg's loss outpaces the long leg's gain by roughly the strike-width gap.

The actual fix didn't require a pricing model. At the two price boundaries (stock → 0, stock → ∞), both legs converge to pure intrinsic value (no pricing model needed at the boundaries specifically, since deep ITM options have ~zero remaining extrinsic value), so the worst case is:

```python
max_loss = max(0.0, net_debit_credit, net_debit_credit + (back_strike - front_strike) * CONTRACT_MULTIPLIER)
```

This is a genuine upper bound (ignoring the back leg's small remaining extrinsic value only ever makes the estimate *more* conservative, never less) — hand-verified to land close to, and safely above, OptionStrat's modeled figure. The lesson generalizes: before reaching for "≈ some simple proxy," check whether the real boundary cases (price → 0, price → ∞, or wherever the structure's risk is actually realized) have a clean closed-form answer. They often do, and it's almost always a better number than the nearest-sounding heuristic.

### Real bug #2: straddle/strangle uncapped side

Building `build_straddle_strangle()`, the first instinct was to reuse `_summarize()` since it was already sitting right there and the structure is a simple 2-leg, single-expiration position. That would have silently capped the genuinely-uncapped side (a short straddle's loss above the call strike, a long straddle's profit on a big move) at whatever the sampled price range happened to be — a plausible-looking but fabricated number. Caught before shipping by asking "is either side of this structure actually bounded?" before writing the payoff code, not after. Ask that question for any new structure before deciding which pattern (A or B above) applies.

### Real bug avoided #3: signed "dealer positioning" gamma exposure

Building a cross-symbol Gamma Exposure leaderboard (`_gamma_exposure_row` in `src/api/routes.py`, `/gamma-exposure` page), the industry-standard version of this metric ("GEX") is *signed*: net dealer gamma exposure, computed as call OI minus put OI weighted by gamma, built on the market convention that dealers are net long calls and short puts. That sign convention is not something this data can verify — open interest tells you how many contracts are open, not which side of the trade a dealer is on. Shipping a signed number would look like a real directional signal while actually being an unverifiable assumption wearing the data's precision.

The fix was the same shape as the other two: don't ship the fabricated-looking version, ship the honest one. The leaderboard reports an **unsigned magnitude** — total dollar-gamma summed across both calls and puts (`gamma × OI × 100 × spot² × 0.01`, summed over strikes) — plus the strike where that gamma is most concentrated, framed as "how much gamma-driven hedging flow lives in this name" and "a candidate pinning level," never as a directional call. This is the same reasoning already established elsewhere in this project for why raw OI/PCR data can't imply direction — see the Delta Neutral page's OI framing.

One side effect worth knowing: this surfaced that Schwab's data has **zero open interest for every SPX contract** in this pipeline (confirmed by direct inspection — `gamma` is populated, `openInterest` is 0 across all 1162+ rows for every expiration). `_gamma_exposure_row` correctly returns `None` for SPX rather than reporting a fabricated `$0` (which would read as "no gamma risk," not "no OI data"), so SPX silently doesn't appear on this leaderboard. If SPX ever needs this metric, the actual blocker is Schwab's OI reporting for cash-settled index options, not a bug in this code.

## New per-strike aggregate: bypass `bundle.metrics`, read `bundle.chain` directly

`_strike_profile_snapshot` (`src/api/routes.py:531`) already established that delta/gamma/OI only exist at the individual-contract level and shouldn't be pre-summarized into `bundle.metrics` (which is built by `VolatilityMetrics.compute_all()` and is inherently per-expiration: `term_structure`, `skew`, `skew_ratio`, `curvature`, `vrp` — never per-strike). `_gamma_exposure_row` is the second instance of this same shape: when a new leaderboard needs strike-level greeks, don't add a new key to `VolatilityMetrics.compute_all()` — read `bundle.chain` directly, exactly like `_strike_profile_snapshot` does (same `_live_dte`-based expiration selection, same `_underlying_price` helper, same call/put-matched-by-strike loop). This is a *different* aggregation from the "don't summarize the smile away" caution in that function's own docstring — collapsing the strike curve into one descriptive leaderboard number (total exposure, peak strike) is a legitimate, different use case from plotting the curve itself, and the two coexist fine.

## Anti-fabrication has a *presentation* layer too, not just a math layer

The first three examples in this file are all math-layer: a number was computed wrongly or couldn't honestly be computed at all. There's a second, quieter failure mode worth watching for — the math is scrupulously honest and the **UI throws the honesty away**.

Concrete case: `HistoryStore.iv_rank()` (`src/history_store.py:211`) returns `n_observations` alongside the rank, and its docstring is explicit that it needs "at least 2 stored snapshot dates" and is "meaningful only after the daily job has been running for a while." All correct. But the Overview's IV Rank card rendered only the rank, so a percentile computed off 6 observations displayed identically to one off 600 — while the function signature advertised `lookback_days=365`, implying a year of data that didn't exist. Nobody wrote a false number; the number lost its error bars on the way to the screen.

The rule that follows: **when a function hands you a sample size, a date, or a coverage count alongside a value, that context is part of the value — render it or deliberately decide not to.** Discovered when the `/data-trust` page (`src/dashboard/data_trust.py`) was built and showed several symbols sitting on 6 observations each. Prefer reporting a derived fact about the sample (that page uses `100/n`, "the finest a percentile can resolve with this many observations") over a made-up confidence tier — a fact about the data isn't a judgement, and doesn't need defending.

Related: nothing in this app distinguishes "today's snapshot" from "a snapshot from three weeks ago" at the point of use. `data_loader.load_latest_snapshot()` returns `as_of`, and only the Overview page surfaces it. Any new page showing snapshot-derived numbers should assume the data may be badly stale, because it regularly is (see `known-issues.md` — the pipeline was dead for 12 consecutive runs without anyone noticing).

## Format helpers

`fmtUsd`/`fmtNum`/`fmtSigned`/`fmtPct` (all in `frontend/src/lib/format.ts`) already handle `null`/`undefined` by rendering `"—"`. Don't add ad-hoc null guards around them — the one place worth deviating is when `null` means something more specific than "no data," like "uncapped" for an open-ended risk side (see the Delta Neutral page's table, which shows the literal word "Uncapped" instead of a dash for exactly this reason).
