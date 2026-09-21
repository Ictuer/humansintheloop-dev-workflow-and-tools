"""Supervisor contract used by the implement loop, and the no-op default."""

from typing import Any, Protocol


class Supervisor(Protocol):
    """What the implement loop needs from whoever supervises the run."""

    def record(self, event: str, **fields: Any) -> Any: ...


class NullSupervisor:
    """Keeps today's behaviour when nothing supervises the run."""

    def record(self, event: str, **fields: Any) -> None:
        return None
