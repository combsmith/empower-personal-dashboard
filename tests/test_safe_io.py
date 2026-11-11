"""
test_safe_io.py — Unit tests for the directory-pinned file I/O primitive.
"""

import os
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from empower_personal_dashboard import _safe_io
from empower_personal_dashboard._safe_io import PinnedDir


class TestPinnedDir(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def test_rejects_non_plain_file_names(self) -> None:
        with PinnedDir(self.root) as d:
            for bad in ("", ".", "..", "a/b", "nul\x00byte"):
                with self.subTest(name=bad):
                    with self.assertRaises(ValueError):
                        d.write_atomic(bad, lambda f: f.write("x"))
                    with self.assertRaises(ValueError):
                        d.open_text(bad)

    def test_rejects_relative_directory(self) -> None:
        with self.assertRaises(ValueError):
            PinnedDir("relative/dir")

    def test_regular_file_in_chain_is_refused(self) -> None:
        blocker = self.root / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(ValueError):
            PinnedDir(blocker / "child", create=True)

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_symlinked_component_is_refused(self) -> None:
        real = self.root / "real"
        real.mkdir()
        (self.root / "link").symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            PinnedDir(self.root / "link" / "child", create=True)
        self.assertFalse((real / "child").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_open_text_refuses_symlinked_leaf(self) -> None:
        secret = self.root / "secret.json"
        secret.write_text("{}", encoding="utf-8")
        (self.root / "session.json").symlink_to(secret)
        with PinnedDir(self.root) as d:
            with self.assertRaises(ValueError):
                d.open_text("session.json")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires mkfifo")
    def test_open_text_refuses_fifo_without_blocking(self) -> None:
        # A FIFO planted at the session path must not hang the reader.
        os.mkfifo(self.root / "session.json")
        outcome = {}

        def attempt() -> None:
            try:
                with PinnedDir(self.root) as d:
                    d.open_text("session.json")
                outcome["result"] = "opened"
            except ValueError:
                outcome["result"] = "refused"

        worker = threading.Thread(target=attempt, daemon=True)
        worker.start()
        worker.join(5)
        if worker.is_alive():
            # Unblock the stuck reader so the daemon thread can exit.
            fd = os.open(self.root / "session.json", os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
            self.fail("open_text blocked on a FIFO")
        self.assertEqual(outcome.get("result"), "refused")

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_write_atomic_replaces_planted_symlink_without_following(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text("UNTOUCHED", encoding="utf-8")
        (self.root / "export.json").symlink_to(outside)
        with PinnedDir(self.root) as d:
            d.write_atomic("export.json", lambda f: f.write("NEW"))
        self.assertFalse((self.root / "export.json").is_symlink())
        self.assertEqual((self.root / "export.json").read_text(encoding="utf-8"), "NEW")
        self.assertEqual(outside.read_text(encoding="utf-8"), "UNTOUCHED")

    def test_write_atomic_failure_keeps_target_and_removes_temp(self) -> None:
        target = self.root / "export.json"
        target.write_text("ORIGINAL", encoding="utf-8")

        def explode(f) -> None:
            f.write("PARTIAL")
            raise RuntimeError("serialization failed")

        with PinnedDir(self.root) as d:
            with self.assertRaises(RuntimeError):
                d.write_atomic("export.json", explode)
        self.assertEqual(target.read_text(encoding="utf-8"), "ORIGINAL")
        self.assertEqual([p.name for p in self.root.iterdir()], ["export.json"])

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_write_atomic_does_not_copy_special_mode_bits(self) -> None:
        # Only the rwx permission bits of an existing file carry over; setuid,
        # setgid and sticky bits are dropped. The mode handed to fchmod is checked
        # as well as the result, because a write clears setuid on most kernels
        # with or without the mask, and some platforms refuse these bits on files.
        target = self.root / "export.json"
        exercised = []
        for special in (stat.S_ISUID, stat.S_ISVTX):
            target.write_text("{}", encoding="utf-8")
            try:
                os.chmod(target, 0o640 | special)
            except OSError:
                continue  # this platform refuses the bit on a regular file
            if not os.stat(target).st_mode & special:
                continue  # filesystem silently dropped the bit
            exercised.append(special)
            with self.subTest(bit=oct(special)):
                with patch.object(_safe_io.os, "fchmod", wraps=os.fchmod) as fchmod:
                    with PinnedDir(self.root) as d:
                        d.write_atomic("export.json", lambda f: f.write("NEW"))
                self.assertEqual([call.args[1] for call in fchmod.call_args_list], [0o640])
                self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o640)
        self.assertTrue(exercised, "no special mode bit could be set, so the mask was never exercised")

    def test_path_fallback_round_trip(self) -> None:
        original = _safe_io.DIR_FD_SUPPORTED
        _safe_io.DIR_FD_SUPPORTED = False
        try:
            with PinnedDir(self.root / "nested", create=True) as d:
                self.assertIsNone(d.fd)
                d.write_atomic("data.json", lambda f: f.write("{}"))
                with d.open_text("data.json") as f:
                    self.assertEqual(f.read(), "{}")
        finally:
            _safe_io.DIR_FD_SUPPORTED = original

    def _use_path_fallback(self) -> None:
        original = _safe_io.DIR_FD_SUPPORTED
        _safe_io.DIR_FD_SUPPORTED = False
        self.addCleanup(setattr, _safe_io, "DIR_FD_SUPPORTED", original)

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_path_fallback_refuses_symlinked_ancestor(self) -> None:
        # #167: the fallback checked only the leaf, so a symlinked ancestor
        # passed and ``mkdir(parents=True)`` created directories through it.
        self._use_path_fallback()
        real = self.root / "real"
        real.mkdir()
        (self.root / "link").symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            PinnedDir(self.root / "link" / "child", create=True)
        self.assertEqual(list(real.iterdir()), [])

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_path_fallback_refuses_directory_swapped_for_symlink_after_pin(self) -> None:
        self._use_path_fallback()
        victim = self.root / "victim"
        victim.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        with PinnedDir(victim) as d:
            os.rename(victim, self.root / "moved")
            victim.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                d.write_atomic("export.json", lambda f: f.write("NEW"))
            with self.assertRaises(ValueError):
                d.open_text("export.json")
            with self.assertRaises(ValueError):
                d.subdir("ledger", create=True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_path_fallback_refuses_directory_replaced_after_pin(self) -> None:
        # A real directory renamed into place (no symlink) is a different
        # directory too; the identity re-check must catch it.
        self._use_path_fallback()
        victim = self.root / "victim"
        victim.mkdir()
        with PinnedDir(victim) as d:
            os.rename(victim, self.root / "moved")
            victim.mkdir()
            with self.assertRaises(ValueError):
                d.write_atomic("export.json", lambda f: f.write("NEW"))
        self.assertEqual(list(victim.iterdir()), [])

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_subdir_is_opened_relative_to_pinned_parent(self) -> None:
        if not _safe_io.DIR_FD_SUPPORTED:
            self.skipTest("requires dir_fd support")
        victim = self.root / "victim"
        victim.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        moved = self.root / "moved"
        with PinnedDir(victim) as d:
            os.rename(victim, moved)
            victim.symlink_to(outside, target_is_directory=True)
            with d.subdir("ledger", create=True) as ledger:
                ledger.write_atomic("main.bean", lambda f: f.write("X"))
            self.assertEqual((moved / "ledger" / "main.bean").read_text(encoding="utf-8"), "X")
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_subdir_refuses_swapped_parent_in_fallback(self) -> None:
        victim = self.root / "victim"
        victim.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        original = _safe_io.DIR_FD_SUPPORTED
        _safe_io.DIR_FD_SUPPORTED = False
        try:
            with PinnedDir(victim) as d:
                os.rename(victim, self.root / "moved")
                victim.symlink_to(outside, target_is_directory=True)
                with self.assertRaises(ValueError):
                    d.subdir("ledger", create=True)
        finally:
            _safe_io.DIR_FD_SUPPORTED = original
        self.assertEqual(list(outside.iterdir()), [])

    def test_subdir_creates_child_in_fallback(self) -> None:
        original = _safe_io.DIR_FD_SUPPORTED
        _safe_io.DIR_FD_SUPPORTED = False
        try:
            with PinnedDir(self.root) as d:
                with d.subdir("ledger", create=True) as ledger:
                    ledger.write_atomic("main.bean", lambda f: f.write("X"))
        finally:
            _safe_io.DIR_FD_SUPPORTED = original
        self.assertEqual((self.root / "ledger" / "main.bean").read_text(encoding="utf-8"), "X")

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_subdir_refuses_symlinked_child(self) -> None:
        (self.root / "real").mkdir()
        (self.root / "ledger").symlink_to(self.root / "real", target_is_directory=True)
        with PinnedDir(self.root) as d:
            with self.assertRaises(ValueError):
                d.subdir("ledger", create=True)

    def test_directory_helpers_round_trip(self) -> None:
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                original = _safe_io.DIR_FD_SUPPORTED
                _safe_io.DIR_FD_SUPPORTED = original and not fallback
                try:
                    base = self.root / f"helpers_{fallback}"
                    with PinnedDir(base, create=True) as d:
                        self.assertIsNone(d.lstat("a.txt"))
                        tmp = d.write_temp("a.txt", lambda f: f.write("A"))
                        self.assertTrue(tmp.startswith(".a.txt."))
                        d.replace(tmp, "a.txt")
                        self.assertTrue(stat.S_ISREG(d.lstat("a.txt").st_mode))
                        self.assertEqual(d.listdir(), ["a.txt"])
                        d.unlink("a.txt")
                        d.unlink("a.txt")  # missing is not an error
                        self.assertEqual(d.listdir(), [])
                finally:
                    _safe_io.DIR_FD_SUPPORTED = original

    def test_write_temp_failure_removes_temp(self) -> None:
        def explode(f) -> None:
            f.write("PARTIAL")
            raise RuntimeError("boom")

        with PinnedDir(self.root) as d:
            with self.assertRaises(RuntimeError):
                d.write_temp("a.txt", explode)
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_open_private_append_creates_owner_only_and_appends(self) -> None:
        previous = os.umask(0o022)
        try:
            with PinnedDir(self.root) as d:
                for text in ("one\n", "two\n"):
                    fd = d.open_private_append("debug.log")
                    with os.fdopen(fd, "a", encoding="utf-8") as f:
                        f.write(text)
        finally:
            os.umask(previous)
        log = self.root / "debug.log"
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertEqual(log.read_text(encoding="utf-8"), "one\ntwo\n")

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_open_private_append_refuses_symlinked_leaf(self) -> None:
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                original = _safe_io.DIR_FD_SUPPORTED
                _safe_io.DIR_FD_SUPPORTED = original and not fallback
                try:
                    base = self.root / f"leaf_{fallback}"
                    base.mkdir()
                    target = base / "target.log"
                    target.write_text("UNTOUCHED", encoding="utf-8")
                    (base / "debug.log").symlink_to(target)
                    with PinnedDir(base) as d:
                        with self.assertRaises(OSError):
                            d.open_private_append("debug.log")
                    self.assertEqual(target.read_text(encoding="utf-8"), "UNTOUCHED")
                finally:
                    _safe_io.DIR_FD_SUPPORTED = original

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_open_private_append_refuses_fifo_without_blocking(self) -> None:
        fifo = self.root / "debug.log"
        os.mkfifo(fifo)
        # A reader makes the write-open succeed immediately; without one,
        # O_NONBLOCK makes it fail (ENXIO) instead of hanging.
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            with PinnedDir(self.root) as d:
                with self.assertRaises(ValueError):
                    d.open_private_append("debug.log")
        finally:
            os.close(reader)
        with PinnedDir(self.root) as d:
            with self.assertRaises(OSError):
                d.open_private_append("debug.log")


if __name__ == "__main__":
    unittest.main()
