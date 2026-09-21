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


class QueuedNotes:
    def __init__(self, *texts):
        self._texts = list(texts)

    def take(self, kind):
        assert kind == "note"
        taken = [{"kind": "note", "text": t} for t in self._texts]
        self._texts = []
        return taken

    def count(self, kind):
        return len(self._texts)


NOTES_HEADER = "Notes from the supervising session (oldest first):"


@pytest.mark.unit
class TestSupervisedClaudeRunnerNotes:

    @pytest.mark.parametrize("label", ["task", "ci_fix", "fix_feedback", "nudge", "resume"])
    def test_notes_are_appended_to_steerable_prompts(self, label):
        inner = FakeClaudeRunner()
        journal = RecordingJournal()
        notes = QueuedNotes("use the staging cluster", "skip the soak test")
        runner = SupervisedClaudeRunner(inner, journal, notes=notes)

        runner.execute(ClaudeCodeCommand(prompt="Do the task.", cwd="/c", label=label, allowed_tools="Read"))

        sent = inner.calls[0][1]
        assert sent.prompt == (
            f"Do the task.\n\n{NOTES_HEADER}\n- use the staging cluster\n- skip the soak test"
        )
        assert sent.allowed_tools == "Read"
        assert notes.count("note") == 0
        assert {"event": "note_delivered", "label": label, "count": 2} in journal.events

    @pytest.mark.parametrize("label", ["triage", "recovery", "scaffolding", "feedback", None])
    def test_other_labels_do_not_receive_notes(self, label):
        inner = FakeClaudeRunner()
        notes = QueuedNotes("keep me")
        runner = SupervisedClaudeRunner(inner, RecordingJournal(), notes=notes)

        runner.execute(ClaudeCodeCommand(prompt="p", cwd="/c", label=label))

        assert inner.calls[0][1].prompt == "p"
        assert notes.count("note") == 1

    def test_mock_commands_do_not_receive_notes(self):
        inner = FakeClaudeRunner()
        notes = QueuedNotes("keep me")
        runner = SupervisedClaudeRunner(inner, RecordingJournal(), notes=notes)

        runner.execute(ClaudeCodeCommand(cwd="/c", mock_command=["/mock", "task"], label="task"))

        assert inner.calls[0][1].mock_command == ["/mock", "task"]
        assert notes.count("note") == 1

    def test_no_queued_notes_leaves_prompt_and_journal_alone(self):
        inner = FakeClaudeRunner()
        journal = RecordingJournal()
        runner = SupervisedClaudeRunner(inner, journal, notes=QueuedNotes())

        runner.execute(ClaudeCodeCommand(prompt="p", cwd="/c", label="task"))

        assert inner.calls[0][1].prompt == "p"
        assert "note_delivered" not in [e["event"] for e in journal.events]


class BrokenNotes:
    def take(self, kind):
        raise PermissionError("inbox unreadable")


@pytest.mark.unit
class TestSupervisedClaudeRunnerBrokenInbox:

    def test_unreadable_inbox_delivers_no_notes(self):
        inner = FakeClaudeRunner()
        runner = SupervisedClaudeRunner(inner, RecordingJournal(), notes=BrokenNotes())

        runner.execute(ClaudeCodeCommand(prompt="p", cwd="/c", label="task"))

        assert inner.calls[0][1].prompt == "p"
