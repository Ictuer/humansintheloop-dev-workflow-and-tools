"""RunJournal appends events and keeps status.json equal to the fold of all events."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from i2code.supervision.run_journal import RunJournal, fold_status
from i2code.supervision.run_paths import RunPaths


def _ev(event, ts="2026-09-21T10:00:00+07:00", **fields):
    return {"ts": ts, "event": event, **fields}


RUN_STARTED = _ev("run_started", pid=42, idea="demo", branch="idea/demo", worktree="/wt",
                  on_failure="wait", nudge_missing_tag=1)
TASK_STARTED = _ev("task_started", task="1.2", title="Do it", index=2, total=5)


@pytest.mark.unit
class TestFoldStatus:

    def test_no_events_means_no_run(self):
        assert fold_status([])["state"] == "none"

    def test_run_started_sets_identity_and_running(self):
        status = fold_status([RUN_STARTED])
        assert status["pid"] == 42
        assert status["idea"] == "demo"
        assert status["state"] == "running"
        assert status["task"] is None
        assert status["blocked"] is None
        assert status["stop_requested"] is False

    def test_task_started_sets_current_task(self):
        status = fold_status([RUN_STARTED, TASK_STARTED])
        assert status["task"] == {"task": "1.2", "title": "Do it", "index": 2, "total": 5}

    def test_claude_started_and_finished(self):
        started = _ev("claude_started", ts="2026-09-21T10:01:00+07:00", label="task", resumes_session=None)
        status = fold_status([RUN_STARTED, TASK_STARTED, started])
        assert status["claude"] == {"label": "task", "since": "2026-09-21T10:01:00+07:00", "resumes_session": None}

        finished = _ev("claude_finished", label="task", exit_code=0, outcome="missing", session_id="s-1")
        status = fold_status([RUN_STARTED, TASK_STARTED, started, finished])
        assert status["claude"] is None
        assert status["last_claude"] == {"label": "task", "exit_code": 0, "outcome": "missing", "session_id": "s-1"}

    def test_task_completed_clears_task(self):
        status = fold_status([RUN_STARTED, TASK_STARTED, _ev("task_completed", task="1.2", duration_s=3, head="b")])
        assert status["task"] is None
        assert status["state"] == "running"

    def test_ci_wait_and_finish(self):
        waiting = _ev("ci_waiting", head="b")
        assert fold_status([RUN_STARTED, waiting])["state"] == "ci_wait"
        finished = _ev("ci_finished", head="b", success=True, failing_workflow=None)
        assert fold_status([RUN_STARTED, waiting, finished])["state"] == "running"

    def test_blocked_then_resumed(self):
        blocked = _ev("blocked", kind="task", reason="missing_tag", task="1.2", detail="d", session_id="s-1",
                      permission_denials=[])
        status = fold_status([RUN_STARTED, TASK_STARTED, blocked])
        assert status["state"] == "blocked"
        assert status["blocked"]["reason"] == "missing_tag"
        assert status["blocked"]["session_id"] == "s-1"

        status = fold_status([RUN_STARTED, TASK_STARTED, blocked, _ev("resumed", mode="continue", note="n")])
        assert status["state"] == "running"
        assert status["blocked"] is None

    def test_stop_requested(self):
        assert fold_status([RUN_STARTED, _ev("stop_requested")])["stop_requested"] is True

    @pytest.mark.parametrize("final", ["completed", "stopped", "failed"])
    def test_run_finished_sets_final_state(self, final):
        status = fold_status([RUN_STARTED, TASK_STARTED, _ev("run_finished", status=final, exit_code=0)])
        assert status["state"] == final
        assert status["exit_code"] == 0
        assert status["claude"] is None

    def test_new_run_resets_previous_run(self):
        blocked = _ev("blocked", kind="task", reason="failure_tag", task="1.2", detail="d", session_id=None,
                      permission_denials=[])
        finished = _ev("run_finished", status="failed", exit_code=1)
        restarted = _ev("run_started", pid=43, idea="demo", branch="idea/demo", worktree="/wt",
                        on_failure="exit", nudge_missing_tag=0)
        status = fold_status([RUN_STARTED, TASK_STARTED, blocked, finished, restarted])
        assert status["pid"] == 43
        assert status["state"] == "running"
        assert status["blocked"] is None
        assert status["task"] is None
        assert status["exit_code"] is None

    def test_every_event_updates_timestamp(self):
        status = fold_status([RUN_STARTED, _ev("pushed", ts="2026-09-21T11:00:00+07:00", head="b")])
        assert status["updated"] == "2026-09-21T11:00:00+07:00"


class _Clock:
    def __init__(self):
        self._now = datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone(timedelta(hours=7)))

    def __call__(self):
        self._now += timedelta(seconds=1)
        return self._now


def _read_events(paths):
    return [json.loads(line) for line in paths.events_file.read_text(encoding="utf-8").splitlines()]


@pytest.mark.unit
class TestRunJournal:

    def test_record_appends_json_line_with_ts_and_event(self, tmp_path):
        paths = RunPaths(tmp_path / "run")
        journal = RunJournal(paths, clock=_Clock())

        journal.record("run_started", pid=42, idea="démo")

        assert _read_events(paths) == [
            {"ts": "2026-09-21T10:00:01+07:00", "event": "run_started", "pid": 42, "idea": "démo"},
        ]

    def test_status_file_equals_fold_of_all_events_after_each_record(self, tmp_path):
        paths = RunPaths(tmp_path / "run")
        journal = RunJournal(paths, clock=_Clock())

        for event, fields in [
            ("run_started", {"pid": 42, "idea": "demo"}),
            ("task_started", {"task": "1.1", "title": "t", "index": 1, "total": 2}),
            ("blocked", {"kind": "task", "reason": "missing_tag", "task": "1.1", "detail": "", "session_id": "s",
                         "permission_denials": []}),
        ]:
            journal.record(event, **fields)
            status = json.loads(paths.status_file.read_text(encoding="utf-8"))
            assert status == fold_status(_read_events(paths))

    def test_journal_continues_an_existing_events_file(self, tmp_path):
        paths = RunPaths(tmp_path / "run")
        RunJournal(paths, clock=_Clock()).record("run_started", pid=1, idea="demo")

        RunJournal(paths, clock=_Clock()).record("stop_requested")

        assert [e["event"] for e in _read_events(paths)] == ["run_started", "stop_requested"]
        assert json.loads(paths.status_file.read_text())["stop_requested"] is True

    def test_leaves_no_temporary_files(self, tmp_path):
        paths = RunPaths(tmp_path / "run")
        journal = RunJournal(paths, clock=_Clock())

        journal.record("run_started", pid=1, idea="demo")

        assert sorted(p.name for p in paths.directory.iterdir()) == ["events.jsonl", "status.json"]
