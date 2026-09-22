"""Options dataclass for the implement command."""

import shlex
from dataclasses import dataclass, fields

import click

CLAUDE_FLAGS_OWNED_BY_I2CODE = (
    "-p", "--print", "--output-format", "-r", "--resume", "-c", "--continue", "--fork-session", "--session-id",
    "--allowedTools", "--allowed-tools",
)


@dataclass
class ImplementOpts:
    """All options for the implement command."""

    idea_directory: str
    cleanup: bool = False
    mock_claude: str | None = None
    setup_only: bool = False
    non_interactive: bool = False
    extra_prompt: str | None = None
    skip_ci_wait: bool = False
    ci_fix_retries: int = 3
    ci_timeout: int = 600
    isolate: bool = False
    isolation_type: str | None = None
    isolated: bool = False
    shell: bool = False
    trunk: bool = False
    dry_run: bool = False
    ignore_uncommitted_idea_changes: bool = False
    address_review_comments: bool = False
    skip_scaffolding: bool = False
    debug_claude: bool = False
    claude_args: str | None = None
    allow_push: bool = False
    nudge_missing_tag: int = 0
    on_failure: str = "exit"
    resume_on_api_error: int = 0

    _INNER_FORWARDED = {
        "cleanup",
        "setup_only",
        "non_interactive",
        "skip_ci_wait",
        "debug_claude",
        "address_review_comments",
        "mock_claude",
        "extra_prompt",
        "ci_fix_retries",
        "ci_timeout",
        "claude_args",
        "allow_push",
        "nudge_missing_tag",
        "resume_on_api_error",
    }

    _INNER_IGNORED = {
        "on_failure",
        "idea_directory",
        "isolate",
        "isolation_type",
        "isolated",
        "shell",
        "trunk",
        "dry_run",
        "ignore_uncommitted_idea_changes",
        "skip_scaffolding",
    }

    _TRUNK_INCOMPATIBLE = [
        ("cleanup", "--cleanup"),
        ("setup_only", "--setup-only"),
        ("isolate", "--isolate"),
        ("isolated", "--isolated"),
        ("skip_ci_wait", "--skip-ci-wait"),
        ("address_review_comments", "--address-review-comments"),
    ]

    def __post_init__(self):
        self._reject_owned_claude_flags()

    def claude_global_args(self):
        """Return --claude-args split like a shell, for every real Claude invocation."""
        return shlex.split(self.claude_args) if self.claude_args else []

    def _reject_owned_claude_flags(self):
        for token in self.claude_global_args():
            flag = token.split("=", 1)[0]
            if flag in CLAUDE_FLAGS_OWNED_BY_I2CODE:
                raise click.UsageError(f"--claude-args cannot contain {flag} (i2code sets it)")

    def validate_trunk_options(self):
        """Raise click.UsageError if --trunk is combined with incompatible options."""
        if not self.trunk:
            return
        incompatible = [flag for attr, flag in self._TRUNK_INCOMPATIBLE
                        if getattr(self, attr)]
        if self.ci_fix_retries != 3:
            incompatible.append("--ci-fix-retries")
        if self.ci_timeout != 600:
            incompatible.append("--ci-timeout")
        if self.nudge_missing_tag != 0:
            incompatible.append("--nudge-missing-tag")
        if self.on_failure != "exit":
            incompatible.append("--on-failure")
        if self.resume_on_api_error != 0:
            incompatible.append("--resume-on-api-error")
        if incompatible:
            raise click.UsageError(
                f"--trunk cannot be combined with: {', '.join(incompatible)}"
            )

    def inner_cli_flags(self):
        """Return CLI flags to pass to the inner i2code implement command."""
        result = []
        for f in fields(self):
            if f.name not in self._INNER_FORWARDED:
                continue
            value = getattr(self, f.name)
            if value == f.default:
                continue
            flag = "--" + f.name.replace("_", "-")
            if f.type is bool:
                result.append(flag)
            else:
                result.extend([flag, str(value)])
        return result
