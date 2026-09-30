#!/bin/bash
# Double-click this in Finder to re-authorise with Schwab.
#
# Schwab invalidates the refresh token 7 days after the browser login and
# offers no API-only way to renew it, so this has to happen once a week.
# Everything except the login itself is automated by src/reauth.py -- the
# new token goes straight to the SCHWAB_TOKEN_B64 GitHub secret, so the
# daily collection picks it up with nothing else to do.
#
# macOS marks a freshly downloaded/created .command as untrusted; if
# double-clicking does nothing, right-click -> Open once to allow it.

cd "$(dirname "$0")" || exit 1

if [ -x .venv/bin/python3 ]; then
    PY=.venv/bin/python3
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    echo "ERROR: no python3 found."
    echo
    read -r -n 1 -p "Press any key to close..."
    exit 1
fi

"$PY" -m src.reauth
STATUS=$?

echo
if [ $STATUS -eq 0 ]; then
    echo "All set. You can close this window."
else
    echo "Something went wrong -- see the message above. Nothing was broken by trying."
fi
echo
read -r -n 1 -p "Press any key to close..."
exit $STATUS
