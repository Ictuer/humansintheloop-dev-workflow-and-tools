"""--resume-on-api-error resumes a session that a temporary Claude API error cut off."""

import tempfile

import pytest

from i2code.implement.claude_runner import CapturedOutput, ClaudeResult, DiagnosticInfo
from i2code.implement.implement_opts import ImplementOpts

from conftest import advance_head, combined, mark_task_complete
from fake_claude_runner import FakeClaudeRunner
from fake_git_repository import FakeGitRepository
from test_worktree_mode import _make_worktree_mode, _setup_idea

SUCCESS = "<SUCCESS>task implemented: bbb</SUCCESS>"


def _api_error(session_id="s-1", message="API Error: 529 Overloaded. This is a server-side issue"):
    return ClaudeResult(returncode=1, output=CapturedOutput(""), session_id=session_id,
                        diagnostics=DiagnosticInfo(error_message=message))


def _success():
    return ClaudeResult(returncode=0, output=CapturedOutput(SUCCESS), result_text=SUCCESS, session_id="s-1")


def _mode(tmpdir, results, *, retries=3, completes_on_call):
    plan_path, idea_dir = _setup_idea(tmpdir, [(1, 1, "Set up", False)], ci_workflow=True)
    fake_repo = FakeGitRepository(working_tree_dir=tmpdir)
    runner = FakeClaudeRunner()
    effects = [lambda: None] * len(results)
    effects[completes_on_call - 1] = combined(advance_head(fake_repo, "bbb"),
                                              mark_task_complete(plan_path, 1, 1, "Set up"))
    runner.set_side_effects(effects)
    runner.set_results(results)
    slept = []
    opts = ImplementOpts(idea_directory=idea_dir, non_interactive=True, mock_claude="/mock",
                         skip_ci_wait=True, resume_on_api_error=retries)
    mode, *_ = _make_worktree_mode(plan_path, idea_dir, tmpdir, fake_repo=fake_repo, fake_runner=runner,
                                   opts=opts, sleep=slept.append)
    return mode, runner, slept


def _labels(runner):
    return [call[1].label for call in runner.calls]


@pytest.mark.unit
class TestResumeOnApiError:

    def test_api_error_then_success_resumes_the_same_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, runner, slept = _mode(tmpdir, [_api_error(), _success()], completes_on_call=2)

            mode.execute()

            assert _labels(runner) == ["task", "retry"]
            assert runner.calls[1][1].mock_command == ["/mock", "retry-s-1"]
            assert slept == [60]

    def test_waits_grow_and_are_capped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, runner, slept = _mode(tmpdir, [_api_error()] * 7 + [_success()], retries=7, completes_on_call=8)

            mode.execute()

            assert slept == [60, 120, 240, 480, 900, 900, 900]
            assert _labels(runner) == ["task"] + ["retry"] * 7

    def test_network_errors_are_api_errors_too(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            error = _api_error(message="API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)")
            mode, runner, _ = _mode(tmpdir, [error, _success()], completes_on_call=2)

            mode.execute()

            assert _labels(runner) == ["task", "retry"]

    def test_still_failing_after_retries_counts_as_a_failed_attempt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, runner, slept = _mode(tmpdir, [_api_error(), _api_error(), _success()], retries=1,
                                        completes_on_call=3)

            mode.execute()

            assert _labels(runner) == ["task", "retry", "task"]

    def test_other_failures_are_not_resumed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plain_failure = ClaudeResult(returncode=1, output=CapturedOutput("boom"), session_id="s-1")
            mode, runner, slept = _mode(tmpdir, [plain_failure, _success()], completes_on_call=2)

            mode.execute()

            assert _labels(runner) == ["task", "task"]
            assert slept == []

    def test_without_session_id_is_not_resumed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, runner, slept = _mode(tmpdir, [_api_error(session_id=None), _success()], completes_on_call=2)

            mode.execute()

            assert _labels(runner) == ["task", "task"]

    def test_default_does_not_resume(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, runner, slept = _mode(tmpdir, [_api_error(), _success()], retries=0, completes_on_call=2)

            mode.execute()

            assert _labels(runner) == ["task", "task"]
            assert slept == []
