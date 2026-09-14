"""Tests for grouping inventoried entries by their direct parent."""

import builtins
import os
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from file_chisel.folder_selection import BatchScanResult, RootScanSuccess
from file_chisel.hierarchy import build_hierarchy, index_children
from file_chisel.inventory import build_inventory
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

    def test_grouping_preserves_entries_and_accepts_a_single_pass_iterable(self):
        first = make_entry("/demo/Documents/first")
        second = make_entry("/demo/Documents/second")
        entries = [first, second]

        children = index_children(iter(entries))

        self.assertEqual(entries, [first, second])
        self.assertIs(children[first.path.parent][0], first)
        self.assertIs(children[first.path.parent][1], second)
        children[first.path.parent].clear()
        self.assertEqual(entries, [first, second])

    def test_frozen_index_uses_the_grouping_once_without_filesystem_access(self):
        record = make_entry("/demo/Documents/report.txt")
        inventory = build_inventory(BatchScanResult((
            RootScanSuccess(Path("/demo/Documents"), (record,)),
        ), ()))
        forbidden = AssertionError("hierarchy accessed the filesystem")

        with mock.patch("file_chisel.hierarchy.index_children", wraps=index_children) as group, \
                mock.patch.object(builtins, "open", side_effect=forbidden), \
                mock.patch.object(Path, "open", side_effect=forbidden), \
                mock.patch.object(Path, "resolve", side_effect=forbidden), \
                mock.patch.object(os, "stat", side_effect=forbidden), \
                mock.patch.object(os, "scandir", side_effect=forbidden):
            hierarchy = build_hierarchy(inventory)

        group.assert_called_once_with(inventory.entries)
        self.assertEqual(hierarchy.children, {record.path.parent: (record,)})
        self.assertIs(hierarchy.children[record.path.parent][0], record)
        with self.assertRaises(TypeError):
            hierarchy.children[record.path.parent] = ()
        with self.assertRaises(FrozenInstanceError):
            hierarchy.children = {}
