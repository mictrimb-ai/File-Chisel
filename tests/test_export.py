import unittest
from pathlib import Path

from file_chisel.export import build_entry_record
from file_chisel.scanner import FileSystemEntry


class ExportTests(unittest.TestCase):
    def test_entry_record_uses_relative_metadata_only(self):
        root = Path("/Users/parker/Documents")
        entry = FileSystemEntry(
            "notes.txt",
            root / "Projects" / "notes.txt",
            "file",
            1200,
            1789765200.0,
        )

        record = build_entry_record(root, entry)

        self.assertEqual(record, {
            "root": "Documents",
            "relative_path": "Projects/notes.txt",
            "type": "file",
            "size": 1200,
        })
        self.assertNotIn("/Users/parker", str(record))
        self.assertNotIn("1789765200", str(record))
