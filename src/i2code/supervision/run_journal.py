"""RunJournal: append-only events.jsonl plus a status.json folded from those events."""

import json
import os
import sys
import tempfile
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List

from i2code.supervision.run_paths import RunPaths

Event = Dict[str, Any]
Status = Dict[str, Any]


def _no_run() -> Status:
    return {
        "pid": None,
        "idea": None,
        "state": "none",
        "task": None,
        "claude": None,
        "last_claude": None,
        "blocked": None,
        "stop_requested": False,
        "exit_code": None,
        "started": None,
        "updated": None,
    }


def _payload(event: Event, *keys: str) -> Dict[str, Any]:
    return {key: event.get(key) for key in keys}


def _run_started(status: Status, event: Event) -> Status:
    return {**_no_run(), "pid": event.get("pid"), "idea": event.get("idea"), "state": "running",
            "started": event["ts"]}


def _task_started(status: Status, event: Event) -> Status:
    return {**status, "task": _payload(event, "task", "title", "index", "total")}


def _task_completed(status: Status, event: Event) -> Status:
    return {**status, "task": None}


def _claude_started(status: Status, event: Event) -> Status:
    claude = {"label": event.get("label"), "since": event["ts"], "resumes_session": event.get("resumes_session")}
    return {**status, "claude": claude}


def _claude_finished(status: Status, event: Event) -> Status:
    last = _payload(event, "label", "exit_code", "outcome", "session_id")
    return {**status, "claude": None, "last_claude": last}


def _ci_waiting(status: Status, event: Event) -> Status:
    return {**status, "state": "ci_wait"}


def _back_to_running(status: Status, event: Event) -> Status:
    return {**status, "state": "running", "blocked": None}


def _blocked(status: Status, event: Event) -> Status:
    blocked = {key: value for key, value in event.items() if key != "event"}
    return {**status, "state": "blocked", "blocked": blocked}


def _stop_requested(status: Status, event: Event) -> Status:
    return {**status, "stop_requested": True}


def _run_finished(status: Status, event: Event) -> Status:
    return {**status, "state": event.get("status"), "exit_code": event.get("exit_code"), "claude": None}


_TRANSITIONS: Dict[str, Callable[[Status, Event], Status]] = {
    "run_started": _run_started,
    "task_started": _task_started,
    "task_completed": _task_completed,
    "claude_started": _claude_started,
    "claude_finished": _claude_finished,
    "ci_waiting": _ci_waiting,
    "ci_finished": _back_to_running,
    "resumed": _back_to_running,
    "blocked": _blocked,
    "stop_requested": _stop_requested,
    "run_finished": _run_finished,
}


def apply_event(status: Status, event: Event) -> Status:
    """Return the status after one event; unknown events only update the timestamp."""
    transition = _TRANSITIONS.get(event["event"])
    changed = transition(status, event) if transition else status
    return {**changed, "updated": event["ts"]}


def fold_status(events: Iterable[Event]) -> Status:
    """Status of the latest run described by the events, oldest first."""
    status = _no_run()
    for event in events:
        status = apply_event(status, event)
    return status


def read_events(paths: RunPaths) -> List[Event]:
    """Read all complete events; partially written or undecodable lines are skipped."""
    if not paths.events_file.exists():
        return []
    events = []
    for line in paths.events_file.read_bytes().split(b"\n"):
        try:
            events.append(json.loads(line.decode("utf-8", errors="replace")))
        except json.JSONDecodeError:
            continue
    return events


def _local_now() -> datetime:
    return datetime.now().astimezone()


class RunJournal:
    """Records events for one run and keeps status.json in step with them.

    Journaling is best effort: an I/O error turns it off for the rest of the run instead of failing the run.
    Events are written ASCII-only so that a line cut short can never leave a broken multibyte character.
    """

    def __init__(self, paths: RunPaths, clock: Callable[[], datetime] = _local_now):
        self._paths = paths
        self._clock = clock
        self._enabled = True
        try:
            self._status = fold_status(read_events(paths))
        except OSError as error:
            self._status = fold_status([])
            self._disable(error)

    @property
    def status(self) -> Status:
        return self._status

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record(self, event: str, **fields: Any) -> Event:
        entry = {"ts": self._clock().isoformat(timespec="seconds"), "event": event, **fields}
        self._status = apply_event(self._status, entry)
        if self._enabled:
            try:
                self._persist(entry)
            except OSError as error:
                self._disable(error)
        return entry

    def _persist(self, entry: Event) -> None:
        self._paths.directory.mkdir(parents=True, exist_ok=True)
        self._append(entry)
        self._write_status()

    def _disable(self, error: OSError) -> None:
        self._enabled = False
        print(f"Warning: run journal disabled ({error}); the run continues without it", file=sys.stderr)

    def _append(self, entry: Event) -> None:
        repair = "\n" if self._last_line_is_unfinished() else ""
        with open(self._paths.events_file, "a", encoding="ascii") as f:
            f.write(repair + json.dumps(entry, ensure_ascii=True) + "\n")

    def _last_line_is_unfinished(self) -> bool:
        events_file = self._paths.events_file
        if not events_file.exists() or events_file.stat().st_size == 0:
            return False
        with open(events_file, "rb") as f:
            f.seek(-1, os.SEEK_END)
            return f.read(1) != b"\n"

    def _write_status(self) -> None:
        fd, tmp = tempfile.mkstemp(dir=self._paths.directory, prefix=".status-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._status, f, ensure_ascii=True, indent=2)
            f.write("\n")
        os.replace(tmp, self._paths.status_file)
