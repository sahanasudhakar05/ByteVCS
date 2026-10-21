"""Integration tests for the ByteVCS Phase 1 commands."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bytevcs.__main__ import init_repository
from bytevcs.object_store import read_object
from bytevcs.repository import (
    add_file,
    checkout_branch,
    create_branch,
    create_commit,
    find_repository,
    merge_branch,
    remove_file,
    show_diff,
    show_status,
    _current_commit_hash,
    _read_snapshot,
)


class ByteVCSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            init_repository(str(self.root))
        self.repo = find_repository(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def output(self, function, *args, **kwargs):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            function(*args, **kwargs)
        return stream.getvalue()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def commit_file(self, name, content, message):
        self.write(name, content)
        with contextlib.redirect_stdout(io.StringIO()):
            add_file(self.repo, name)
            create_commit(self.repo, message)
        return _current_commit_hash(self.repo)

    def test_add_commit_log_and_clean_status(self):
        self.commit_file("hello.txt", "hello\n", "first")
        output = self.output(show_status, self.repo)
        self.assertIn("working tree clean", output)

    def test_untracked_and_staged_status(self):
        self.write("new.txt", "one\n")
        self.assertIn("new.txt", self.output(show_status, self.repo))
        with contextlib.redirect_stdout(io.StringIO()):
            add_file(self.repo, "new.txt")
        self.assertIn("Changes to be committed", self.output(show_status, self.repo))

    def test_unstaged_diff(self):
        self.commit_file("hello.txt", "old\n", "first")
        self.write("hello.txt", "new\n")
        output = self.output(show_diff, self.repo, False, "hello.txt")
        self.assertIn("-old", output)
        self.assertIn("+new", output)

    def test_staged_diff(self):
        self.commit_file("hello.txt", "old\n", "first")
        self.write("hello.txt", "new\n")
        with contextlib.redirect_stdout(io.StringIO()):
            add_file(self.repo, "hello.txt")
        output = self.output(show_diff, self.repo, True, "hello.txt")
        self.assertIn("-old", output)
        self.assertIn("+new", output)

    def test_stage_deletion_and_commit(self):
        self.commit_file("gone.txt", "bye\n", "add file")
        with contextlib.redirect_stdout(io.StringIO()):
            remove_file(self.repo, "gone.txt")
        self.assertFalse((self.root / "gone.txt").exists())
        self.assertIn("deleted: gone.txt", self.output(show_status, self.repo))
        with contextlib.redirect_stdout(io.StringIO()):
            create_commit(self.repo, "delete file")
        self.assertNotIn("gone.txt", _read_snapshot(self.repo, _current_commit_hash(self.repo)))

    def test_branch_checkout(self):
        self.commit_file("hello.txt", "main version\n", "main")
        with contextlib.redirect_stdout(io.StringIO()):
            create_branch(self.repo, "feature")
        # Change and commit on main.
        self.write("hello.txt", "new main version\n")
        with contextlib.redirect_stdout(io.StringIO()):
            add_file(self.repo, "hello.txt")
            create_commit(self.repo, "main update")
            checkout_branch(self.repo, "feature")
        self.assertEqual((self.root / "hello.txt").read_text(), "main version\n")

    def test_fast_forward_merge(self):
        self.commit_file("hello.txt", "one\n", "base")
        with contextlib.redirect_stdout(io.StringIO()):
            create_branch(self.repo, "feature")
            checkout_branch(self.repo, "feature")
        self.commit_file("hello.txt", "two\n", "feature change")
        with contextlib.redirect_stdout(io.StringIO()):
            checkout_branch(self.repo, "main")
            merge_branch(self.repo, "feature")
        self.assertEqual((self.root / "hello.txt").read_text(), "two\n")

    def test_three_way_non_conflicting_merge(self):
        self.commit_file("a.txt", "a0\n", "base")
        self.write("b.txt", "b0\n")
        with contextlib.redirect_stdout(io.StringIO()):
            add_file(self.repo, "b.txt")
            create_commit(self.repo, "add b")
            create_branch(self.repo, "feature")
        # Change a on main.
        self.write("a.txt", "a-main\n")
        with contextlib.redirect_stdout(io.StringIO()):
            add_file(self.repo, "a.txt")
            create_commit(self.repo, "main change")
            checkout_branch(self.repo, "feature")
        # Change b on feature.
        self.write("b.txt", "b-feature\n")
        with contextlib.redirect_stdout(io.StringIO()):
            add_file(self.repo, "b.txt")
            create_commit(self.repo, "feature change")
            checkout_branch(self.repo, "main")
            merge_branch(self.repo, "feature")
        self.assertEqual((self.root / "a.txt").read_text(), "a-main\n")
        self.assertEqual((self.root / "b.txt").read_text(), "b-feature\n")
        merge_commit = json.loads(read_object(str(self.repo), _current_commit_hash(self.repo)))
        self.assertEqual(merge_commit["merge_parent"], (self.repo / ".bytevcs" / "refs" / "heads" / "feature").read_text().strip())

    def test_same_file_merge_conflict_does_not_overwrite(self):
        self.commit_file("a.txt", "base\n", "base")
        with contextlib.redirect_stdout(io.StringIO()):
            create_branch(self.repo, "feature")
        self.write("a.txt", "main version\n")
        with contextlib.redirect_stdout(io.StringIO()):
            add_file(self.repo, "a.txt")
            create_commit(self.repo, "main change")
            checkout_branch(self.repo, "feature")
        self.write("a.txt", "feature version\n")
        with contextlib.redirect_stdout(io.StringIO()):
            add_file(self.repo, "a.txt")
            create_commit(self.repo, "feature change")
            checkout_branch(self.repo, "main")
        before = _current_commit_hash(self.repo)
        output = self.output(merge_branch, self.repo, "feature")
        self.assertIn("Merge conflicts", output)
        self.assertEqual(_current_commit_hash(self.repo), before)
        self.assertEqual((self.root / "a.txt").read_text(), "main version\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
