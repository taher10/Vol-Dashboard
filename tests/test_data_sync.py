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
            data_sync, "dirty_tracked_data", lambda: ["database/schwab_database.db"]
        )

        result = data_sync.pull_latest()

        assert result.changed is False
        assert "schwab_database.db" in result.message
        assert not any(c[1] == "pull" for c in calls), "must not pull over local changes"

    def test_dirty_detection_reads_porcelain_for_the_data_files(self, git_calls):
        calls, scripted = git_calls
        scripted[("status", "--porcelain")] = _ok(" M database/schwab_database.db\n")

        assert data_sync.dirty_tracked_data() == ["database/schwab_database.db"]
        status = next(c for c in calls if c[1] == "status")
        assert "database/schwab_database.db" in status


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
