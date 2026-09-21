"""Wiring tests for assemble_implement (the implement composition root)."""

import pytest
from git import Repo

from i2code.implement.claude_runner import ClaudeCodeCommand
from i2code.implement.command_assembler import assemble_implement
from i2code.implement.implement_opts import ImplementOpts


@pytest.fixture
def idea_dir(tmp_path):
    Repo.init(tmp_path)
    directory = tmp_path / "docs" / "ideas" / "active" / "demo"
    directory.mkdir(parents=True)
    return directory


def _claude_argv(command):
    runner = command.mode_factory._claude_runner
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
