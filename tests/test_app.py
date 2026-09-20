"""Headless controller tests and Tk event wiring tested with small fakes."""

import builtins
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from file_chisel.app import ApplicationController, FolderSelectionView, main
from file_chisel.folder_selection import (
    BatchStatus,
    FolderScanCoordinator,
    FolderScanRunner,
    NoFoldersSelectedError,
    ScanAlreadyRunningError,
    ScanClosedError,
    ScanRunState,
)
from file_chisel.scanner import FileSystemEntry
from file_chisel.hierarchy import build_hierarchy
from tests.test_proposal import sample_proposal


class ControlledThread:
    """Let a test decide when the otherwise asynchronous work runs."""

    def __init__(self, *, target, args, daemon):
        self.target, self.args = target, args
        self.daemon = daemon

    def start(self):
        pass

    def finish(self):
        self.target(*self.args)


class FakeRoot:
    def __init__(self):
        self.destroyed = False
        self.owner = threading.get_ident()
        self.callbacks = {}
        self.next_id = 0
        self.clipboard = ""

    def assert_live(self):
        if self.destroyed:
            raise AssertionError("widget access after destruction")
        if threading.get_ident() != self.owner:
            raise AssertionError("widget access outside GUI thread")

    def title(self, *args, **kwargs):
        self.assert_live()

    minsize = columnconfigure = rowconfigure = protocol = mainloop = title

    def clipboard_clear(self):
        self.assert_live()
        self.clipboard = ""

    def clipboard_append(self, text):
        self.assert_live()
        self.clipboard += text

    def after(self, delay, callback):
        self.assert_live()
        self.next_id += 1
        self.callbacks[self.next_id] = callback
        return self.next_id

    def after_cancel(self, callback_id):
        self.assert_live()
        del self.callbacks[callback_id]

    def fire_next(self):
        callback_id = next(iter(self.callbacks))
        self.callbacks.pop(callback_id)()

    def destroy(self):
        self.assert_live()
        self.destroyed = True


class FakeWidget:
    def __init__(self, parent, **kwargs):
        self.root = parent if isinstance(parent, FakeRoot) else parent.root
        self.root.assert_live()
        self.options = kwargs

    def grid(self, *args, **kwargs):
        self.root.assert_live()

    columnconfigure = rowconfigure = set = grid

    def configure(self, **kwargs):
        self.root.assert_live()
        self.options.update(kwargs)


class FakeTreeview(FakeWidget):
    """Model the Treeview calls used here, including open-event ordering."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.rows = {"": {"children": []}}
        self.bindings = {}
        self.next_item = 0
        self.focused = ""

    heading = column = xview = yview = FakeWidget.grid

    def bind(self, sequence, callback):
        self.root.assert_live()
        self.bindings[sequence] = callback

    def insert(self, parent, index, **kwargs):
        self.root.assert_live()
        assert index == "end"
        self.next_item += 1
        item = f"I{self.next_item}"
        self.rows[item] = {
            "parent": parent, "children": [], "text": "", "values": (),
            "open": False, **kwargs,
        }
        self.rows[parent]["children"].append(item)
        return item

    def item(self, item, option=None, **kwargs):
        self.root.assert_live()
        self.rows[item].update(kwargs)
        return self.rows[item][option] if option else dict(self.rows[item])

    def exists(self, item):
        self.root.assert_live()
        return item in self.rows

    def get_children(self, item=None):
        self.root.assert_live()
        return tuple(self.rows[item or ""]["children"])

    def delete(self, *items):
        self.root.assert_live()
        for item in items:
            for child in tuple(self.rows[item]["children"]):
                self.delete(child)
            parent = self.rows[item]["parent"]
            self.rows[parent]["children"].remove(item)
            del self.rows[item]

    def focus(self, item=None):
        self.root.assert_live()
        if item is not None:
            self.focused = item
        return self.focused

    def open_item(self, item):
        self.focus(item)
        self.bindings["<<TreeviewOpen>>"](SimpleNamespace(widget=self))
        self.item(item, open=True)

    def selection(self):
        self.root.assert_live()
        return (self.focused,) if self.focused else ()


class FakeText(FakeWidget):
    yview = FakeWidget.grid

    def insert(self, index, text):
        self.root.assert_live()
        self.options["text"] = text

    def delete(self, *args):
        self.root.assert_live()
        self.options["text"] = ""


class FakeVariable:
    def __init__(self, *, master, value):
        self.root = master
        self.value = value

    def get(self):
        self.root.assert_live()
        return self.value

    def set(self, value):
        self.root.assert_live()
        self.value = value


FAKE_TK = SimpleNamespace(BooleanVar=FakeVariable, Text=FakeText)
FAKE_TTK = SimpleNamespace(
    Frame=FakeWidget, Label=FakeWidget, Checkbutton=FakeWidget, Button=FakeWidget,
    Treeview=FakeTreeview, Scrollbar=FakeWidget,
)


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.workers = []

        def make_thread(**kwargs):
            worker = ControlledThread(**kwargs)
            self.workers.append(worker)
            return worker

        patcher = mock.patch("file_chisel.folder_selection.Thread", side_effect=make_thread)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.scanner = mock.Mock(return_value=[])
        self.runner = FolderScanRunner(FolderScanCoordinator(Path("/fixture"), self.scanner))
        self.controller = ApplicationController(runner=self.runner)
        self.addCleanup(self.controller.close)

    def make_view(self):
        root = FakeRoot()
        view = FolderSelectionView(
            root, self.controller, tk_module=FAKE_TK, ttk_module=FAKE_TTK,
        )
        self.addCleanup(view.close)
        return root, view

    def scan_in_view(self, keys):
        root, view = self.make_view()
        for key in keys:
            self.controller.set_selected(key, True)
        view.start_scan()
        self.workers[-1].finish()
        root.fire_next()
        return root, view

    def finish_callbacks(self, root):
        for _ in range(1000):
            if not root.callbacks:
                return
            root.fire_next()
        self.fail("GUI callbacks did not finish")

    def children_by_name(self, tree, parent=""):
        return {tree.item(item, "text"): item for item in tree.get_children(parent)}

    @staticmethod
    def record(path, entry_type="file"):
        path = Path(path)
        return FileSystemEntry(path.name, path, entry_type, 0, 0.0)

    def test_initialization_and_checkbox_changes_do_not_scan(self):
        self.assertEqual(len(self.controller.options), 3)
        self.assertEqual(self.controller.selected_keys, ())
        self.assertFalse(self.controller.can_scan)
        self.assertIsNone(self.controller.inventory)
        self.controller.set_selected("documents", True)
        self.assertTrue(self.controller.can_scan)
        self.controller.set_selected("documents", False)
        self.assertFalse(self.controller.can_scan)
        self.assertEqual(self.workers, [])
        self.scanner.assert_not_called()
        with self.assertRaises(ValueError):
            self.controller.set_selected("other", True)

    def test_explicit_scan_has_no_selection_and_overlap_guards(self):
        with self.assertRaises(NoFoldersSelectedError):
            self.controller.start_scan()
        self.controller.set_selected("downloads", True)
        self.controller.start_scan()
        self.assertEqual(self.controller.status, "Scanning selected folders…")
        self.assertFalse(self.controller.can_scan)
        with self.assertRaises(ScanAlreadyRunningError):
            self.controller.start_scan()
        with self.assertRaises(ScanAlreadyRunningError):
            self.controller.set_selected("desktop", True)
        self.workers[0].finish()
        self.assertTrue(self.controller.poll())
        self.scanner.assert_called_once_with(Path("/fixture/Downloads"))
        self.assertIsNotNone(self.controller.inventory)
        self.assertEqual(self.controller.inventory.entries, ())
        self.assertTrue(self.controller.can_scan)
        self.assertEqual(self.controller.result_rows, ("Downloads — 0 entries",))

    def test_partial_results_keep_empty_success_separate_from_failure(self):
        record = FileSystemEntry("secret.txt", Path("/fixture/Documents/secret.txt"), "file", 2, 1.0)
        self.scanner.side_effect = [[record], PermissionError("denied"), []]
        for key in ("desktop", "documents", "downloads"):
            self.controller.set_selected(key, True)
        self.controller.start_scan()
        self.workers[0].finish()
        self.controller.poll()
        self.assertEqual(self.controller.status, "Scan partially completed.")
        self.assertEqual(self.controller.result.status, BatchStatus.PARTIAL)
        self.assertEqual(self.controller.inventory.entries, (record,))
        self.assertEqual(len(self.controller.inventory.failed_roots), 1)
        rows = self.controller.result_rows
        self.assertEqual(rows[0], "Documents — 1 entry")
        self.assertIn("/fixture/Downloads", rows[1])
        self.assertIn("Permission denied", rows[1])
        self.assertNotIn("0 entries", rows[1])
        self.assertEqual(rows[2], "Desktop — 0 entries")
        self.assertNotIn("secret.txt", "\n".join(rows))
        self.assertEqual(self.controller.summary_lines, (
            "1 file · 0 folders · 2 B",
            "File types: .txt: 1 file · 2 B",
            "Needs attention: 0 large files (100 MB+) · 0 empty folders · "
            "0 likely duplicate groups · 1 failed folder",
        ))

    def test_expected_and_unexpected_failure_both_restore_scan_eligibility(self):
        for error in (OSError("I/O failure"), RuntimeError("programming bug")):
            with self.subTest(error=error):
                self.scanner.side_effect = error
                self.controller.set_selected("documents", True)
                self.controller.start_scan()
                self.workers[-1].finish()
                self.assertTrue(self.controller.poll())
                self.assertTrue(self.controller.can_scan)
                if isinstance(error, OSError):
                    self.assertEqual(self.controller.status, "Scan failed.")
                    self.assertEqual(self.controller.result.status, BatchStatus.FAILED)
                    self.assertIsNotNone(self.controller.inventory)
                else:
                    self.assertIn("unexpected error", self.controller.status)
                    self.assertIs(self.controller.error, error)
                    self.assertIsNone(self.controller.result)
                    self.assertIsNone(self.controller.inventory)

    def test_changed_selection_clears_outdated_feedback_without_another_scan(self):
        self.controller.set_selected("documents", True)
        self.controller.start_scan()
        self.workers[0].finish()
        self.controller.poll()
        self.controller.set_selected("desktop", True)
        self.assertIsNone(self.controller.result)
        self.assertIsNone(self.controller.inventory)
        self.assertEqual(self.controller.result_rows, ())
        self.assertEqual(self.controller.summary_lines, ())
        self.assertEqual(self.scanner.call_count, 1)

    def test_close_ignores_late_completion_and_rejects_new_work(self):
        self.controller.set_selected("documents", True)
        self.controller.start_scan()
        self.workers[0].finish()
        self.controller.close()
        self.controller.close()
        self.assertFalse(self.controller.poll())
        self.assertFalse(self.controller.can_scan)
        self.assertEqual(self.controller.status, "Closed.")
        self.assertIsNone(self.controller.inventory)
        self.assertEqual(self.controller.result_rows, ())
        with self.assertRaises(ScanClosedError):
            self.controller.start_scan()

    def test_export_requires_current_idle_inventory(self):
        with self.assertRaises(ValueError):
            self.controller.export_to(Path("/unused/export.json"))
        self.controller.set_selected("documents", True)
        self.controller.start_scan()
        self.assertFalse(self.controller.can_export)
        self.workers[0].finish()
        self.controller.poll()
        self.assertTrue(self.controller.can_export)
        self.controller.set_selected("desktop", True)
        self.assertFalse(self.controller.can_export)
        with self.assertRaises(ValueError):
            self.controller.export_to(Path("/unused/export.json"))

    def test_proposal_actions_require_a_successful_idle_scan(self):
        self.assertFalse(self.controller.can_propose)
        with self.assertRaises(ValueError):
            self.controller.ai_request()
        with self.assertRaises(ValueError):
            self.controller.import_proposal(Path("/not-opened.json"))
        root, view = self.make_view()
        self.assertEqual(view.request_button.options["state"], "disabled")
        self.assertEqual(view.import_button.options["state"], "disabled")
        self.scanner.side_effect = OSError("failed")
        self.controller.set_selected("documents", True)
        view.start_scan()
        self.workers[-1].finish()
        root.fire_next()
        self.assertTrue(self.controller.can_export)
        self.assertFalse(self.controller.can_propose)
        view.copy_ai_request()
        self.assertEqual(root.clipboard, "")

    def test_copy_ai_request_contains_snapshot_without_scanning_or_sending_it(self):
        self.scanner.return_value = [self.record("/fixture/Documents/report.txt")]
        root, view = self.scan_in_view(["documents"])
        view.request_button.options["command"]()
        self.assertIn('"relative_path": "report.txt"', root.clipboard)
        self.assertNotIn("/fixture", root.clipboard)
        self.assertIn("AI request copied", view.status_label.options["text"])
        self.assertEqual(view.import_button.options["state"], "normal")
        self.scanner.assert_called_once()
        with mock.patch.object(root, "clipboard_append", side_effect=RuntimeError("unavailable")):
            view.copy_ai_request()
        self.assertIn("Clipboard unavailable", self.controller.status)
        self.assertTrue(self.controller.can_propose)

    def test_proposal_import_cancel_replacement_and_failure_keep_the_app_usable(self):
        self.scanner.return_value = [self.record("/fixture/Documents/report.txt")]
        root, view = self.scan_in_view(["documents"])
        snapshot = self.controller.inventory
        preview_one, preview_two = mock.Mock(), mock.Mock()
        factory = mock.Mock(side_effect=[preview_one, preview_two])
        view._preview_factory = factory
        view._open_dialog = mock.Mock(return_value="")
        with mock.patch("file_chisel.app.load_proposal", side_effect=AssertionError("cancel read a file")):
            view.import_proposal()
        factory.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proposal.json"
            document = sample_proposal(snapshot)
            path.write_text(json.dumps(document), encoding="utf-8")
            view._open_dialog.return_value = str(path)
            view.import_button.options["command"]()
            first = self.controller.proposal
            self.assertIsNotNone(first)
            factory.assert_called_once_with(root, first)
            self.assertIn("No files moved", self.controller.status)
            document["summary"] = "A revised AI explanation."
            path.write_text(json.dumps(document), encoding="utf-8")
            view.import_proposal()
            preview_one.close.assert_called_once()
            self.assertEqual(self.controller.proposal.summary, document["summary"])
            path.write_text("not json", encoding="utf-8")
            view.import_proposal()
            preview_two.close.assert_called_once()
            self.assertIsNone(self.controller.proposal)
            self.assertIn("Proposal not imported", self.controller.status)
            self.assertEqual(view.import_button.options["state"], "normal")
            self.assertIs(self.controller.inventory, snapshot)
        self.scanner.assert_called_once()

    def test_proposal_preview_is_cleared_on_selection_scan_failure_and_close(self):
        for action in ("selection", "scan", "start_failure", "close"):
            with self.subTest(action=action):
                self.scanner.return_value = [self.record("/fixture/Documents/report.txt")]
                root, view = self.scan_in_view(["documents"])
                preview = mock.Mock()
                view._proposal_preview = preview
                self.controller.proposal = view._preview_proposal = object()
                if action == "selection":
                    view.variables["desktop"].set(True)
                    view._selection_changed("desktop")
                elif action == "start_failure":
                    with mock.patch("file_chisel.folder_selection.Thread") as thread:
                        thread.return_value.start.side_effect = RuntimeError("cannot start")
                        view.start_scan()
                elif action == "scan":
                    view.start_scan()
                    self.assertEqual(view.import_button.options["state"], "disabled")
                else:
                    view.close()
                    view.copy_ai_request()
                    view.import_proposal()
                self.assertIsNone(self.controller.proposal)
                preview.close.assert_called_once()
                if action == "scan":
                    self.scanner.return_value = []
                    self.workers[-1].finish()
                    root.fire_next()
                if action != "close":
                    view.close()
                # A fresh controller is required after closing the runner.
                self.runner = FolderScanRunner(FolderScanCoordinator(Path("/fixture"), self.scanner))
                self.controller = ApplicationController(runner=self.runner)
                self.addCleanup(self.controller.close)

    def test_preview_window_failure_allows_retry(self):
        self.scanner.return_value = [self.record("/fixture/Documents/report.txt")]
        root, view = self.scan_in_view(["documents"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proposal.json"
            path.write_text(json.dumps(sample_proposal(self.controller.inventory)), encoding="utf-8")
            view._open_dialog = mock.Mock(return_value=str(path))
            preview = mock.Mock()
            view._preview_factory = mock.Mock(side_effect=[RuntimeError("window unavailable"), preview])
            view.import_proposal()
            self.assertIn("window could not open", self.controller.status)
            self.assertIsNone(self.controller.proposal)
            self.assertEqual(view.import_button.options["state"], "normal")
            view.import_proposal()
            self.assertIsNotNone(self.controller.proposal)
            self.assertIn("imported for preview", self.controller.status)
        view.close()
        preview.close.assert_called_once()

    def test_gui_export_cancel_new_file_and_existing_file(self):
        import tempfile

        self.scanner.return_value = [self.record("/fixture/Documents/notes.txt")]
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "inventory.json"
            selected = iter(("", str(destination), str(destination)))
            root = FakeRoot()
            view = FolderSelectionView(
                root, self.controller, tk_module=FAKE_TK, ttk_module=FAKE_TTK,
                save_dialog=lambda **kwargs: next(selected),
            )
            self.addCleanup(view.close)
            button = view.export_button
            self.assertEqual(button.options["state"], "disabled")
            view.save_inventory()
            self.assertFalse(destination.exists())
            self.controller.set_selected("documents", True)
            view.start_scan()
            self.assertEqual(button.options["state"], "disabled")
            self.workers[-1].finish()
            root.fire_next()
            self.assertEqual(button.options["state"], "normal")
            view.save_inventory()
            self.assertFalse(destination.exists())
            view.save_inventory()
            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(payload["entries"][0]["relative_path"], "notes.txt")
            view.save_inventory()
            self.assertIn("already exists", view.status_label.options["text"])
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), payload)
            view.variables["documents"].set(False)
            view._selection_changed("documents")
            self.assertEqual(button.options["state"], "disabled")
            view.close()
            view.save_inventory()

    def test_view_checkbox_button_and_completion_wiring(self):
        root, view = self.make_view()
        self.assertEqual(len(view.checkboxes), 3)
        self.assertTrue(all(not variable.get() for variable in view.variables.values()))
        self.assertEqual(view.scan_button.options["state"], "disabled")
        view.start_scan()
        self.assertEqual(self.workers, [])
        view.variables["documents"].set(True)
        view.checkboxes[0].options["command"]()
        self.scanner.assert_not_called()
        self.assertEqual(view.scan_button.options["state"], "normal")
        view.scan_button.options["command"]()
        self.assertEqual(view.scan_button.options["state"], "disabled")
        self.assertTrue(all(checkbox.options["state"] == "disabled" for checkbox in view.checkboxes))
        view.start_scan()
        self.assertEqual(len(self.workers), 1)
        # Polling before completion returns and schedules another callback.
        root.fire_next()
        self.assertEqual(len(root.callbacks), 1)
        self.workers[0].finish()
        root.fire_next()
        self.assertEqual(root.callbacks, {})
        self.assertEqual(view.scan_button.options["state"], "normal")
        self.assertEqual(view.results_label.options["text"], "Documents — 0 entries")
        self.assertEqual(view.status_label.options["text"], "Scan completed successfully.")
        self.assertEqual(
            view.summary_label.options["text"],
            "0 files · 0 folders · 0 B\n"
            "File types: None\n"
            "Needs attention: 0 large files (100 MB+) · 0 empty folders · "
            "0 likely duplicate groups · 0 failed folders",
        )

    def test_view_close_cancels_polling_and_late_callbacks_do_not_touch_widgets(self):
        root, view = self.make_view()
        self.controller.set_selected("documents", True)
        view.start_scan()
        late_callback = next(iter(root.callbacks.values()))
        view.close()
        self.assertTrue(root.destroyed)
        self.assertEqual(root.callbacks, {})
        self.assertEqual(self.runner.state, ScanRunState.CLOSED)
        self.workers[0].finish()
        late_callback()
        view.start_scan()
        view.close()
        self.scanner.assert_not_called()

    def test_view_recovers_from_thread_start_failure(self):
        root, view = self.make_view()
        self.controller.set_selected("documents", True)
        with mock.patch("file_chisel.folder_selection.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("cannot start thread")
            view.start_scan()
        self.assertEqual(self.runner.state, ScanRunState.IDLE)
        self.assertEqual(view.scan_button.options["state"], "normal")
        self.assertIn("could not start", view.status_label.options["text"])
        self.assertEqual(root.callbacks, {})
        self.scanner.assert_not_called()

    def test_view_hierarchy_matches_the_acceptance_example_without_filesystem_access(self):
        self.scanner.side_effect = [
            [
                self.record("/fixture/Documents/Projects", "directory"),
                self.record("/fixture/Documents/Projects/notes.txt"),
                self.record("/fixture/Documents/Empty Notes", "directory"),
            ],
            PermissionError(13, "denied", "/private/hidden-name"),
            [self.record("/fixture/Desktop/photo.jpg")],
        ]
        root, view = self.scan_in_view(["documents", "downloads", "desktop"])
        tree = view.hierarchy_view.tree
        roots = self.children_by_name(tree)
        self.assertEqual(list(roots), ["Documents", "Downloads", "Desktop"])
        self.assertTrue(all(not tree.item(item, "open") for item in roots.values()))
        self.assertEqual(tree.get_children(roots["Downloads"]), ())
        failure = tree.item(roots["Downloads"], "values")
        self.assertEqual(failure, (
            "Folder", "Scan failed: Permission denied. Check access to this folder. Contents unknown.",
        ))
        self.assertNotIn("hidden-name", failure[1])
        self.assertEqual(
            list(self.children_by_name(tree, roots["Documents"])),
            ["Expand to view contents"],
        )
        forbidden = AssertionError("expansion accessed the filesystem")
        with mock.patch.object(builtins, "open", side_effect=forbidden), \
                mock.patch.object(Path, "open", side_effect=forbidden), \
                mock.patch.object(Path, "resolve", side_effect=forbidden), \
                mock.patch.object(os, "stat", side_effect=forbidden), \
                mock.patch.object(os, "scandir", side_effect=forbidden):
            tree.open_item(roots["Documents"])
            self.finish_callbacks(root)
            documents = self.children_by_name(tree, roots["Documents"])
            self.assertEqual(list(documents), ["Projects", "Empty Notes"])
            self.assertEqual(tree.item(documents["Empty Notes"], "values"), ("Folder", "Empty folder"))
            self.assertEqual(tree.get_children(documents["Empty Notes"]), ())
            tree.open_item(documents["Projects"])
            tree.open_item(roots["Desktop"])
            self.finish_callbacks(root)
            self.assertEqual(list(self.children_by_name(tree, documents["Projects"])), ["notes.txt"])
            self.assertEqual(list(self.children_by_name(tree, roots["Desktop"])), ["photo.jpg"])
            tree.item(roots["Documents"], open=False)
            tree.open_item(roots["Documents"])
            self.assertEqual(root.callbacks, {})
        self.assertEqual(self.scanner.call_count, 3)

    def test_view_uses_recorded_types_instead_of_filename_suffixes(self):
        self.scanner.return_value = [
            self.record("/fixture/Documents/README"),
            self.record("/fixture/Documents/archive.v1", "directory"),
            self.record("/fixture/Documents/link.txt", "symlink"),
            self.record("/fixture/Documents/device", "other"),
        ]
        root, view = self.scan_in_view(["documents"])
        tree = view.hierarchy_view.tree
        documents = tree.get_children()[0]
        tree.open_item(documents)
        self.finish_callbacks(root)
        rows = self.children_by_name(tree, documents)
        self.assertEqual(
            [tree.item(item, "values") for item in rows.values()],
            [("File", ""), ("Folder", "Empty folder"), ("Symbolic link", ""), ("Other", "")],
        )
        for item in rows.values():
            self.assertEqual(tree.get_children(item), ())
            tree.open_item(item)
        self.assertEqual(root.callbacks, {})
        self.scanner.assert_called_once()

    def test_view_batches_wide_folders_fairly_and_reuses_the_worker_index(self):
        wide = [self.record(f"/fixture/Documents/item-{i}") for i in range(250)]
        small = [self.record(f"/fixture/Desktop/photo-{i}") for i in range(3)]
        self.scanner.side_effect = [wide, small]
        with mock.patch("file_chisel.folder_selection.build_hierarchy", wraps=build_hierarchy) as build:
            root, view = self.scan_in_view(["documents", "desktop"])
            tree = view.hierarchy_view.tree
            roots = self.children_by_name(tree)
            documents, desktop = roots["Documents"], roots["Desktop"]
            tree.open_item(documents)
            tree.open_item(desktop)
            tree.open_item(documents)
            self.assertEqual(len(root.callbacks), 1)
            root.fire_next()
            partial = self.children_by_name(tree, documents)
            self.assertLess(len(partial), len(wide))
            self.assertGreater(len(partial), 1)
            self.assertEqual(list(self.children_by_name(tree, desktop)), [entry.name for entry in small])
            self.assertEqual(len(root.callbacks), 1)
            self.finish_callbacks(root)
            self.assertEqual(list(self.children_by_name(tree, documents)), [entry.name for entry in wide])
            before = tree.get_children(documents)
            tree.item(documents, open=False)
            tree.open_item(documents)
            view._render()
            view._render()
            self.assertEqual(tree.get_children(documents), before)
            self.assertEqual(root.callbacks, {})
            build.assert_called_once_with(self.controller.inventory)

    def test_selection_change_invalidates_pending_and_late_hierarchy_callbacks(self):
        self.scanner.return_value = [self.record("/fixture/Documents/item")]
        root, view = self.scan_in_view(["documents"])
        tree = view.hierarchy_view.tree
        tree.open_item(tree.get_children()[0])
        stale_callback = next(iter(root.callbacks.values()))
        view.variables["desktop"].set(True)
        view._selection_changed("desktop")
        self.assertEqual(root.callbacks, {})
        self.assertEqual(tree.get_children(), ())
        self.assertIsNone(self.controller.hierarchy)
        stale_callback()
        self.assertEqual(tree.get_children(), ())
        self.scanner.assert_called_once()

        # A late old batch must not cancel or populate a new snapshot's batch.
        self.scanner.side_effect = [[self.record("/fixture/Documents/new-item")], []]
        view.start_scan()
        self.workers[-1].finish()
        root.fire_next()
        documents = self.children_by_name(tree)["Documents"]
        tree.open_item(documents)
        pending = dict(root.callbacks)
        stale_callback()
        self.assertEqual(root.callbacks, pending)
        self.finish_callbacks(root)
        self.assertEqual(list(self.children_by_name(tree, documents)), ["new-item"])

    def test_new_scan_and_start_failure_clear_a_previously_displayed_hierarchy(self):
        for fail_start in (False, True):
            with self.subTest(fail_start=fail_start):
                self.scanner.return_value = [self.record("/fixture/Documents/item")]
                root, view = self.scan_in_view(["documents"])
                tree = view.hierarchy_view.tree
                tree.open_item(tree.get_children()[0])
                stale_callback = next(iter(root.callbacks.values()))
                if fail_start:
                    with mock.patch("file_chisel.folder_selection.Thread") as thread:
                        thread.return_value.start.side_effect = RuntimeError("cannot start")
                        view.start_scan()
                    self.assertEqual(root.callbacks, {})
                    self.assertTrue(self.controller.can_scan)
                else:
                    self.scanner.side_effect = RuntimeError("unexpected scan failure")
                    view.start_scan()
                    self.workers[-1].finish()
                    root.fire_next()
                    self.scanner.side_effect = None
                    self.assertIn("unexpected error", self.controller.status)
                self.assertEqual(tree.get_children(), ())
                self.assertIsNone(self.controller.inventory)
                self.assertIsNone(self.controller.hierarchy)
                stale_callback()
                self.assertEqual(tree.get_children(), ())

    def test_close_cancels_hierarchy_batches_and_late_events_do_not_touch_widgets(self):
        self.scanner.return_value = [self.record("/fixture/Documents/item")]
        root, view = self.scan_in_view(["documents"])
        tree = view.hierarchy_view.tree
        tree.open_item(tree.get_children()[0])
        stale_callback = next(iter(root.callbacks.values()))
        view.close()
        self.assertEqual(root.callbacks, {})
        self.assertTrue(root.destroyed)
        self.assertIsNone(self.controller.hierarchy)
        stale_callback()
        view.hierarchy_view._opened()
        view.hierarchy_view.close()

    def test_main_accepts_a_fixture_home_and_cleans_up_its_window(self):
        root = FakeRoot()
        fake_tk = SimpleNamespace(
            BooleanVar=FakeVariable, Tk=mock.Mock(return_value=root),
            TclError=RuntimeError, ttk=FAKE_TTK,
        )
        with mock.patch.dict(sys.modules, {"tkinter": fake_tk, "tkinter.ttk": FAKE_TTK}), \
                mock.patch("file_chisel.app.ApplicationController", wraps=ApplicationController) as factory:
            main(Path("/fixture/custom-home"))
        factory.assert_called_once_with(Path("/fixture/custom-home"))
        self.assertTrue(root.destroyed)

    def test_fresh_import_works_without_tk_or_home_access(self):
        script = """
import builtins
from pathlib import Path
from file_chisel import scanner

def forbidden(*args, **kwargs):
    raise AssertionError('import accessed a home folder or started scanning')

scanner.scan_directory = forbidden
Path.home = forbidden
original_import = builtins.__import__
def without_tk(name, *args, **kwargs):
    if name == 'tkinter' or name.startswith('tkinter.'):
        raise ModuleNotFoundError('Tkinter deliberately unavailable')
    return original_import(name, *args, **kwargs)

builtins.__import__ = without_tk
from file_chisel import folder_selection, app
controller = app.ApplicationController(home=Path('/fixture'))
assert not controller.can_scan
controller.close()
"""
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).absolute().parents[1] / "src")
        result = subprocess.run(
            [sys.executable, "-c", script], env=environment,
            text=True, capture_output=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
