"""RunPaths resolves the run state directory under the git common dir."""

import subprocess

import pytest
from git import Repo

from i2code.supervision.run_paths import RunPaths


def _repo_with_commit(path):
    repo = Repo.init(path)
    (path / "README.md").write_text("x")
    repo.index.add(["README.md"])
    repo.index.commit("init")
    return repo


@pytest.mark.unit
class TestRunPaths:

    def test_main_checkout_uses_its_git_dir(self, tmp_path):
        repo = _repo_with_commit(tmp_path / "main")

        paths = RunPaths.for_idea(repo, "demo")

        assert paths.directory == (tmp_path / "main" / ".git" / "i2code" / "implement" / "demo").resolve()
        assert paths.events_file == paths.directory / "events.jsonl"
        assert paths.status_file == paths.directory / "status.json"
        assert paths.inbox_dir == paths.directory / "inbox"

    def test_linked_worktree_shares_the_main_git_dir(self, tmp_path):
        main = _repo_with_commit(tmp_path / "main")
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "idea/demo", str(tmp_path / "wt")],
            cwd=tmp_path / "main", check=True,
        )

        from_worktree = RunPaths.for_idea(Repo(tmp_path / "wt"), "demo")

        assert from_worktree == RunPaths.for_idea(main, "demo")
