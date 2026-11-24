"""
Tests for src/dashboard/data_sync.py.

The dashboard's refresh control used to call Schwab. That became the wrong
operation once data_loader started reading the committed database, and then
an actively harmful one: a successful live pull rotates the Schwab refresh
token, invalidating SCHWAB_TOKEN_B64 and stopping the next scheduled
collection. The button's *success* case broke the pipeline.

So the two properties worth pinning are that this never touches a broker,
and that it never discards local changes to the tracked database files --
one day of recovered data has already been lost to exactly that.
"""

import subprocess
from pathlib import Path

import pytest

from src.dashboard import data_sync

# Captured before the autouse stub below can replace it, so TestRefreshChain
# can put the real implementation back for the tests that are about it.
_REAL_REFRESH_CHAIN = data_sync._refresh_chain


@pytest.fixture
def git_calls(monkeypatch):
    """Record git invocations and script their results."""
    calls: list[list[str]] = []
    scripted: dict[tuple, subprocess.CompletedProcess] = {}

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        key = tuple(cmd[1:3])
        return scripted.get(
            key, subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls, scripted


@pytest.fixture(autouse=True)
def _no_real_chain_fetch(monkeypatch):
    """Keep pull_latest's git logic separable from the asset download.

    pull_latest now does two things -- fast-forward the metric history and
    fetch the chain database -- and the tests in this file are about the
    first. _refresh_chain is exercised directly in TestRefreshChain below.
    """
    monkeypatch.setattr(data_sync, "_refresh_chain", lambda: data_sync._ChainOutcome(changed=False))


def _ok(stdout=""):
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


class TestNeverCallsABroker:
    def test_only_runs_git(self, git_calls):
        calls, scripted = git_calls
        scripted[("rev-parse", "--short")] = _ok("abc1234\n")

        data_sync.pull_latest()

        assert calls, "expected at least one subprocess call"
        assert all(c[0] == "git" for c in calls), calls

    def test_pull_is_fast_forward_only(self, git_calls):
        """--ff-only means this can never create a merge commit or rewrite
        history on someone's working repo."""
        calls, scripted = git_calls
        scripted[("rev-parse", "--short")] = _ok("abc1234\n")

        data_sync.pull_latest()

        pull = next(c for c in calls if c[1] == "pull")
        assert "--ff-only" in pull

    def test_no_user_input_reaches_the_command(self, git_calls):
        """pull_latest takes no arguments, so there is no injection surface
        to begin with -- asserted here so a future 'pull this branch'
        parameter has to come with a deliberate look at this test."""
        import inspect

        assert not [
            p for p in inspect.signature(data_sync.pull_latest).parameters.values()
            if p.default is inspect.Parameter.empty
        ]


class TestRefusesToClobberLocalData:
    def test_dirty_database_blocks_the_pull(self, git_calls, monkeypatch):
        calls, scripted = git_calls
        monkeypatch.setattr(
            data_sync, "dirty_tracked_data", lambda: ["history/vol_history.db"]
        )

        result = data_sync.pull_latest()

        assert result.changed is False
        assert "vol_history.db" in result.message
        assert not any(c[1] == "pull" for c in calls), "must not pull over local changes"

    def test_dirty_detection_reads_porcelain_for_the_data_files(self, git_calls):
        calls, scripted = git_calls
        scripted[("status", "--porcelain")] = _ok(" M history/vol_history.db\n")

        assert data_sync.dirty_tracked_data() == ["history/vol_history.db"]
        status = next(c for c in calls if c[1] == "status")
        assert "history/vol_history.db" in status

    def test_the_chain_database_is_not_guarded_because_it_is_not_tracked(self):
        """It left Git LFS for a release asset on 2026-11-22, so git can
        neither report it dirty nor overwrite it.

        This is not a loosened safeguard. Listing it here was what made the
        Sync button refuse to run from a checkout whose chain database
        differed from origin/main at all -- which, on a feature branch, is
        always. chain_release carries the protection that matters now, by
        refusing any asset holding fewer rows than the local copy."""
        assert "database/schwab_database.db" not in data_sync._TRACKED_DATA
        assert data_sync._TRACKED_DATA == ("history/vol_history.db",)


class TestOutcomesAreReportedNotRaised:
    def test_unchanged_head_reports_already_up_to_date(self, git_calls):
        calls, scripted = git_calls
        scripted[("rev-parse", "--short")] = _ok("abc1234\n")

        result = data_sync.pull_latest()

        assert result.changed is False
        assert "up to date" in result.message.lower()

    def test_failed_pull_is_a_message_not_an_exception(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            if cmd[1] == "pull":
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fatal: could not resolve host\n")
            if cmd[1] == "status":
                return _ok("")  # clean tree
            return _ok("abc1234\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = data_sync.pull_latest()

        assert result.changed is False
        assert "could not resolve host" in result.message

    def test_timeout_is_a_message_not_an_exception(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            if cmd[1] == "pull":
                raise subprocess.TimeoutExpired(cmd, data_sync._TIMEOUT_SECONDS)
            if cmd[1] == "status":
                return _ok("")  # clean tree
            return _ok("abc1234\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = data_sync.pull_latest()

        assert result.changed is False
        assert "timed out" in result.message.lower()

    def test_moved_head_reports_changed(self, monkeypatch):
        heads = iter(["abc1234\n", "def5678\n"])

        def fake_run(cmd, **kwargs):
            if cmd[1] == "rev-parse":
                return _ok(next(heads))
            return _ok("")  # clean tree, successful pull

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = data_sync.pull_latest()

        assert result.changed is True
        assert result.before == "abc1234"
        assert result.after == "def5678"


# ---------------------------------------------------------------------------
# upstream_status
# ---------------------------------------------------------------------------

_REMOTE = "a" * 40
_LOCAL = "b" * 40


@pytest.fixture(autouse=True)
def _clear_upstream_cache():
    """The answer is cached at module scope, so it leaks between tests."""
    data_sync.reset_upstream_cache()
    yield
    data_sync.reset_upstream_cache()


class TestUpstreamStatusIsReadOnly:
    """The banner polls this on every page load.

    A fetch would write objects and move remote-tracking refs in the user's
    repository as a side effect of rendering a page, which is not a thing a
    status check gets to do. ls-remote asks for one ref and downloads
    nothing.
    """

    def test_uses_ls_remote_and_never_fetches(self, git_calls):
        calls, scripted = git_calls
        scripted[("ls-remote", "origin")] = _ok(f"{_REMOTE}\trefs/heads/main\n")
        scripted[("rev-parse", "HEAD")] = _ok(f"{_LOCAL}\n")

        data_sync.upstream_status()

        verbs = [c[1] for c in calls if c[0] == "git"]
        assert "ls-remote" in verbs
        assert "fetch" not in verbs, f"status check must not fetch: {calls}"
        assert "pull" not in verbs, f"status check must not pull: {calls}"

    def test_never_touches_the_working_tree(self, git_calls):
        _, scripted = git_calls
        scripted[("ls-remote", "origin")] = _ok(f"{_REMOTE}\trefs/heads/main\n")
        scripted[("rev-parse", "HEAD")] = _ok(f"{_LOCAL}\n")

        calls, _ = git_calls
        data_sync.upstream_status()

        mutating = {"checkout", "reset", "merge", "clean", "restore"}
        assert not [c for c in calls if len(c) > 1 and c[1] in mutating], calls


class TestUpstreamStatusAnswers:
    def test_up_to_date_when_shas_match(self, git_calls):
        _, scripted = git_calls
        scripted[("ls-remote", "origin")] = _ok(f"{_REMOTE}\trefs/heads/main\n")
        scripted[("rev-parse", "HEAD")] = _ok(f"{_REMOTE}\n")

        status = data_sync.upstream_status()

        assert status.has_updates is False
        assert status.behind == 0

    def test_exact_count_when_the_objects_are_present(self, git_calls):
        """A previous fetch left them behind, so the count is knowable."""
        _, scripted = git_calls
        scripted[("ls-remote", "origin")] = _ok(f"{_REMOTE}\trefs/heads/main\n")
        scripted[("rev-parse", "HEAD")] = _ok(f"{_LOCAL}\n")
        scripted[("cat-file", "-e")] = _ok()
        scripted[("rev-list", "--count")] = _ok("3\n")

        status = data_sync.upstream_status()

        assert status.has_updates is True
        assert status.behind == 3

    def test_reports_updates_without_a_count_when_objects_are_absent(self, git_calls):
        """The common case: new commits exist and have never been fetched.

        Saying "there is new data, I cannot say how much" is the honest
        answer. Inventing a number here would be worse than omitting one.
        """
        _, scripted = git_calls
        scripted[("ls-remote", "origin")] = _ok(f"{_REMOTE}\trefs/heads/main\n")
        scripted[("rev-parse", "HEAD")] = _ok(f"{_LOCAL}\n")
        scripted[("cat-file", "-e")] = subprocess.CompletedProcess([], 1, stdout="", stderr="")

        status = data_sync.upstream_status()

        assert status.has_updates is True
        assert status.behind is None

    def test_ahead_of_origin_is_not_behind_it(self, git_calls):
        """Unpushed local commits make the SHAs differ with nothing to pull.

        Announcing "new data available" here would send someone to a Sync
        button that correctly reports it is already up to date.
        """
        _, scripted = git_calls
        scripted[("ls-remote", "origin")] = _ok(f"{_REMOTE}\trefs/heads/main\n")
        scripted[("rev-parse", "HEAD")] = _ok(f"{_LOCAL}\n")
        scripted[("cat-file", "-e")] = _ok()
        scripted[("rev-list", "--count")] = _ok("0\n")

        status = data_sync.upstream_status()

        assert status.has_updates is False
        assert status.behind == 0

    def test_surfaces_blocking_local_changes(self, git_calls):
        """pull_latest refuses when the tracked databases are dirty.

        Knowing that before clicking is the point -- this is exactly the
        state that made a Sync button appear broken.
        """
        _, scripted = git_calls
        scripted[("status", "--porcelain")] = _ok(" M history/vol_history.db\n")
        scripted[("ls-remote", "origin")] = _ok(f"{_REMOTE}\trefs/heads/main\n")
        scripted[("rev-parse", "HEAD")] = _ok(f"{_LOCAL}\n")
        scripted[("cat-file", "-e")] = _ok()
        scripted[("rev-list", "--count")] = _ok("2\n")

        status = data_sync.upstream_status()

        assert status.dirty == ["history/vol_history.db"]
        assert status.has_updates is True


class TestUpstreamStatusFailsQuietly:
    """Nobody asked for this check, so it must not produce an error.

    It runs on a timer behind a banner. A laptop on a train should show no
    banner, not a red network error over the dashboard.
    """

    def test_offline_reports_no_updates_rather_than_raising(self, git_calls):
        _, scripted = git_calls
        scripted[("ls-remote", "origin")] = subprocess.CompletedProcess(
            [], 128, stdout="", stderr="fatal: unable to access 'https://github.com/...': Could not resolve host\n"
        )

        status = data_sync.upstream_status()

        assert status.has_updates is False
        assert status.reason and "Could not reach origin" in status.reason

    def test_timeout_is_not_an_exception(self, git_calls, monkeypatch):
        def fake_run(cmd, **kwargs):
            if cmd[1] == "ls-remote":
                raise subprocess.TimeoutExpired(cmd, 10)
            return _ok()

        monkeypatch.setattr(subprocess, "run", fake_run)

        status = data_sync.upstream_status()

        assert status.has_updates is False
        assert status.reason and "timed out" in status.reason

    def test_a_remote_with_no_main_branch(self, git_calls):
        _, scripted = git_calls
        scripted[("ls-remote", "origin")] = _ok("")

        status = data_sync.upstream_status()

        assert status.has_updates is False
        assert status.reason


class TestUpstreamStatusCaching:
    def test_repeated_calls_hit_the_network_once(self, git_calls):
        """Several components mount this on one page load."""
        calls, scripted = git_calls
        scripted[("ls-remote", "origin")] = _ok(f"{_REMOTE}\trefs/heads/main\n")
        scripted[("rev-parse", "HEAD")] = _ok(f"{_REMOTE}\n")

        data_sync.upstream_status()
        data_sync.upstream_status()
        data_sync.upstream_status()

        assert len([c for c in calls if c[1] == "ls-remote"]) == 1

    def test_force_bypasses_the_cache(self, git_calls):
        calls, scripted = git_calls
        scripted[("ls-remote", "origin")] = _ok(f"{_REMOTE}\trefs/heads/main\n")
        scripted[("rev-parse", "HEAD")] = _ok(f"{_REMOTE}\n")

        data_sync.upstream_status()
        data_sync.upstream_status(force=True)

        assert len([c for c in calls if c[1] == "ls-remote"]) == 2

    def test_a_pull_invalidates_it(self, git_calls):
        """Otherwise the banner keeps offering data already taken."""
        calls, scripted = git_calls
        scripted[("ls-remote", "origin")] = _ok(f"{_REMOTE}\trefs/heads/main\n")
        scripted[("rev-parse", "HEAD")] = _ok(f"{_LOCAL}\n")
        scripted[("cat-file", "-e")] = _ok()
        scripted[("rev-list", "--count")] = _ok("1\n")
        scripted[("rev-parse", "--short")] = _ok("abc1234\n")

        assert data_sync.upstream_status().has_updates is True
        data_sync.pull_latest()
        data_sync.upstream_status()

        assert len([c for c in calls if c[1] == "ls-remote"]) == 2, (
            "pull should have invalidated the cached answer"
        )


class TestRefreshChain:
    """The half of a sync that is no longer a git operation.

    The chain database left Git LFS for a release asset on 2026-11-22, so
    "show me what the pipeline collected" is now a pull plus a download.
    """

    @pytest.fixture
    def chain(self, monkeypatch, tmp_path):
        """The real _refresh_chain, with chain_release.sync() under control.

        The autouse stub in this module replaces _refresh_chain for every
        other test, which would otherwise shadow the very function this
        class exists to test -- hence restoring the copy captured at import.
        """
        from src import chain_release

        local = tmp_path / "schwab_database.db"
        monkeypatch.setattr(data_sync, "_refresh_chain", _REAL_REFRESH_CHAIN)
        monkeypatch.setattr(data_sync, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(data_sync, "_CHAIN_DB", "schwab_database.db")
        return local, chain_release

    def test_nothing_new_upstream_reports_no_change(self, chain, monkeypatch):
        """Months that are finished never change again, so a second sync
        transfers nothing -- which is what makes the button cheap to press."""
        local, chain_release = chain
        monkeypatch.setattr(
            chain_release, "sync",
            lambda *a, **k: {"index": 7, "downloaded": [], "skipped": ["2026-11"], "months": ["2026-11"]},
        )

        assert data_sync._refresh_chain() == data_sync._ChainOutcome(changed=False)

    def test_a_downloaded_month_reports_a_change(self, chain, monkeypatch):
        local, chain_release = chain
        monkeypatch.setattr(
            chain_release, "sync",
            lambda *a, **k: {"index": 7, "downloaded": [("2026-11", 24)], "skipped": [], "months": ["2026-11"]},
        )

        assert data_sync._refresh_chain().changed is True

    def test_a_success_adds_no_note(self, chain, monkeypatch):
        """`note` is rendered as "Synced the metric history, but {note}", so
        putting good news in it would produce a sentence that reads as a
        partial failure."""
        local, chain_release = chain
        monkeypatch.setattr(
            chain_release, "sync",
            lambda *a, **k: {"index": 7, "downloaded": [("2026-11", 24)], "skipped": [], "months": ["2026-11"]},
        )

        assert data_sync._refresh_chain().note is None

    def test_it_asks_for_every_month_not_just_the_recent_ones(self, chain, monkeypatch):
        """The dashboard can be asked for any date in the archive. A
        developer who synced only the current month would get an empty
        surface for last August with no indication why -- `newest` is for
        the nightly job, which only appends to the current month."""
        local, chain_release = chain
        seen = {}

        def record(db_path=None, **kwargs):
            seen.update(kwargs)
            seen["db_path"] = db_path
            return {"index": 1, "downloaded": [], "skipped": [], "months": []}

        monkeypatch.setattr(chain_release, "sync", record)
        data_sync._refresh_chain()

        assert seen.get("newest") is None
        assert seen.get("months") is None
        assert Path(seen["db_path"]).name == "schwab_database.db"

    def test_a_failed_sync_is_reported_not_raised(self, chain, monkeypatch):
        """The history pull may well have succeeded. "Most of your data
        arrived" is a different message from "sync failed"."""
        local, chain_release = chain

        def boom(*a, **k):
            raise chain_release.ChainDownloadError("404, no asset on that tag")

        monkeypatch.setattr(chain_release, "sync", boom)

        outcome = data_sync._refresh_chain()

        assert outcome.changed is False
        assert "404" in outcome.note

    def test_pull_latest_surfaces_the_chain_failure(self, git_calls, monkeypatch):
        """A sync that moved the history but not the chain must not read as
        an unqualified success."""
        _, scripted = git_calls
        scripted[("rev-parse", "--short")] = _ok("abc1234\n")
        monkeypatch.setattr(
            data_sync,
            "_refresh_chain",
            lambda: data_sync._ChainOutcome(changed=False, note="the chain store did not update (404)"),
        )

        assert "404" in data_sync.pull_latest().message
