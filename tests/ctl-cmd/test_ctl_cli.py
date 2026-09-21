"""i2code ctl status/events read the run journal of an idea."""

import json
import os

import pytest
from click.testing import CliRunner
from git import Repo

from i2code.ctl_cmd.cli import ctl
from i2code.supervision.inbox import Inbox
from i2code.supervision.run_journal import RunJournal
from i2code.supervision.run_paths import RunPaths

DEAD_PID = 999_999_999


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = Repo.init(tmp_path)
    monkeypatch.chdir(tmp_path)
    return repo


def _journal(repo):
    return RunJournal(RunPaths.for_idea(repo, "demo"))


def _start_run(journal, pid=None):
    journal.record("run_started", pid=pid or os.getpid(), idea="demo", branch="idea/demo", worktree="/wt",
                   on_failure="wait", nudge_missing_tag=1)
    journal.record("task_started", task="1.2", title="Do it", index=2, total=5)


def _invoke(*args, obj=None):
    return CliRunner().invoke(ctl, list(args), obj=obj or {}, catch_exceptions=False)


@pytest.mark.unit
class TestCtlStatus:

    def test_no_run_recorded(self, repo):
        result = _invoke("status", "demo")

        assert result.exit_code == 0
        assert "no run recorded for demo" in result.output

    def test_running_run_shows_task_and_claude(self, repo):
        journal = _journal(repo)
        _start_run(journal)
        journal.record("claude_started", label="task", resumes_session=None)

        result = _invoke("status", "demo")

        assert result.exit_code == 0
        assert f"state: running (pid {os.getpid()}, alive)" in result.output
        assert "task: 1.2 (2/5) Do it" in result.output
        assert "claude: task since " in result.output
        assert str(RunPaths.for_idea(repo, "demo").events_file) in result.output

    def test_blocked_run_shows_reason_and_resume_hint(self, repo):
        journal = _journal(repo)
        _start_run(journal)
        journal.record("blocked", kind="task", reason="failure_tag", task="1.2", detail="needs SECRET_X",
                       session_id="s-1", permission_denials=[])

        result = _invoke("status", "demo")

        assert "state: blocked" in result.output
        assert "blocked: task failure_tag — needs SECRET_X" in result.output
        assert "i2code ctl resume demo" in result.output

    def test_dead_pid_with_unfinished_state_is_dead(self, repo):
        _start_run(_journal(repo), pid=DEAD_PID)

        result = _invoke("status", "demo")

        assert f"state: dead (last state: running, pid {DEAD_PID})" in result.output

    def test_finished_run_is_not_reported_dead(self, repo):
        journal = _journal(repo)
        _start_run(journal, pid=DEAD_PID)
        journal.record("run_finished", status="completed", exit_code=0)

        result = _invoke("status", "demo")

        assert "state: completed (exit 0)" in result.output

    def test_json_adds_alive_and_events_file(self, repo):
        _start_run(_journal(repo))

        result = _invoke("status", "demo", "--json")

        status = json.loads(result.output)
        assert status["state"] == "running"
        assert status["alive"] is True
        assert status["events_file"] == str(RunPaths.for_idea(repo, "demo").events_file)

    def test_idea_path_resolves_to_idea_name(self, repo):
        _start_run(_journal(repo))

        result = _invoke("status", "docs/ideas/active/demo")

        assert "task: 1.2 (2/5) Do it" in result.output


@pytest.mark.unit
class TestCtlEvents:

    def test_prints_last_n_events_as_json_lines(self, repo):
        journal = _journal(repo)
        _start_run(journal)
        journal.record("pushed", head="abc")

        result = _invoke("events", "demo", "--limit", "2")

        lines = [json.loads(line) for line in result.output.splitlines()]
        assert [e["event"] for e in lines] == ["task_started", "pushed"]

    def test_no_events(self, repo):
        result = _invoke("events", "demo")

        assert result.exit_code == 0
        assert result.output == ""

    def test_follow_streams_new_events_until_run_finished(self, repo):
        journal = _journal(repo)
        _start_run(journal)
        appended = iter([
            lambda: journal.record("pushed", head="abc"),
            lambda: None,
            lambda: journal.record("run_finished", status="completed", exit_code=0),
        ])

        def fake_sleep(_seconds):
            next(appended)()

        result = _invoke("events", "demo", "--limit", "1", "--follow", obj={"sleep": fake_sleep})

        events = [json.loads(line)["event"] for line in result.output.splitlines()]
        assert events == ["task_started", "pushed", "run_finished"]


@pytest.mark.unit
class TestCtlNote:

    def test_note_is_queued_even_without_a_run(self, repo):
        result = _invoke("note", "demo", "use the staging cluster")

        assert result.exit_code == 0
        assert "queued note for demo (1 pending)" in result.output
        inbox = Inbox(RunPaths.for_idea(repo, "demo"))
        assert inbox.take("note") == [{"kind": "note", "text": "use the staging cluster"}]

    def test_status_shows_queued_notes(self, repo):
        _start_run(_journal(repo))
        _invoke("note", "demo", "one")
        _invoke("note", "demo", "two")

        assert "queued notes: 2" in _invoke("status", "demo").output
        assert json.loads(_invoke("status", "demo", "--json").output)["queued_notes"] == 2
