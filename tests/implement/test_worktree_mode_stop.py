"""i2code ctl stop ends a run gracefully at the next checkpoint."""

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

SUCCESS = "<SUCCESS>task implemented: x</SUCCESS>"


def _two_task_mode(tmpdir, supervisor, **opt_overrides):
    plan_path, idea_dir = _setup_idea(tmpdir, [(1, 1, "First", False), (1, 2, "Second", False)], ci_workflow=True)
    fake_repo = FakeGitRepository(working_tree_dir=tmpdir)
    runner = FakeClaudeRunner()
    runner.set_side_effects([
        combined(advance_head(fake_repo, "bbb"), mark_task_complete(plan_path, 1, 1, "First")),
        combined(advance_head(fake_repo, "ccc"), mark_task_complete(plan_path, 1, 2, "Second")),
    ])
    runner.set_results([ClaudeResult(returncode=0, output=CapturedOutput(SUCCESS))] * 2)
    opts = ImplementOpts(idea_directory=idea_dir, skip_ci_wait=True, **opt_overrides)
    mode, *_ = _make_worktree_mode(plan_path, idea_dir, tmpdir, fake_repo=fake_repo, fake_runner=runner,
                                   opts=opts, supervisor=supervisor)
    return mode, runner


@pytest.mark.unit
class TestStopAtCheckpoints:

    def test_stop_before_the_next_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            supervisor = RecordingSupervisor(stop_at_checkpoint=2)
            mode, runner = _two_task_mode(tmpdir, supervisor)

            mode.execute()

            assert len(runner.calls) == 1
            assert supervisor.names()[-2:] == ["stop_requested", "run_finished"]
            assert supervisor.events[-1] == {"event": "run_finished", "status": "stopped", "exit_code": 0}

    def test_no_stop_runs_every_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            supervisor = RecordingSupervisor()
            mode, runner = _two_task_mode(tmpdir, supervisor)

            mode.execute()

            assert len(runner.calls) == 2
            assert supervisor.checkpoints >= 3

    def test_stop_in_review_poll_loop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path, idea_dir = _setup_idea(tmpdir, [(1, 1, "Done", True)], ci_workflow=True)
            fake_gh = FakeGitHubClient()
            fake_repo = FakeGitRepository(working_tree_dir=tmpdir, gh_client=fake_gh)
            fake_repo.pr_number = 7
            supervisor = RecordingSupervisor(stop_at_checkpoint=2)
            slept = []
            opts = ImplementOpts(idea_directory=idea_dir, skip_ci_wait=True, address_review_comments=True)
            mode, *_ = _make_worktree_mode(plan_path, idea_dir, tmpdir, fake_repo=fake_repo, fake_gh=fake_gh,
                                           opts=opts, supervisor=supervisor, sleep=slept.append)

            mode.execute()

            assert supervisor.events[-1]["status"] == "stopped"

    def test_stale_requests_are_discarded_at_start(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            supervisor = RecordingSupervisor()
            mode, _ = _two_task_mode(tmpdir, supervisor)

            mode.execute()

            assert supervisor.stale_requests_discarded is True
