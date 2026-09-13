---
name: vol-dashboard-development
description: How to build features, fix bugs, verify changes, run the tests, and manage the git/PR workflow on the Vol Dashboard project (taher10/Vol-Dashboard, an options-volatility trading dashboard with a FastAPI backend and Next.js frontend) so that development quality compounds across sessions instead of resetting every time. Covers the established architecture patterns (thin FastAPI routes, reusable leaderboard/structure-builder shapes), the anti-fabrication rule for financial calculations, when to use Plan Mode, the six-step verification checklist, the pytest suite, the four-layer monitoring stack, and the git/PR workflow given the `gh` CLI is never authenticated in this environment. ALWAYS consult this skill when working anywhere in this repo -- adding a new page/endpoint, fixing a bug, writing or running tests, verifying a change, committing/pushing/opening a PR, or troubleshooting anything about the daily Schwab data pipeline -- stale or missing data, gaps in history, a failing workflow, thin IV Rank samples, or a red Data Trust banner -- even if the user doesn't say "skill" or reference this file by name. Critically, diagnose which workflow STEP failed before assuming the Schwab token is at fault, because three distinct causes produce an identical-looking dead pipeline, and the token is not always one of them. This skill is meant to grow: at the end of any real feature or bug-fix session, fold whatever was newly learned into references/known-issues.md or references/architecture-patterns.md, and correct SKILL.md itself if the finding contradicts anything stated there.
---

# Vol Dashboard development

This skill exists because this project keeps re-learning the same lessons. Credential problems have broken the daily pipeline five separate times, and a "close enough" financial calculation has silently misstated real risk three times. Each was fixed, then forgotten, then hit again. The point of this skill isn't a style guide — it's a place for those lessons to actually stick, so the next session starts from where the last one ended instead of from zero.

The cost of not having this is concrete and unrecoverable: the pipeline died for twelve consecutive runs in 2026 and nobody noticed for a fortnight. Schwab serves current chains only, so there is no backfill — 2026-08-28 to 09-11 is a permanent hole in every history-derived number this dashboard computes.

**If you take one thing from this skill: check `references/known-issues.md` before debugging anything that looks weird, and add to it before you finish.** Most "new" problems in this repo aren't new.

**A note on keeping this file honest.** Every "closing the loop" pass so far folded findings into the reference files and left this one untouched, so it drifted behind them and ended up asserting something that had become false (that the token was always the cause). Reference files are where detail belongs, but when a finding contradicts something stated *here*, fix it here too.

## Before writing any new code: look for a shape to reuse

This backend has a real architectural rule, not just a preference: `src/api/routes.py` is a thin HTTP wrapper — parameter parsing and DataFrame-to-JSON serialization only. Every real computation lives in `src/dashboard/*.py` (`strategy_engine.py`, `decision_engine.py`, `backtest_engine.py`) or the storage layers (`src/history_store.py`, `src/schwab_database.py`). Before writing a new endpoint or a new option-structure builder, read `references/architecture-patterns.md` — there is almost certainly an existing shape to copy (a cross-symbol leaderboard endpoint, a structure builder) rather than a reason to invent a new one. Copying the existing shape isn't just tidiness: it's also what keeps the anti-fabrication rule below enforced consistently instead of re-derived per feature.

## Never fabricate a number

If a value can't be honestly computed with the data and methods already in the codebase — most often because it would require an options-pricing model this app deliberately doesn't have — leave it `None`/`null` and say why, in both the code comment and the UI copy. Do not reach for the closest-sounding approximation as a placeholder.

Four cases now, all written up in `references/architecture-patterns.md`. The first two were real shipped bugs:

- A calendar spread's max loss was computed as "≈ net debit paid." Checked against a real OptionStrat quote, this understated real risk by roughly 15x once the two legs' strikes differed. The fix was a genuine worst-case bound derived from real strike/premium algebra (boundary analysis, no pricing model needed) — a better number was available without modeling, it just required actually working out the boundary cases instead of reaching for the closest simple approximation.
- A short/long straddle's genuinely uncapped side (unlimited loss above a short call's strike, unlimited profit on a long straddle) must never get a finite number from naive payoff-curve sampling, which will silently produce one if you let it.

The other two are worth knowing because they're less obvious than a wrong formula:

- **A disclosed convention is not a licence to imply precision.** Industry "GEX" is signed, resting on an assumption about which side of the open interest dealers hold — unverifiable from this data. The leaderboard ships an unsigned magnitude instead, because a signed number would have looked like a real directional signal while being an assumption wearing the data's precision.
- **Anti-fabrication has a presentation layer, not just a maths layer.** `iv_rank()` is scrupulously honest: it returns `n_observations` and documents that it needs history to mean anything. The Overview card then threw that away and rendered a bare rank, so a percentile over 6 observations looked identical to one over 600. Nobody wrote a false number; it lost its error bars on the way to the screen. **When a function hands you a sample size, a date, or a coverage count alongside a value, that context is part of the value.**

The pattern that's held up: if a structure has a side that's mathematically open-ended, hand-derive the closed-form math for both sides explicitly (see `build_cash_secured_put`/`build_covered_call`/`build_calendar_call`/`build_straddle_strangle` in `strategy_engine.py`) rather than reusing `_summarize()`'s curve-sampling, which assumes every side is bounded.

## When to use Plan Mode

Use it when a change touches both backend and frontend, or introduces a new data/math concept (a new metric, a new option structure, a new external data source). Don't use it for a single-function bug fix, even an important one — the dte-staleness fix and the max-loss fix were both single, well-understood function changes and didn't need it. The cost of skipping Plan Mode when you needed it is redone work; the cost of using it when you didn't is a slower turn. Err toward Plan Mode when genuinely unsure, but don't reach for it reflexively.

## Verify before calling anything done

Full checklist, with the reasoning for each of its six steps, is in `references/verification-checklist.md` — read it, don't skip straight to "looks right." The short version: hand-check new formulas against a real reference with actual numbers, run `pytest` and `npx tsc --noEmit` after touching Python or TypeScript, restart both dev servers fresh when testing in the browser, read console errors from a *new* tab (stale ones accumulate misleading leftovers from restarts), and spot-check that you didn't break an existing consumer.

The step most recently added, because it cost a red CI run: **run the literal command from the workflow YAML, not a convenient equivalent.** The suite was verified with `python -m pytest` while CI ran bare `pytest`; the `-m` form implicitly puts the working directory on `sys.path` and the bare one doesn't, so `from src...` resolved locally and never could in CI. "It works on my machine" is two assumptions — the command *and* the environment — and both have broken this project.

## Git, commits, and PRs

Full detail in `references/git-workflow.md`. The two things most likely to bite you if skipped: the `gh` CLI has never been authenticated in this environment, so don't attempt `gh pr create` — push the branch and hand back a manual compare URL instead. And never stage `database/schwab_database.db`, `history/vol_history.db`, or `src/.vscode/` — they're local/regenerable or personal, not project state.

## If the data looks stale or the daily pipeline seems broken

**Identify which *step* of the workflow failed before prescribing anything.** `references/known-issues.md` opens with the exact unauthenticated API call that tells you, and a table mapping each failing step to its cause. Use it — this is the single highest-value thing in the whole skill.

An earlier version of this file said the Schwab token "has been the cause every single time." That was wrong, and believing it wasted real time. Three genuinely different causes have now produced an identical-looking symptom (the pipeline is dead, the data is stale):

- Failure at `Run daily snapshot` with `invalid_grant` — expired refresh token.
- Failure at `Restore token.json from secret` — the secret itself is unusable, and unlike an expired token this **cannot self-heal**, because `token_sync` only runs after a successful fetch.
- Failure at `Run daily snapshot` where *every* symbol reports the same **non-auth** error — dependency drift, not a token problem at all. Nothing is pinned, so CI resolves fresh versions each run while local environments don't.

The failing step can even change partway through one outage: a recoverable expired token became an unrecoverable bad secret mid-way through the 2026-08-28 outage. So re-check the step rather than trusting last week's diagnosis.

There is also a `data-validator` agent (`.claude/agents/data-validator.md`) that runs this whole procedure for you.

## Monitoring, and what each layer can and can't tell you

Four scheduled workflows, and the distinction between them matters — conflating them is what let a 12-run outage go unnoticed for a fortnight:

| Workflow | Answers | Blind to |
|---|---|---|
| `preflight-auth.yml` (15:00 UTC) | will tonight's run authenticate? | anything after auth |
| `daily-snapshot.yml` (21:30 UTC) | collects the data | — |
| `pipeline-heartbeat.yml` (22:15 UTC) | did the workflow *run*? | whether it **succeeded** |
| `data-validation.yml` (22:45 UTC) | did data actually *land*, and is it sound? | — |

The heartbeat's blind spot is the important one: during 2026-08-28 to 09-11 a run existed every weekday and failed every weekday, so the heartbeat passed for twelve consecutive days while nothing was collected. "It ran" was never the same as "it worked."

## Tests

There's a pytest suite (`tests/`, 56 tests) over the monitoring logic — `data_trust`, `validate_collection`, and `data_quality.degenerate_metric_mask`. Run it with `pytest` (config in `pytest.ini`); `pytest` lives in `requirements-dev.txt`, deliberately kept out of `requirements.txt` so the collection workflows don't install a test runner they never use.

Two habits worth keeping when you extend it:

- **Test that the checks stay quiet, not just that they fire.** Roughly a third of the suite asserts silence — on expiry Fridays, on the pre-VRP history, on newly added symbols. An alert that fires every week gets muted, and a muted alert is how the outage above went unseen.
- **Verify the tests by breaking the code.** Passing tests prove nothing on their own. Deliberately mutating the source caught a genuinely weak test here: a bound parametrised as `MAX_PLAUSIBLE_IV + 1` moves with the threshold, so loosening 500 to 5000 broke nothing. Literal observed values fixed it.

## Closing the loop

Before you consider a feature or fix finished, ask: did anything happen this session that isn't already written down here? A new reusable pattern, a gotcha that cost real time to figure out, a design decision with real reasoning behind it (like the four anti-fabrication cases above)? If so, add it to the relevant reference file now, in your own words, with enough context that a future session with zero memory of this conversation would actually benefit from it. That step is what makes this a skill instead of a snapshot.

Two things that make the difference between a useful entry and a useless one:

- **Write what you verified, and say so when you didn't.** The strongest entries here carry the actual evidence — the failing step name, the real numbers, the command that reproduces it. The weakest carried a plausible inference stated as fact, and one of those (that dependency drift was caused by `token_sync` corrupting the secret) turned out to be wrong. Label a hypothesis as a hypothesis and the next session can disprove it cheaply instead of building on it.
- **Check whether the new finding contradicts this file, not just the references.** See the note at the top — that failure mode has already happened once.
