# Vol Dashboard

An SPX options-volatility pipeline and Streamlit dashboard: pull option chains and price history from the Schwab API, compute vol-surface metrics (term structure, skew, curvature, VRP), persist everything to CSV, and explore/score contracts interactively.

## How it fits together

```
src/auth.py              Schwab OAuth (schwab-py) — reads config.ini or .env
src/options_fetcher.py   Pulls monthly SPX option chains + price history from Schwab
src/metrics.py           VolatilityMetrics — term structure, skew, curvature, realized vol, VRP
src/data_store.py        CSVStore — persists/loads snapshots under data/raw and data/processed
src/job.py               OptionsVolJob — orchestrates auth -> fetch -> save -> compute -> save
src/charts.py            Static matplotlib PNG charts from the latest saved metrics (-> charts/)
src/debug_session.py     Loads latest CSVs into named DataFrames for REPL/debugger exploration

src/dashboard/app.py               Streamlit multipage entrypoint (Overview) + shared sidebar/config
src/dashboard/data_loader.py       Plain-Python data access for the dashboard (wraps CSVStore/job)
src/dashboard/chart_components.py Plotly figure builders (interactive versions of src/charts.py)
src/dashboard/decision_engine.py   Pure pandas scoring: score_expiries(), score_contracts()
src/dashboard/pages/
  1_Expiry_Drilldown.py   Smile + richness/skew/curvature for one expiry
  2_Strike_Selector.py    Ranked, scored contracts for one expiry/side, highlighted on the smile
  3_Decision_Screener.py  Top-ranked contracts across the whole filtered chain
```

**Pipeline (`src/job.py`)**: authenticate with Schwab → fetch a monthly option chain (±N strikes around ATM per expiry, `data_dir`/expiry window/strike spacing configurable) and 1yr daily price history → save both as CSV under `data/raw/` → compute `term_structure`, `skew`, `skew_ratio`, `curvature`, and (if price history is available) `vrp` → save each under `data/processed/`. Re-running on the same UTC day overwrites that day's files.

**Dashboard (`src/dashboard/`)**: a Streamlit multipage app that reads the latest saved snapshot (never talks to Schwab directly except via a sidebar "Refresh Live Data" button, which re-runs the job). `decision_engine.py` layers a 0–100 composite score onto contracts based on value (cheap/rich vs. the local IV smile), delta fit to a target, and liquidity — used by the Strike Selector and Decision Screener pages.

## What `app.py` does

[src/dashboard/app.py](src/dashboard/app.py) is the Streamlit entrypoint and also the shared module the other pages import from (since Streamlit multipage apps run each page as an independent script). It:

1. **Renders the shared sidebar** (`render_sidebar()`) — save symbol, a "Refresh Live Data" button (re-runs `OptionsVolJob` and clears the cache), trade intent (buy/sell), target delta & tolerance, score weights, and contract filters (DTE range, option type, min volume/OI, max spread %) — packaged into an `AppConfig` dataclass. Widgets use explicit `key=` values so `st.session_state` keeps settings in sync as the user navigates between pages.
2. **Loads and caches the latest snapshot** (`get_snapshot` / `_cached_snapshot`) — `st.cache_data` keyed on `(save_symbol, latest_chain_mtime)`, so a same-day manual refresh still busts the cache even though `CSVStore` overwrites same-day files. `load_snapshot_safely()` wraps this to show a friendly Streamlit warning/error instead of a traceback when no snapshot exists yet or loading fails.
3. **Renders the Overview page** (`render_overview()`) when run directly — term structure, skew, and curvature charts, an optional VRP chart (skipped with an info message if no price history was saved), and an "Expiry Richness" table from `decision_engine.score_expiries()`.

Because all UI logic lives inside functions guarded by `if __name__ == "__main__":`, importing `app.py` from a page script (to reuse `render_sidebar`, `get_snapshot`, `get_expiry_scores`, etc.) does not re-render the Overview page.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # or: cp config.ini.example config.ini
# fill in SCHWAB_API_KEY / SCHWAB_APP_SECRET (config.ini takes priority over .env if both exist)

python -m src.job --first-time   # one-time OAuth browser flow, saves token.json
python -m src.job                # fetch + persist a snapshot
streamlit run src/dashboard/app.py
```

Other useful commands:

```bash
python -m src.job --backfill     # recompute metrics from every saved snapshot
python -m src.charts             # generate static PNG charts into charts/
```

## Deploying on Streamlit Community Cloud

The app's disk is ephemeral there, and `schwab-py`'s first-time OAuth flow needs a local browser, so the pattern is: authenticate locally, ship the resulting token as a secret, and let the app re-materialize it on cold start.

1. **Authenticate locally** (if you haven't already): `cp .env.example .env`, fill in your Schwab API key/secret, then `python -m src.job --first-time`. This saves `token.json`.
2. **Base64-encode it**: `base64 -i token.json | tr -d '\n'` (macOS). Copy the output.
3. **Deploy** on [share.streamlit.io](https://share.streamlit.io): connect this GitHub repo, set the main file path to `src/dashboard/app.py`.
4. **Add secrets** in the app's Settings → Secrets:
   ```toml
   SCHWAB_API_KEY = "your_client_id"
   SCHWAB_APP_SECRET = "your_client_secret"
   SCHWAB_TOKEN_B64 = "paste the base64 string from step 2 here"
   ```
   `app.py` reads `SCHWAB_TOKEN_B64` on startup ([`_bootstrap_token_from_secret`](src/dashboard/app.py)) and writes it to `token.json` if no token is present yet — see [`write_token_from_base64`](src/auth.py). It never overwrites a token already on disk, so schwab-py's automatic in-place refresh during a session is preserved.
5. Once deployed, use the sidebar's **Refresh Live Data** button to pull a live snapshot — the dashboard has no data until that's clicked at least once per cold start, since `data/` isn't persisted either.
6. **When the token eventually expires** (schwab-py refresh tokens are long-lived but not permanent), repeat steps 1–2 locally and update the `SCHWAB_TOKEN_B64` secret, then reboot the app from the Streamlit Cloud dashboard.

## Notes

- `data/`, `.env`, `config.ini`, `token.json`, and `.streamlit/secrets.toml` are gitignored (per `.gitignore`) since they hold credentials/tokens or can grow large — `.env.example` and `config.ini.example` are the checked-in templates.
