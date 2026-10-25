"""Replay a proposed batch against the scanned snapshot in memory only."""

from __future__ import annotations

from dataclasses import dataclass, replace

from file_chisel.move_plan import MovePlan, build_move_plan
from file_chisel.move_safety import assess_move_plan
from file_chisel.proposal import FolderProposal, ProposalLocation, _path_key


@dataclass(frozen=True)
class SimulatedEntry:
    """An entry's scanned identity and its location in one hypothetical state."""

    source: ProposalLocation | None
    location: ProposalLocation
    entry_type: str
    source_size: int | None


@dataclass(frozen=True)
class SimulationResult:
    """A logical outcome, never a sequence of filesystem operations."""

    original: tuple[SimulatedEntry, ...]
    resulting: tuple[SimulatedEntry, ...] | None
    blocked_reasons: tuple[str, ...]
    folder_count: int
    move_count: int

    @property
    def blocked(self) -> bool:
        return self.resulting is None


def _ordered(entries) -> tuple[SimulatedEntry, ...]:
    return tuple(sorted(entries, key=lambda entry: entry.location))


def simulate_move_plan(plan: MovePlan, proposal: FolderProposal) -> SimulationResult:
    """Replay all actions atomically on copied metadata and compare the result.

    This models final locations only. It cannot certify a safe operation order or
    say whether anything has changed on the real disk since the scan.
    """

    if plan != build_move_plan(proposal):
        raise ValueError("The move plan does not match this proposal.")

    original = _ordered(
        SimulatedEntry(node.source, node.source, node.entry_type, node.source_size)
        for node in proposal.nodes if node.source is not None
    )
    if len({_path_key(entry.location) for entry in original}) != len(original):
        raise ValueError("The scanned snapshot contains ambiguous source locations.")

    safety = assess_move_plan(plan, proposal)
    reasons = []
    if safety.conflict_count:
        reasons.append(f"{safety.conflict_count} scanned-snapshot conflict(s)")
    if safety.cycles:
        reasons.append(f"{len(safety.cycles)} move cycle(s) needing an unplanned holding step")
    if reasons:
        return SimulationResult(original, None, tuple(reasons), len(plan.folders), len(plan.moves))

    working = {entry.location: entry for entry in original}
    moved = {}
    for move in plan.moves:
        try:
            moved[move.source] = working.pop(move.source)
        except KeyError as error:
            raise ValueError("A move source is missing from the scanned snapshot.") from error

    for folder in plan.folders:
        working[folder.destination] = SimulatedEntry(None, folder.destination, "directory", None)
    for move in plan.moves:
        working[move.destination] = replace(moved[move.source], location=move.destination)

    resulting = _ordered(working.values())
    if len({_path_key(entry.location) for entry in resulting}) != len(resulting):
        raise ValueError("The simulated hierarchy contains colliding locations.")
    successful_roots = {name for name, status in proposal.roots if status == "success"}
    for entry in resulting:
        if entry.location.root not in successful_roots:
            raise ValueError("The simulated hierarchy references an unscanned root.")
        parent = entry.location.parent
        if parent.relative_path and (
            parent not in working or working[parent].entry_type != "directory"
        ):
            raise ValueError("The simulated hierarchy has a missing or non-directory parent.")
    expected = _ordered(
        SimulatedEntry(node.source, node.location, node.entry_type, node.source_size)
        for node in proposal.nodes
    )
    if resulting != expected or len(working) != len(expected):
        raise ValueError("The move plan does not reproduce the proposed hierarchy.")

    return SimulationResult(original, resulting, (), len(plan.folders), len(plan.moves))
