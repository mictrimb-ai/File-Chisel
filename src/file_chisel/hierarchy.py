"""Build folder relationships from existing scan metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

from file_chisel.scanner import FileSystemEntry

if TYPE_CHECKING:
    from file_chisel.inventory import ScanInventory


def index_children(entries):
    """Group existing entries by their direct parent, retaining input order."""

    children = {}

    for entry in entries:
        parent = entry.path.parent

        if parent not in children:
            children[parent] = [entry]
        else:
            children[parent].append(entry)

    return children


@dataclass(frozen=True)
class HierarchyIndex:
    """A read-only lookup shared with the GUI after worker completion."""

    children: Mapping[Path, tuple[FileSystemEntry, ...]]


def build_hierarchy(inventory: ScanInventory) -> HierarchyIndex:
    """Freeze Parker's parent lookup without accessing the filesystem."""

    grouped = index_children(inventory.entries)
    return HierarchyIndex(MappingProxyType({
        parent: tuple(entries) for parent, entries in grouped.items()
    }))
