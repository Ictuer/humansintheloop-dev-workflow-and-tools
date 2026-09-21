"""Human-readable view of a supervised run's status."""

import os
from typing import Any, Dict, List, Optional

FINAL_STATES = ("completed", "stopped", "failed")


def is_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _state_line(status: Dict[str, Any], alive: bool) -> str:
    state, pid = status["state"], status["pid"]
    if state in FINAL_STATES:
        return f"state: {state} (exit {status['exit_code']})"
    if not alive:
        return f"state: dead (last state: {state}, pid {pid})"
    return f"state: {state} (pid {pid}, alive)"


def _task_line(task: Optional[Dict[str, Any]]) -> List[str]:
    if not task:
        return []
    return [f"task: {task['task']} ({task['index']}/{task['total']}) {task['title']}"]


def _claude_lines(status: Dict[str, Any]) -> List[str]:
    lines = []
    claude = status.get("claude")
    if claude:
        resumes = f" (resumes {claude['resumes_session']})" if claude.get("resumes_session") else ""
        lines.append(f"claude: {claude['label']} since {claude['since']}{resumes}")
    last = status.get("last_claude")
    if last:
        lines.append(f"last claude: {last['label']} exit {last['exit_code']} outcome {last['outcome']} "
                     f"session {last['session_id']}")
    return lines


def _blocked_lines(blocked: Optional[Dict[str, Any]], idea: str) -> List[str]:
    if not blocked:
        return []
    return [
        f"blocked: {blocked['kind']} {blocked['reason']} — {blocked.get('detail') or ''}".rstrip(),
        f"  resume: i2code ctl resume {idea} [--note TEXT] [--fresh]    stop: i2code ctl stop {idea}",
    ]


def describe(idea: str, status: Dict[str, Any], alive: bool, events_file: str, queued_notes: int = 0) -> List[str]:
    """Lines shown by ``i2code ctl status``."""
    if status["state"] == "none":
        return [f"no run recorded for {idea} ({events_file})"]
    lines = [f"idea: {idea}", _state_line(status, alive)]
    lines += _task_line(status.get("task"))
    lines += _claude_lines(status)
    lines += _blocked_lines(status.get("blocked"), idea)
    if status.get("stop_requested"):
        lines.append("stop requested")
    if queued_notes:
        lines.append(f"queued notes: {queued_notes}")
    lines.append(f"events: {events_file}")
    return lines
