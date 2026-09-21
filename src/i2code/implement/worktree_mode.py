"""WorktreeMode: execute plan tasks using worktree + PR + CI loop."""

import os
import sys
import time
from dataclasses import dataclass, field

from i2code.claude.permissions import calculate_claude_permissions
from i2code.implement.claude_runner import ClaudeCodeCommand
from i2code.implement.command_builder import CommandBuilder, TaskCommandOpts
from i2code.implement.pr_helpers import is_pr_complete
from i2code.implement.timing import Timer, timed
from i2code.implement.console import print_message
from i2code.implement.task_execution import TaskExecution
from i2code.supervision.supervisor import NullSupervisor, RunStopped, Supervisor

REVIEW_POLL_INTERVAL_SECONDS = 30


def _exit_code(exit_request: SystemExit) -> int:
    if exit_request.code is None:
        return 0
    return exit_request.code if isinstance(exit_request.code, int) else 1


def _task_id(task):
    return f"{task.number.thread}.{task.number.task}"


def _format_duration(seconds):
    rounded = int(seconds)
    if rounded < 60:
        unit = "second" if rounded == 1 else "seconds"
        return f"{rounded} {unit}"
    minutes = rounded // 60
    unit = "minute" if minutes == 1 else "minutes"
    return f"{minutes} {unit}"


@dataclass
class LoopSteps:
    """Pipeline collaborators used during the worktree task loop."""
    claude_runner: object
    state: object
    ci_monitor: object
    build_fixer: object
    review_processor: object
    commit_recovery: object
    clock: object = None
    sleep: object = None
    supervisor: Supervisor = field(default_factory=NullSupervisor)


class WorktreeMode:
    """Execution mode that runs tasks with worktree, PR creation, and CI integration.

    Args:
        opts: ImplementOpts with execution parameters.
        git_repo: GitRepository (or FakeGitRepository) for branch/push/PR/CI operations.
        work_project: IdeaProject for the working directory (may differ from project in worktree mode).
        loop_steps: LoopSteps grouping claude_runner, state, ci_monitor, build_fixer, review_processor, and commit_recovery.
    """

    def __init__(self, opts, git_repo, work_project, loop_steps):
        self._opts = opts
        self._git_repo = git_repo
        self._work_project = work_project
        self._loop_steps = loop_steps
        self._clock = loop_steps.clock or time.monotonic
        self._sleep = loop_steps.sleep or time.sleep
        self._supervisor = loop_steps.supervisor

    def execute(self):
        """Run the worktree task loop until all tasks are complete, journaling how the run ends."""
        self._supervisor.discard_stale_requests()
        self._supervisor.record(
            "run_started", pid=os.getpid(), idea=self._work_project.name,
            branch=self._git_repo.branch, worktree=self._git_repo.working_tree_dir,
            on_failure=self._opts.on_failure, nudge_missing_tag=self._opts.nudge_missing_tag,
        )
        try:
            self._run_loop()
        except RunStopped:
            print_message("Stopped by the supervising session.")
            self._supervisor.record("run_finished", status="stopped", exit_code=0)
            return
        except SystemExit as exit_request:
            self._record_run_finished(_exit_code(exit_request))
            raise
        except KeyboardInterrupt:
            self._record_run_finished(130)
            raise
        self._record_run_finished(0)

    def _record_run_finished(self, exit_code):
        status = "completed" if exit_code == 0 else "failed"
        self._supervisor.record("run_finished", status=status, exit_code=exit_code)

    def _run_loop(self):
        with timed("commit_recovery"):
            self._loop_steps.commit_recovery.commit_if_needed()

        with timed("branch_has_been_pushed"):
            pushed = self._git_repo.branch_has_been_pushed()
        if pushed and self._git_repo.has_unpushed_commits():
            self._push_and_ensure_pr()
            self._loop_steps.ci_monitor.wait_for_workflow_completion(self._git_repo.branch, self._git_repo.head_sha)

        while True:
            self._supervisor.raise_if_stop_requested()
            t = Timer.start()
            if self._loop_steps.build_fixer.check_and_fix_ci():
                t.print("check_and_fix_ci (fix)")
                continue
            t.print("check_and_fix_ci")

            t = Timer.start()
            if self._loop_steps.review_processor.process_feedback():
                t.print("process_feedback (acted)")
                continue
            t.print("process_feedback")

            next_task = self._work_project.get_next_task()
            if next_task is None:
                self._handle_all_tasks_complete()
                return

            self._execute_task(next_task)

    def _handle_all_tasks_complete(self):
        """Print completion, then optionally poll for review feedback."""
        self._print_completion()
        if self._opts.address_review_comments:
            self._review_poll_loop()

    def _review_poll_loop(self):
        """Poll for review feedback until the PR is merged or closed."""
        print_message("Waiting for review feedback...")
        while True:
            self._supervisor.raise_if_stop_requested()
            if self._loop_steps.build_fixer.check_and_fix_ci():
                continue

            if self._loop_steps.review_processor.process_feedback():
                print_message("Waiting for review feedback...")
                continue

            pr_state = self._git_repo.gh_client.get_pr_state(self._git_repo.pr_number)
            if is_pr_complete(pr_state):
                print_message(f"PR has been {pr_state.lower()}.")
                return

            interval = _format_duration(REVIEW_POLL_INTERVAL_SECONDS)
            print_message(f"No new feedback. Checking again in {interval}...")
            self._sleep(REVIEW_POLL_INTERVAL_SECONDS)

    def _execute_task(self, next_task):
        """Execute a single task: run Claude, push, create PR, wait for CI."""
        task_description = next_task.print()
        progress = self._work_project.task_progress()
        print_message(f"Executing task {progress.current} of {progress.total}: {task_description}")
        self._supervisor.record(
            "task_started", task=_task_id(next_task), title=next_task.task.title,
            index=progress.current, total=progress.total,
        )

        start = self._clock()
        self._run_claude_and_validate(next_task, task_description)
        elapsed = self._clock() - start
        self._supervisor.record(
            "task_completed", task=_task_id(next_task), duration_s=round(elapsed, 1),
            head=self._git_repo.head_sha,
        )
        duration = _format_duration(elapsed)
        print_message(f"Task {progress.current} of {progress.total} completed successfully in {duration}.", flush=True)
        self._push_and_ensure_pr()
        self._loop_steps.ci_monitor.wait_for_workflow_completion(self._git_repo.branch, self._git_repo.head_sha)

    def _run_claude_and_validate(self, next_task, task_description):
        """Run Claude on the task until it passes validation (see TaskExecution)."""
        TaskExecution(
            self._opts, self._git_repo, self._work_project, self._loop_steps.claude_runner, self._supervisor,
        ).run(next_task, self._build_command(task_description))

    def _push_and_ensure_pr(self):
        """Push changes and create a Draft PR if one doesn't exist."""
        print_message("Pushing changes...")

        with timed("push"):
            while not self._git_repo.push():
                print("Error: Could not push commit to branch", file=sys.stderr)
                if self._opts.on_failure != "wait":
                    sys.exit(1)
                self._supervisor.block("push", "push_failed", task=None, detail="git push failed",
                                       session_id=None, permission_denials=[])
        self._supervisor.record("pushed", head=self._git_repo.head_sha)

        if self._git_repo.pr_number is None:
            with timed("ensure_pr"):
                self._git_repo.ensure_pr(
                    self._work_project.directory, self._work_project.name,
                )
            pr_url = self._git_repo.gh_client.get_pr_url(self._git_repo.pr_number)
            print_message(f"Created Draft PR #{self._git_repo.pr_number}: {pr_url}")

    def _print_completion(self):
        """Print completion message with PR URL if available."""
        print_message("All tasks completed!")
        if self._git_repo.pr_number:
            with timed("mark_pr_ready"):
                self._git_repo.gh_client.mark_pr_ready(self._git_repo.pr_number)
            print_message("PR marked ready for review")
            pr_url = self._git_repo.gh_client.get_pr_url(self._git_repo.pr_number)
            if pr_url:
                print_message(f"PR: {pr_url}")

    def _build_command(self, task_description):
        cwd = self._git_repo.working_tree_dir
        if self._opts.mock_claude:
            return ClaudeCodeCommand(
                cwd=cwd,
                mock_command=[self._opts.mock_claude, task_description],
                label="task",
            )

        extra_cli_args = None
        if self._opts.non_interactive:
            permissions = calculate_claude_permissions(cwd)
            extra_cli_args = ["--allowedTools", ",".join(permissions)]
        return CommandBuilder().build_task_command(
            self._work_project.directory,
            task_description,
            TaskCommandOpts(
                interactive=not self._opts.non_interactive,
                extra_prompt=self._opts.extra_prompt,
                extra_cli_args=extra_cli_args,
                allow_push=self._opts.allow_push,
            ),
            cwd=cwd,
        )
