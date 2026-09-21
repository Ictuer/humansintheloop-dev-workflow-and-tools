"""Tests for the implement --extra-prompt-file option."""

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
class TestExtraPromptFile:

    def test_file_content_becomes_extra_prompt(self, tmp_path):
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("Chạy liên tục.\nSecond line.\n", encoding="utf-8")

        result, received_opts = _invoke(["--extra-prompt-file", str(prompt_file)])

        assert result.exit_code == 0, result.output
        assert received_opts[0].extra_prompt == "Chạy liên tục.\nSecond line.\n"

    def test_both_extra_prompt_options_is_usage_error(self, tmp_path):
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("from file", encoding="utf-8")

        result, received_opts = _invoke(
            ["--extra-prompt", "inline", "--extra-prompt-file", str(prompt_file)],
        )

        assert result.exit_code == 2
        assert "--extra-prompt and --extra-prompt-file cannot be combined" in result.output
        assert received_opts == []

    def test_missing_file_is_usage_error(self, tmp_path):
        result, received_opts = _invoke(
            ["--extra-prompt-file", str(tmp_path / "missing.md")],
        )

        assert result.exit_code == 2
        assert "does not exist" in result.output
        assert received_opts == []

    def test_without_option_extra_prompt_is_unchanged(self):
        result, received_opts = _invoke(["--extra-prompt", "inline"])

        assert result.exit_code == 0, result.output
        assert received_opts[0].extra_prompt == "inline"
