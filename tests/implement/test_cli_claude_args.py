"""Tests for the implement --claude-args option."""

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from i2code.implement.cli import implement_cmd


def _invoke(args):
    received_opts = []

    def fake_factory(opts):
        received_opts.append(opts)
        return MagicMock()

    result = CliRunner().invoke(
        implement_cmd, ["/tmp/fake-idea", *args],
        obj={"command_factory": fake_factory},
    )
    return result, received_opts


@pytest.mark.unit
class TestClaudeArgsOption:

    def test_value_reaches_opts(self):
        result, received_opts = _invoke(["--claude-args", "--effort high"])

        assert result.exit_code == 0, result.output
        assert received_opts[0].claude_global_args() == ["--effort", "high"]

    def test_owned_flag_is_usage_error(self):
        result, received_opts = _invoke(["--claude-args", "--resume abc"])

        assert result.exit_code == 2
        assert "--claude-args cannot contain --resume" in result.output
        assert received_opts == []
