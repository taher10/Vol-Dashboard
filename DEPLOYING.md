# Deploying the demo

A public, read-only copy of the dashboard: every page, chart and leaderboard,
with the two credential-backed routes removed. Backend on Render, frontend on
Vercel, both free tier.

## What makes it safe

In order of how much each actually matters:

1. **No credentials on the server.** `render.yaml` sets no `SCHWAB_*` and no
   `ANTHROPIC_API_KEY`, and none should ever be added. Everything below is
   defence in depth; this is the real protection.
2. **The SDKs are not installed.** `requirements-demo.txt` omits `schwab-py`
   and `anthropic`, so the machine cannot reach either service even if a
   route were exposed by mistake. `tests/test_demo_mode.py` boots the app
   with both blocked, so a module-scope import that reintroduces them fails
   CI rather than a deploy.
3. **`DEMO_MODE=1` removes the routes.** `POST /api/refresh` and
   `POST /api/chat` return 404, and the frontend hides their controls by
   reading the flag from `/api/health`.

Why those two routes and nothing else:

| Route | If a stranger calls it |
|---|---|
| `POST /api/refresh` | Burns the ~449-call daily Schwab budget, holds the server for ~8.5 min, and **rotates the refresh token** -- which desynchronises `SCHWAB_TOKEN_B64` and stops the daily collection until someone re-authorises in a browser. |
| `POST /api/chat` | Spends `ANTHROPIC_API_KEY` with no per-caller cap. |

Everything else is a read against a frozen SQLite file.

**CORS is not a security control here.** It constrains browsers; `curl` is not
a browser. `FRONTEND_ORIGIN` is still set, because leaving it open would let
any page script-read the API through a visitor for no benefit -- but it is not
what makes this deployment safe.

## 1. Build the demo database

```bash
git pull                                   # ship collected data, not local test runs
python -m src.build_demo_db --days 5
```

Writes `demo-data/` (gitignored). Sources are opened read-only; a demo build
cannot damage the live databases.

`--days` trades size against history depth -- 5 full days is roughly 67MB
against the live ~260MB. The script skips snapshot days with fewer than
`--min-symbols` (default 20 of 24): a local single-symbol run would otherwise
become the newest date and give the demo a leaderboard where one symbol is
current and the rest are a week old.

The metric history (`vol_history.db`, ~1MB) is copied whole, because IV Rank
and the trailing z-scores need the long series to mean anything.

## 2. Publish it

The database is **not** committed. The live chain database is ~260MB in Git
LFS, and pulling LFS on every deploy would spend a quarter of the free
monthly bandwidth each time.

Upload `demo-data/schwab_database.db` as a **GitHub Release asset** and copy
its download URL. Release assets are free, do not touch LFS, and are simple
to replace when you want the demo to show fresher data.

## 3. Backend on Render

Render dashboard -> **New** -> **Blueprint** -> point at this repo. It reads
`render.yaml`. Then set the two `sync: false` variables in the dashboard:

| Variable | Value |
|---|---|
| `DEMO_DB_URL` | the Release asset URL from step 2 |
| `FRONTEND_ORIGIN` | your Vercel URL (set after step 4, then redeploy) |

The build fails loudly if `DEMO_DB_URL` is missing or the download has no
rows -- a failed build is easier to diagnose than an API that 500s on every
page.

Free tier sleeps after inactivity, so the first request after a quiet spell
takes ~30s to wake. Fine for a demo; mention it if you are showing it live.

## 4. Frontend on Vercel

Import the repo, set **Root Directory** to `frontend/`. `vercel.json` handles
the rest. One environment variable:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | your Render URL, e.g. `https://vol-dashboard-demo-api.onrender.com` |

Then go back to Render, set `FRONTEND_ORIGIN` to the Vercel domain, and
redeploy the backend so CORS matches.

## 5. Check it

```bash
curl https://<render-url>/api/health
# {"status":"ok","demo_mode":true}

curl -s -o /dev/null -w '%{http_code}\n' -X POST 'https://<render-url>/api/refresh?symbols=SPX'
# 404
```

Open the Vercel URL: every page should render, with no "Refresh Live Data"
button and no chat launcher.

## Running the demo locally

```bash
python -m src.build_demo_db --days 5

DEMO_MODE=1 FRONTEND_ORIGIN=http://localhost:3001 \
  SCHWAB_DB_PATH=demo-data/schwab_database.db \
  uvicorn src.api.main:app --port 8001

cd frontend && NEXT_PUBLIC_API_URL=http://localhost:8001 npm run dev -- --port 3001
```

**Run one frontend at a time.** Both dev servers share `frontend/.next`, so
running the demo alongside your normal one leaks the demo's API URL into the
normal build. Deployed they are separate builds and this does not apply;
locally, `rm -rf frontend/.next` when switching.

Your own dashboard is unaffected either way: `DEMO_MODE` defaults to off, so
with nothing set you get the full local behaviour, chat and refresh included.

## Refreshing the demo's data

Automated: `.github/workflows/publish-demo.yml` rebuilds the snapshot and
republishes it every Sunday at 07:00 UTC. Run it on demand from the Actions
tab ("Publish demo snapshot" -> Run workflow) whenever you want the demo
current before showing it to someone.

**The download URL never changes.** Every run replaces the asset on one
release with the fixed tag `demo-latest`, so `DEMO_DB_URL` is set once and
never edited again:

```
https://github.com/taher10/Vol-Dashboard/releases/download/demo-latest/schwab_database.db
```

That is the point of the fixed tag. Dated tags read better in the Releases
list but require editing Render on every publish, and forgetting that step
is exactly how the demo spent days serving a snapshot Render was still
dutifully downloading from an old URL.

### One secret to add

| Secret | Where to get it |
|---|---|
| `RENDER_DEPLOY_HOOK` | Render -> the service -> Settings -> Deploy Hook -> copy the URL |

Add it under the repo's Settings -> Secrets and variables -> Actions.
Without it the snapshot still publishes; only the automatic redeploy is
skipped, and Render picks the new file up on its next deploy anyway.

### What stops a bad snapshot going out

The job refuses to publish when the build looks wrong -- fewer than two
days, a newest day with under 20 symbols (a partial collection), or no
precomputed metrics (which would leave the demo taking minutes per page).
Leaving the demo on last week's good snapshot beats replacing it with a
broken one.

Two runs cannot overlap: a `concurrency` group prevents them deleting each
other's asset mid-upload, which would leave `demo-latest` with no file and
break Render's next build.

### Doing it by hand

Still works, and is what to use if you want a dated tag kept for the record:

```bash
git pull && python -m src.build_demo_db --days 5
```

Then upload as a Release and point `DEMO_DB_URL` at it.

## Before making it genuinely public

The market data comes through a personal Schwab developer app. Showing
derived metrics (IV rank, skew, term structure) to a few named people is a
different proposition from publishing raw per-contract quotes at a public
URL. Read the developer agreement before the latter, and consider putting
the demo behind a password either way -- Vercel offers this on paid plans,
and a single shared password changes the character of this considerably.
