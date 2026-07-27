# Vol Dashboard — Web App (prototype)

A second, browser-based UI for the same options-volatility data as the root
project's Streamlit app. Same underlying pipeline and saved snapshots — see
the [root README](../README.md) for how the data gets there (Schwab fetch,
metrics, CSV/SQLite persistence). This app only reads it.

This is a **design-first prototype**, not a production deployment. See
[Scope and limitations](#scope-and-limitations) before treating it as more
than that.

## Architecture

```
src/api/        FastAPI backend — read-only REST endpoints over the existing
                 data_loader.py / decision_engine.py / history_store.py
frontend/        Next.js 16 + TypeScript + Tailwind + shadcn/ui + Recharts —
                 consumes that API over HTTP
```

`src/api/routes.py` is deliberately thin: it does query-param parsing and
DataFrame -> JSON serialization only. All scoring/metrics logic
(`score_expiries`, `score_contracts`, IV rank, trailing z-score, etc.) still
lives in `src/dashboard/data_loader.py`, `src/dashboard/decision_engine.py`,
and `src/history_store.py` — nothing is duplicated between the Streamlit app
and this one. The API also intentionally never imports `src/dashboard/app.py`
(which would pull in Streamlit) and exposes no "refresh live data" endpoint —
refreshing stays an owner-run/GitHub Actions job, same as today.

Endpoints (`src/api/main.py` + `src/api/routes.py`):

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness check |
| `GET /api/symbols` | List of symbols in `src/symbols.py`'s registry, with display colors |
| `GET /api/overview` | Term structure / skew / curvature / VRP series + expiry richness scores + takeaway, for one or more comma-separated symbols |
| `GET /api/expiry/{symbol}` | Vol smile + score for one expiry (defaults to the nearest), plus neighboring-expiry context |
| `GET /api/contracts/{symbol}` | Scored, filterable contracts — scoped to one expiry when `expiration` is given (Strike Selector), or across the whole chain when omitted (Decision Screener) |
| `GET /api/history/{symbol}/iv-rank` | IV rank/percentile vs. trailing history (from `history/vol_history.db`) |
| `GET /api/history/{symbol}/zscore` | Trailing z-score for a chosen metric |

CORS is restricted to `FRONTEND_ORIGIN` (backend env var, default
`http://localhost:3000`) rather than left open, since this is an
unauthenticated read endpoint.

### Pages (mirror the Streamlit app's 4 views)

| Route | Streamlit equivalent | What it shows |
|---|---|---|
| `/` (Overview) | `app.py` | Multi-symbol term structure / skew / curvature / VRP charts + expiry richness table |
| `/expiry/[symbol]` (Expiry Drilldown) | `pages/1_Expiry_Drilldown.py` | Vol smile and richness/skew/curvature for one expiry |
| `/strikes/[symbol]` (Strike Selector) | `pages/2_Strike_Selector.py` | Ranked, scored contracts for one expiry/side, highlighted on the smile |
| `/screener/[symbol]` (Decision Screener) | `pages/3_Decision_Screener.py` | Top-ranked contracts across the whole filtered chain |

`frontend/src/lib/store.ts` is a Zustand store (persisted to
`localStorage`) holding the shared settings — symbols, trade intent, target
delta/tolerance, score weights, and contract filters. It's the web
equivalent of the Streamlit sidebar's `AppConfig`/`st.session_state`, except
it's a real durable client-side store shared across all 4 pages instead of
per-session Streamlit state.

## Local setup

Requires **Node.js >= 20.9.0** (`frontend/package.json`'s `engines` field —
Next.js 16's minimum). If your default `node` is older (this machine's
system default is v18), a keg-only `node@20` was installed via Homebrew
specifically for this project (`brew install node@20`, not linked as the
system default, so it doesn't affect other projects). `frontend/dev-with-node20.sh`
puts it first on `PATH` just for the dev-server invocation:

```bash
export PATH="/opt/homebrew/opt/node@20/bin:$PATH"
```

### Run both servers together (recommended)

From the repo root:

```bash
./dev.sh
```

This starts the FastAPI backend on `:8000` (`uvicorn src.api.main:app --reload`)
and the Next.js dev server on `:3000` (via `frontend/dev-with-node20.sh`).
It requires the Python venv (`.venv`) and `frontend/node_modules` to already
exist:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
npm --prefix frontend install
```

### Run frontend only

```bash
cp frontend/.env.local.example frontend/.env.local   # set NEXT_PUBLIC_API_URL if needed
npm --prefix frontend install
./frontend/dev-with-node20.sh          # npm run dev, with node@20 on PATH
```

Other `frontend/package.json` scripts: `npm run build` (production build),
`npm run start` (serve a production build), `npm run lint` (ESLint).

### Run backend only

```bash
.venv/bin/uvicorn src.api.main:app --reload --port 8000
```

Like the Streamlit app, the API reads whatever's already saved under
`data/processed/` and `history/vol_history.db` — run the root project's
`python -m src.job` (or `src.daily_snapshot`) first if there's no snapshot
yet; a 404 with a "no saved snapshot" detail means there's nothing to read.

## Environment variables

`frontend/.env.local.example`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

The only frontend env var — base URL the browser calls for all API requests
(`frontend/src/lib/api.ts`). Copy to `frontend/.env.local` (gitignored) and
change it if the backend isn't on the default host/port.

On the backend side (not a frontend file, but relevant when running the two
apart), `src/api/main.py` reads `FRONTEND_ORIGIN` (comma-separated allowed
origins, default `http://localhost:3000`) for CORS — update it if the
frontend dev server runs on a different port.

## Scope and limitations

This prototype is intentionally missing several things a real public
deployment would need:

- **No auth.** Every endpoint is open and unauthenticated. Fine for local/owner
  use; not fine for anything reachable by the public as-is.
- **No live "refresh data" action from the web app.** Both UIs are read-only
  over the same saved snapshot files (`data/processed/`, `history/vol_history.db`).
  Refreshing from Schwab stays an owner-run command (`python -m src.job`) or the
  scheduled GitHub Actions job (`.github/workflows/daily-snapshot.yml`) — there
  is no button anywhere in this web app that triggers a live fetch.
- **No real database.** The API reads the same CSV/SQLite files as the
  Streamlit app; no new persistence layer was introduced.
- **No hosting/deployment.** This only runs locally today; there's no
  Vercel/cloud config for the frontend or a hosting story for the API.
- **Unresolved: Schwab data-redistribution compliance.** The intended
  longer-term direction is a single shared read-only feed serving any
  visitor, not a per-user brokerage connection — but redistributing a
  Schwab-sourced feed to unrelated third-party users may not comply with
  Schwab's API terms of use. That needs a deliberate decision (e.g.
  switching to a data vendor whose terms permit redistribution) before any
  real public launch. This prototype does not resolve it — it's flagged here
  so it isn't missed.
