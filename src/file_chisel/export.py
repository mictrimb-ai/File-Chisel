"""Privacy-conscious export of inventory metadata."""

from pathlib import Path

from file_chisel.scanner import FileSystemEntry


def build_entry_record(
    root: Path,
    entry: FileSystemEntry,
) -> dict[str, object]:
    """Return one root-relative metadata record for export."""

    relative_path = entry.path.relative_to(root)

    return {
        "root": root.name,
        "relative_path": relative_path.as_posix(),
        "type": entry.entry_type,
        "size": entry.size,
    }
