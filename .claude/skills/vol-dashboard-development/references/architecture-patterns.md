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

## Format helpers

`fmtUsd`/`fmtNum`/`fmtSigned`/`fmtPct` (all in `frontend/src/lib/format.ts`) already handle `null`/`undefined` by rendering `"—"`. Don't add ad-hoc null guards around them — the one place worth deviating is when `null` means something more specific than "no data," like "uncapped" for an open-ended risk side (see the Delta Neutral page's table, which shows the literal word "Uncapped" instead of a dash for exactly this reason).
