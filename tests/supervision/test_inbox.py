"""Inbox: file queue from i2code ctl to the running implement."""

import json

import pytest

from i2code.supervision.inbox import Inbox
from i2code.supervision.run_paths import RunPaths


class _Ticks:
    def __init__(self):
        self._ns = 1_000

    def __call__(self):
        self._ns += 1
        return self._ns


@pytest.fixture
def inbox(tmp_path):
    return Inbox(RunPaths(tmp_path / "run"), clock_ns=_Ticks())


@pytest.mark.unit
class TestInbox:

    def test_post_writes_one_json_file_named_by_time_and_kind(self, inbox):
        path = inbox.post("note", text="use the staging cluster")

        assert path.parent == inbox.paths.inbox_dir
        assert path.name == "00000000000000001001-note.json"
        assert json.loads(path.read_text(encoding="utf-8")) == {"kind": "note", "text": "use the staging cluster"}
        assert [p.name for p in inbox.paths.inbox_dir.iterdir()] == [path.name]

    def test_take_returns_payloads_oldest_first_and_deletes_them(self, inbox):
        inbox.post("note", text="first")
        inbox.post("note", text="second")

        assert [m["text"] for m in inbox.take("note")] == ["first", "second"]
        assert inbox.take("note") == []

    def test_take_leaves_other_kinds(self, inbox):
        inbox.post("note", text="keep me")
        inbox.post("resume", note=None, fresh=False)

        assert inbox.take("resume") == [{"kind": "resume", "note": None, "fresh": False}]
        assert inbox.count("note") == 1

    def test_count_and_empty_inbox(self, inbox):
        assert inbox.count("note") == 0
        assert inbox.take("note") == []

        inbox.post("note", text="x")

        assert inbox.count("note") == 1

    def test_ignores_temporary_and_foreign_files(self, inbox):
        inbox.post("note", text="real")
        (inbox.paths.inbox_dir / ".tmp-123.json").write_text("{partial")
        (inbox.paths.inbox_dir / "README").write_text("not a message")

        assert [m["text"] for m in inbox.take("note")] == ["real"]

    def test_discard_removes_kinds_and_keeps_notes(self, inbox):
        inbox.post("note", text="keep")
        inbox.post("resume", note=None, fresh=False)
        inbox.post("stop")

        inbox.discard("resume", "stop")

        assert inbox.count("resume") == 0
        assert inbox.count("stop") == 0
        assert inbox.count("note") == 1
