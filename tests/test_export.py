import builtins
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from file_chisel.export import (
    build_entry_record, build_export_document, encode_export, save_export,
)
from file_chisel.folder_selection import (
    BatchScanResult, RootScanFailure, RootScanSuccess, ScanErrorKind,
)
from file_chisel.inventory import build_inventory
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

    def test_partial_scan_exports_relative_metadata_without_error_details(self):
        home = Path("/Users/private-user")
        documents = home / "Documents"
        desktop = home / "Desktop"
        report = FileSystemEntry("Report.txt", documents / "Report.txt", "file", 12, 1.0)
        empty = FileSystemEntry("Empty", documents / "Empty", "directory", 800, 2.0)
        copy = FileSystemEntry("Report copy.txt", desktop / "Report copy.txt", "file", 12, 3.0)
        failure = RootScanFailure(
            home / "Downloads", ScanErrorKind.FILESYSTEM,
            "Access denied at /Users/private-user/private-path",
        )
        inventory = build_inventory(BatchScanResult((
            RootScanSuccess(documents, (report, empty)),
            RootScanSuccess(desktop, (copy,)),
        ), (failure,)))

        forbidden = AssertionError("export inspected the filesystem")
        with mock.patch.object(builtins, "open", side_effect=forbidden), \
                mock.patch.object(os, "stat", side_effect=forbidden), \
                mock.patch.object(os, "scandir", side_effect=forbidden), \
                mock.patch.object(Path, "open", side_effect=forbidden):
            text = encode_export(inventory)
        self.assertEqual(text, json.dumps(json.loads(text), sort_keys=True, indent=2,
                                          ensure_ascii=False) + "\n")
        document = json.loads(text)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["roots"], [
            {"name": "Desktop", "status": "success"},
            {"name": "Documents", "status": "success"},
            {"name": "Downloads", "status": "failed", "error_kind": "filesystem"},
        ])
        self.assertEqual(document["entries"], [
            {"root": "Desktop", "relative_path": "Report copy.txt", "type": "file", "size": 12},
            {"root": "Documents", "relative_path": "Empty", "type": "directory", "size": 800},
            {"root": "Documents", "relative_path": "Report.txt", "type": "file", "size": 12},
        ])
        self.assertEqual(document["empty_directories"], [
            {"root": "Documents", "relative_path": "Empty"},
        ])
        self.assertEqual(document["likely_duplicates"], [{
            "normalized_name": "report.txt", "size": 12,
            "files": [
                {"root": "Desktop", "relative_path": "Report copy.txt"},
                {"root": "Documents", "relative_path": "Report.txt"},
            ],
        }])
        self.assertEqual(document["summary"]["file_count"], 2)
        self.assertEqual(document["summary"]["total_file_size"], 24)
        self.assertEqual(document["summary"]["failed_root_count"], 1)
        for sensitive in ("/Users/private-user", "private-path", "modified_time", "\"message\""):
            self.assertNotIn(sensitive, text)

        reordered = build_inventory(BatchScanResult((
            RootScanSuccess(desktop, (copy,)),
            RootScanSuccess(documents, (empty, report)),
        ), (failure,)))
        self.assertEqual(encode_export(reordered), text)

    def test_all_failed_scan_has_explicit_outcome_and_no_entries(self):
        failure = RootScanFailure(
            Path("/home/Downloads"), ScanErrorKind.INVALID_ROOT, "Missing at /home",
        )
        inventory = build_inventory(BatchScanResult((), (failure,)))
        document = build_export_document(inventory)
        self.assertEqual(document["roots"], [
            {"name": "Downloads", "status": "failed", "error_kind": "invalid_root"},
        ])
        self.assertEqual(document["entries"], [])
        self.assertEqual(document["summary"]["failed_root_count"], 1)

    def test_export_rejects_path_escapes_and_root_name_collisions(self):
        root = Path("/home/Documents")
        for path in (root, root / ".." / "secrets.txt", Path("/home/elsewhere.txt")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                build_entry_record(root, FileSystemEntry(path.name, path, "file", 1, 0.0))

        inventory = build_inventory(BatchScanResult((
            RootScanSuccess(Path("/one/Documents"), ()),
            RootScanSuccess(Path("/two/Documents"), ()),
        ), ()))
        with self.assertRaises(ValueError):
            build_export_document(inventory)

        user_home = build_inventory(BatchScanResult((
            RootScanSuccess(Path("/home/private-user"), ()),
        ), ()))
        with self.assertRaises(ValueError):
            build_export_document(user_home)

    def test_save_creates_new_json_and_refuses_to_overwrite(self):
        inventory = build_inventory(BatchScanResult((
            RootScanSuccess(Path("/home/Documents"), ()),
        ), ()))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "export.json"
            save_export(inventory, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), encode_export(inventory))
            destination.write_text("untouched", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                save_export(inventory, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "untouched")
