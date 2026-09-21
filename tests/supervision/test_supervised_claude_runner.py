"""SupervisedClaudeRunner journals every Claude invocation around the wrapped runner."""

import pytest

from i2code.implement.claude_runner import ClaudeCodeCommand, ClaudeResult, RunStats, SessionId
from i2code.supervision.supervised_claude_runner import SupervisedClaudeRunner

from fake_claude_runner import FakeClaudeRunner


class RecordingJournal:
    def __init__(self):
        self.events = []

    def record(self, event, **fields):
        self.events.append({"event": event, **fields})


@pytest.mark.unit
class TestSupervisedClaudeRunnerJournal:

    def test_records_started_and_finished_around_execution(self):
        inner = FakeClaudeRunner()
        inner.set_result(ClaudeResult(
            returncode=0, result_text="All good <SUCCESS>task implemented: abc</SUCCESS>", session_id="s-1",
            stats=RunStats(num_turns=12, cost_usd=1.5, duration_s=90.0),
        ))
        journal = RecordingJournal()
        runner = SupervisedClaudeRunner(inner, journal)
        command = ClaudeCodeCommand(prompt="p", cwd="/c", label="task")

        result = runner.execute(command)

        assert result is not None and result.session_id == "s-1"
        assert inner.calls[0][1] is command
        assert journal.events == [
            {"event": "claude_started", "label": "task", "resumes_session": None},
            {"event": "claude_finished", "label": "task", "exit_code": 0, "outcome": "success",
             "session_id": "s-1", "num_turns": 12, "cost_usd": 1.5, "duration_s": 90.0,
             "summary": "All good <SUCCESS>task implemented: abc</SUCCESS>"},
        ]

    def test_resumed_session_is_recorded(self):
        journal = RecordingJournal()
        runner = SupervisedClaudeRunner(FakeClaudeRunner(), journal)

        runner.execute(ClaudeCodeCommand(prompt="p", cwd="/c", label="nudge",
                                         session_id=SessionId("s-9", is_new=False)))

        assert journal.events[0] == {"event": "claude_started", "label": "nudge", "resumes_session": "s-9"}

    def test_summary_is_truncated_to_300_characters(self):
        inner = FakeClaudeRunner()
        inner.set_result(ClaudeResult(returncode=0, result_text="x" * 1000))
        journal = RecordingJournal()

        SupervisedClaudeRunner(inner, journal).execute(ClaudeCodeCommand(prompt="p", cwd="/c", label="task"))

        assert journal.events[1]["summary"] == "x" * 300
