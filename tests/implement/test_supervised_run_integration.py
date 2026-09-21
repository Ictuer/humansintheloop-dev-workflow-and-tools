"""End to end: a mock-Claude implement run is blocked, inspected, resumed and stopped through i2code ctl.

``i2code implement`` runs as its own process (it installs signal handlers, so it must own its main thread) with
real git (a local bare repository as origin), a real worktree, the real run journal and inbox, and a mock Claude
script that prints stream-json; only the GitHub client is replaced, inside that process.
``i2code ctl`` runs as separate processes, as a supervising session would run it.
"""

import json
import os
import stat
import subprocess
import sys
import textwrap
import time

import pytest

IDEA = "demo"
PLAN = textwrap.dedent("""\
    # Demo Plan

    ## Instructions for Coding Agent

    - Use TDD

    ---

    ## Steel Thread 1: Demo

    - [ ] **Task 1.1: Needs approval**
      - TaskType: OUTCOME
      - Entrypoint: `true`
      - Observable: done
      - Evidence: `true`
      - Steps:
        - [ ] Step

    - [ ] **Task 1.2: Straightforward**
      - TaskType: OUTCOME
      - Entrypoint: `true`
      - Observable: done
      - Evidence: `true`
      - Steps:
        - [ ] Step
    """)

MOCK_CLAUDE = textwrap.dedent(f"""\
    #!{sys.executable}
    # Mock Claude: Task 1.1 first reports a blocker; resume or any other task completes the next task.
    import json, pathlib, re, subprocess, sys

    def emit(text):
        print(json.dumps({{"type": "system", "subtype": "init", "session_id": "mock-session"}}))
        print(json.dumps({{"type": "result", "result": text, "session_id": "mock-session",
                          "num_turns": 1, "total_cost_usd": 0.0, "duration_ms": 10}}))

    arg = sys.argv[1]
    if "Task 1.1" in arg:
        emit("<FAILURE>needs approval from a human</FAILURE>")
        sys.exit(0)

    plan = pathlib.Path("{IDEA}/{IDEA}-plan.md")
    plan.write_text(re.sub(r"- \\[ \\] \\*\\*Task", "- [x] **Task", plan.read_text(), count=1))
    ci = pathlib.Path(".github/workflows/ci.yml")
    ci.parent.mkdir(parents=True, exist_ok=True)
    ci.write_text("name: CI\\n")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "mock: " + arg], check=True)
    emit("<SUCCESS>task implemented: mock</SUCCESS>")
    """)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _main_repo(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "test@example.com")
    _git(main, "config", "user.name", "Test")
    idea = main / IDEA
    idea.mkdir()
    (idea / f"{IDEA}-idea.md").write_text("# Demo idea\n")
    (idea / f"{IDEA}-spec.md").write_text("# Demo spec\n")
    (idea / f"{IDEA}-plan.md").write_text(PLAN)
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "Add demo idea")
    _git(tmp_path, "init", "-q", "--bare", "origin.git")
    _git(main, "remote", "add", "origin", str(tmp_path / "origin.git"))
    _git(main, "push", "-q", "-u", "origin", "main")
    return main


def _mock_claude(tmp_path):
    script = tmp_path / "mock-claude.py"
    script.write_text(MOCK_CLAUDE)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _ctl(main, *args):
    return subprocess.run(
        [sys.executable, "-c", "from i2code.cli import main; main()", "ctl", *args],
        cwd=main, capture_output=True, text=True, timeout=60,
    )


def _ctl_status(main):
    result = _ctl(main, "status", IDEA, "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _wait_for_state(main, state, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _ctl_status(main)
        if status["state"] == state:
            return status
        time.sleep(0.5)
    raise AssertionError(f"run never reached {state}: {_ctl_status(main)}")


def _events(main):
    result = _ctl(main, "events", IDEA, "--limit", "200")
    return [json.loads(line) for line in result.stdout.splitlines()]


IMPLEMENT_WITH_FAKE_GITHUB = textwrap.dedent(f"""\
    import sys
    sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
    from fake_github_client import FakeGitHubClient
    import i2code.implement.command_assembler as assembler
    assembler.GitHubClient = lambda cwd: FakeGitHubClient()
    from i2code.cli import main
    main()
    """)


class _ImplementProcess:
    def __init__(self, main, mock, log_path):
        self._log = open(log_path, "w")
        self._process = subprocess.Popen(
            [sys.executable, "-c", IMPLEMENT_WITH_FAKE_GITHUB, "implement", str(main / IDEA),
             "--non-interactive", "--mock-claude", str(mock), "--skip-ci-wait", "--on-failure", "wait"],
            cwd=main, stdout=self._log, stderr=subprocess.STDOUT,
        )

    def wait(self, timeout=60):
        try:
            return self._process.wait(timeout)
        finally:
            self._log.close()

    def kill(self):
        if self._process.poll() is None:
            self._process.kill()
            self._process.wait()
        self._log.close()


@pytest.fixture
def supervised_run(tmp_path):
    main = _main_repo(tmp_path)
    run = _ImplementProcess(main, _mock_claude(tmp_path), tmp_path / "implement.log")
    yield main, run
    run.kill()


@pytest.mark.integration
class TestSupervisedRun:

    def test_blocked_run_is_resumed_through_ctl_and_completes(self, supervised_run):
        main, run = supervised_run

        status = _wait_for_state(main, "blocked")
        assert status["blocked"]["reason"] == "failure_tag"
        assert status["blocked"]["detail"] == "needs approval from a human"
        assert status["blocked"]["session_id"] == "mock-session"
        assert "i2code ctl resume demo" in _ctl(main, "status", IDEA).stdout

        refused = _ctl(main, "stop", "no-such-idea")
        assert refused.returncode == 1
        resumed = _ctl(main, "resume", IDEA, "--note", "approved")
        assert resumed.returncode == 0, resumed.stderr

        assert run.wait() == 0
        names = [e["event"] for e in _events(main)]
        assert names.count("task_completed") == 2
        assert names.index("blocked") < names.index("resumed") < names.index("task_completed")
        claude_labels = [e["label"] for e in _events(main) if e["event"] == "claude_started"]
        assert claude_labels == ["task", "resume", "task"]
        assert _ctl_status(main)["state"] == "completed"

    def test_blocked_run_is_stopped_through_ctl(self, supervised_run):
        main, run = supervised_run

        _wait_for_state(main, "blocked")
        stopped = _ctl(main, "stop", IDEA)
        assert stopped.returncode == 0, stopped.stderr

        assert run.wait() == 0
        final = _ctl_status(main)
        assert final["state"] == "stopped"
        assert final["exit_code"] == 0
        assert os.path.exists(final["events_file"])
