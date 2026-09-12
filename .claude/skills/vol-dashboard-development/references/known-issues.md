# Known issues log

This file is meant to grow. When you hit something that cost real time to figure out — a recurring failure, a non-obvious gotcha, a "why does this keep happening" — add it here in the same format as the entries below, even if it feels minor. The whole point of this file is that the next session (which has none of this conversation's memory) gets to skip straight to the fix.

Newest entries at the top. Each entry: what it looks like, why it happens, the actual fix, and how many times it's recurred (a growing count on the same issue is a signal the fix needs to be made more automatic, not just documented better).

---

## FIRST: check *which step* failed before assuming it's the token

The daily-snapshot workflow has now failed for two genuinely different reasons that look identical from the outside ("the pipeline is dead, data is stale"). Which step failed tells you which problem you have, and the fixes are different — so always check this first rather than reaching for the token re-auth below. No auth needed:

```bash
RID=$(curl -s "https://api.github.com/repos/taher10/Vol-Dashboard/actions/workflows/daily-snapshot.yml/runs?per_page=1" | python3 -c "import json,sys;print(json.load(sys.stdin)['workflow_runs'][0]['id'])")
curl -s "https://api.github.com/repos/taher10/Vol-Dashboard/actions/runs/$RID/jobs" | python3 -c "
import json,sys
for j in json.load(sys.stdin)['jobs']:
    for s in j['steps']: print(s['conclusion'], s['name'])
"
```

- Failed at **`Run daily snapshot`** → expired refresh token. See the section below.
- Failed at **`Restore token.json from secret`** → the `SCHWAB_TOKEN_B64` secret itself is unusable (empty, malformed base64, or not JSON). See the section below that.
- Failed at **`Commit history if it changed`** → repo size / Git LFS. See further below.

---

## `SCHWAB_TOKEN_B64` secret unusable — and it can never self-heal

**Recurred: 2 times (2026-07-29 → 08-05, and 2026-09-10 → ongoing).**

**What it looks like:** the workflow fails at **`Restore token.json from secret`**, and `Run daily snapshot` shows as `skipped` — the pipeline never even attempts to fetch. Unlike an expired token, this never recovers on its own, no matter how many days pass. Symptom on the dashboard: every page silently keeps rendering the last good snapshot (weeks old) with no visible indication anything is wrong.

**Watch for it following an expired-token outage.** In the 2026-08-28 outage the workflow failed at `Run daily snapshot` (expired token) for ten consecutive runs, then on 2026-09-10 switched to failing at `Restore token.json from secret` — a recoverable problem became an unrecoverable one partway through. Worth knowing because it means *the failing step can change mid-outage*: re-check it rather than assuming the cause you diagnosed last week still applies.

**Unconfirmed but worth checking first if this recurs:** `Persist refreshed token back to SCHWAB_TOKEN_B64` runs with `if: always()`, so it executes (and reports success) even on runs where the snapshot failed. `token_sync.py` itself looks safe — it no-ops when `token.json` is absent, and `base64.b64encode` can't introduce the trailing newline that breaks decoding — but it will faithfully push whatever `token.json` holds. If schwab-py truncates or rewrites that file on a failed refresh, the sync would then overwrite a merely-expired secret with an unusable one, which matches the observed transition. Confirming needs the CI logs (requires auth). The alternative, equally plausible explanation is a manual secret update done without `| tr -d '\n'`. Distinguish by asking whether anyone touched the secret around the transition date before assuming the automation did it.

**Why it happens:** the step calls `write_token_from_base64()` (`src/auth.py:40`), which validates eagerly and raises `InvalidTokenSecretError` if the secret is empty, isn't valid base64, or doesn't decode to JSON. The most likely cause is the secret being set without `| tr -d '\n'` (a newline breaks the decode), or being cleared/never re-set.

**The structural trap — why this one is worse than an expired token:** `src/token_sync.py` (the "Persist refreshed token back to `SCHWAB_TOKEN_B64`" step) is what normally keeps the refresh token alive forever, and it only runs *after* a successful snapshot. If the secret becomes unreadable, the snapshot never runs, so the sync never runs, so the secret is never repaired — a permanent deadlock that needs a human to break. That's the difference from the token-expiry issue below, which at least self-heals once re-seeded. Don't expect this one to fix itself while you look at something else.

**The fix (only the user can do this — it needs their browser and their repo settings):**

1. Locally: `python -m src.job --first-time` (interactive Schwab login).
2. `base64 -i token.json | tr -d '\n'` — **the `tr -d '\n'` is not optional**, a trailing newline is itself a likely cause of this failure.
3. Paste into GitHub → Settings → Secrets and variables → Actions → `SCHWAB_TOKEN_B64` → Update.
4. Manually re-run the workflow and confirm `Restore token.json from secret` passes, rather than waiting for the next cron and assuming.

**Worth raising, not silently re-fixing:** the workflow has a `Notify on workflow-level failure` step that posts to `NOTIFY_WEBHOOK_URL` and ran (successfully) on all 12 failed runs. Either that secret isn't configured or the alerts aren't reaching anyone — because two weeks of total data loss went unnoticed. Fixing the alerting is worth more than fixing this instance of the token.

---

## Schwab OAuth refresh token expires after ~7 days of disuse

**Recurred: 5 times.** (3 previously documented, plus two more confirmed from the workflow run history on 2026-09-12: **2026-08-14 → 08-20**, recovered 08-21, and **2026-08-28 → 09-09**, which then degraded into the secret-unusable failure above.)

**This has now passed the "stop re-fixing it by hand" threshold this file set after recurrence 3.** The `token_sync.py` auto-refresh was supposed to end this permanently and demonstrably hasn't — the pipeline has failed in roughly alternating fortnights all year. The next fix should target *why nobody noticed for two weeks* (see the alerting note below) rather than re-seeding the token again, because a silent 12-run outage is the actual damage, not the token itself.

**What it looks like:** the daily GitHub Actions snapshot workflow starts failing at the "Run daily snapshot" step (not the git-commit step), every single day, with no code changes to explain it. Locally, `python -m src.job` or any `/api/refresh` call fails with:
```
"error_description":"Refresh token is invalid, expired or revoked","error":"invalid_grant"
```

**Why it happens:** Schwab's refresh token has an absolute ~7-day lifespan and rotates on every use (each refresh invalidates the previous token). `src/token_sync.py` is supposed to keep this alive indefinitely by pushing the freshly-rotated token back to the `SCHWAB_TOKEN_B64` GitHub secret after every successful CI run — but this only works if BOTH halves of the fix actually get done. Every recurrence so far has been the same partial fix: the user reauthenticates locally (fixing `token.json` on their machine) but never updates the CI secret, so CI keeps reading the old dead token while local data collection works fine — which makes it easy to think the problem is solved when only half of it is.

**The fix — always both steps, never just the first:**

1. Locally: `python -m src.job --first-time` (opens a Schwab login in the browser — this is the one part that needs interactive human auth, can't be automated).
2. **Then, immediately, don't skip this:**
   ```bash
   base64 -i token.json | tr -d '\n'
   ```
   Copy the output → GitHub repo → Settings → Secrets and variables → Actions → `SCHWAB_TOKEN_B64` → Update → paste → Save.
3. Verify by manually re-running the daily-snapshot workflow (or checking `curl -s "https://api.github.com/repos/taher10/Vol-Dashboard/actions/workflows/daily-snapshot.yml/runs?per_page=3"` for a fresh success) rather than just assuming it worked.

If this recurs a 4th time, the real fix is probably to stop relying on manual completion of step 2 — e.g. a startup check in the daily workflow that fails loudly and immediately (not just eventually via `invalid_grant`) with a clear "you only did step 1 last time" message, or a scheduled reminder. Worth raising with the user rather than just fixing it silently again.

---

## Repo size / Git LFS for `database/schwab_database.db`

**Recurred: 1 time (so far).**

**What it looks like:** the daily-snapshot workflow's "Commit history if it changed" step fails, even though the actual data-fetch step succeeded for every symbol.

**Why it happens:** `database/schwab_database.db` grows ~13-14MB/day (raw per-contract options chain data accumulating). It crossed GitHub's 100MB per-file push limit.

**The fix:** the file is now tracked via Git LFS (`.gitattributes`), which was the actual fix applied. GitHub's free LFS tier is 1GB storage/bandwidth per month — at the growth rate above, that's roughly 10 weeks of runway before this needs revisiting (either a retention window that prunes old raw snapshots, or a paid LFS data pack). If this recurs, check LFS usage/quota first before assuming it's a new problem.

---

## A skill created mid-session isn't invocable via the `Skill` tool until restart

**Recurred: 1 time (so far, but structural -- will recur for anyone building a new skill).**

**What it looks like:** you write a new project skill's `SKILL.md` and reference files to `.claude/skills/<name>/`, then try to actually invoke it with the `Skill` tool in the same session (e.g. to test it) and get `Unknown skill: <name>`, even though the files are correctly written and committed.

**Why it happens:** project skills appear to get indexed once, at session start. Files created after that point aren't picked up by the `Skill` tool's lookup within the same session, regardless of whether they're valid or on disk.

**The fix:** there isn't one within the same session -- read the skill's `SKILL.md`/`references/*.md` files directly with the Read tool and follow them manually; that achieves the same outcome as invoking the skill. To actually confirm the `Skill` tool sees it, that requires a fresh session (new conversation) after the files exist. Don't burn time re-trying the `Skill` tool call in a loop within the same session expecting a different result.

---

## SPX has zero open interest in every contract, in this data source

**Recurred: 1 time (so far).**

**What it looks like:** a new per-strike aggregate (first hit building the Gamma Exposure leaderboard) silently has no row for SPX, while every other symbol does -- no error, just a missing row.

**Why it happens:** confirmed by direct inspection of `bundle.chain` for SPX: `gamma` is populated normally, but `openInterest` is exactly `0` for every single contract, every expiration. This looks like a real gap in how Schwab reports OI for cash-settled index options through this API, not a bug in this codebase's fetch/parse logic (other numeric fields on the same rows are fine).

**The fix:** none needed on this end -- any OI-weighted per-strike metric (gamma exposure, and anything similar built later) should expect SPX to legitimately return `None`/be absent rather than try to "fix" it into showing a `0`. A `0` here would be worse than missing, since it reads as "no gamma risk" rather than "no OI data." If a future feature genuinely needs SPX's OI, that's a Schwab data-availability question to chase down externally, not something to work around by approximating a number that isn't there.

---

## Template for new entries

```markdown
## <short description of the symptom>

**Recurred: N time(s).**

**What it looks like:** <the actual error message or observed behavior, verbatim if possible>

**Why it happens:** <the real root cause, not just the symptom>

**The fix:** <the actual steps, in order, specific enough that a fresh session with zero context could follow them>
```
