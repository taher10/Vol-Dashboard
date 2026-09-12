# Verification checklist

Every one of these earns its place because skipping it once already produced a wrong or broken result on this project. Run through all of them before saying a change is done, not just the ones that feel relevant.

## 1. Hand-check new math against a real reference

"The formula looks right" is not verification. For anything touching money (a new payoff calculation, a new leaderboard metric, a formula change), pull real numbers — from the app's own API, from an external source like OptionStrat, or by hand-deriving the expected result — and compare digit by digit. The calendar max-loss bug (see `architecture-patterns.md`) was caught exactly this way: comparing the app's number against a real OptionStrat quote for the same trade, not by re-reading the code and deciding it looked plausible.

When you can't get an external reference, at least reproduce the calculation independently (e.g. a quick Python one-liner using the same inputs) rather than trusting the code path you just wrote to check itself.

## 2. Typecheck after any TypeScript change

```bash
cd frontend && npx tsc --noEmit
```

Run this after touching any `.ts`/`.tsx` file, before browser verification. It catches type mismatches (e.g. a field that became nullable after a backend change) faster than the browser will.

## 3. Restart both dev servers fresh when testing in the browser

```bash
# backend
lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null | xargs -r kill -9
uvicorn src.api.main:app --reload --port 8000 &

# frontend -- ALWAYS clear .next first, see below
rm -rf frontend/.next
cd frontend && ./dev-with-node20.sh &
```

**Known gotcha:** the frontend's Turbopack build cache (`frontend/.next`) has repeatedly corrupted itself after many restarts within the same long session, throwing `EPERM: operation not permitted` errors on cache/log files and serving a bare "Internal Server Error" page. This isn't a real permissions problem worth debugging — `rm -rf frontend/.next` before every restart is the fix, every time. Don't spend time investigating the EPERM itself.

## 4. Read console errors from a fresh tab

Browser tabs that were open *during* a dev-server restart accumulate stale error messages (failed HMR websocket reconnects, 404s from the moment the server was down) that look like real bugs but aren't. Before concluding there's a real console error, open a brand new tab and reload the page there — if the error doesn't reproduce in the fresh tab, it was stale noise from the restart, not a real regression. This has produced false alarms more than once; always confirm in a fresh tab before reporting an error as real.

## 5. Confirm you didn't break anything already working

If the change touches a shared piece (a format helper, a type used in multiple places, a route other pages depend on), spot-check at least one of those other consumers still works — don't assume isolation. Example: relaxing `StrategyCandidate.max_profit`/`max_loss` to nullable (for calendars) required checking Strategy Builder and the Trade Ideas panel still rendered correctly, since they consume the same type.
