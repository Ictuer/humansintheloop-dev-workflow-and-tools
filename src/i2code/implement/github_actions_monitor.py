"""GithubActionsMonitor: waits for CI completion and reports results."""

from i2code.implement.console import print_message
from i2code.supervision.supervisor import NullSupervisor, Supervisor


class GithubActionsMonitor:
    """Monitors GitHub Actions CI status for a branch.

    Args:
        gh_client: GitHubClient (or FakeGitHubClient) for CI operations.
        skip_ci_wait: When True, skip waiting for CI entirely.
        ci_timeout: Timeout in seconds for CI completion.
        supervisor: Records ci_waiting and ci_finished in the run journal.
    """

    def __init__(self, gh_client, skip_ci_wait, ci_timeout, supervisor: Supervisor = NullSupervisor()):
        self._gh_client = gh_client
        self._skip_ci_wait = skip_ci_wait
        self._ci_timeout = ci_timeout
        self._supervisor = supervisor

    def wait_for_workflow_completion(self, branch, head_sha):
        """Wait for CI completion if configured."""
        if not self._skip_ci_wait:
            print_message("Waiting for CI to complete...")
            self._supervisor.record("ci_waiting", head=head_sha)
            ci_success, failing_run = self._gh_client.wait_for_workflow_completion(
                branch, head_sha, timeout_seconds=self._ci_timeout,
            )
            failing_workflow = failing_run.get("name", "unknown") if failing_run else None
            self._supervisor.record("ci_finished", head=head_sha, success=ci_success, failing_workflow=failing_workflow)

            if not ci_success and failing_run:
                workflow_name = failing_run.get("name", "unknown")
                print_message(f"CI failed: {workflow_name}. Will fix on next iteration.")
            elif ci_success:
                print_message("CI passed!")
