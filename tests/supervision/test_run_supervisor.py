"""RunSupervisor blocks a failed run until i2code ctl resumes or stops it."""

import json

import pytest

from i2code.supervision.inbox import Inbox
from i2code.supervision.run_journal import RunJournal
from i2code.supervision.run_paths import RunPaths
from i2code.supervision.run_supervisor import ResumeRequest, RunStopped, RunSupervisor


class _ScriptedSleep:
    """Runs one scripted action per sleep call, recording the requested durations."""

    def __init__(self, *actions):
        self._actions = list(actions)
        self.durations = []

    def __call__(self, seconds):
        self.durations.append(seconds)
        self._actions.pop(0)()


@pytest.fixture
def paths(tmp_path):
    return RunPaths(tmp_path / "run")


def _supervisor(paths, sleep, messages):
    return RunSupervisor(RunJournal(paths), Inbox(paths), idea="demo", sleep=sleep, echo=messages.append)


def _events(paths):
    journal = RunJournal(paths)
    return journal.status, [line for line in paths.events_file.read_text().splitlines()]


@pytest.mark.unit
class TestRunSupervisorBlock:

    def test_waits_for_resume_and_returns_request(self, paths):
        inbox = Inbox(paths)
        sleep = _ScriptedSleep(lambda: None, lambda: inbox.post("resume", note="copied lefthook.yml", fresh=False))
        messages = []
        supervisor = _supervisor(paths, sleep, messages)

        request = supervisor.block("task", "failure_tag", task="4.5", detail="needs lefthook.yml",
                                   session_id="s-1", permission_denials=[])

        assert request == ResumeRequest(note="copied lefthook.yml", fresh=False)
        assert sleep.durations == [5, 5]
        status, _ = _events(paths)
        assert status["state"] == "running"
        assert "Blocked (task: failure_tag)" in messages[0]
        assert "i2code ctl resume demo" in messages[0]

    def test_records_blocked_then_resumed(self, paths):
        inbox = Inbox(paths)
        sleep = _ScriptedSleep(lambda: inbox.post("resume", note=None, fresh=True))
        supervisor = _supervisor(paths, sleep, [])

        supervisor.block("task", "missing_tag", task="1.1", detail="", session_id=None, permission_denials=[])

        _, lines = _events(paths)
        assert '"event": "blocked"' in lines[0] and '"reason": "missing_tag"' in lines[0]
        assert '"event": "resumed"' in lines[1] and '"mode": "fresh"' in lines[1]

    def test_stop_while_blocked_raises_run_stopped(self, paths):
        inbox = Inbox(paths)
        sleep = _ScriptedSleep(lambda: inbox.post("stop"))
        supervisor = _supervisor(paths, sleep, [])

        with pytest.raises(RunStopped):
            supervisor.block("push", "push_failed", task=None, detail="", session_id=None, permission_denials=[])

        status, _ = _events(paths)
        assert status["stop_requested"] is True

    def test_notes_stay_queued_while_blocked(self, paths):
        inbox = Inbox(paths)
        sleep = _ScriptedSleep(lambda: inbox.post("note", text="for the next prompt"),
                               lambda: inbox.post("resume", note=None, fresh=False))
        supervisor = _supervisor(paths, sleep, [])

        supervisor.block("task", "failure_tag", task="1.1", detail="", session_id="s", permission_denials=[])

        assert inbox.count("note") == 1

    def test_record_delegates_to_journal(self, paths):
        supervisor = _supervisor(paths, _ScriptedSleep(), [])

        supervisor.record("pushed", head="abc")

        status, _ = _events(paths)
        assert status["updated"] is not None


@pytest.mark.unit
class TestRunSupervisorStop:

    def test_checkpoint_without_stop_request_returns(self, paths):
        _supervisor(paths, _ScriptedSleep(), []).raise_if_stop_requested()

    def test_checkpoint_with_stop_request_raises(self, paths):
        Inbox(paths).post("stop")

        with pytest.raises(RunStopped):
            _supervisor(paths, _ScriptedSleep(), []).raise_if_stop_requested()

    def test_discard_stale_requests_keeps_notes(self, paths):
        inbox = Inbox(paths)
        inbox.post("resume", note=None, fresh=False)
        inbox.post("stop")
        inbox.post("note", text="keep")

        _supervisor(paths, _ScriptedSleep(), []).discard_stale_requests()

        assert (inbox.count("resume"), inbox.count("stop"), inbox.count("note")) == (0, 0, 1)


@pytest.mark.unit
class TestRunSupervisorWithoutJournal:

    def test_block_exits_when_the_journal_is_disabled(self, tmp_path):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x")
        paths = RunPaths(blocker / "run")
        messages = []
        supervisor = _supervisor(paths, _ScriptedSleep(), messages)

        with pytest.raises(SystemExit) as exit_info:
            supervisor.block("task", "failure_tag", task="1.1", detail="", session_id=None, permission_denials=[])

        assert exit_info.value.code == 1
        assert "cannot wait for i2code ctl" in messages[-1]


@pytest.mark.unit
class TestRunSupervisorResumeRaces:

    def test_resume_queued_before_the_block_is_discarded(self, paths):
        inbox = Inbox(paths)
        inbox.post("resume", note="meant for an earlier block", fresh=False)
        sleep = _ScriptedSleep(lambda: None, lambda: inbox.post("resume", note="for this block", fresh=False))
        supervisor = _supervisor(paths, sleep, [])

        request = supervisor.block("task", "failure_tag", task="1.1", detail="", session_id="s", permission_denials=[])

        assert request.note == "for this block"
        assert len(sleep.durations) == 2

    def test_resumes_in_one_poll_are_merged(self, paths):
        inbox = Inbox(paths)

        def two_resumes():
            inbox.post("resume", note="first", fresh=False)
            inbox.post("resume", note="second", fresh=True)

        supervisor = _supervisor(paths, _ScriptedSleep(two_resumes), [])

        request = supervisor.block("task", "failure_tag", task="1.1", detail="", session_id="s", permission_denials=[])

        assert request == ResumeRequest(note="first\nsecond", fresh=True)
        resumed = [json.loads(line) for line in paths.events_file.read_text().splitlines()][-1]
        assert resumed["event"] == "resumed" and resumed["merged"] == 2
