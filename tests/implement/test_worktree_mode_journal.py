"""WorktreeMode records the run, task, push and CI lifecycle in the run journal."""

import os
import tempfile

import pytest

from i2code.implement.claude_runner import CapturedOutput, ClaudeResult
from i2code.implement.implement_opts import ImplementOpts

from conftest import advance_head, combined, mark_task_complete
from fake_claude_runner import FakeClaudeRunner
from fake_git_repository import FakeGitRepository
from fake_github_client import FakeGitHubClient
from fake_supervisor import RecordingSupervisor
from test_worktree_mode import _make_worktree_mode, _setup_idea


def _two_task_run(tmpdir, ci_results=((True, None), (True, None))):
    plan_path, idea_dir = _setup_idea(tmpdir, [(1, 1, "First", False), (1, 2, "Second", False)], ci_workflow=True)
    fake_gh = FakeGitHubClient()
    fake_repo = FakeGitRepository(working_tree_dir=tmpdir, gh_client=fake_gh)
    fake_repo.branch = "idea/test-feature"
    for sha, result in zip(("bbb", "ccc"), ci_results):
        fake_gh.set_workflow_completion_result("idea/test-feature", sha, result)
    fake_runner = FakeClaudeRunner()
    fake_runner.set_side_effects([
        combined(advance_head(fake_repo, "bbb"), mark_task_complete(plan_path, 1, 1, "First")),
        combined(advance_head(fake_repo, "ccc"), mark_task_complete(plan_path, 1, 2, "Second")),
    ])
    supervisor = RecordingSupervisor()
    mode, *_ = _make_worktree_mode(
        plan_path, idea_dir, tmpdir, fake_repo=fake_repo, fake_runner=fake_runner, fake_gh=fake_gh,
        opts=ImplementOpts(idea_directory=idea_dir), supervisor=supervisor,
    )
    return mode, supervisor


@pytest.mark.unit
class TestWorktreeModeJournal:

    def test_two_task_run_records_lifecycle_in_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, supervisor = _two_task_run(tmpdir)

            mode.execute()

            assert supervisor.names() == [
                "run_started",
                "task_started", "task_completed", "pushed", "ci_waiting", "ci_finished",
                "task_started", "task_completed", "pushed", "ci_waiting", "ci_finished",
                "run_finished",
            ]

    def test_event_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, supervisor = _two_task_run(tmpdir, ci_results=((False, {"name": "CI"}), (True, None)))

            mode.execute()

            started = supervisor.first("run_started")
            assert started["pid"] == os.getpid()
            assert started["idea"] == "test-feature"
            assert started["branch"] == "idea/test-feature"
            assert started["worktree"] == tmpdir
            assert supervisor.first("task_started") == {
                "event": "task_started", "task": "1.1", "title": "First", "index": 1, "total": 2,
            }
            completed = supervisor.first("task_completed")
            assert completed["task"] == "1.1" and completed["head"] == "bbb"
            assert isinstance(completed["duration_s"], (int, float))
            assert supervisor.first("pushed") == {"event": "pushed", "head": "bbb"}
            assert supervisor.first("ci_waiting") == {"event": "ci_waiting", "head": "bbb"}
            assert supervisor.first("ci_finished") == {
                "event": "ci_finished", "head": "bbb", "success": False, "failing_workflow": "CI",
            }
            assert supervisor.events[-1] == {"event": "run_finished", "status": "completed", "exit_code": 0}

    def test_skipped_ci_wait_records_no_ci_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path, idea_dir = _setup_idea(tmpdir, [(1, 1, "Only", False)], ci_workflow=True)
            fake_repo = FakeGitRepository(working_tree_dir=tmpdir)
            fake_runner = FakeClaudeRunner()
            fake_runner.set_side_effect(
                combined(advance_head(fake_repo, "bbb"), mark_task_complete(plan_path, 1, 1, "Only")))
            supervisor = RecordingSupervisor()
            mode, *_ = _make_worktree_mode(
                plan_path, idea_dir, tmpdir, fake_repo=fake_repo, fake_runner=fake_runner,
                opts=ImplementOpts(idea_directory=idea_dir, skip_ci_wait=True), supervisor=supervisor,
            )

            mode.execute()

            assert "ci_waiting" not in supervisor.names()

    def test_system_exit_records_failed_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path, idea_dir = _setup_idea(tmpdir, [(1, 1, "Only", False)], ci_workflow=True)
            fake_repo = FakeGitRepository(working_tree_dir=tmpdir)
            fake_runner = FakeClaudeRunner()
            fake_runner.set_side_effect(
                combined(advance_head(fake_repo, "bbb"), mark_task_complete(plan_path, 1, 1, "Only")))
            fake_runner.set_result(ClaudeResult(returncode=0, output=CapturedOutput("no tag")))
            supervisor = RecordingSupervisor()
            mode, *_ = _make_worktree_mode(
                plan_path, idea_dir, tmpdir, fake_repo=fake_repo, fake_runner=fake_runner,
                opts=ImplementOpts(idea_directory=idea_dir, non_interactive=True, mock_claude="/mock",
                                   skip_ci_wait=True),
                supervisor=supervisor,
            )

            with pytest.raises(SystemExit):
                mode.execute()

            assert supervisor.events[-1] == {"event": "run_finished", "status": "failed", "exit_code": 1}
