"""Compare saved scan metadata with a fresh, read-only scan of the same roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from file_chisel.inventory import ScanInventory
from file_chisel.scanner import FileSystemEntry, InvalidScanRootError, scan_directory


@dataclass(frozen=True)
class LiveFinding:
    root: str
    relative_path: str
    kind: str  # appeared, disappeared, or metadata_changed


@dataclass(frozen=True)
class LiveCheckResult:
    checked_roots: int
    findings: tuple[LiveFinding, ...]
    unavailable_roots: tuple[str, ...]

    @property
    def matches(self) -> bool:
        return self.checked_roots > 0 and not self.findings and not self.unavailable_roots


def _metadata(entry: FileSystemEntry) -> tuple:
    # Directory size and mtime vary with their children; compare its type only.
    if entry.entry_type == "directory":
        return (entry.entry_type,)
    return entry.entry_type, entry.size, entry.modified_time


def check_current_folders(
    inventory: ScanInventory,
    scanner: Callable[[Path], list[FileSystemEntry]] = scan_directory,
) -> LiveCheckResult:
    """Report drift at check time, without opening contents or changing the disk.

    Failed roots were never known and are outside the proposal. A scan error is
    uncertainty, never a clean match. This is not an execution-time guarantee.
    """

    findings = []
    unavailable = []
    for success in inventory.successful_roots:
        root = success.root
        try:
            current = scanner(root)
        except (InvalidScanRootError, OSError):
            unavailable.append(root.name)
            continue

        before = {entry.path.relative_to(root): entry for entry in success.entries}
        after = {entry.path.relative_to(root): entry for entry in current}
        for relative_path in sorted(before.keys() | after.keys()):
            if relative_path not in before:
                kind = "appeared"
            elif relative_path not in after:
                kind = "disappeared"
            elif _metadata(before[relative_path]) != _metadata(after[relative_path]):
                kind = "metadata_changed"
            else:
                continue
            findings.append(LiveFinding(root.name, relative_path.as_posix(), kind))

    return LiveCheckResult(len(inventory.successful_roots), tuple(findings), tuple(unavailable))
