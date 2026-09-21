"""Wiring tests for assemble_implement (the implement composition root)."""

import json

import pytest
from git import Repo

from i2code.implement.claude_runner import ClaudeCodeCommand
from i2code.implement.command_assembler import assemble_implement
from i2code.implement.implement_opts import ImplementOpts
from i2code.supervision.run_paths import RunPaths
from i2code.supervision.run_supervisor import RunSupervisor


@pytest.fixture
def idea_dir(tmp_path):
    Repo.init(tmp_path)
    directory = tmp_path / "docs" / "ideas" / "active" / "demo"
    directory.mkdir(parents=True)
    return directory


def _claude_argv(command):
    runner = command.mode_factory._claude_runner.inner
    return runner._build_argv(ClaudeCodeCommand(prompt="p", cwd="/c"), False)


@pytest.mark.unit
class TestAssembleImplementClaudeArgs:

    def test_claude_args_reach_every_invocation(self, idea_dir):
        command = assemble_implement(ImplementOpts(
            idea_directory=str(idea_dir), non_interactive=True, claude_args="--effort high",
        ))

        assert _claude_argv(command)[-4:] == ["--effort", "high", "-p", "p"]

    def test_without_claude_args_argv_is_unchanged(self, idea_dir):
        command = assemble_implement(ImplementOpts(idea_directory=str(idea_dir), non_interactive=True))

        assert _claude_argv(command) == ["claude", "--verbose", "--output-format=stream-json", "-p", "p"]


@pytest.mark.unit
class TestAssembleImplementJournal:

    def test_claude_invocations_are_journaled_under_the_git_dir(self, idea_dir, tmp_path):
        command = assemble_implement(ImplementOpts(idea_directory=str(idea_dir), non_interactive=True))
        runner = command.mode_factory._claude_runner

        runner.execute(ClaudeCodeCommand(cwd=str(tmp_path), mock_command=["true"], label="task"))

        paths = RunPaths.for_idea(Repo(tmp_path), "demo")
        events = [json.loads(line) for line in paths.events_file.read_text().splitlines()]
        assert [(e["event"], e["label"]) for e in events] == [("claude_started", "task"), ("claude_finished", "task")]

    def test_worktree_loop_and_ci_monitor_share_the_runner_journal(self, idea_dir, tmp_path):
        from fake_git_repository import FakeGitRepository
        from fake_workflow_state import FakeWorkflowState

        command = assemble_implement(ImplementOpts(idea_directory=str(idea_dir), non_interactive=True))
        journal = command.mode_factory._claude_runner._journal

        mode = command.mode_factory.make_worktree_mode(
            git_repo=FakeGitRepository(working_tree_dir=str(tmp_path)),
            state=FakeWorkflowState(),
            work_project=command.project,
        )

        supervisor = mode._loop_steps.supervisor
        assert isinstance(supervisor, RunSupervisor)
        assert supervisor._journal is journal
        assert supervisor._inbox.paths == RunPaths.for_idea(Repo(tmp_path), "demo")
        assert mode._loop_steps.ci_monitor._supervisor is supervisor
        assert mode._loop_steps.build_fixer._supervisor is supervisor

    def test_runner_takes_notes_from_the_idea_inbox(self, idea_dir, tmp_path):
        command = assemble_implement(ImplementOpts(idea_directory=str(idea_dir), non_interactive=True))

        notes = command.mode_factory._claude_runner._notes

        assert notes.paths == RunPaths.for_idea(Repo(tmp_path), "demo")


@pytest.mark.unit
class TestAssembleImplementTrunkIsNotSupervised:

    def test_trunk_mode_runner_writes_no_journal(self, idea_dir, tmp_path):
        command = assemble_implement(ImplementOpts(idea_directory=str(idea_dir), non_interactive=True, trunk=True))

        command.mode_factory._claude_runner.execute(
            ClaudeCodeCommand(cwd=str(tmp_path), mock_command=["true"], label="task"))

        assert not RunPaths.for_idea(Repo(tmp_path), "demo").events_file.exists()
