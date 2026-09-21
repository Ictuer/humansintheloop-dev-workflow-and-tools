"""RunPaths: where a supervised implement run keeps its journal, status and inbox."""

from dataclasses import dataclass
from pathlib import Path

from git import Repo


@dataclass(frozen=True)
class RunPaths:
    """Run state directory ``<git common dir>/i2code/implement/<idea>/``.

    The git common dir is shared by the main checkout and every linked worktree,
    and nothing under it is ever committed.
    """

    directory: Path

    @classmethod
    def for_idea(cls, repo: Repo, idea_name: str) -> "RunPaths":
        common_dir = Path(repo.common_dir).resolve()
        return cls(common_dir / "i2code" / "implement" / idea_name)

    @property
    def events_file(self) -> Path:
        return self.directory / "events.jsonl"

    @property
    def status_file(self) -> Path:
        return self.directory / "status.json"

    @property
    def inbox_dir(self) -> Path:
        return self.directory / "inbox"
