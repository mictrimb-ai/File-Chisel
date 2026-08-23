"""Read-only filesystem inventory support."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import List, Union


class InvalidScanRootError(ValueError):
    """Raised when a scan root is not an existing, real directory."""


@dataclass(frozen=True)
class FileSystemEntry:
    """Metadata collected for one filesystem entry."""

    name: str
    path: Path
    entry_type: str
    size: int
    modified_time: float


def scan_directory(
    root: Union[str, os.PathLike[str]],
) -> List[FileSystemEntry]:
    """Return a metadata-only inventory of the descendants of *root*.

    Traversal is depth-first and pre-order, with sibling entries sorted by
    name. Symbolic links are inventoried but never followed. Returned paths
    are absolute, produced lexically rather than by resolving links.

    Args:
        root: An existing directory to inventory.

    Raises:
        InvalidScanRootError: If *root* is missing, a symbolic link, or not a
            directory.
        OSError: If another filesystem error prevents a complete scan.
    """

    root_path = Path(os.path.abspath(os.fspath(root)))

    try:
        root_metadata = os.stat(root_path, follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise InvalidScanRootError(
            f"Scan root must be an existing directory: {root_path}"
        ) from error

    if not stat.S_ISDIR(root_metadata.st_mode):
        raise InvalidScanRootError(
            f"Scan root must be an existing directory: {root_path}"
        )

    records: List[FileSystemEntry] = []
    _scan_descendants(root_path, records)
    return records


def _scan_descendants(
    directory: Path,
    records: List[FileSystemEntry],
) -> None:
    """Append descendants of *directory* in deterministic DFS pre-order."""

    with os.scandir(directory) as iterator:
        entries = sorted(iterator, key=lambda entry: entry.name)

    for entry in entries:
        metadata = entry.stat(follow_symlinks=False)
        entry_type = _entry_type(metadata.st_mode)
        entry_path = Path(entry.path)
        records.append(
            FileSystemEntry(
                name=entry.name,
                path=entry_path,
                entry_type=entry_type,
                size=metadata.st_size,
                modified_time=metadata.st_mtime,
            )
        )

        if entry_type == "directory":
            _scan_descendants(entry_path, records)


def _entry_type(mode: int) -> str:
    """Classify a mode obtained without following symbolic links."""

    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    return "other"
