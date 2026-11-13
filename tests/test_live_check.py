"""Live metadata checks compare a fresh scan without modifying files."""

import builtins
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from file_chisel.folder_selection import BatchScanResult, RootScanFailure, RootScanSuccess, ScanErrorKind
from file_chisel.inventory import build_inventory
from file_chisel.live_check import LiveCheckResult, check_current_folders
from file_chisel.scanner import scan_directory


class LiveCheckTests(unittest.TestCase):
    def test_no_successful_roots_is_not_a_match(self):
        self.assertFalse(LiveCheckResult(0, (), ()).matches)

    def test_matches_then_reports_added_removed_and_changed_entries(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / "Documents"
            root.mkdir()
            (root / "report.txt").write_bytes(b"old")
            (root / "gone.txt").write_bytes(b"gone")
            (root / "folder").mkdir()
            snapshot = build_inventory(BatchScanResult(
                (RootScanSuccess(root, tuple(scan_directory(root))),), (),
            ))
            with mock.patch.object(builtins, "open", side_effect=AssertionError("opened contents")), \
                    mock.patch.object(os, "rename", side_effect=AssertionError("renamed file")), \
                    mock.patch.object(os, "mkdir", side_effect=AssertionError("created folder")):
                self.assertTrue(check_current_folders(snapshot).matches)

            (root / "gone.txt").unlink()
            (root / "report.txt").write_bytes(b"new longer data")
            (root / "folder" / "new.txt").write_bytes(b"new")
            result = check_current_folders(snapshot)
            self.assertFalse(result.matches)
            self.assertEqual(
                {(item.relative_path, item.kind) for item in result.findings},
                {("gone.txt", "disappeared"), ("report.txt", "metadata_changed"),
                 ("folder/new.txt", "appeared")},
            )
            self.assertEqual(result.unavailable_roots, ())
            self.assertEqual(len(snapshot.entries), 3)

    def test_missing_or_symlink_root_is_uncertain_and_failed_roots_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as home:
            base = Path(home)
            root = base / "Documents"
            root.mkdir()
            failed = base / "Downloads"
            snapshot = build_inventory(BatchScanResult(
                (RootScanSuccess(root, ()),),
                (RootScanFailure(failed, ScanErrorKind.FILESYSTEM, "unavailable"),),
            ))
            scanner = mock.Mock(wraps=scan_directory)
            self.assertTrue(check_current_folders(snapshot, scanner).matches)
            scanner.assert_called_once_with(root)
            root.rmdir()
            root.symlink_to(base, target_is_directory=True)
            result = check_current_folders(snapshot)
            self.assertFalse(result.matches)
            self.assertEqual(result.unavailable_roots, ("Documents",))

    def test_scan_failure_cannot_look_like_a_match(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / "Desktop"
            root.mkdir()
            snapshot = build_inventory(BatchScanResult(
                (RootScanSuccess(root, ()),), (),
            ))
            result = check_current_folders(snapshot, mock.Mock(side_effect=PermissionError("denied")))
            self.assertFalse(result.matches)
            self.assertEqual(result.unavailable_roots, ("Desktop",))


if __name__ == "__main__":
    unittest.main()
