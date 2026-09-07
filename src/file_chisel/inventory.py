"""Immutable snapshots built from completed metadata-only scans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from file_chisel.analysis import (
    LikelyDuplicateGroup,
    find_empty_directories,
    find_likely_duplicate_files,
)
from file_chisel.scanner import FileSystemEntry

if TYPE_CHECKING:
    from file_chisel.folder_selection import (
        BatchScanResult,
        RootScanFailure,
        RootScanSuccess,
    )


@dataclass(frozen=True)
class ScanInventory:
    """One completed scan organized for later display, summary, and export."""

    successful_roots: tuple[RootScanSuccess, ...]
    failed_roots: tuple[RootScanFailure, ...]
    entries: tuple[FileSystemEntry, ...]
    empty_directories: tuple[FileSystemEntry, ...]
    likely_duplicate_groups: tuple[LikelyDuplicateGroup, ...]


def build_inventory(result: BatchScanResult) -> ScanInventory:
    """Build a metadata-only snapshot from one completed batch result."""

    successful_roots = tuple(result.successes)
    failed_roots = tuple(result.failures)
    entries = tuple(
        entry
        for root_result in successful_roots
        for entry in root_result.entries
    )
    return ScanInventory(
        successful_roots=successful_roots,
        failed_roots=failed_roots,
        entries=entries,
        empty_directories=find_empty_directories(entries),
        likely_duplicate_groups=find_likely_duplicate_files(entries),
    )
