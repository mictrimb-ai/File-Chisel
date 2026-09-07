"""Selection, real scanner delegation, and deterministic worker lifecycle tests."""

import builtins
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from file_chisel.folder_selection import (
    BatchStatus,
    FolderScanCoordinator,
    FolderScanRunner,
    NoFoldersSelectedError,
    ScanAlreadyRunningError,
    ScanClosedError,
    ScanErrorKind,
    ScanRunState,
    folder_options,
)
from file_chisel.scanner import FileSystemEntry, InvalidScanRootError


class FolderSelectionTests(unittest.TestCase):
    def test_options_are_absolute_ordered_and_do_not_inspect_folders(self):
        forbidden = AssertionError("folder options inspected the filesystem")
        with mock.patch.object(Path, "home", return_value=Path("/fixture/home")), \
                mock.patch.object(Path, "resolve", side_effect=forbidden), \
                mock.patch.object(Path, "stat", side_effect=forbidden), \
                mock.patch.object(os, "scandir", side_effect=forbidden):
            options = folder_options()
            relative = folder_options(Path("fixture"))
        self.assertEqual(
            [(item.key, item.label, item.path) for item in options],
            [
                ("documents", "Documents", Path("/fixture/home/Documents")),
                ("downloads", "Downloads", Path("/fixture/home/Downloads")),
                ("desktop", "Desktop", Path("/fixture/home/Desktop")),
            ],
        )
        self.assertTrue(all(item.path.is_absolute() for item in relative))

    def test_empty_and_unknown_selections_never_call_the_scanner(self):
        scanner = mock.Mock()
        coordinator = FolderScanCoordinator(Path("/fixture"), scanner)
        with self.assertRaises(NoFoldersSelectedError):
            coordinator.scan_selected([])
        with self.assertRaises(ValueError):
            coordinator.scan_selected(["documents", "elsewhere"])
        scanner.assert_not_called()

    def test_subset_is_scanned_once_in_canonical_order(self):
        scanner = mock.Mock(return_value=[])
        coordinator = FolderScanCoordinator(Path("/fixture"), scanner)
        result = coordinator.scan_selected(iter(["desktop", "documents", "desktop"]))
        self.assertEqual(scanner.call_args_list, [
            mock.call(Path("/fixture/Documents")), mock.call(Path("/fixture/Desktop")),
        ])
        self.assertEqual(result.status, BatchStatus.SUCCESS)
        self.assertEqual([item.entry_count for item in result.successes], [0, 0])

    def test_success_preserves_records_as_an_immutable_inventory(self):
        record = FileSystemEntry(
            "private.txt", Path("/fixture/Documents/private.txt"), "file", 4, 1.0,
        )
        records = [record]
        coordinator = FolderScanCoordinator(Path("/fixture"), mock.Mock(return_value=records))
        result = coordinator.scan_selected(["documents"])
        records.clear()
        self.assertEqual(result.successes[0].entries, (record,))
        self.assertEqual(result.successes[0].entry_count, 1)

    def test_real_scanner_distinguishes_empty_missing_file_and_symlink_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "Documents").mkdir()
            coordinator = FolderScanCoordinator(home)
            with mock.patch.object(Path, "resolve", side_effect=AssertionError("followed link")):
                result = coordinator.scan_selected(["documents", "downloads"])
            self.assertEqual(result.status, BatchStatus.PARTIAL)
            self.assertEqual(result.successes[0].entry_count, 0)
            self.assertEqual(result.failures[0].kind, ScanErrorKind.INVALID_ROOT)
            self.assertEqual(result.failures[0].root, home / "Downloads")

            (home / "Desktop").write_bytes(b"fixture")
            result = coordinator.scan_selected(["desktop"])
            self.assertEqual(result.status, BatchStatus.FAILED)
            self.assertEqual(result.failures[0].kind, ScanErrorKind.INVALID_ROOT)

            (home / "Downloads").symlink_to(home / "Documents", target_is_directory=True)
            with mock.patch.object(Path, "resolve", side_effect=AssertionError("followed link")):
                result = coordinator.scan_selected(["downloads"])
            self.assertEqual(result.status, BatchStatus.FAILED)
            self.assertEqual(result.failures[0].kind, ScanErrorKind.INVALID_ROOT)

    def test_partial_failure_preserves_both_nonempty_and_empty_success(self):
        record = FileSystemEntry("item", Path("/fixture/Documents/item"), "file", 0, 1.0)
        scanner = mock.Mock(side_effect=[
            [record], PermissionError(13, "Permission denied", "/private/secret.txt"), [],
        ])
        result = FolderScanCoordinator(Path("/fixture"), scanner).scan_selected(
            ["desktop", "downloads", "documents"]
        )
        self.assertEqual(result.status, BatchStatus.PARTIAL)
        self.assertEqual([item.entry_count for item in result.successes], [1, 0])
        self.assertEqual(result.failures[0].root, Path("/fixture/Downloads"))
        self.assertEqual(result.failures[0].kind, ScanErrorKind.FILESYSTEM)
        self.assertIn("Permission denied", result.failures[0].message)
        self.assertNotIn("secret", result.failures[0].message)
        self.assertFalse(hasattr(result.failures[0], "entry_count"))

    def test_all_failed_roots_are_explicit_failures(self):
        scanner = mock.Mock(side_effect=[InvalidScanRootError("missing"), OSError("I/O")])
        result = FolderScanCoordinator(Path("/fixture"), scanner).scan_selected(
            ["documents", "downloads"]
        )
        self.assertEqual(result.status, BatchStatus.FAILED)
        self.assertEqual(result.successes, ())
        self.assertEqual([item.kind for item in result.failures], [
            ScanErrorKind.INVALID_ROOT, ScanErrorKind.FILESYSTEM,
        ])

    def test_programming_error_is_not_mislabeled_as_a_filesystem_failure(self):
        coordinator = FolderScanCoordinator(Path("/fixture"), mock.Mock(side_effect=TypeError("bug")))
        with self.assertRaises(TypeError):
            coordinator.scan_selected(["documents"])

    def test_coordination_only_delegates_and_does_not_read_contents_or_traverse(self):
        scanner = mock.Mock(return_value=[])
        forbidden = AssertionError("coordinator performed filesystem access")
        with mock.patch.object(builtins, "open", side_effect=forbidden), \
                mock.patch.object(Path, "open", side_effect=forbidden), \
                mock.patch.object(Path, "resolve", side_effect=forbidden), \
                mock.patch.object(os, "stat", side_effect=forbidden), \
                mock.patch.object(os, "scandir", side_effect=forbidden):
            coordinator = FolderScanCoordinator(Path("/fixture"), scanner)
            coordinator.scan_selected(["downloads"])
        scanner.assert_called_once_with(Path("/fixture/Downloads"))


class FolderScanRunnerTests(unittest.TestCase):
    def setUp(self):
        self.workers = []

        def make_thread(*args, **kwargs):
            worker = threading.Thread(*args, **kwargs)
            self.workers.append(worker)
            return worker

        patcher = mock.patch("file_chisel.folder_selection.Thread", side_effect=make_thread)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.finish_workers)

    def finish_workers(self):
        for worker in self.workers:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), "test worker failed to finish")

    def make_runner(self, scanner):
        runner = FolderScanRunner(FolderScanCoordinator(Path("/fixture"), scanner))
        self.addCleanup(runner.close)
        return runner

    def test_initialization_and_invalid_start_do_not_launch_work(self):
        scanner = mock.Mock()
        runner = self.make_runner(scanner)
        self.assertEqual(runner.state, ScanRunState.IDLE)
        self.assertIsNone(runner.poll_completion())
        with self.assertRaises(NoFoldersSelectedError):
            runner.start([])
        with self.assertRaises(ValueError):
            runner.start(["unknown"])
        self.assertEqual(self.workers, [])
        scanner.assert_not_called()

    def test_busy_rejection_snapshot_and_nonblocking_poll(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        called = []
        worker_ids = []

        def scan(root):
            called.append(root)
            worker_ids.append(threading.get_ident())
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("test did not release scanner")
            return []

        runner = self.make_runner(scan)
        keys = ["documents", "desktop"]
        runner.start(keys)
        self.assertTrue(entered.wait(timeout=2))
        keys[:] = ["downloads"]
        self.assertIsNone(runner.poll_completion())
        with self.assertRaises(ScanAlreadyRunningError):
            runner.start(["downloads"])
        self.assertEqual(len(self.workers), 1)
        release.set()
        self.finish_workers()
        # A completed but uncollected result still excludes overlapping starts.
        with self.assertRaises(ScanAlreadyRunningError):
            runner.start(["downloads"])
        completion = runner.poll_completion()
        self.assertEqual(completion.result.status, BatchStatus.SUCCESS)
        self.assertEqual(called, [Path("/fixture/Documents"), Path("/fixture/Desktop")])
        self.assertTrue(all(ident != threading.get_ident() for ident in worker_ids))
        self.assertEqual(runner.state, ScanRunState.IDLE)
        self.assertIsNone(runner.poll_completion())

    def test_unexpected_worker_failure_is_delivered_and_a_later_run_succeeds(self):
        scanner = mock.Mock(side_effect=[RuntimeError("unexpected"), []])
        runner = self.make_runner(scanner)
        runner.start(["documents"])
        self.finish_workers()
        completion = runner.poll_completion()
        self.assertIsInstance(completion.error, RuntimeError)
        self.assertIsNone(completion.result)
        runner.start(["downloads"])
        self.finish_workers()
        self.assertEqual(runner.poll_completion().result.status, BatchStatus.SUCCESS)

    def test_close_during_scan_does_not_join_or_start_another_root(self):
        for fail_current_root in (False, True):
            with self.subTest(fail_current_root=fail_current_root):
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                called = []

                def scan(root):
                    called.append(root)
                    entered.set()
                    if not release.wait(timeout=2):
                        raise AssertionError("test did not release scanner")
                    if fail_current_root:
                        raise PermissionError("denied")
                    return []

                runner = self.make_runner(scan)
                runner.start(["documents", "downloads", "desktop"])
                self.assertTrue(entered.wait(timeout=2))
                worker = self.workers[-1]
                self.assertTrue(worker.daemon)
                with mock.patch.object(worker, "join", side_effect=AssertionError("close joined")):
                    runner.close()
                    runner.close()
                self.assertEqual(runner.state, ScanRunState.CLOSED)
                with self.assertRaises(ScanClosedError):
                    runner.start(["documents"])
                release.set()
                self.finish_workers()
                self.assertEqual(called, [Path("/fixture/Documents")])
                self.assertIsNone(runner.poll_completion())

    def test_close_discards_an_already_queued_completion(self):
        runner = self.make_runner(mock.Mock(return_value=[]))
        runner.start(["documents"])
        self.finish_workers()
        runner.close()
        self.assertIsNone(runner.poll_completion())
        self.assertEqual(runner.state, ScanRunState.CLOSED)

    def test_thread_start_failure_restores_idle_state(self):
        scanner = mock.Mock(return_value=[])
        runner = self.make_runner(scanner)
        with mock.patch("file_chisel.folder_selection.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("cannot start thread")
            with self.assertRaises(RuntimeError):
                runner.start(["documents"])
        self.assertEqual(runner.state, ScanRunState.IDLE)
        scanner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
