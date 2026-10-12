"""Dependency-free filesystem acceptance, also runnable with Linux stdlib unittest.

Run: PYTHONPATH=src python -m unittest discover -s tests -p test_report_files_portable.py -v
This is component acceptance, not a substitute for the full base/agent suite.
"""

import os
import tempfile
import unittest
from pathlib import Path

from agenttest.report_files import pinned_directory, writer_lock


class PortableReportFiles(unittest.TestCase):
    def setUp(self):
        Path("runs").mkdir(exist_ok=True)
        # WSL's /mnt/d is DrvFS, not a native POSIX filesystem (e.g. FIFO unsupported).
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agenttest-portable-", dir="runs" if os.name == "nt" else None)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).absolute()
        self.root = self.base / "reports"
        self.run_id = "a" * 32

    def test_exclusive_create_and_atomic_publication(self):
        with pinned_directory(self.root, self.run_id, create=True) as directory:
            fd = directory.open("report-unique.tmp", create=True)
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"complete")
            with self.assertRaises(FileExistsError):
                directory.open("report-unique.tmp", create=True)
            directory.replace("report-unique.tmp", "report.json")
            with os.fdopen(directory.open("report.json", readonly=True), "rb") as stream:
                self.assertEqual(stream.read(), b"complete")

    def test_competing_writer_is_rejected_and_exception_releases_lock(self):
        with pinned_directory(self.root, self.run_id, create=True) as first:
            with self.assertRaisesRegex(RuntimeError, "injected"):
                with writer_lock(first):
                    with pinned_directory(self.root, self.run_id, create=False) as second:
                        with self.assertRaisesRegex(ValueError, "writer already active"):
                            with writer_lock(second):
                                self.fail("Second writer acquired lock")
                    raise RuntimeError("injected")
            with writer_lock(first):
                pass

    def test_different_ids_can_hold_locks_at_the_same_time(self):
        with pinned_directory(self.root, self.run_id, create=True) as first:
            with writer_lock(first):
                with pinned_directory(self.root, "b" * 32, create=True) as second:
                    with writer_lock(second):
                        pass

    def test_hardlinked_report_is_rejected(self):
        outside = self.base / "outside"
        outside.write_bytes(b"untouched")
        with pinned_directory(self.root, self.run_id, create=True) as directory:
            os.link(outside, directory.path / "report.json")
            with self.assertRaises(ValueError):
                directory.inspect("report.json")
            with self.assertRaises(ValueError):
                directory.open("report.json", readonly=True)
        self.assertEqual(outside.read_bytes(), b"untouched")

    @unittest.skipUnless(os.name == "posix", "POSIX nofollow/dir_fd semantics")
    def test_preplanted_links_cannot_escape(self):
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_bytes(b"untouched")
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            with pinned_directory(self.root, self.run_id, create=True):
                self.fail("Root link accepted")
        self.root.unlink()
        with pinned_directory(self.root, self.run_id, create=True) as directory:
            (directory.path / "report.json").symlink_to(sentinel)
            with self.assertRaises(ValueError):
                directory.inspect("report.json")
            (directory.path / "report-unique.tmp").symlink_to(sentinel)
            with self.assertRaises(FileExistsError):
                directory.open("report-unique.tmp", create=True)
        self.assertEqual(list(outside.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b"untouched")

    @unittest.skipUnless(os.name == "posix", "POSIX directory replacement semantics")
    def test_directory_swap_keeps_io_on_pinned_inode(self):
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / "report.json"
        sentinel.write_bytes(b"untouched")
        with pinned_directory(self.root, self.run_id, create=True) as directory:
            directory.path.rename(self.base / "moved")
            directory.path.symlink_to(outside, target_is_directory=True)
            with os.fdopen(directory.open("new.tmp", create=True), "wb") as stream:
                stream.write(b"complete")
            directory.replace("new.tmp", "report.json")
        self.assertEqual(sentinel.read_bytes(), b"untouched")
        self.assertEqual((self.base / "moved" / "report.json").read_bytes(), b"complete")

    @unittest.skipUnless(os.name == "posix", "POSIX FIFO semantics")
    def test_fifo_rejected_without_blocking(self):
        with pinned_directory(self.root, self.run_id, create=True) as directory:
            os.mkfifo(directory.path / "report.json")
            with self.assertRaises(ValueError):
                directory.open("report.json", readonly=True)


if __name__ == "__main__":
    unittest.main()
