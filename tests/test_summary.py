import builtins
import os
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from file_chisel.analysis import LikelyDuplicateGroup
from file_chisel.scanner import FileSystemEntry
from file_chisel.summary import (
    LARGE_FILE_THRESHOLD,
    FileTypeSummary,
    build_summary,
    calculate_file_totals,
    format_bytes,
    format_summary_lines,
)


def entry(path, entry_type="file", size=10):
    path = Path(path)
    return FileSystemEntry(path.name, path, entry_type, size, 1.0)


class InventorySummaryTests(unittest.TestCase):
    def test_counts_and_sums_only_regular_files(self):
        entries = [
            entry("/scan/report.txt", size=100),
            entry("/scan/photo.jpg", size=250),
            entry("/scan/folder", "directory", size=900),
            entry("/scan/link", "symlink", size=500),
            entry("/scan/device", "other", size=300),
        ]

        self.assertEqual(calculate_file_totals(entries), (2, 350))

    def test_empty_inventory_returns_zero_totals(self):
        self.assertEqual(calculate_file_totals([]), (0, 0))

    def test_builds_immutable_file_types_and_problem_counts(self):
        report = entry("/scan/report.TXT", size=100)
        report_copy = entry("/scan/report copy.txt", size=100)
        photo = entry("/scan/photo.JPG", size=LARGE_FILE_THRESHOLD)
        readme = entry("/scan/README", size=25)
        empty = entry("/scan/Empty Notes", "directory", size=900)
        link = entry("/scan/link.jpg", "symlink", size=500)
        entries = [report, report_copy, photo, readme, empty, link]
        duplicate = LikelyDuplicateGroup(
            normalized_name="report.txt",
            size=100,
            files=(report, report_copy),
        )

        summary = build_summary(
            iter(entries),
            empty_directories=iter((empty,)),
            likely_duplicate_groups=iter((duplicate,)),
            failed_root_count=2,
        )

        self.assertEqual((summary.file_count, summary.directory_count), (4, 1))
        self.assertEqual(summary.total_file_size, LARGE_FILE_THRESHOLD + 225)
        self.assertEqual(summary.file_types, (
            FileTypeSummary(".jpg", 1, LARGE_FILE_THRESHOLD),
            FileTypeSummary(".txt", 2, 200),
            FileTypeSummary("", 1, 25),
        ))
        self.assertEqual(summary.large_file_count, 1)
        self.assertEqual(summary.empty_directory_count, 1)
        self.assertEqual(summary.likely_duplicate_group_count, 1)
        self.assertEqual(summary.potential_duplicate_size, 100)
        self.assertEqual(summary.failed_root_count, 2)
        self.assertEqual(entries[-1], link)
        with self.assertRaises(FrozenInstanceError):
            summary.file_count = 0

    def test_formats_bytes_and_compact_summary_lines(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1024), "1.0 KB")
        self.assertEqual(format_bytes(5 * 1024 * 1024), "5.0 MB")
        with self.assertRaises(ValueError):
            format_bytes(-1)

        summary = build_summary([
            entry("/scan/report.txt", size=100),
            entry("/scan/photo.jpg", size=LARGE_FILE_THRESHOLD),
            entry("/scan/README", size=25),
        ])
        lines = format_summary_lines(summary, maximum_file_types=2)

        self.assertEqual(lines[0], "3 files · 0 folders · 100.0 MB")
        self.assertEqual(
            lines[1],
            "File types: .jpg: 1 file · 100.0 MB; .txt: 1 file · 100 B; +1 more",
        )
        self.assertIn("1 large file", lines[2])
        self.assertIn("0 likely duplicate groups", lines[2])

    def test_summary_building_performs_no_filesystem_access(self):
        record = entry("/private/report.txt", size=20)
        forbidden = AssertionError("summary accessed the filesystem")

        with mock.patch.object(builtins, "open", side_effect=forbidden), \
                mock.patch.object(os, "stat", side_effect=forbidden), \
                mock.patch.object(os, "scandir", side_effect=forbidden), \
                mock.patch.object(Path, "open", side_effect=forbidden), \
                mock.patch.object(Path, "iterdir", side_effect=forbidden):
            summary = build_summary([record])

        self.assertEqual(summary.file_count, 1)
        self.assertEqual(summary.total_file_size, 20)


if __name__ == "__main__":
    unittest.main()
