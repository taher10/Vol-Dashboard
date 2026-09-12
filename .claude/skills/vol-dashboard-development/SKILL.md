---
name: vol-dashboard-development
description: How to build features, fix bugs, verify changes, and manage the git/PR workflow on the Vol Dashboard project (taher10/Vol-Dashboard, an options-volatility trading dashboard with a FastAPI backend and Next.js frontend) so that development quality compounds across sessions instead of resetting every time. Covers the established architecture patterns (thin FastAPI routes, reusable leaderboard/structure-builder shapes), the anti-fabrication rule for financial calculations, when to use Plan Mode, the verification checklist, and the git/PR workflow given the `gh` CLI is never authenticated in this environment. ALWAYS consult this skill when working anywhere in this repo -- adding a new page/endpoint, fixing a bug, running verification, committing/pushing/opening a PR, or troubleshooting why the daily Schwab data pipeline looks broken -- even if the user doesn't say "skill" or reference this file by name. This skill is meant to grow: at the end of any real feature or bug-fix session, come back and fold whatever was newly learned into references/known-issues.md or references/architecture-patterns.md before finishing.
---

# Vol Dashboard development

This skill exists because this project has already re-learned the same lessons more than once — the Schwab token expiring has broken the daily pipeline three separate times, and a "close enough" financial calculation has silently understated real risk twice. Both were fixed, then forgotten, then hit again. The point of this skill isn't a style guide — it's a place for those lessons to actually stick, so the next session starts from where the last one ended instead of from zero.

**If you take one thing from this skill: check `references/known-issues.md` before debugging anything that looks weird, and add to it before you finish.** Most "new" problems in this repo aren't new.

## Before writing any new code: look for a shape to reuse

This backend has a real architectural rule, not just a preference: `src/api/routes.py` is a thin HTTP wrapper — parameter parsing and DataFrame-to-JSON serialization only. Every real computation lives in `src/dashboard/*.py` (`strategy_engine.py`, `decision_engine.py`, `backtest_engine.py`) or the storage layers (`src/history_store.py`, `src/schwab_database.py`). Before writing a new endpoint or a new option-structure builder, read `references/architecture-patterns.md` — there is almost certainly an existing shape to copy (a cross-symbol leaderboard endpoint, a structure builder) rather than a reason to invent a new one. Copying the existing shape isn't just tidiness: it's also what keeps the anti-fabrication rule below enforced consistently instead of re-derived per feature.

## Never fabricate a number

This is the rule this project has broken (and caught) twice. If a value can't be honestly computed with the data and methods already in the codebase — most often because it would require an options-pricing model this app deliberately doesn't have — leave it `None`/`null` and say why, in both the code comment and the UI copy. Do not reach for the closest-sounding approximation as a placeholder. Two real, hand-verified examples of exactly this mistake, both in `references/architecture-patterns.md` for the full story:

- A calendar spread's max loss was computed as "≈ net debit paid." Checked against a real OptionStrat quote, this understated real risk by roughly 15x once the two legs' strikes differed. The fix was a genuine worst-case bound derived from real strike/premium algebra (boundary analysis, no pricing model needed) — a better number was available without modeling, it just required actually working out the boundary cases instead of reaching for the closest simple approximation.
- A short/long straddle's genuinely uncapped side (unlimited loss above a short call's strike, unlimited profit on a long straddle) must never get a finite number from naive payoff-curve sampling, which will silently produce one if you let it.

The pattern that's held up: if a structure has a side that's mathematically open-ended, hand-derive the closed-form math for both sides explicitly (see `build_cash_secured_put`/`build_covered_call`/`build_calendar_call`/`build_straddle_strangle` in `strategy_engine.py`) rather than reusing `_summarize()`'s curve-sampling, which assumes every side is bounded.

## When to use Plan Mode

Use it when a change touches both backend and frontend, or introduces a new data/math concept (a new metric, a new option structure, a new external data source). Don't use it for a single-function bug fix, even an important one — the dte-staleness fix and the max-loss fix were both single, well-understood function changes and didn't need it. The cost of skipping Plan Mode when you needed it is redone work; the cost of using it when you didn't is a slower turn. Err toward Plan Mode when genuinely unsure, but don't reach for it reflexively.

## Verify before calling anything done

Full checklist, with the reasoning for each step, is in `references/verification-checklist.md` — read it, don't skip straight to "looks right." The short version: hand-check new formulas against a real external reference with actual numbers, run `npx tsc --noEmit` in `frontend/` after any TypeScript change, restart both dev servers fresh when testing in the browser, and always read console errors from a *new* tab rather than one that was already open during a dev-server restart (stale tabs accumulate misleading leftover errors).

## Git, commits, and PRs

Full detail in `references/git-workflow.md`. The two things most likely to bite you if skipped: the `gh` CLI has never been authenticated in this environment, so don't attempt `gh pr create` — push the branch and hand back a manual compare URL instead. And never stage `database/schwab_database.db`, `history/vol_history.db`, or `src/.vscode/` — they're local/regenerable or personal, not project state.

## If the data looks stale or the daily pipeline seems broken

Check `references/known-issues.md` first — specifically the Schwab token section. This has been the cause every single time so far.

## Closing the loop

Before you consider a feature or fix finished, ask: did anything happen this session that isn't already written down here? A new reusable pattern, a gotcha that cost real time to figure out, a design decision with real reasoning behind it (like the two anti-fabrication fixes above)? If so, add it to the relevant reference file now, in your own words, with enough context that a future session with zero memory of this conversation would actually benefit from it. That step is what makes this a skill instead of a snapshot.
