"""Tests for metadata-only inventory analysis."""

import builtins
import os
import unittest
from pathlib import Path
from unittest import mock

from file_chisel.analysis import (
    LikelyDuplicateGroup,
    find_empty_directories,
    find_likely_duplicate_files,
    normalize_filename,
)
from file_chisel.scanner import FileSystemEntry


def entry(path, entry_type="file", size=10, modified_time=1.0):
    """Create an inventory record without touching the filesystem."""

    path = Path(path)
    return FileSystemEntry(path.name, path, entry_type, size, modified_time)


class FilenameNormalizationTests(unittest.TestCase):
    def test_normalizes_case_numbered_and_copy_suffixes(self):
        cases = {
            "Report (1).TXT": "report.txt",
            "Report (17).txt": "report.txt",
            "Report copy.txt": "report.txt",
            "Report COPY 12.txt": "report.txt",
        }

        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(normalize_filename(filename), expected)

    def test_does_not_strip_unseparated_or_empty_base_suffixes(self):
        cases = {
            "Reportcopy.txt": "reportcopy.txt",
            "copy.txt": "copy.txt",
            "copy 2.txt": "copy 2.txt",
            "(17).txt": "(17).txt",
            "Reportcopy 2.txt": "reportcopy 2.txt",
        }

        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(normalize_filename(filename), expected)

    def test_removes_at_most_one_suffix_and_preserves_final_extension(self):
        self.assertEqual(normalize_filename("Report copy (2).TXT"), "report copy.txt")
        self.assertEqual(normalize_filename("Report copy.PDF"), "report.pdf")

    def test_normalizes_canonically_equivalent_unicode_names(self):
        expected = "caf\u00e9.txt"

        self.assertEqual(normalize_filename("Caf\u00e9.txt"), expected)
        self.assertEqual(normalize_filename("Cafe\u0301 (1).txt"), expected)


class InventoryAnalysisTests(unittest.TestCase):
    def test_finds_only_direct_child_free_directories(self):
        records = [
            entry("/scan/empty", "directory"),
            entry("/scan/parent", "directory"),
            entry("/scan/parent/child", "directory"),
            entry("/scan/parent/child/item.txt"),
            entry("/scan/file.txt"),
            entry("/scan/link", "symlink"),
            entry("/scan/device", "other"),
        ]

        self.assertEqual(find_empty_directories(records), (records[0],))

    def test_non_direct_descendant_does_not_make_a_directory_nonempty(self):
        directory = entry("/scan/folder", "directory")
        disconnected_descendant = entry("/scan/folder/missing/item.txt")

        self.assertEqual(
            find_empty_directories([disconnected_descendant, directory]),
            (directory,),
        )

    def test_groups_regular_files_by_normalized_name_and_size(self):
        original = entry("/scan/a/Report.TXT", size=42)
        numbered = entry("/scan/b/Report (17).txt", size=42)
        copied = entry("/scan/c/Report COPY 12.txt", size=42)

        self.assertEqual(
            find_likely_duplicate_files([copied, original, numbered]),
            (
                LikelyDuplicateGroup(
                    normalized_name="report.txt",
                    size=42,
                    files=(original, numbered, copied),
                ),
            ),
        )

    def test_rejects_false_positives_and_ignored_entry_types(self):
        records = [
            entry("/scan/report.txt", size=10),
            entry("/scan/unrelated.txt", size=10),
            entry("/scan/report copy.txt", size=11),
            entry("/scan/report copy.pdf", size=10),
            entry("/scan/elsewhere/report (2).txt", "symlink", size=10),
            entry("/scan/other/report (3).txt", "other", size=10),
            entry("/scan/folder/report copy.txt", "directory", size=10),
        ]

        self.assertEqual(find_likely_duplicate_files(records), ())

    def test_results_are_deterministic_for_reordered_input(self):
        records = [
            entry("/scan/z-empty", "directory"),
            entry("/scan/a-empty", "directory"),
            entry("/z/Beta copy.bin", size=2),
            entry("/a/Beta.bin", size=2),
            entry("/z/alpha (2).txt", size=1),
            entry("/a/Alpha.txt", size=1),
        ]

        forward_empty = find_empty_directories(records)
        reverse_empty = find_empty_directories(reversed(records))
        forward_duplicates = find_likely_duplicate_files(records)
        reverse_duplicates = find_likely_duplicate_files(reversed(records))

        self.assertEqual(forward_empty, reverse_empty)
        self.assertEqual(
            [item.path for item in forward_empty],
            [Path("/scan/a-empty"), Path("/scan/z-empty")],
        )
        self.assertEqual(forward_duplicates, reverse_duplicates)
        self.assertEqual(
            [group.normalized_name for group in forward_duplicates],
            ["alpha.txt", "beta.bin"],
        )

    def test_does_not_mutate_input(self):
        records = [entry("/scan/report.txt"), entry("/scan/report copy.txt")]
        before = list(records)

        find_empty_directories(records)
        find_likely_duplicate_files(records)

        self.assertEqual(records, before)
        self.assertIs(records[0], before[0])
        self.assertIs(records[1], before[1])

    def test_analysis_does_not_access_filesystem_or_file_contents(self):
        records = [
            entry("/private/empty", "directory"),
            entry("/private/report.txt"),
            entry("/private/report (1).txt"),
        ]
        forbidden = AssertionError("analysis attempted filesystem access")

        with mock.patch.object(builtins, "open", side_effect=forbidden), mock.patch.object(
            os, "scandir", side_effect=forbidden
        ), mock.patch.object(os, "stat", side_effect=forbidden), mock.patch.object(
            Path, "open", side_effect=forbidden
        ), mock.patch.object(Path, "read_bytes", side_effect=forbidden), mock.patch.object(
            Path, "read_text", side_effect=forbidden
        ), mock.patch.object(Path, "iterdir", side_effect=forbidden), mock.patch.object(
            Path, "glob", side_effect=forbidden
        ), mock.patch.object(Path, "rglob", side_effect=forbidden):
            self.assertEqual(find_empty_directories(records), (records[0],))
            self.assertEqual(len(find_likely_duplicate_files(records)), 1)


if __name__ == "__main__":
    unittest.main()
