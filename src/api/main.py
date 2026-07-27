"""
src/api/main.py

FastAPI entrypoint for the web dashboard API (mostly read-only, plus one
mutating /api/refresh for personal use — see routes.py's module docstring).

Run locally:
    uvicorn src.api.main:app --reload --port 8000

CORS is scoped to FRONTEND_ORIGIN (default the Next.js dev server) rather
than left wide open ("*") — most of this API has no auth, and an open CORS
policy would let any third-party page script-read it via a victim's
browser, which is unnecessary exposure for no benefit here. This matters
more now that POST /api/refresh exists (it triggers a live, credential-
backed Schwab pull) — CORS being origin-scoped is what stops an arbitrary
web page from invoking it via a visitor's browser.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router

app = FastAPI(
    title="Vol Dashboard API",
    description="Read-only options-volatility data API backing the web dashboard.",
    version="0.1.0",
)

_frontend_origins = [
    origin.strip()
    for origin in os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
