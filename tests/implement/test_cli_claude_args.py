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


@pytest.mark.unit
class TestNudgeMissingTagCli:

    def test_value_reaches_opts(self):
        result, received_opts = _invoke(["--nudge-missing-tag", "2"])

        assert result.exit_code == 0, result.output
        assert received_opts[0].nudge_missing_tag == 2

    def test_negative_is_usage_error(self):
        result, received_opts = _invoke(["--nudge-missing-tag", "-1"])

        assert result.exit_code == 2
        assert received_opts == []


@pytest.mark.unit
class TestOnFailureCli:

    def test_wait_reaches_opts(self):
        result, received_opts = _invoke(["--on-failure", "wait"])

        assert result.exit_code == 0, result.output
        assert received_opts[0].on_failure == "wait"

    def test_default_is_exit(self):
        _, received_opts = _invoke([])

        assert received_opts[0].on_failure == "exit"

    def test_unknown_value_is_usage_error(self):
        result, received_opts = _invoke(["--on-failure", "ignore"])

        assert result.exit_code == 2
        assert received_opts == []


@pytest.mark.unit
class TestResumeOnApiErrorCli:

    def test_value_reaches_opts(self):
        result, received_opts = _invoke(["--resume-on-api-error", "5"])

        assert result.exit_code == 0, result.output
        assert received_opts[0].resume_on_api_error == 5
