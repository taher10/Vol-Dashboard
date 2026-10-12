"""
src/api/demo.py

One switch that turns this deployment into something safe to hand a
stranger.

Why this exists
---------------
Nothing in this API is authenticated -- there is no Depends(), no API key,
no session. That is fine on localhost and unacceptable on a public URL,
because two endpoints are backed by real credentials:

  POST /api/refresh  runs a live Schwab pull. Anyone reaching it can burn
                     the ~449-call daily budget, occupy the server for the
                     ~8.5 minutes a full run takes, and -- the real damage --
                     rotate the Schwab refresh token, which desynchronises
                     SCHWAB_TOKEN_B64 and stops the daily collection until
                     someone re-authorises by hand.

  POST /api/chat     spends ANTHROPIC_API_KEY. Six tool iterations and 4096
                     tokens are capped per request, but nothing caps how
                     many requests a stranger can make.

CORS does not help with either: it constrains browsers, and neither curl
nor a script is a browser.

A flag rather than a branch
---------------------------
The tempting alternative is a `demo` branch with the risky routes deleted.
That rots -- every later fix has to be applied twice, the two drift, and
the failure mode is someone merging the credentialed code back into the
public deployment. A runtime flag keeps one codebase honest: the demo is
the same software with less surface, so a feature built tomorrow appears in
both without anyone remembering to port it.

Default off, so a developer who sets nothing gets today's behaviour exactly.
The demo deployment sets DEMO_MODE=1 and carries no credentials at all,
which is the real protection -- the flag only removes the routes, while an
environment with no SCHWAB_* and no ANTHROPIC_API_KEY has nothing to spend
or rotate even if one slipped through.
"""

from __future__ import annotations

import os

from fastapi import HTTPException

_TRUTHY = {"1", "true", "yes", "on"}


def demo_mode() -> bool:
    """True when this process is serving the public demo.

    Read per call rather than captured at import so tests (and a local
    preview run) can flip it with monkeypatch/env without reimporting the
    application.
    """
    return os.environ.get("DEMO_MODE", "").strip().lower() in _TRUTHY


def block_in_demo(what: str) -> None:
    """Refuse a credential-backed route when serving the demo.

    404 rather than 403: 403 confirms the endpoint exists and is merely
    switched off, which is an invitation to probe. 404 is what a visitor
    would see if it had never been written, and it matches what the
    frontend does -- the controls for these routes are not rendered at all,
    so a demo user has no path to them that isn't deliberate.
    """
    if demo_mode():
        raise HTTPException(status_code=404, detail=f"{what} is not available.")
