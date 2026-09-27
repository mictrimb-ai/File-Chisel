"""Describe requested folder creations and file moves without touching disk."""

from __future__ import annotations

from dataclasses import dataclass

from file_chisel.proposal import FolderProposal, ProposalLocation


@dataclass(frozen=True)
class FolderCreation:
    destination: ProposalLocation
    reason: str


@dataclass(frozen=True)
class FileMove:
    source: ProposalLocation
    destination: ProposalLocation
    reason: str


@dataclass(frozen=True)
class MovePlan:
    """Exact requested changes to a scanned snapshot, not an execution sequence."""

    inventory_id: str
    folders: tuple[FolderCreation, ...]
    moves: tuple[FileMove, ...]


def build_move_plan(proposal: FolderProposal) -> MovePlan:
    """Extract changes from a validated proposal using metadata already in memory.

    Folder rows are ordered parent before child. File rows are sorted for review;
    their order must not be interpreted as a safe order for filesystem operations.
    """

    folders = []
    moves = []
    for node in proposal.nodes:
        if node.source is None:
            if node.entry_type != "directory":
                raise ValueError("Only folders can be proposed without a source.")
            folders.append(FolderCreation(node.location, node.reason))
        elif node.source != node.location:
            if node.entry_type != "file":
                raise ValueError("Only regular files can be relocated by this proposal.")
            moves.append(FileMove(node.source, node.location, node.reason))

    return MovePlan(
        proposal.inventory_id,
        tuple(sorted(folders, key=lambda item: (
            item.destination.root,
            item.destination.relative_path.count("/"),
            item.destination.relative_path,
        ))),
        tuple(sorted(moves, key=lambda item: (
            item.source.root, item.source.relative_path,
            item.destination.root, item.destination.relative_path,
        ))),
    )
