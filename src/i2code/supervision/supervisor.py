"""Supervisor contract used by the implement loop, and the no-op default."""

from dataclasses import dataclass
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class ResumeRequest:
    """What ``i2code ctl resume`` asked for."""

    note: Optional[str] = None
    fresh: bool = False


class RunStopped(Exception):
    """The supervising session asked the run to stop."""


class EventRecorder(Protocol):
    """Anything that can append an event to the run journal."""

    def record(self, event: str, **fields: Any) -> Any: ...


class Supervisor(EventRecorder, Protocol):
    """What the implement loop needs from whoever supervises the run."""

    def block(self, kind: str, reason: str, **details: Any) -> ResumeRequest: ...

    def raise_if_stop_requested(self) -> None: ...

    def discard_stale_requests(self) -> None: ...


class NullSupervisor:
    """Keeps today's behaviour when nothing supervises the run."""

    def record(self, event: str, **fields: Any) -> None:
        return None

    def raise_if_stop_requested(self) -> None:
        return None

    def discard_stale_requests(self) -> None:
        return None

    def block(self, kind: str, reason: str, **details: Any) -> ResumeRequest:
        raise RuntimeError(f"cannot wait for a supervisor ({kind}: {reason}) without a run journal")
