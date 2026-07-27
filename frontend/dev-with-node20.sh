#!/usr/bin/env bash
# The system default `node` is v18 (too old for Next.js 16, which needs
# >=20.9.0); this repo also has node@20 installed via Homebrew (keg-only,
# not linked as default so it doesn't disturb other projects on this
# machine). Put it first on PATH just for this dev-server invocation.
export PATH="/opt/homebrew/opt/node@20/bin:$PATH"
cd "$(dirname "$0")"
exec npm run dev
