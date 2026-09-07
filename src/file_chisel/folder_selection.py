"""Select known home folders and coordinate read-only scans without a GUI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Callable, Iterable

from file_chisel.inventory import ScanInventory, build_inventory
from file_chisel.scanner import FileSystemEntry, InvalidScanRootError, scan_directory


@dataclass(frozen=True)
class FolderOption:
    key: str
    label: str
    path: Path


def folder_options(home: Path | None = None) -> tuple[FolderOption, ...]:
    """Build the three absolute paths without checking or following them."""

    home_path = Path(os.path.abspath(Path.home() if home is None else home))
    return tuple(
        FolderOption(label.lower(), label, home_path / label)
        for label in ("Documents", "Downloads", "Desktop")
    )


class NoFoldersSelectedError(ValueError):
    """A scan needs at least one explicitly selected folder."""


class ScanAlreadyRunningError(RuntimeError):
    """Another scan is running or its completion has not been collected."""


class ScanClosedError(RuntimeError):
    """The runner has been closed and cannot accept another scan."""


class _ScanCancelled(Exception):
    """Internal signal to stop between roots after window shutdown."""


class ScanErrorKind(Enum):
    INVALID_ROOT = "invalid_root"
    FILESYSTEM = "filesystem"


@dataclass(frozen=True)
class RootScanFailure:
    root: Path
    kind: ScanErrorKind
    message: str


@dataclass(frozen=True)
class RootScanSuccess:
    root: Path
    entries: tuple[FileSystemEntry, ...]

    @property
    def entry_count(self) -> int:
        return len(self.entries)


class BatchStatus(Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class BatchScanResult:
    successes: tuple[RootScanSuccess, ...]
    failures: tuple[RootScanFailure, ...]

    def __post_init__(self) -> None:
        if not self.successes and not self.failures:
            raise ValueError("A batch must contain at least one root outcome.")

    @property
    def status(self) -> BatchStatus:
        if not self.failures:
            return BatchStatus.SUCCESS
        if self.successes:
            return BatchStatus.PARTIAL
        return BatchStatus.FAILED


class FolderScanCoordinator:
    """Delegate selected roots to the existing scanner in a fixed order."""

    def __init__(
        self,
        home: Path | None = None,
        scanner: Callable[[Path], list[FileSystemEntry]] | None = None,
    ) -> None:
        self.options = folder_options(home)
        self._scanner = scan_directory if scanner is None else scanner

    def selected_options(self, selected_keys: Iterable[str]) -> tuple[FolderOption, ...]:
        keys = frozenset(selected_keys)
        if not keys:
            raise NoFoldersSelectedError("Select at least one folder to scan.")
        if keys - {option.key for option in self.options}:
            raise ValueError("Selection contains an unknown folder.")
        return tuple(option for option in self.options if option.key in keys)

    def scan_selected(
        self,
        selected_keys: Iterable[str],
        *,
        stop_event: Event | None = None,
    ) -> BatchScanResult:
        """Return complete per-root outcomes; cancellation is checked between roots.

        An active scan_directory call cannot be interrupted here. If shutdown
        was requested while it ran, the next root will not be started.
        """

        successes = []
        failures = []
        for option in self.selected_options(selected_keys):
            if stop_event is not None and stop_event.is_set():
                raise _ScanCancelled()
            try:
                records = self._scanner(option.path)
            except InvalidScanRootError:
                failures.append(RootScanFailure(
                    option.path,
                    ScanErrorKind.INVALID_ROOT,
                    "Folder is missing, is not a directory, or is a symbolic link.",
                ))
            except OSError as error:
                # Raw exception strings can include private descendant filenames.
                message = (
                    "Permission denied. Check access to this folder."
                    if isinstance(error, PermissionError)
                    else "A filesystem error prevented a complete scan."
                )
                failures.append(RootScanFailure(
                    option.path, ScanErrorKind.FILESYSTEM, message,
                ))
            else:
                successes.append(RootScanSuccess(option.path, tuple(records)))
        return BatchScanResult(tuple(successes), tuple(failures))


class ScanRunState(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    CLOSED = "closed"


@dataclass(frozen=True)
class ScanCompletion:
    result: BatchScanResult | None = None
    inventory: ScanInventory | None = None
    error: Exception | None = None

    def __post_init__(self) -> None:
        has_success = self.result is not None or self.inventory is not None
        if has_success == (self.error is not None):
            raise ValueError("Completion needs exactly one result or error.")
        if (self.result is None) != (self.inventory is None):
            raise ValueError("A successful completion needs both result and inventory.")


class FolderScanRunner:
    """Deliver one worker's result through polling, without calling Tk APIs."""

    def __init__(self, coordinator: FolderScanCoordinator) -> None:
        self.options = coordinator.options
        self._coordinator = coordinator
        self._state = ScanRunState.IDLE
        self._lock = Lock()
        self._stop = Event()
        self._completions: Queue[ScanCompletion] = Queue()

    @property
    def state(self) -> ScanRunState:
        with self._lock:
            return self._state

    def start(self, selected_keys: Iterable[str]) -> None:
        # Snapshot and validate synchronously before creating a worker.
        options = self._coordinator.selected_options(selected_keys)
        keys = tuple(option.key for option in options)
        with self._lock:
            if self._state is ScanRunState.CLOSED:
                raise ScanClosedError("The scan window is closed.")
            if self._state is ScanRunState.SCANNING:
                raise ScanAlreadyRunningError("A scan is already running.")
            self._state = ScanRunState.SCANNING
            worker = Thread(target=self._run, args=(keys,), daemon=True)
            try:
                worker.start()
            except Exception:
                self._state = ScanRunState.IDLE
                raise

    def _run(self, keys: tuple[str, ...]) -> None:
        try:
            result = self._coordinator.scan_selected(keys, stop_event=self._stop)
            inventory = build_inventory(result)
        except _ScanCancelled:
            return
        except Exception as error:
            completion = ScanCompletion(error=error)
        else:
            completion = ScanCompletion(result=result, inventory=inventory)
        with self._lock:
            if self._state is not ScanRunState.CLOSED:
                self._completions.put_nowait(completion)

    def poll_completion(self) -> ScanCompletion | None:
        with self._lock:
            if self._state is ScanRunState.CLOSED:
                return None
            try:
                completion = self._completions.get_nowait()
            except Empty:
                return None
            self._state = ScanRunState.IDLE
            return completion

    def close(self) -> None:
        """Suppress results and further roots, without waiting for an active scan."""

        with self._lock:
            self._state = ScanRunState.CLOSED
            self._stop.set()
            while True:
                try:
                    self._completions.get_nowait()
                except Empty:
                    break
