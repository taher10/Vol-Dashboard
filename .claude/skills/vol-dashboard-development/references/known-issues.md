# Known issues log

This file is meant to grow. When you hit something that cost real time to figure out — a recurring failure, a non-obvious gotcha, a "why does this keep happening" — add it here in the same format as the entries below, even if it feels minor. The whole point of this file is that the next session (which has none of this conversation's memory) gets to skip straight to the fix.

Newest entries at the top. Each entry: what it looks like, why it happens, the actual fix, and how many times it's recurred (a growing count on the same issue is a signal the fix needs to be made more automatic, not just documented better).

---

## Schwab OAuth refresh token expires after ~7 days of disuse

**Recurred: 3 times.**

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

## Template for new entries

```markdown
## <short description of the symptom>

**Recurred: N time(s).**

**What it looks like:** <the actual error message or observed behavior, verbatim if possible>

**Why it happens:** <the real root cause, not just the symptom>

**The fix:** <the actual steps, in order, specific enough that a fresh session with zero context could follow them>
```
