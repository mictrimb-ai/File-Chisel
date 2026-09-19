"""Immutable summaries derived from scanner-produced metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import Iterable

from file_chisel.analysis import LikelyDuplicateGroup
from file_chisel.scanner import FileSystemEntry


LARGE_FILE_THRESHOLD = 100 * 1024 * 1024


@dataclass(frozen=True)
class FileTypeSummary:
    """Regular files sharing one case-insensitive final extension."""

    extension: str
    file_count: int
    total_size: int


@dataclass(frozen=True)
class InventorySummary:
    """A compact, metadata-only description of one scan inventory."""

    file_count: int
    directory_count: int
    total_file_size: int
    file_types: tuple[FileTypeSummary, ...]
    large_file_count: int
    empty_directory_count: int
    likely_duplicate_group_count: int
    potential_duplicate_size: int
    failed_root_count: int


def calculate_file_totals(
    entries: Iterable[FileSystemEntry],
) -> tuple[int, int]:
    """Return the regular-file count and their combined byte size."""

    file_count = 0
    total_size = 0

    for entry in entries:
        if entry.entry_type == "file":
            file_count += 1
            total_size += entry.size

    return file_count, total_size


def build_summary(
    entries: Iterable[FileSystemEntry],
    *,
    empty_directories: Iterable[FileSystemEntry] = (),
    likely_duplicate_groups: Iterable[LikelyDuplicateGroup] = (),
    failed_root_count: int = 0,
) -> InventorySummary:
    """Build a deterministic summary without accessing the filesystem."""

    inventory = tuple(entries)
    duplicate_groups = tuple(likely_duplicate_groups)
    file_count, total_file_size = calculate_file_totals(inventory)
    files = tuple(entry for entry in inventory if entry.entry_type == "file")

    by_extension: dict[str, list[int]] = {}
    for entry in files:
        extension = PurePath(entry.name).suffix.casefold()
        totals = by_extension.setdefault(extension, [0, 0])
        totals[0] += 1
        totals[1] += entry.size

    file_types = tuple(sorted(
        (
            FileTypeSummary(extension, totals[0], totals[1])
            for extension, totals in by_extension.items()
        ),
        key=lambda item: (-item.total_size, -item.file_count, item.extension),
    ))
    return InventorySummary(
        file_count=file_count,
        directory_count=sum(
            entry.entry_type == "directory" for entry in inventory
        ),
        total_file_size=total_file_size,
        file_types=file_types,
        large_file_count=sum(
            entry.size >= LARGE_FILE_THRESHOLD for entry in files
        ),
        empty_directory_count=sum(1 for _ in empty_directories),
        likely_duplicate_group_count=len(duplicate_groups),
        potential_duplicate_size=sum(
            group.size * (len(group.files) - 1) for group in duplicate_groups
        ),
        failed_root_count=failed_root_count,
    )


def format_bytes(size: int) -> str:
    """Format a nonnegative byte count for the compact GUI summary."""

    if size < 0:
        raise ValueError("Byte size cannot be negative.")
    value = float(size)
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"


def format_summary_lines(
    summary: InventorySummary,
    *,
    maximum_file_types: int = 5,
) -> tuple[str, ...]:
    """Return short lines suitable for the inventory summary label."""

    if maximum_file_types < 0:
        raise ValueError("Maximum file types cannot be negative.")
    type_items = [
        f"{item.extension or 'No extension'}: "
        f"{item.file_count} {pluralize(item.file_count, 'file')} · "
        f"{format_bytes(item.total_size)}"
        for item in summary.file_types[:maximum_file_types]
    ]
    if len(summary.file_types) > maximum_file_types:
        type_items.append(f"+{len(summary.file_types) - maximum_file_types} more")
    file_types = "; ".join(type_items) if type_items else "None"
    duplicate_detail = (
        f" · up to {format_bytes(summary.potential_duplicate_size)}"
        if summary.likely_duplicate_group_count else ""
    )
    return (
        f"{summary.file_count} {pluralize(summary.file_count, 'file')} · "
        f"{summary.directory_count} {pluralize(summary.directory_count, 'folder')} · "
        f"{format_bytes(summary.total_file_size)}",
        f"File types: {file_types}",
        f"Needs attention: {summary.large_file_count} large "
        f"{pluralize(summary.large_file_count, 'file')} (100 MB+) · "
        f"{summary.empty_directory_count} empty "
        f"{pluralize(summary.empty_directory_count, 'folder')} · "
        f"{summary.likely_duplicate_group_count} likely duplicate "
        f"{pluralize(summary.likely_duplicate_group_count, 'group')}"
        f"{duplicate_detail} · {summary.failed_root_count} failed "
        f"{pluralize(summary.failed_root_count, 'folder')}",
    )


def pluralize(count: int, singular: str) -> str:
    """Return a regular singular or plural label."""

    return singular if count == 1 else singular + "s"
