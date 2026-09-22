"""SupervisedClaudeRunner: journals every Claude invocation made through the wrapped runner."""

from dataclasses import replace
from typing import Any, Dict, List, Optional, Protocol

from i2code.implement.claude_runner import ClaudeCodeCommand, ClaudeResult
from i2code.supervision.supervisor import EventRecorder

SUMMARY_LENGTH = 300
STEERABLE_LABELS = frozenset({"task", "ci_fix", "fix_feedback", "nudge", "resume", "retry"})
NOTES_HEADER = "Notes from the supervising session (oldest first):"


class ClaudeExecutor(Protocol):
    def execute(self, command: ClaudeCodeCommand) -> ClaudeResult: ...


class NoteQueue(Protocol):
    def take(self, kind: str) -> List[Dict[str, Any]]: ...


class NoNotes:
    def take(self, kind: str) -> List[Dict[str, Any]]:
        return []


def _is_steerable(command: ClaudeCodeCommand) -> bool:
    return command.mock_command is None and command.prompt is not None and command.label in STEERABLE_LABELS


def _with_notes(prompt: str, notes: List[str]) -> str:
    bullets = "\n".join(f"- {note}" for note in notes)
    return f"{prompt}\n\n{NOTES_HEADER}\n{bullets}"


def _resumed_session(command: ClaudeCodeCommand) -> Optional[str]:
    session = command.session_id
    if session is None or session.is_new:
        return None
    return session.session_id


class SupervisedClaudeRunner:
    """Decorates a Claude runner: journals each invocation and delivers queued supervisor notes."""

    def __init__(self, inner: ClaudeExecutor, journal: EventRecorder, notes: NoteQueue = NoNotes()):
        self.inner = inner
        self._journal = journal
        self._notes = notes

    def execute(self, command: ClaudeCodeCommand) -> ClaudeResult:
        command = self._deliver_notes(command)
        self._journal.record("claude_started", label=command.label, resumes_session=_resumed_session(command))
        result = self.inner.execute(command)
        self._journal.record(
            "claude_finished",
            label=command.label,
            exit_code=result.returncode,
            outcome=result.outcome,
            session_id=result.session_id,
            num_turns=result.stats.num_turns,
            cost_usd=result.stats.cost_usd,
            duration_s=result.stats.duration_s,
            summary=result.result_text[:SUMMARY_LENGTH],
        )
        return result

    def _deliver_notes(self, command: ClaudeCodeCommand) -> ClaudeCodeCommand:
        if not _is_steerable(command):
            return command
        try:
            notes = [message["text"] for message in self._notes.take("note")]
        except OSError:
            return command
        if not notes:
            return command
        self._journal.record("note_delivered", label=command.label, count=len(notes))
        return replace(command, prompt=_with_notes(command.prompt or "", notes))
