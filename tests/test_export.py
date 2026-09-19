import builtins
import gc
import io
import json
import os
import tempfile
import unittest
import warnings
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

    def test_failed_write_or_close_leaves_destination_available_for_retry(self):
        inventory = build_inventory(BatchScanResult((
            RootScanSuccess(Path("/home/Documents"), ()),
        ), ()))
        real_open = io.open

        class FailingWriter:
            def __init__(self, wrapped, failure):
                self.wrapped = wrapped
                self.failure = failure

            def __getattr__(self, name):
                return getattr(self.wrapped, name)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

            def write(self, contents):
                if self.failure == "write":
                    self.wrapped.write(contents[:10])
                    raise OSError("Injected disk-full error")
                return self.wrapped.write(contents)

            def close(self):
                self.wrapped.close()
                if self.failure == "close":
                    self.failure = None
                    raise OSError("Injected close error")

        for failure in ("write", "close"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "inventory.json"
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always", ResourceWarning)
                    with mock.patch.object(io, "open", side_effect=lambda *args, **kwargs:
                                           FailingWriter(real_open(*args, **kwargs), failure)):
                        with self.assertRaises(OSError):
                            save_export(inventory, destination)
                    gc.collect()
                self.assertEqual([str(item.message) for item in caught
                                  if issubclass(item.category, ResourceWarning)], [])
                self.assertFalse(destination.exists())
                self.assertEqual(list(Path(directory).iterdir()), [])
                save_export(inventory, destination)
                self.assertEqual(destination.read_text(encoding="utf-8"), encode_export(inventory))

    def test_sync_or_publish_error_leaves_no_destination_or_staging_files(self):
        inventory = build_inventory(BatchScanResult((
            RootScanSuccess(Path("/home/Documents"), ()),
        ), ()))
        for operation in ("fsync", "link"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "inventory.json"
                with mock.patch(f"file_chisel.export.os.{operation}",
                                side_effect=OSError("Injected I/O error")):
                    with self.assertRaises(OSError):
                        save_export(inventory, destination)
                self.assertFalse(destination.exists())
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_competing_file_at_publication_is_preserved(self):
        inventory = build_inventory(BatchScanResult((
            RootScanSuccess(Path("/home/Documents"), ()),
        ), ()))
        real_link = os.link
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "inventory.json"

            def publish(source, target):
                # The staging file must already contain a complete document.
                self.assertEqual(Path(source).read_text(encoding="utf-8"), encode_export(inventory))
                Path(target).write_text("another writer's file", encoding="utf-8")
                real_link(source, target)

            with mock.patch("file_chisel.export.os.link", side_effect=publish):
                with self.assertRaises(FileExistsError):
                    save_export(inventory, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "another writer's file")
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    def test_existing_symlink_is_preserved_without_touching_its_target(self):
        inventory = build_inventory(BatchScanResult((
            RootScanSuccess(Path("/home/Documents"), ()),
        ), ()))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "original.txt"
            destination = Path(directory) / "inventory.json"
            destination.symlink_to(target)
            with self.assertRaises(FileExistsError):
                save_export(inventory, destination)
            self.assertTrue(destination.is_symlink())
            self.assertFalse(target.exists())
            target.write_text("unchanged", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                save_export(inventory, destination)
            self.assertTrue(destination.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")
