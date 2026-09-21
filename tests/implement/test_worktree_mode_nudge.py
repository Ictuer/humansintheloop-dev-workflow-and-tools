"""--nudge-missing-tag resumes a Claude session that ended without an outcome tag."""

import tempfile

import pytest

from i2code.implement.claude_runner import CapturedOutput, ClaudeResult, SessionId
from i2code.implement.implement_opts import ImplementOpts

from conftest import advance_head, combined, mark_task_complete
from fake_claude_runner import FakeClaudeRunner
from fake_git_repository import FakeGitRepository
from test_worktree_mode import _make_worktree_mode, _setup_idea

SUCCESS = "<SUCCESS>task implemented: bbb</SUCCESS>"


def _result(text, session_id="s-1"):
    return ClaudeResult(returncode=0, output=CapturedOutput(text), result_text=text, session_id=session_id)


def _mode(tmpdir, results, *, nudges=1, completes_on_call=1):
    plan_path, idea_dir = _setup_idea(tmpdir, [(1, 1, "Set up", False)], ci_workflow=True)
    fake_repo = FakeGitRepository(working_tree_dir=tmpdir)
    fake_runner = FakeClaudeRunner()
    effects = [lambda: None] * len(results)
    effects[completes_on_call - 1] = combined(advance_head(fake_repo, "bbb"), mark_task_complete(plan_path, 1, 1, "Set up"))
    fake_runner.set_side_effects(effects)
    fake_runner.set_results(results)
    opts = ImplementOpts(idea_directory=idea_dir, non_interactive=True, mock_claude="/mock",
                         skip_ci_wait=True, nudge_missing_tag=nudges)
    mode, *_ = _make_worktree_mode(plan_path, idea_dir, tmpdir, fake_repo=fake_repo, fake_runner=fake_runner,
                                   opts=opts)
    return mode, fake_runner


@pytest.mark.unit
class TestNudgeMissingTag:

    def test_nudge_then_success_completes_the_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, runner = _mode(tmpdir, [_result("Done, waiting for CI..."), _result(SUCCESS)])

            mode.execute()

            assert len(runner.calls) == 2
            nudge = runner.calls[1][1]
            assert nudge.label == "nudge"
            assert nudge.mock_command == ["/mock", "nudge-s-1"]

    def test_nudge_can_finish_work_that_was_not_committed_yet(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, runner = _mode(tmpdir, [_result("Waiting for CI..."), _result(SUCCESS)], completes_on_call=2)

            mode.execute()

            assert [call[1].label for call in runner.calls] == ["task", "nudge"]

    def test_still_missing_after_nudges_exits_as_before(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, runner = _mode(tmpdir, [_result("no tag"), _result("still no tag")])

            with pytest.raises(SystemExit) as exit_info:
                mode.execute()

            assert exit_info.value.code == 1
            assert len(runner.calls) == 2

    def test_failure_tag_is_not_nudged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, runner = _mode(tmpdir, [_result("<FAILURE>needs SECRET_X</FAILURE>")])

            with pytest.raises(SystemExit):
                mode.execute()

            assert len(runner.calls) == 1

    def test_no_session_id_is_not_nudged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, runner = _mode(tmpdir, [_result("no tag", session_id=None)])

            with pytest.raises(SystemExit):
                mode.execute()

            assert len(runner.calls) == 1

    def test_default_does_not_nudge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mode, runner = _mode(tmpdir, [_result("no tag")], nudges=0)

            with pytest.raises(SystemExit):
                mode.execute()

            assert len(runner.calls) == 1

    def test_real_command_nudge_resumes_the_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path, idea_dir = _setup_idea(tmpdir, [(1, 1, "Set up", False)], ci_workflow=True)
            fake_repo = FakeGitRepository(working_tree_dir=tmpdir)
            fake_runner = FakeClaudeRunner()
            fake_runner.set_side_effects([
                combined(advance_head(fake_repo, "bbb"), mark_task_complete(plan_path, 1, 1, "Set up")),
                lambda: None,
            ])
            fake_runner.set_results([_result("no tag"), _result(SUCCESS)])
            opts = ImplementOpts(idea_directory=idea_dir, non_interactive=True, skip_ci_wait=True,
                                 nudge_missing_tag=1)
            mode, *_ = _make_worktree_mode(plan_path, idea_dir, tmpdir, fake_repo=fake_repo,
                                           fake_runner=fake_runner, opts=opts)

            mode.execute()

            task, nudge = fake_runner.calls[0][1], fake_runner.calls[1][1]
            assert nudge.session_id == SessionId("s-1", is_new=False)
            assert nudge.allowed_tools == task.allowed_tools


@pytest.mark.unit
class TestNudgeAgreesWithValidation:

    def test_success_in_stdout_is_not_nudged_even_if_final_text_has_no_tag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = ClaudeResult(returncode=0, output=CapturedOutput(f"...{SUCCESS}...\nDone."),
                                  result_text="Done.", session_id="s-1")
            mode, runner = _mode(tmpdir, [result])

            mode.execute()

            assert len(runner.calls) == 1
