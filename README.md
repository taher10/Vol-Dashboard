# Vol Dashboard

An SPX/equity options-volatility pipeline and web dashboard: pull option chains from the Schwab API (options-only — see below), compute vol-surface metrics (term structure, skew, curvature), persist everything to CSV/SQLite, and explore contracts and build risk-defined trade structures interactively.

## Web app

`src/api/` (FastAPI) + `frontend/` (Next.js) is the browser-based UI over the saved snapshot data — a design-first prototype, not a production deployment. It has 4 views (Overview, Expiry Drilldown, Strategy Builder, History), reusing the existing pipeline/scoring code with no logic duplicated. Quick start:

```bash
./dev.sh   # backend on :8000, frontend on :3000 — requires .venv and frontend/node_modules already installed
```

It deliberately has no auth, no live "refresh data" action (refreshing stays an owner-run/GitHub Actions job, same as today), and no hosting/deployment set up yet — and it does not resolve whether redistributing a Schwab-sourced feed to unrelated third-party users complies with Schwab's API terms of use, which the longer-term "single shared public feed" direction will need to address. See `frontend/README.md` for the full write-up (architecture, endpoints, setup, and scope/limitations in detail).

## How it fits together

```
src/auth.py              Schwab OAuth (schwab-py) — reads config.ini or .env
src/options_fetcher.py   Pulls option chains from Schwab (weekly + monthly expiries), rate-limited
src/metrics.py           VolatilityMetrics — term structure, skew, curvature (+ VRP if price history is present)
src/data_store.py        CSVStore — persists/loads snapshots under data/raw and data/processed
src/job.py               OptionsVolJob — orchestrates auth -> fetch -> save -> compute -> save
src/charts.py            Static matplotlib PNG charts from the latest saved metrics (-> charts/)
src/debug_session.py     Loads latest CSVs into named DataFrames for REPL/debugger exploration

src/dashboard/data_loader.py       Plain-Python data access layer (wraps CSVStore/job) — used by src/api
src/dashboard/decision_engine.py   Pure pandas: score_expiries() (expiry richness)
src/dashboard/strategy_engine.py   Pure pandas: recommend_trade() -- one sized vertical spread for a stated direction/timeline/risk/capital
```

**Pipeline (`src/job.py`)**: authenticate with Schwab → fetch a chain (weekly expiries through 60 DTE + the full monthly cadence beyond, ±N strikes around ATM per expiry, `data_dir`/expiry window/strike spacing configurable) → save as CSV under `data/raw/` → compute `term_structure`, `skew`, `skew_ratio`, `curvature` → save each under `data/processed/`. Re-running on the same UTC day overwrites that day's files.

**Deliberately options-only, no price-history fetch.** An earlier version also pulled 1yr daily price history per symbol (1 extra API call/symbol/day) to compute VRP (implied vs. realized vol). Removed by request: stock/price data is readily available elsewhere, and every call spent on it is one fewer available for options coverage under the shared rate limiter (`options_fetcher._SCHWAB_RATE_LIMITER`, ~100 req/min). `vrp` is simply absent from a fresh pull's metrics now — the dashboard already shows a "needs price history" message for this case rather than a wrong number.

**Data layer (`src/dashboard/`)**: read by the FastAPI backend (`src/api/`), never talks to Schwab directly except via `POST /api/refresh` (re-runs the job). `strategy_engine.recommend_trade()` turns a stated bullish/bearish view, timeline, risk appetite, and available capital into exactly one concrete vertical-spread recommendation — sized to that capital — with closed-form max profit/loss/breakeven/payoff, for the Strategy Builder.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # or: cp config.ini.example config.ini
# fill in SCHWAB_API_KEY / SCHWAB_APP_SECRET (config.ini takes priority over .env if both exist)

python -m src.job --first-time   # one-time OAuth browser flow, saves token.json
python -m src.job                # fetch + persist a snapshot
./dev.sh                         # backend on :8000, frontend on :3000
```

Other useful commands:

```bash
python -m src.job --backfill     # recompute metrics from every saved snapshot
python -m src.charts             # generate static PNG charts into charts/
```

`write_token_from_base64` ([src/auth.py](src/auth.py)) decodes `SCHWAB_TOKEN_B64` into `token.json` on startup if no token is present yet — used by the GitHub Actions daily job (see secrets below) so the workflow doesn't need a browser OAuth flow on every run. It never overwrites a token already on disk, so schwab-py's automatic in-place refresh during a session is preserved.

## Multi-symbol support and historical data

`src/symbols.py`'s `SYMBOL_REGISTRY` lists every symbol the pipeline knows about (SPX + the MAG7 equities) and each one's fetch parameters — an index needs a `$`-prefixed `api_symbol` and is validated at `strike_increment=100`/`strikes_each_side=5`; equities need `strike_increment=None` (skip the fixed-$-increment filter entirely) and a much wider `strikes_each_side=20` so skew/curvature can actually reach 25-delta at an equity's tighter native strike spacing. The web app's Symbol Picker reads from this registry directly.

**Why there's a second persistence layer.** `CSVStore` (`data/`, gitignored) only ever holds the *latest* snapshot — it overwrites same-day files, and the deployed app's disk is ephemeral, so nothing accumulates real day-over-day history there. IV Rank, a metric's z-score against its own trailing distribution, and a day-over-day digest all need that history, so there's a second store for it:

- **`src/history_store.py`**'s `HistoryStore` — a small SQLite database at `history/vol_history.db`, one row per `(symbol, snapshot_date, expiration)`, holding `atm_iv`/`skew`/`curvature`/`realized_vol`/`vrp`. Query helpers: `iv_rank()`, `trailing_zscore()`, `prior_snapshot()`.
- **`src/daily_snapshot.py`** — headless multi-symbol runner (`python -m src.daily_snapshot`) that fetches every symbol in the registry and appends into `HistoryStore`. One symbol failing doesn't abort the others.
- **`.github/workflows/daily-snapshot.yml`** — runs the above on a schedule (weekdays, ~21:30 UTC, after the US market close) and commits `history/vol_history.db` if it changed. This is a **git-committed SQLite file, not a hosted database** — a deliberate choice to avoid a new external service for a single-user tool; it does mean history only accumulates from whenever the workflow was enabled, there's no historical backfill.

  Requires these **GitHub repo secrets** (Settings → Secrets and variables → Actions):
  ```
  SCHWAB_API_KEY
  SCHWAB_APP_SECRET
  SCHWAB_CALLBACK_URL
  SCHWAB_TOKEN_B64
  ```
  `SCHWAB_TOKEN_B64` is `base64 -i token.json | tr -d '\n'` run locally after a one-time `python -m src.job --first-time` — see `write_token_from_base64` above.

  **Keeping the token alive without re-authenticating every week.** Schwab's refresh token is only valid ~7 days and rotates on every use (a new refresh token invalidates the previous one). Since each scheduled run starts from a fresh, ephemeral GitHub Actions runner, whatever schwab-py refreshes `token.json` to *during* a run is normally lost the moment that run ends — the next day's run goes back to `SCHWAB_TOKEN_B64`'s original bytes, and once Schwab's rotation invalidates that frozen token, every run fails with `invalid_grant` until someone manually redoes the OAuth flow and re-uploads the secret.

  `src/token_sync.py` closes that loop: after every run, it re-encrypts whatever `token.json` is currently on disk and pushes it back to `SCHWAB_TOKEN_B64` via the GitHub API, so the next run always starts from the freshest refresh token. As long as the workflow fires at least once within the 7-day window (it's scheduled daily), this should keep running indefinitely without manual re-auth. To enable it, add one more secret:
  ```
  SECRETS_PAT   # a GitHub PAT that can manage this repo's Actions secrets:
                #   classic PAT -> 'repo' scope
                #   fine-grained PAT -> this repo, 'Secrets' permission = Read and write
  ```
  Without `SECRETS_PAT`, everything still works exactly as before (the sync step just no-ops) — you'll simply need to redo `python -m src.job --first-time` and re-upload `SCHWAB_TOKEN_B64` by hand whenever the refresh token eventually expires.

  **Failure notifications.** Set `NOTIFY_WEBHOOK_URL` (a Slack or Discord incoming-webhook URL) as a repo secret and:
  - `src/daily_snapshot.py` posts a one-line alert there if any symbol *actually* fails (not the expected "no live quotes outside market hours" skip) — including a clear call-out when the failure is the refresh-token-expired case above.
  - `.github/workflows/pipeline-heartbeat.yml` posts there if the scheduled `daily-snapshot.yml` run itself didn't fire at all (a known GitHub Actions cron reliability gap — see that workflow's comments).

  Both are no-ops if `NOTIFY_WEBHOOK_URL` isn't set — GitHub's own default failure-email notifications still apply either way.

## Notes

- `data/`, `.env`, `config.ini`, and `token.json` are gitignored (per `.gitignore`) since they hold credentials/tokens or can grow large — `.env.example` and `config.ini.example` are the checked-in templates. `history/vol_history.db` is deliberately **not** gitignored — see above.
