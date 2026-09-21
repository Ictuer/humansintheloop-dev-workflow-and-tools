"""Inbox: messages from ``i2code ctl`` to the running implement, one JSON file each."""

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

from i2code.supervision.run_paths import RunPaths

_MESSAGE_NAME = re.compile(r"^\d{20}-(?P<kind>[a-z_]+)\.json$")

Message = Dict[str, Any]


class Inbox:
    """File queue: ``post`` writes atomically, ``take`` reads oldest first and deletes."""

    def __init__(self, paths: RunPaths, clock_ns: Callable[[], int] = time.time_ns):
        self.paths = paths
        self._clock_ns = clock_ns

    def post(self, kind: str, **payload: Any) -> Path:
        directory = self.paths.inbox_dir
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"kind": kind, **payload}, f, ensure_ascii=False)
        target = directory / f"{self._clock_ns():020d}-{kind}.json"
        os.replace(tmp, target)
        return target

    def take(self, kind: str) -> List[Message]:
        messages = []
        for path in self._files(kind):
            messages.append(json.loads(path.read_text(encoding="utf-8")))
            path.unlink()
        return messages

    def count(self, kind: str) -> int:
        return len(self._files(kind))

    def discard(self, *kinds: str) -> None:
        for kind in kinds:
            for path in self._files(kind):
                path.unlink()

    def _files(self, kind: str) -> List[Path]:
        directory = self.paths.inbox_dir
        if not directory.is_dir():
            return []
        return sorted(
            path for path in directory.iterdir()
            if (match := _MESSAGE_NAME.match(path.name)) and match.group("kind") == kind
        )
