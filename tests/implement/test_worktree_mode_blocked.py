"""--on-failure=wait blocks a failed task until the supervisor resumes or stops the run."""

import tempfile

import pytest

from i2code.implement.claude_runner import CapturedOutput, ClaudeResult, SessionId
from i2code.implement.implement_opts import ImplementOpts
from i2code.supervision.supervisor import ResumeRequest, RunStopped

from conftest import advance_head, combined, mark_task_complete
from fake_claude_runner import FakeClaudeRunner
from fake_git_repository import FakeGitRepository
from fake_supervisor import RecordingSupervisor
from test_worktree_mode import _make_worktree_mode, _setup_idea

SUCCESS = "<SUCCESS>task implemented: bbb</SUCCESS>"


def _result(text, session_id="s-1", returncode=0, denials=None):
    result = ClaudeResult(returncode=returncode, output=CapturedOutput(text), result_text=text, session_id=session_id)
    if denials:
        result.diagnostics.permission_denials = denials
    return result


class _Run:
    def __init__(self, tmpdir, results, block_responses, *, completes_on_call=None, mock=True, on_failure="wait",
                 **opt_overrides):
        plan_path, idea_dir = _setup_idea(tmpdir, [(1, 1, "Set up", False)], ci_workflow=True)
        self.repo = FakeGitRepository(working_tree_dir=tmpdir)
        self.runner = FakeClaudeRunner()
        effects = [lambda: None] * len(results)
        if completes_on_call:
            effects[completes_on_call - 1] = combined(
                advance_head(self.repo, "bbb"), mark_task_complete(plan_path, 1, 1, "Set up"))
        self.runner.set_side_effects(effects)
        self.runner.set_results(results)
        self.supervisor = RecordingSupervisor(block_responses)
        opts = ImplementOpts(idea_directory=idea_dir, non_interactive=True, skip_ci_wait=True, on_failure=on_failure,
                             **({"mock_claude": "/mock"} if mock else {}), **opt_overrides)
        self.mode, *_ = _make_worktree_mode(plan_path, idea_dir, tmpdir, fake_repo=self.repo,
                                            fake_runner=self.runner, opts=opts, supervisor=self.supervisor)

    def commands(self):
        return [call[1] for call in self.runner.calls]


@pytest.mark.unit
class TestBlockOnTaskFailure:

    def test_failure_tag_blocks_then_resume_continues_the_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result("<FAILURE>needs lefthook.yml copied</FAILURE>"), _result(SUCCESS)],
                       [ResumeRequest(note="copied it", fresh=False)], completes_on_call=2)

            run.mode.execute()

            blocked = run.supervisor.first("blocked")
            assert blocked["kind"] == "task" and blocked["reason"] == "failure_tag"
            assert blocked["task"] == "1.1"
            assert blocked["detail"] == "needs lefthook.yml copied"
            assert blocked["session_id"] == "s-1"
            assert [c.label for c in run.commands()] == ["task", "resume"]
            assert run.commands()[1].mock_command == ["/mock", "resume-s-1"]
            assert run.supervisor.events[-1]["status"] == "completed"

    def test_resume_can_use_work_committed_before_the_block(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result("<FAILURE>marked but CI red</FAILURE>"), _result(SUCCESS)],
                       [ResumeRequest()], completes_on_call=1)

            run.mode.execute()

            assert run.supervisor.names().count("blocked") == 1

    def test_missing_tag_blocks_after_nudges(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result("waiting"), _result("still waiting"), _result(SUCCESS)],
                       [ResumeRequest()], completes_on_call=1, nudge_missing_tag=1)

            run.mode.execute()

            assert run.supervisor.first("blocked")["reason"] == "missing_tag"
            assert [c.label for c in run.commands()] == ["task", "nudge", "resume"]

    def test_attempts_exhausted_blocks_after_three_attempts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result("x", returncode=1)] * 3 + [_result(SUCCESS)],
                       [ResumeRequest()], completes_on_call=4)

            run.mode.execute()

            assert run.supervisor.first("blocked")["reason"] == "attempts_exhausted"
            assert [c.label for c in run.commands()] == ["task", "task", "task", "resume"]

    def test_fresh_resume_starts_a_new_session_with_the_note(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result("<FAILURE>stuck</FAILURE>"), _result(SUCCESS)],
                       [ResumeRequest(note="try the other approach", fresh=True)], completes_on_call=2, mock=False)

            run.mode.execute()

            first, second = run.commands()
            assert second.label == "task"
            assert second.session_id is None
            assert second.prompt == f"{first.prompt}\n\nMessage from the supervising session:\ntry the other approach"

    def test_continue_resume_of_real_command_resumes_the_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result("<FAILURE>stuck</FAILURE>"), _result(SUCCESS)],
                       [ResumeRequest(note="go on")], completes_on_call=2, mock=False)

            run.mode.execute()

            resume = run.commands()[1]
            assert resume.session_id == SessionId("s-1", is_new=False)
            assert "go on" in resume.prompt

    def test_without_session_id_resume_starts_fresh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result("<FAILURE>stuck</FAILURE>", session_id=None), _result(SUCCESS)],
                       [ResumeRequest(note="n")], completes_on_call=2, mock=False)

            run.mode.execute()

            assert run.commands()[1].label == "task"
            assert run.supervisor.first("blocked")["session_id"] is None

    def test_second_failure_blocks_again(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result("<FAILURE>one</FAILURE>"), _result("<FAILURE>two</FAILURE>"), _result(SUCCESS)],
                       [ResumeRequest(), ResumeRequest()], completes_on_call=3)

            run.mode.execute()

            assert [b["detail"] for b in run.supervisor.all("blocked")] == ["one", "two"]

    def test_permission_denials_are_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            denial = {"tool_name": "Write", "tool_input": {"file_path": "/wt/lefthook.yml"}}
            run = _Run(tmpdir, [_result("<FAILURE>denied</FAILURE>", denials=[denial]), _result(SUCCESS)],
                       [ResumeRequest()], completes_on_call=2)

            run.mode.execute()

            assert run.supervisor.first("blocked")["permission_denials"] == ["Write: /wt/lefthook.yml"]

    def test_stop_while_blocked_finishes_the_run_as_stopped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result("<FAILURE>stuck</FAILURE>")], [RunStopped()])

            run.mode.execute()

            assert run.supervisor.events[-1] == {"event": "run_finished", "status": "stopped", "exit_code": 0}

    def test_run_started_records_supervision_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result(SUCCESS)], [], completes_on_call=1, nudge_missing_tag=2)

            run.mode.execute()

            started = run.supervisor.first("run_started")
            assert started["on_failure"] == "wait" and started["nudge_missing_tag"] == 2


@pytest.mark.unit
class TestOnFailureExitIsUnchanged:

    def test_failure_tag_exits_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result("<FAILURE>stuck</FAILURE>")], [], on_failure="exit")

            with pytest.raises(SystemExit) as exit_info:
                run.mode.execute()

            assert exit_info.value.code == 1
            assert "blocked" not in run.supervisor.names()


@pytest.mark.unit
class TestBlockOnPushFailure:

    def test_push_failure_blocks_then_resume_pushes_again(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result(SUCCESS)], [ResumeRequest()], completes_on_call=1)
            outcomes = iter([False, True])
            run.repo.push = lambda: (run.repo.calls.append(("push",)) or next(outcomes))

            run.mode.execute()

            blocked = run.supervisor.first("blocked")
            assert blocked["kind"] == "push" and blocked["reason"] == "push_failed"
            assert run.repo.calls.count(("push",)) == 2
            assert run.supervisor.events[-1]["status"] == "completed"

    def test_push_failure_with_exit_still_exits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run = _Run(tmpdir, [_result(SUCCESS)], [], completes_on_call=1, on_failure="exit")
            run.repo.push = lambda: False

            with pytest.raises(SystemExit):
                run.mode.execute()
