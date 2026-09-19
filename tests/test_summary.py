import unittest
from pathlib import Path

from file_chisel.scanner import FileSystemEntry
from file_chisel.summary import calculate_file_totals


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


if __name__ == "__main__":
    unittest.main()
    