"""Assess a proposed move plan against its scanned snapshot, without disk I/O."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain

from file_chisel.move_plan import FileMove, MovePlan
from file_chisel.proposal import FolderProposal, ProposalLocation, _path_key


@dataclass(frozen=True)
class SafetyFinding:
    """A destination that conflicts with or depends on its scanned occupant."""

    kind: str  # "conflict" or "dependency"
    destination: ProposalLocation
    explanation: str


@dataclass(frozen=True)
class MoveSafetyReport:
    """Static findings only; the present-day filesystem is never verified here."""

    findings: tuple[SafetyFinding, ...]
    cycles: tuple[tuple[ProposalLocation, ...], ...]

    @property
    def conflict_count(self) -> int:
        return sum(finding.kind == "conflict" for finding in self.findings)

    @property
    def dependency_count(self) -> int:
        return sum(finding.kind == "dependency" for finding in self.findings)


def moves_into_moving_sources(plan: MovePlan) -> tuple[FileMove, ...]:
    """Return moves whose destination was another move's scanned source."""

    sources = {_path_key(move.source) for move in plan.moves}
    return tuple(move for move in plan.moves if _path_key(move.destination) in sources)


def assess_move_plan(plan: MovePlan, proposal: FolderProposal) -> MoveSafetyReport:
    """Report snapshot conflicts and ordering hazards without proposing execution."""

    if plan.inventory_id != proposal.inventory_id:
        raise ValueError("The plan and proposal refer to different inventories.")

    stationary = {
        _path_key(node.location): node
        for node in proposal.nodes if node.source == node.location
    }
    moving = {_path_key(move.source): move for move in plan.moves}
    targets: set[tuple[str, str]] = set()
    findings: list[SafetyFinding] = []

    for destination in chain(
        (folder.destination for folder in plan.folders),
        (move.destination for move in plan.moves),
    ):
        key = _path_key(destination)
        if key in targets:
            findings.append(SafetyFinding(
                "conflict", destination, "Two proposed actions target the same location.",
            ))
        targets.add(key)
        if key in stationary:
            findings.append(SafetyFinding(
                "conflict", destination,
                "A scanned entry is staying at this location; it must not be overwritten.",
            ))
        elif key in moving:
            findings.append(SafetyFinding(
                "dependency", destination,
                "A scanned file occupies this location and is scheduled to move away first.",
            ))

    cycles: list[tuple[ProposalLocation, ...]] = []
    visited: set[tuple[str, str]] = set()
    for move in plan.moves:
        cursor = _path_key(move.source)
        route: list[tuple[str, str]] = []
        positions: dict[tuple[str, str], int] = {}
        while cursor in moving and cursor not in visited and cursor not in positions:
            positions[cursor] = len(route)
            route.append(cursor)
            cursor = _path_key(moving[cursor].destination)
        if cursor in positions:
            ring = [moving[key].source for key in route[positions[cursor]:]]
            smallest = min(range(len(ring)), key=lambda index: ring[index])
            cycles.append(tuple(ring[smallest:] + ring[:smallest]))
        visited.update(route)

    return MoveSafetyReport(tuple(findings), tuple(sorted(cycles)))
