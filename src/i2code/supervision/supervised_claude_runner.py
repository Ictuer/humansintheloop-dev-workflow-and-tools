"""SupervisedClaudeRunner: journals every Claude invocation made through the wrapped runner."""

from typing import Optional, Protocol

from i2code.implement.claude_runner import ClaudeCodeCommand, ClaudeResult
from i2code.supervision.supervisor import Supervisor

SUMMARY_LENGTH = 300


class ClaudeExecutor(Protocol):
    def execute(self, command: ClaudeCodeCommand) -> ClaudeResult: ...


def _resumed_session(command: ClaudeCodeCommand) -> Optional[str]:
    session = command.session_id
    if session is None or session.is_new:
        return None
    return session.session_id


class SupervisedClaudeRunner:
    """Decorates a Claude runner with claude_started/claude_finished journal events."""

    def __init__(self, inner: ClaudeExecutor, journal: Supervisor):
        self.inner = inner
        self._journal = journal

    def execute(self, command: ClaudeCodeCommand) -> ClaudeResult:
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
