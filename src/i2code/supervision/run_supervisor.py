"""RunSupervisor: journal + inbox for one run; blocks a failed run until ctl resumes or stops it."""

import sys
import time
from typing import Any, Callable

from i2code.implement.console import print_message
from i2code.supervision.inbox import Inbox
from i2code.supervision.run_journal import RunJournal
from i2code.supervision.supervisor import ResumeRequest, RunStopped

__all__ = ["ResumeRequest", "RunStopped", "RunSupervisor"]

BLOCKED_POLL_SECONDS = 5


class RunSupervisor:
    """Supervisor backed by the run journal and the ctl inbox."""

    def __init__(
        self,
        journal: RunJournal,
        inbox: Inbox,
        idea: str,
        sleep: Callable[[float], None] = time.sleep,
        echo: Callable[[str], None] = print_message,
    ):
        self._journal = journal
        self._inbox = inbox
        self._idea = idea
        self._sleep = sleep
        self._echo = echo

    def record(self, event: str, **fields: Any) -> Any:
        return self._journal.record(event, **fields)

    def block(self, kind: str, reason: str, **details: Any) -> ResumeRequest:
        self.record("blocked", kind=kind, reason=reason, **details)
        if not self._journal.enabled:
            self._echo(f"Failed ({kind}: {reason}); cannot wait for i2code ctl without a run journal, exiting.")
            sys.exit(1)
        self._echo(
            f"Blocked ({kind}: {reason}). Waiting for "
            f"`i2code ctl resume {self._idea} [--note TEXT] [--fresh]` or `i2code ctl stop {self._idea}`..."
        )
        while True:
            self._sleep(BLOCKED_POLL_SECONDS)
            self.raise_if_stop_requested()
            resumes = self._inbox.take("resume")
            if resumes:
                return self._resumed(resumes[0])

    def discard_stale_requests(self) -> None:
        """Drop resume/stop requests left by an earlier run; queued notes stay."""
        self._inbox.discard("resume", "stop")

    def raise_if_stop_requested(self) -> None:
        if self._inbox.take("stop"):
            self.record("stop_requested")
            raise RunStopped()

    def _resumed(self, message: dict) -> ResumeRequest:
        request = ResumeRequest(note=message.get("note"), fresh=bool(message.get("fresh")))
        self.record("resumed", mode="fresh" if request.fresh else "continue", note=request.note)
        return request
