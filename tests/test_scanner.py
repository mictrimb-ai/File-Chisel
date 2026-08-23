"""Tests for the read-only filesystem scanner."""

import builtins
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from file_chisel.scanner import (
    FileSystemEntry,
    InvalidScanRootError,
    scan_directory,
)


class ScannerTests(unittest.TestCase):
    def test_recursively_returns_metadata_in_depth_first_preorder(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            alpha = root / "alpha"
            nested = alpha / "nested"
            alpha.mkdir()
            nested.mkdir()
            (alpha / "first.txt").write_bytes(b"first")
            (nested / "deep.txt").write_bytes(b"deep content")
            (root / "zulu.txt").write_bytes(b"z")

            records = scan_directory(root)

            self.assertEqual(
                [record.path.relative_to(root) for record in records],
                [
                    Path("alpha"),
                    Path("alpha/first.txt"),
                    Path("alpha/nested"),
                    Path("alpha/nested/deep.txt"),
                    Path("zulu.txt"),
                ],
            )
            self.assertTrue(all(record.path.is_absolute() for record in records))
            self.assertTrue(all(isinstance(record, FileSystemEntry) for record in records))

            by_path = {record.path: record for record in records}
            file_record = by_path[alpha / "first.txt"]
            file_metadata = os.stat(alpha / "first.txt", follow_symlinks=False)
            self.assertEqual(file_record.name, "first.txt")
            self.assertEqual(file_record.entry_type, "file")
            self.assertEqual(file_record.size, len(b"first"))
            self.assertEqual(file_record.modified_time, file_metadata.st_mtime)
            self.assertEqual(by_path[alpha].entry_type, "directory")

    def test_empty_directory_returns_empty_inventory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            self.assertEqual(scan_directory(temporary_directory), [])

    def test_scanner_does_not_open_file_contents(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            test_file = root / "content.txt"
            test_file.write_bytes(b"private contents")

            with mock.patch.object(
                builtins,
                "open",
                side_effect=AssertionError("scanner attempted to open a file"),
            ), mock.patch.object(
                Path,
                "open",
                side_effect=AssertionError("scanner attempted Path.open"),
            ), mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("scanner attempted Path.read_bytes"),
            ), mock.patch.object(
                Path,
                "read_text",
                side_effect=AssertionError("scanner attempted Path.read_text"),
            ):
                records = scan_directory(root)

            self.assertEqual([record.path for record in records], [test_file])

    def test_scan_does_not_mutate_entries(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            folder = root / "folder"
            folder.mkdir()
            file_path = folder / "data.bin"
            file_path.write_bytes(b"unchanged bytes")

            before = self._tree_snapshot(root)
            records = scan_directory(root)
            after = self._tree_snapshot(root)

            self.assertEqual(after, before)
            self.assertEqual(file_path.read_bytes(), b"unchanged bytes")
            self.assertEqual(len(records), 2)

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_directory_symlink_is_recorded_but_not_traversed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "root"
            external = Path(temporary_directory) / "external"
            root.mkdir()
            external.mkdir()
            (external / "outside.txt").write_bytes(b"outside")
            link = root / "linked-directory"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symbolic link creation is unavailable: {error}")

            records = scan_directory(root)

            self.assertEqual([record.path for record in records], [link])
            self.assertEqual(records[0].entry_type, "symlink")
            self.assertEqual(
                records[0].size,
                os.stat(link, follow_symlinks=False).st_size,
            )

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_broken_symlink_is_inventoried(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            link = root / "broken-link"
            try:
                link.symlink_to(root / "missing-target")
            except OSError as error:
                self.skipTest(f"symbolic link creation is unavailable: {error}")

            records = scan_directory(root)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].path, link)
            self.assertEqual(records[0].entry_type, "symlink")

    def test_missing_root_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing = Path(temporary_directory) / "missing"

            with self.assertRaisesRegex(
                InvalidScanRootError,
                "must be an existing directory",
            ):
                scan_directory(missing)

    def test_file_root_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            file_path = Path(temporary_directory) / "file.txt"
            file_path.write_bytes(b"data")

            with self.assertRaisesRegex(
                InvalidScanRootError,
                "must be an existing directory",
            ):
                scan_directory(file_path)

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_symlink_root_is_rejected_instead_of_followed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            target = base / "target"
            link = base / "root-link"
            target.mkdir()
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symbolic link creation is unavailable: {error}")

            with self.assertRaisesRegex(
                InvalidScanRootError,
                "must be an existing directory",
            ):
                scan_directory(link)

    def test_relative_root_produces_absolute_paths_without_resolving_links(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            absolute_root = Path(temporary_directory)
            relative_root = absolute_root.relative_to(Path.cwd())
            file_path = absolute_root / "file.txt"
            file_path.write_bytes(b"data")

            with mock.patch.object(
                Path,
                "resolve",
                side_effect=AssertionError("paths must not be resolved"),
            ):
                records = scan_directory(relative_root)

            self.assertEqual(records[0].path, file_path)
            self.assertTrue(records[0].path.is_absolute())

    @staticmethod
    def _tree_snapshot(root):
        snapshot = {}
        for path in sorted(root.rglob("*")):
            metadata = os.stat(path, follow_symlinks=False)
            relative_path = path.relative_to(root)
            if path.is_dir():
                entry_type = "directory"
                contents = None
            elif path.is_file():
                entry_type = "file"
                contents = path.read_bytes()
            else:
                entry_type = "other"
                contents = None
            snapshot[relative_path] = (entry_type, metadata.st_size, contents)
        return snapshot


if __name__ == "__main__":
    unittest.main()
