"""Summary calculations derived from scanned metadata."""

from typing import Iterable

from file_chisel.scanner import FileSystemEntry


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
