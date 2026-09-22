"""TaskExecution: run one plan task through Claude — attempts, nudges, validation, blocking on failure."""

import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from i2code.implement.claude_runner import (
    ClaudeCodeCommand,
    ClaudeResult,
    check_claude_success,
    print_task_failure_diagnostics,
)
from i2code.implement.command_builder import CommandBuilder
from i2code.implement.console import print_message
from i2code.implement.git_setup import has_ci_workflow_files
from i2code.supervision.supervisor import ResumeRequest, Supervisor

MAX_ATTEMPTS = 3
API_RETRY_FIRST_WAIT_SECONDS = 60
API_RETRY_MAX_WAIT_SECONDS = 900
DETAIL_LENGTH = 500
_FAILURE_PAYLOAD = re.compile(r"<FAILURE>(.*?)</FAILURE>", re.DOTALL)


@dataclass(frozen=True)
class TaskFailure:
    """Why a task invocation did not pass validation."""

    reason: str
    result: ClaudeResult
    retryable: bool


def _tag_reason(result: ClaudeResult) -> str:
    return "failure_tag" if result.outcome == "failure" else "missing_tag"


def _failure_detail(result: ClaudeResult) -> str:
    text = result.result_text or result.output.stdout
    match = _FAILURE_PAYLOAD.search(text)
    return match.group(1).strip() if match else text[-DETAIL_LENGTH:]


def _denial_target(tool_input: Dict[str, Any]) -> str:
    for key in ("command", "file_path", "description"):
        if tool_input.get(key):
            return str(tool_input[key])
    return json.dumps(tool_input, ensure_ascii=False)


def _permission_denials(result: ClaudeResult) -> List[str]:
    return [
        f"{denial.get('tool_name', 'Unknown')}: {_denial_target(denial.get('tool_input', {}))}"
        for denial in result.diagnostics.permission_denials
    ]


class TaskExecution:
    """Runs a task command until it passes validation, blocking for the supervisor when --on-failure=wait."""

    def __init__(self, opts, git_repo, work_project, claude_runner, supervisor: Supervisor,
                 sleep: Callable[[float], None] = time.sleep):
        self._opts = opts
        self._git_repo = git_repo
        self._work_project = work_project
        self._claude_runner = claude_runner
        self._supervisor = supervisor
        self._sleep = sleep

    def run(self, next_task, claude_cmd: ClaudeCodeCommand) -> None:
        head_before = self._git_repo.head_sha
        failure = self._attempt(next_task, claude_cmd, head_before)
        while failure is not None:
            resume = self._block_or_exit(next_task, failure)
            resumed_cmd = self._resume_command(claude_cmd, failure, resume)
            failure = self._validate(next_task, self._run_and_recover(resumed_cmd), head_before)

    def _attempt(self, next_task, claude_cmd, head_before) -> Optional[TaskFailure]:
        failure = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            print_message(f"Running Claude (attempt {attempt}/{MAX_ATTEMPTS})...")
            failure = self._validate(next_task, self._run_and_recover(claude_cmd), head_before)
            if failure is None or not failure.retryable:
                return failure
        print(f"Error: Task failed after {MAX_ATTEMPTS} attempts.", file=sys.stderr)
        assert failure is not None
        return TaskFailure("attempts_exhausted", failure.result, retryable=False)

    def _validate(self, next_task, result: ClaudeResult, head_before) -> Optional[TaskFailure]:
        head_after = self._git_repo.head_sha
        if self._reports_blocker(result):
            print_task_failure_diagnostics(result, head_before, head_after)
            return TaskFailure("failure_tag", result, retryable=False)
        if not check_claude_success(result.returncode, head_before, head_after):
            print_task_failure_diagnostics(result, head_before, head_after)
            return TaskFailure("no_commit", result, retryable=True)
        if self._opts.non_interactive and "<SUCCESS>" not in result.output.stdout:
            print_task_failure_diagnostics(result, head_before, head_after)
            return TaskFailure(_tag_reason(result), result, retryable=False)
        if not self._work_project.is_task_completed(next_task.number.thread, next_task.number.task):
            print("Error: Task was not marked complete in plan file.", file=sys.stderr)
            return TaskFailure("not_marked_complete", result, retryable=True)
        if not has_ci_workflow_files(self._git_repo.working_tree_dir):
            print("Error: No GitHub Actions workflow file found in .github/workflows/", file=sys.stderr)
            print("Tasks must create a CI workflow (e.g., .github/workflows/ci.yml) before pushing.", file=sys.stderr)
            return TaskFailure("no_ci_workflow", result, retryable=True)
        return None

    def _reports_blocker(self, result: ClaudeResult) -> bool:
        """With --on-failure=wait an explicit <FAILURE> blocks at once: a fresh session would hit the same blocker."""
        return self._opts.on_failure == "wait" and self._opts.non_interactive and result.outcome == "failure"

    def _block_or_exit(self, next_task, failure: TaskFailure) -> ResumeRequest:
        if self._opts.on_failure != "wait":
            sys.exit(1)
        return self._supervisor.block(
            "task", failure.reason,
            task=f"{next_task.number.thread}.{next_task.number.task}",
            detail=_failure_detail(failure.result),
            session_id=failure.result.session_id,
            permission_denials=_permission_denials(failure.result),
        )

    def _resume_command(self, claude_cmd, failure: TaskFailure, resume: ResumeRequest) -> ClaudeCodeCommand:
        session_id = failure.result.session_id
        if resume.fresh or session_id is None:
            return CommandBuilder().with_supervisor_message(claude_cmd, resume.note)
        return CommandBuilder().build_resume_command(claude_cmd, session_id, resume.note)

    def _run_and_recover(self, claude_cmd: ClaudeCodeCommand) -> ClaudeResult:
        """Run Claude, resuming its session after temporary API errors and when it ended without an outcome tag."""
        claude_result = self._run_with_api_retries(claude_cmd)
        for _ in range(self._opts.nudge_missing_tag):
            session_id = claude_result.session_id
            if session_id is None or not self._needs_nudge(claude_result):
                break
            print_message("Claude ended without an outcome tag; resuming its session to ask for one...")
            nudge_cmd = CommandBuilder().build_nudge_command(claude_cmd, session_id)
            claude_result = self._claude_runner.execute(nudge_cmd)
        return claude_result

    def _run_with_api_retries(self, claude_cmd: ClaudeCodeCommand) -> ClaudeResult:
        """Resume a session cut off by a temporary Claude API error, waiting longer each time (--resume-on-api-error)."""
        claude_result = self._claude_runner.execute(claude_cmd)
        for retry in range(self._opts.resume_on_api_error):
            session_id = claude_result.session_id
            if session_id is None or not self._cut_off_by_api_error(claude_result):
                break
            wait = min(API_RETRY_FIRST_WAIT_SECONDS * 2 ** retry, API_RETRY_MAX_WAIT_SECONDS)
            print_message(f"{claude_result.diagnostics.error_message} — resuming the Claude session in {wait} s...")
            self._sleep(wait)
            retry_cmd = CommandBuilder().build_api_retry_command(claude_cmd, session_id)
            claude_result = self._claude_runner.execute(retry_cmd)
        return claude_result

    def _cut_off_by_api_error(self, claude_result: ClaudeResult) -> bool:
        return (
            self._opts.non_interactive
            and claude_result.returncode != 0
            and "API Error" in (claude_result.diagnostics.error_message or "")
        )

    def _needs_nudge(self, claude_result: ClaudeResult) -> bool:
        return (
            self._opts.non_interactive
            and claude_result.returncode == 0
            and claude_result.outcome == "missing"
            and "<SUCCESS>" not in claude_result.output.stdout
        )
