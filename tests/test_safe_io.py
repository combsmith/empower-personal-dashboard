"""
test_safe_io.py — Unit tests for the directory-pinned file I/O primitive.
"""

import os
import stat
import tempfile
import threading
import unittest
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
        # setgid and sticky bits are dropped.
        # (A write already clears setuid on most kernels; the sticky bit is not
        # cleared that way, so it is the case that really exercises the mask.)
        target = self.root / "export.json"
        for special in (stat.S_ISUID, stat.S_ISVTX):
            with self.subTest(bit=oct(special)):
                target.write_text("{}", encoding="utf-8")
                try:
                    os.chmod(target, 0o640 | special)
                except OSError:
                    self.skipTest(f"cannot set {oct(special)} on a regular file here")
                if not os.stat(target).st_mode & special:
                    continue  # filesystem silently dropped the bit
                with PinnedDir(self.root) as d:
                    d.write_atomic("export.json", lambda f: f.write("NEW"))
                self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o640)

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


if __name__ == "__main__":
    unittest.main()
