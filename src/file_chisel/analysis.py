"""Read-only analysis of scanner-produced filesystem metadata."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath
from typing import Iterable, Tuple

from file_chisel.scanner import FileSystemEntry


_COPY_SUFFIX = re.compile(r"\s+(?:\([1-9]\d*\)|copy(?:\s+[1-9]\d*)?)$")


@dataclass(frozen=True)
class LikelyDuplicateGroup:
    """Files whose names and sizes suggest, but do not prove, duplication."""

    normalized_name: str
    size: int
    files: Tuple[FileSystemEntry, ...]


def normalize_filename(filename: str) -> str:
    """Return a case-insensitive filename with one copy suffix removed.

    A suffix is recognized only at the end of the stem, after whitespace and
    a nonempty base.  The final extension remains part of the result.
    """

    casefolded = unicodedata.normalize("NFC", filename.casefold())
    extension = PurePath(casefolded).suffix
    stem = casefolded[: -len(extension)] if extension else casefolded
    match = _COPY_SUFFIX.search(stem)
    if match is not None and match.start() > 0:
        stem = stem[: match.start()]
    return stem + extension


def find_empty_directories(
    entries: Iterable[FileSystemEntry],
) -> Tuple[FileSystemEntry, ...]:
    """Return inventoried directories that have no inventoried direct child."""

    inventory = tuple(entries)
    parent_paths = {entry.path.parent for entry in inventory}
    empty_directories = (
        entry
        for entry in inventory
        if entry.entry_type == "directory" and entry.path not in parent_paths
    )
    return tuple(sorted(empty_directories, key=_entry_sort_key))


def find_likely_duplicate_files(
    entries: Iterable[FileSystemEntry],
) -> Tuple[LikelyDuplicateGroup, ...]:
    """Group regular files with matching sizes and normalized filenames."""

    candidates = {}
    for entry in tuple(entries):
        if entry.entry_type != "file":
            continue
        key = (normalize_filename(entry.name), entry.size)
        candidates.setdefault(key, []).append(entry)

    groups = [
        LikelyDuplicateGroup(
            normalized_name=normalized_name,
            size=size,
            files=tuple(sorted(files, key=_entry_sort_key)),
        )
        for (normalized_name, size), files in candidates.items()
        if len(files) > 1
    ]
    return tuple(
        sorted(
            groups,
            key=lambda group: (
                group.normalized_name,
                group.size,
                tuple(_entry_sort_key(entry) for entry in group.files),
            ),
        )
    )


def _entry_sort_key(entry: FileSystemEntry) -> tuple:
    """Return a total, stable ordering key for an inventory record."""

    return (
        str(entry.path),
        entry.name,
        entry.entry_type,
        entry.size,
        entry.modified_time,
    )
