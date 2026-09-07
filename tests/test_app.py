"""Headless controller tests and Tk event wiring tested with small fakes."""

import os
import subprocess
import sys
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

    def assert_live(self):
        if self.destroyed:
            raise AssertionError("widget access after destruction")
        if threading.get_ident() != self.owner:
            raise AssertionError("widget access outside GUI thread")

    def title(self, *args, **kwargs):
        self.assert_live()

    minsize = columnconfigure = rowconfigure = protocol = mainloop = title

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

    columnconfigure = grid

    def configure(self, **kwargs):
        self.root.assert_live()
        self.options.update(kwargs)


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


FAKE_TK = SimpleNamespace(BooleanVar=FakeVariable)
FAKE_TTK = SimpleNamespace(
    Frame=FakeWidget, Label=FakeWidget, Checkbutton=FakeWidget, Button=FakeWidget,
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
