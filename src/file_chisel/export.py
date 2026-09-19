"""Privacy-conscious JSON export of completed scan metadata."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import closing
from pathlib import Path

from file_chisel.inventory import ScanInventory
from file_chisel.scanner import FileSystemEntry

EXPORT_ROOTS = frozenset(("Documents", "Downloads", "Desktop"))


def build_entry_record(
    root: Path,
    entry: FileSystemEntry,
) -> dict[str, object]:
    """Return one root-relative metadata record for export."""

    relative_path = entry.path.relative_to(root)
    if relative_path == Path(".") or ".." in relative_path.parts:
        raise ValueError("Inventory entry is not a descendant of its scan root.")

    return {
        "root": root.name,
        "relative_path": relative_path.as_posix(),
        "type": entry.entry_type,
        "size": entry.size,
    }


def build_export_document(inventory: ScanInventory) -> dict[str, object]:
    """Convert one snapshot to portable metadata without filesystem access."""

    roots = list(
        ((result.root, "success", None) for result in inventory.successful_roots),
    ) + list(
        ((failure.root, "failed", failure.kind.value)
         for failure in inventory.failed_roots),
    )
    if any(root.name not in EXPORT_ROOTS for root, _, _ in roots):
        raise ValueError("Only selected Documents, Downloads, or Desktop scans can be exported.")
    if len({root.name for root, _, _ in roots}) != len(roots):
        raise ValueError("Scan roots must have distinct names for export.")

    records = []
    locations = {}
    for result in inventory.successful_roots:
        for entry in result.entries:
            record = build_entry_record(result.root, entry)
            if entry in locations:
                raise ValueError("One entry belongs to more than one scan root.")
            locations[entry] = {
                "root": record["root"],
                "relative_path": record["relative_path"],
            }
            records.append(record)

    def location(entry: FileSystemEntry) -> dict[str, str]:
        try:
            return locations[entry]
        except KeyError as error:
            raise ValueError("Analysis references an entry outside the scan.") from error

    def location_key(item: dict[str, str]) -> tuple[str, str]:
        return item["root"], item["relative_path"]

    summary = inventory.summary
    return {
        "schema_version": 1,
        "roots": [
            {"name": root.name, "status": status, **({"error_kind": kind} if kind else {})}
            for root, status, kind in sorted(roots, key=lambda item: item[0].name)
        ],
        "entries": sorted(records, key=lambda record: (record["root"], record["relative_path"])),
        "summary": {
            "file_count": summary.file_count,
            "directory_count": summary.directory_count,
            "total_file_size": summary.total_file_size,
            "file_types": [
                {"extension": item.extension, "file_count": item.file_count,
                 "total_size": item.total_size}
                for item in summary.file_types
            ],
            "large_file_count": summary.large_file_count,
            "empty_directory_count": summary.empty_directory_count,
            "likely_duplicate_group_count": summary.likely_duplicate_group_count,
            "potential_duplicate_size": summary.potential_duplicate_size,
            "failed_root_count": summary.failed_root_count,
        },
        "empty_directories": sorted(
            (location(entry) for entry in inventory.empty_directories),
            key=location_key,
        ),
        "likely_duplicates": [
            {"normalized_name": group.normalized_name, "size": group.size,
             "files": sorted((location(entry) for entry in group.files), key=location_key)}
            for group in inventory.likely_duplicate_groups
        ],
    }


def encode_export(inventory: ScanInventory) -> str:
    """Render deterministic, readable JSON with one final newline."""

    return json.dumps(
        build_export_document(inventory), ensure_ascii=False, sort_keys=True, indent=2,
    ) + "\n"


def save_export(inventory: ScanInventory, destination: Path) -> None:
    """Publish complete JSON at a new path, with no replacement or partial file."""

    contents = encode_export(inventory)
    destination = Path(destination)
    # Staging alongside the target keeps publication on the same filesystem.
    # Cleanup affects only this app-owned directory, never the destination.
    with tempfile.TemporaryDirectory(
        dir=destination.parent, prefix=".file-chisel-export-", ignore_cleanup_errors=True,
    ) as staging:
        # Close through the wrapper even if closing its underlying file fails.
        with closing(tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=staging, delete=False,
        )) as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        # A hard link publishes the closed, complete file atomically and fails
        # if another file (including a symlink) already occupies the target.
        os.link(output.name, destination)
