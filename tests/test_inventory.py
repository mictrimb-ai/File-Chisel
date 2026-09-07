"""Tests for immutable inventory snapshots built from scan metadata."""

import builtins
import os
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from file_chisel.folder_selection import (
    BatchScanResult,
    RootScanFailure,
    RootScanSuccess,
    ScanErrorKind,
)
from file_chisel.inventory import build_inventory
from file_chisel.scanner import FileSystemEntry


def entry(path, entry_type="file", size=10, modified_time=1.0):
    path = Path(path)
    return FileSystemEntry(path.name, path, entry_type, size, modified_time)


class ScanInventoryTests(unittest.TestCase):
    def test_builds_the_defined_partial_inventory(self):
        report = entry("/home/Documents/report.txt", size=100)
        empty_notes = entry("/home/Documents/Empty Notes", "directory")
        report_copy = entry("/home/Desktop/Report copy.txt", size=100)
        photo = entry("/home/Desktop/photo.jpg", size=200)
        documents = RootScanSuccess(
            Path("/home/Documents"), (report, empty_notes),
        )
        desktop = RootScanSuccess(
            Path("/home/Desktop"), (report_copy, photo),
        )
        downloads = RootScanFailure(
            Path("/home/Downloads"),
            ScanErrorKind.INVALID_ROOT,
            "Folder is missing, is not a directory, or is a symbolic link.",
        )

        inventory = build_inventory(
            BatchScanResult((documents, desktop), (downloads,))
        )

        self.assertEqual(inventory.successful_roots, (documents, desktop))
        self.assertEqual(inventory.failed_roots, (downloads,))
        self.assertEqual(
            inventory.entries, (report, empty_notes, report_copy, photo),
        )
        self.assertEqual(inventory.empty_directories, (empty_notes,))
        self.assertEqual(len(inventory.likely_duplicate_groups), 1)
        duplicate_group = inventory.likely_duplicate_groups[0]
        self.assertEqual(duplicate_group.normalized_name, "report.txt")
        self.assertEqual(duplicate_group.size, 100)
        self.assertEqual(set(duplicate_group.files), {report, report_copy})

    def test_all_failed_batch_produces_an_empty_successful_inventory(self):
        failure = RootScanFailure(
            Path("/home/Downloads"), ScanErrorKind.INVALID_ROOT, "Missing.",
        )

        inventory = build_inventory(BatchScanResult((), (failure,)))

        self.assertEqual(inventory.successful_roots, ())
        self.assertEqual(inventory.failed_roots, (failure,))
        self.assertEqual(inventory.entries, ())
        self.assertEqual(inventory.empty_directories, ())
        self.assertEqual(inventory.likely_duplicate_groups, ())

    def test_snapshot_is_frozen_and_building_it_performs_no_filesystem_access(self):
        record = entry("/private/report.txt")
        result = BatchScanResult(
            (RootScanSuccess(Path("/private"), (record,)),), (),
        )
        forbidden = AssertionError("inventory building accessed the filesystem")

        with mock.patch.object(builtins, "open", side_effect=forbidden), \
                mock.patch.object(os, "stat", side_effect=forbidden), \
                mock.patch.object(os, "scandir", side_effect=forbidden), \
                mock.patch.object(Path, "open", side_effect=forbidden), \
                mock.patch.object(Path, "iterdir", side_effect=forbidden):
            inventory = build_inventory(result)

        self.assertEqual(inventory.entries, (record,))
        with self.assertRaises(FrozenInstanceError):
            inventory.entries = ()


if __name__ == "__main__":
    unittest.main()
