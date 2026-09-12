# Git and PR workflow

## `gh` CLI is never authenticated here

Confirmed repeatedly across many sessions — `gh auth status` always fails in this environment. Don't attempt `gh pr create`, `gh pr list`, or anything else requiring auth; it will just fail and waste a turn. Instead:

1. Commit and push the branch normally (`git push -u origin <branch>` on first push).
2. Hand back a manual compare URL: `https://github.com/taher10/Vol-Dashboard/compare/main...<branch>?expand=1` — this pre-fills a ready-to-submit PR the user opens with one click.
3. To check whether a PR already exists or was merged (e.g. before creating a duplicate), the public GitHub API works fine without auth for this public repo: `curl -s "https://api.github.com/repos/taher10/Vol-Dashboard/pulls?head=taher10:<branch>&state=all"`.

## Never stage these

- `database/schwab_database.db`, `history/vol_history.db` — local, regenerable test/dev data from manual pipeline runs. Not project state; committing them just churns the git history with noise (they're real production data only when the automated daily-snapshot GitHub Action commits them itself).
- `src/.vscode/` — personal editor config, not project config.

Never use `git add -A`/`git add .`. Stage files explicitly by name.

## Syncing a feature branch that's drifted behind main

`main` moves on its own via the automated daily-snapshot commits and other merged PRs, so a feature branch worked on across more than a day or two will usually be behind by the time it's ready to commit. This exact sequence has been used safely and repeatably across multiple PRs on this project:

```bash
git stash push -u -m "wip: <description> before syncing with main"   # reversible, not a discard
git merge origin/main                                                 # should fast-forward cleanly
git stash pop
```

If `git stash pop` reports a conflict, it will almost always be on `database/schwab_database.db` and/or `history/vol_history.db` (binary files, can't be text-merged) — resolve by keeping main's committed version and discarding the local stashed one, since local test data was never meant to be preserved:

```bash
git checkout --ours -- database/schwab_database.db history/vol_history.db
git add database/schwab_database.db history/vol_history.db
```

Then stage the real code changes explicitly (never these two files) and commit.

## Before any operation that could discard uncommitted work

Run `git status` first. If there's anything uncommitted that isn't disposable test data, stash it (`-u` to include untracked files) rather than using `git checkout --`/`git reset --hard`/similar directly — stashing is reversible, discarding isn't. This project has hit exactly this situation (mid-session branch switches with real uncommitted feature work sitting in the tree) more than once.

## Commit messages and PR descriptions

End every commit with:
```
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```
End every PR description with:
```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```
