# Known issues log

This file is meant to grow. When you hit something that cost real time to figure out — a recurring failure, a non-obvious gotcha, a "why does this keep happening" — add it here in the same format as the entries below, even if it feels minor. The whole point of this file is that the next session (which has none of this conversation's memory) gets to skip straight to the fix.

Newest entries at the top. Each entry: what it looks like, why it happens, the actual fix, and how many times it's recurred (a growing count on the same issue is a signal the fix needs to be made more automatic, not just documented better).

---

## A clean `git status` does not mean a branch switch is safe

**Recurred: 1 time (2026-11-24), and it destroyed live data in a tree that `git status` called clean.**

**What it looks like:** you replace a large tracked file with an untracked local
replacement at the same path (or near it), `git status` reports clean because the
new path is gitignored, you switch branches to start unrelated work — and the
replacement is gone.

**Why it happens.** Two steps, neither of which warns:

1. Checking out a commit where that path **was** tracked makes git materialize it.
   For an LFS path the smudge filter writes the real blob straight over whatever
   is sitting there. Git does not consider this a conflict: it is writing a file
   it believes it owns, and the thing it overwrote was never in the index.
2. Fast-forwarding from there to a commit where the path was *removed* then
   deletes it.

So `git checkout main && git merge --ff-only origin/main` — where local `main`
still points at a pre-removal commit — silently overwrites and then deletes.
`git status` was clean the whole time, so nothing was stashable and the
"stash before you discard" rule in `git-workflow.md` does not help here.

**The actual fix** is to check what the *local* ref points at, not just the tree:

```bash
git ls-tree <target-ref> path/to/file   # empty output = safe to switch
```

Do this for any path holding real local data before switching. `git checkout -b
new-branch origin/main` is safer than `git checkout main` + merge, because it
goes straight to the target commit instead of transiting a stale local `main`.

**What saved it, and will not always:** the file was byte-identical to a cached
LFS object under `.git/lfs/objects/`, so it could be restored with a plain `cp`
and the derived store rebuilt from it. `git lfs prune` would have removed that
cache. Verify a cache object before trusting it — **an LFS object's filename is
the sha256 of its contents**, so `shasum -a 256` against the filename proves
both identity and integrity in one step.

**Also note what was *not* recoverable from the derived data.** The partitions
survived (untracked, never tracked, so nothing touched them), but the 1MB index
file held `stock_prices` and `precomputed_metrics`, which live nowhere else.
`rebuild_index()` reconstructs chain rows from partitions and would have
silently produced a store missing both. Re-deriving from the legacy source was
the only complete fix.

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

- Failed at **`Run daily snapshot`** → two possibilities, and the run summary tells them apart. If the per-symbol failure detail says `invalid_grant` / "Refresh token is invalid", it's an expired refresh token (section below). If *every* symbol failed with the same non-auth error, it's almost certainly a dependency drift — see "An unrelated dependency can break the Schwab pipeline" below, and don't touch the token.
- Failed at **`Restore token.json from secret`** → the `SCHWAB_TOKEN_B64` secret itself is unusable (empty, malformed base64, or not JSON). See the section below that.
- Failed at **`Fetch the chain store`** → the `chain-latest` release assets are missing or unreachable, or the release still holds the pre-migration single-table database. The job stops here on purpose: appending to an empty store and publishing that would replace every collected session. See "The chain store" below.
- Failed at **`Commit history if it changed`** → repo size. `history/vol_history.db` is 2.6MB and ordinary, so this is now rare; it used to mean Git LFS. See further below.
- Failed at **`Publish the chain database`** → the day's chains could not be saved. Read the message: it distinguishes "no baseline" (the download step never finished) from "fewer rows than we started with" (the database was reset or truncated mid-run). Both are refusals, not crashes.

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

## The chain store: compressed monthly partitions published as release assets

**Recurred: 4 times, each time as a different-looking failure.**

**The history, because the shape repeats and the explanations kept being wrong:**

| When | State | What broke |
|---|---|---|
| 2026-08-14 | committed to git | crossed the 100MB per-file push limit; "Commit history" failed silently while fetch and token steps passed |
| 2026-08-20 | moved to Git LFS | fixed that. Comment claimed a "1GB/month" tier and "~10 weeks of runway" — **there is no such tier**; it is 10GB of storage |
| 2026-10-15 | LFS, 1.8GB used | read as comfort, and the comment was rewritten to say LFS was *not* the near-term constraint |
| 2026-11-19 | LFS, 9.01GB of 10GB | one run from a hard stop |
| 2026-11-22 | release asset, tag `chain-latest` | LFS usage frozen — but the next ceiling was **2GB per asset**, ~114 sessions, ~5.4 months. A known future wall is not a fix |
| 2026-11-22 | compressed monthly partitions | 658MB → 52MB (12.6x). Nothing left with a ceiling that arrives |
| 2026-11-24 | partitions published, live | migrated in CI **from Git LFS**, because the seed asset the plan depended on was never published — see the ordering trap below |

**Why it kept being misread:** LFS cannot delta-compress a SQLite file, so **every push stores a complete new copy**. 58 commits × a file growing to 658MB = 9GB, with the daily growth rate (12.2MB/session) never once changing. Storage is a **stock, not a flow** — it does not reset monthly, and deleting branches or rewriting history does not reclaim it. Anyone reasoning from "how fast does the file grow" gets a number that is five times too small.

With a `$0` Git LFS budget the overage is **refused rather than billed**, so the wall at 10GB is `git push` being rejected: data collected, then unsavable.

**Why moving it was not enough.** The release-asset migration was correct and insufficient: it replaced a wall at 10GB of LFS with a wall at 2GB per asset, about 5.4 months out. Building on storage whose failure date you can calculate is the thing to avoid, not the particular date.

**What actually made the growth sustainable.** Three findings, all from measuring rather than guessing:

- **36% of the 658MB was index.** `idx_options_symbol_date` duplicated the primary key's own leading columns outright. `idx_options_contract` cost 80MB to serve one endpoint the frontend has never called.
- **`symbol`, `snapshot_date` and `option_type` were repeated strings** on every one of 2,042,262 rows.
- **Every read was `WHERE symbol = ? AND snapshot_date = ?`** returning a whole DataFrame. The table paid full relational cost for a key-value access pattern.

So a chain is now one compressed blob per `(symbol, snapshot_date)`, keyed the way it is read. `src/chain_codec.py` has the format and the numbers: **13.5x smaller, and 2.8ms to decode a chain against 6.3ms for the SELECT it replaced.** Faster *and* smaller, because there is no row materialization and no index descent.

**The arrangement now:**

```
database/schwab_database.db           ~1MB    chain_index + stock_prices + precomputed_metrics
database/schwab_database_chains/
    2026-07.db ... 2026-11.db         ~10MB each, one per calendar month
```

Both untracked and gitignored. Published on the `chain-latest` release as `schwab_database.db` plus `chains-YYYY-MM.db`. `daily-snapshot.yml` fetches **the two newest partitions** (`sync --newest 2`), appends, and uploads what it holds. `src/chain_release.py` owns both directions.

**Nothing here has a ceiling that arrives:** a month's partition is bounded by the length of a month; the asset count grows by twelve a year against no meaningful limit; a nightly run moves ~30MB whatever year it is.

**The ordering trap, which did happen (2026-11-24).** The migration was staged as
three pull requests with a manual workflow dispatch required *between* the first
and the second: PR 1 added a `publish-chain.yml` that seeded the release from
LFS, and PR 2 **deleted that workflow** as cleanup. All three were merged in one
sitting without dispatching it. Result: no `chain-latest` release existed, the
workflow that would have created it was gone from `main`, and the migration job
— which downloaded the asset it expected to find — had no input.

Two lessons, in order of usefulness:

- **A plan step that lives only in a human's head between two merges will be
  skipped.** If a sequence needs a manual action between merges, the later PR
  must fail loudly without it, not quietly assume it happened. Deleting the tool
  that performs step 1 inside the PR for step 2 removes the ability to recover.
- **Prefer deriving from a source that cannot go missing.** The fix was to
  migrate directly from the LFS blob at a **pinned commit** (the parent of the
  commit that untracked the file) via a second `actions/checkout` with
  `lfs: true` and `path:`. That needs `.gitattributes` to track the path *at
  that commit*, which it does — at `main` it does not, which is why hand-rolled
  `git lfs pull` does not work here. This was also strictly better than the
  original plan: the 658MB file never became a release asset at all.

It failed safe because `sync()` refuses a release it cannot list rather than
starting empty. The cost was one night of collection at most, not the archive.

**What to know before changing it:**
- **Compare dumps, not bytes, across machines.** Two SQLite files holding
  identical data are byte-identical only within one SQLite version. The runner's
  3.45.1 and a local 3.50.2 differ in ~7,000 bytes per 9MB partition — page
  padding and the version field at offset 96 — while every `payload` BLOB and
  the page count match exactly. Verify with `sqlite3.iterdump()` (or
  `.dump`), which was identical for all six published files; a `shasum`
  comparison will report a false mismatch and send you hunting a corruption
  that is not there.
- **The index is derived, never the record.** `rebuild_index()` reconstructs it from the partitions, which is why a corrupt 1MB file costs a scan instead of orphaning every chain behind it.
- **Never call `rebuild_index()` on a partial store.** It derives the index from the partitions *present*, so a CI run holding two months would rebuild an index describing two months — and publishing that over one describing five erases the only record of the rest. `SchwabDatabase` heals an index that is *empty*, never one that is merely partial. This is also why the index file is published rather than regenerated on arrival.
- **Never let a job start from an empty store.** The fetch step fails loudly, `sync` refuses when it cannot *list* the release (so "could not find out" never resolves to "nothing exists"), and the publish step refuses without a baseline to compare against.
- **Nothing may shrink during a run.** `chain_release baseline` is taken right after the fetch; `check --baseline` refuses any count that went down, per partition and for the index. Exit 3 means "consistent, nothing collected" — a run outside market hours — and leaves the release alone.
- **The storage format must stay exact, not merely close.** pandas' JSON round-trip returns `0.30000000000000004` for a stored `0.3` and corrupts 9 of 17 numeric columns. Schwab serves current chains only, so there is nothing to recompute from. `_encode_frame` in `schwab_database.py` still uses JSON and that is fine *only* for `precomputed_metrics`, which is derived.
- **The migration is verified slice-by-slice, not sampled.** All 1,177 chains / 2,042,262 rows compared under `assert_frame_equal(check_exact=True, check_dtype=True)`. Keep that bar for any future format change.
- **`contract_history` is the one read the layout does not favour** — it crosses every date instead of selecting one, so it decodes each of a symbol's chains. That is the trade for dropping the 80MB index, and it is well under a second.
- **The 9.01GB of LFS is permanent.** GitHub will not release it without support intervention. Staying flat below 10GB is the goal, not getting it back.
- **Pre-migration states are still recoverable** — the LFS objects remain in git history, so `git lfs pull` at an old commit works.
- Check `github.com/settings/billing/summary` before acting on any figure, including these.

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

## An unrelated dependency can break the Schwab pipeline (CI installs differ from local)

**Recurred: 1 time (2026-09-12).**

**What it looks like:** the workflow gets past auth and into the per-symbol loop, then *every* symbol fails with the identical non-auth error. The first real instance:

```
❌ 24 failed: AAPL, AMD, AMZN, ...
AAPL: 'Timeout' object cannot be interpreted as an integer
```

The giveaway that this isn't a token problem: auth succeeded (the run reached the fetch loop), and all 24 symbols failed the same way. A token problem fails differently and says so.

**Why it happens:** `requirements.txt` pins nothing — every entry is `>=`. CI runs `pip install -r requirements.txt` on a fresh runner, so it resolves to whatever is newest that day, while your local venv keeps whatever it installed months ago. The two environments drift apart silently and the *local* one is the misleading one, because it's the one that still works.

The actual chain, which is worth reading because nothing about it is local to the code that broke:

1. `anthropic` (used only by the chatbot in `src/api/chat.py` — nothing to do with Schwab) released a major version that requires the new `httpx2` package.
2. `httpx2` therefore got installed in CI. It was never installed locally.
3. Authlib 1.8.0 has a compat shim that *prefers* `httpx2` and only falls back to legacy `httpx` when httpx2 is absent. Its mere presence flipped Authlib onto it.
4. schwab-py's session is an Authlib client, and `src/auth.py` was assigning `client.session.timeout = httpx.Timeout(...)` — a `Timeout` from the *legacy* library, now a foreign type to the httpx2-backed session. httpx2 tried to coerce it to a number and raised.

**The fix:** assign a plain number, not a library-specific object (`src/auth.py`, both `authenticate()` and `get_client()`). Both libraries accept a float, so the coupling is gone. Full reasoning is in that module's docstring.

**The generalisable lesson:** when CI fails and local works with no code change, diff the environments before debugging the code. This reproduces it exactly, and takes about a minute:

```bash
python -m venv /tmp/fullenv && /tmp/fullenv/bin/pip install -q -r requirements.txt
diff <(pip list) <(/tmp/fullenv/bin/pip list)
```

Pay attention to packages present in CI and *absent* locally, not just version bumps — `httpx2` appearing out of nowhere was the whole story here, and no version number in the diff would have pointed at it.

**Still unpinned, deliberately.** Pinning would have prevented this, but the trigger was `anthropic`, and freezing that would hold back the chatbot SDK for the sake of the data pipeline. The float fix removes the coupling that made the drift fatal. If this class of failure recurs with a different package, revisit — a constraints file covering just the Schwab path (`schwab-py`, `authlib`) is the narrower option.

---

## Anything opening `database/schwab_database.db` breaks in CI

**Recurred: 2 times (2026-09-15 data-validation, 2026-09-22 tests).**

**What it looks like:** a workflow that passes locally dies in CI with

```
sqlite3.DatabaseError: file is not a database
```

**Why it happens:** the chain store is not in the repository — it is a set of
release assets. Only `daily-snapshot.yml` fetches it, because it has to in
order to append to it. Every other workflow deliberately does not: there is
no reason to fetch an archive you will not open. So in every other job that path
holds **nothing at all**.

Before 2026-11-22 the file was Git LFS-tracked and the same mistake produced
a ~134-byte pointer at that path instead, which SQLite opens and rejects with
the error above. The cause changed; the rule did not.

It has now caught the same person twice in a week -- first by adding the
raw-chain cross-check to `data-validation.yml`, then by writing a unit test
that constructed `SchwabDatabase()` on the default path.

**The rule:** before any code path opens that database, ask which workflow
runs it. If the answer is anything other than `daily-snapshot.yml`, the file
will not be there.

- **Production code** should degrade, not crash -- see
  `validate_collection.open_chain_db()`, which returns `(db, reason)` and
  announces the skip loudly rather than swallowing it.
- **Tests** should never touch it at all. Monkeypatch
  `src.schwab_database._DEFAULT_DB_PATH` to a `tmp_path`, or pass an explicit
  `db_path`. A unit test has no business opening the real archive even when the
  file is real. `tests/conftest.py` enforces this session-wide, and also
  points `chain_release.ASSET_URL` somewhere unreachable so no test can
  download the real asset over the network.
- **Reproduce the CI condition in one line** rather than guessing. All three
  states `tests/test_api_import.py` parametrises over are worth trying;
  absent is the realistic one:
  ```bash
  mv database/schwab_database.db /tmp/real.db     # absent -- what CI sees
  pytest            # or whatever the workflow runs
  mv /tmp/real.db database/schwab_database.db
  ```
- **Getting it back** after any of this: `python -m src.chain_release download`.

---

## Template for new entries

```markdown
## <short description of the symptom>

**Recurred: N time(s).**

**What it looks like:** <the actual error message or observed behavior, verbatim if possible>

**Why it happens:** <the real root cause, not just the symptom>

**The fix:** <the actual steps, in order, specific enough that a fresh session with zero context could follow them>
```
