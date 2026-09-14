"""Tests for grouping inventoried entries by their direct parent."""

import unittest
from pathlib import Path

from file_chisel.hierarchy import index_children
from file_chisel.scanner import FileSystemEntry


def make_entry(path, entry_type="file"):
    path = Path(path)
    return FileSystemEntry(path.name, path, entry_type, 0, 0.0)


class HierarchyTests(unittest.TestCase):
    def test_groups_entries_under_their_direct_parent(self):
        projects = make_entry("/demo/Documents/Projects", "directory")
        notes = make_entry("/demo/Documents/Projects/notes.txt")
        empty_notes = make_entry("/demo/Documents/Empty Notes", "directory")
        photo = make_entry("/demo/Desktop/photo.jpg")

        actual = index_children([projects, notes, empty_notes, photo])

        expected = {
            Path("/demo/Documents"): [projects, empty_notes],
            Path("/demo/Documents/Projects"): [notes],
            Path("/demo/Desktop"): [photo],
        }
        self.assertEqual(actual, expected)

    def test_empty_inventory_returns_an_empty_dictionary(self):
        self.assertEqual(index_children([]), {})