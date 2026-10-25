"""Replay a proposed batch against the scanned snapshot in memory only."""

from __future__ import annotations

from heapq import heappop, heappush
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
class SimulationStep:
    """One step that is possible in the copied scan state at that moment."""

    kind: str  # "create_folder" or "move_file"
    source: ProposalLocation | None
    destination: ProposalLocation


@dataclass(frozen=True)
class SimulationResult:
    """A snapshot-only order; current disk conditions are still unknown."""

    original: tuple[SimulatedEntry, ...]
    resulting: tuple[SimulatedEntry, ...] | None
    steps: tuple[SimulationStep, ...]
    blocked_reasons: tuple[str, ...]
    folder_count: int
    move_count: int

    @property
    def blocked(self) -> bool:
        return self.resulting is None


def _ordered(entries) -> tuple[SimulatedEntry, ...]:
    return tuple(sorted(entries, key=lambda entry: entry.location))


def simulate_move_plan(plan: MovePlan, proposal: FolderProposal) -> SimulationResult:
    """Order and replay all actions on copied metadata, then compare the result.

    A step only runs after its destination is vacated and its parent exists as
    a directory in the copy. This order is not certified for the real disk.
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
        return SimulationResult(original, None, (), tuple(reasons), len(plan.folders), len(plan.moves))

    actions = tuple(
        SimulationStep("create_folder", None, folder.destination)
        for folder in plan.folders
    ) + tuple(
        SimulationStep("move_file", move.source, move.destination)
        for move in plan.moves
    )
    original_keys = {_path_key(entry.location) for entry in original}
    folder_targets = {
        _path_key(action.destination): index for index, action in enumerate(actions)
        if action.kind == "create_folder"
    }
    moving_sources = {
        _path_key(action.source): index for index, action in enumerate(actions)
        if action.kind == "move_file"
    }
    dependencies: list[set[int]] = []
    dependents: list[list[int]] = [[] for _ in actions]
    for index, action in enumerate(actions):
        needs = set()
        parent = action.destination.parent
        if parent.relative_path and _path_key(parent) in folder_targets:
            needs.add(folder_targets[_path_key(parent)])
        destination_key = _path_key(action.destination)
        if destination_key in original_keys and destination_key in moving_sources:
            needs.add(moving_sources[destination_key])
        dependencies.append(needs)
        for earlier in needs:
            dependents[earlier].append(index)

    remaining = [len(needs) for needs in dependencies]
    ready = []
    for index, count in enumerate(remaining):
        if count == 0:
            heappush(ready, index)

    working = {_path_key(entry.location): entry for entry in original}
    steps = []
    while ready:
        index = heappop(ready)
        action = actions[index]
        destination_key = _path_key(action.destination)
        parent = action.destination.parent
        if parent.relative_path:
            parent_entry = working.get(_path_key(parent))
            if (parent_entry is None or parent_entry.entry_type != "directory"
                    or parent_entry.location != parent):
                return SimulationResult(
                    original, None, (), ("a destination parent is unavailable",),
                    len(plan.folders), len(plan.moves),
                )
        if destination_key in working:
            return SimulationResult(
                original, None, (), ("a destination is still occupied",),
                len(plan.folders), len(plan.moves),
            )
        if action.kind == "create_folder":
            working[destination_key] = SimulatedEntry(None, action.destination, "directory", None)
        else:
            source_key = _path_key(action.source)
            source_entry = working.get(source_key)
            if source_entry is None or source_entry.location != action.source:
                return SimulationResult(
                    original, None, (), ("a move source is unavailable",),
                    len(plan.folders), len(plan.moves),
                )
            del working[source_key]
            working[destination_key] = replace(source_entry, location=action.destination)
        steps.append(action)
        for later in dependents[index]:
            remaining[later] -= 1
            if remaining[later] == 0:
                heappush(ready, later)

    if len(steps) != len(actions):
        return SimulationResult(
            original, None, (), ("ordering cycle needs an unplanned holding step",),
            len(plan.folders), len(plan.moves),
        )

    resulting = _ordered(working.values())
    successful_roots = {name for name, status in proposal.roots if status == "success"}
    for entry in resulting:
        if entry.location.root not in successful_roots:
            raise ValueError("The simulated hierarchy references an unscanned root.")
        parent = entry.location.parent
        if parent.relative_path and (
            _path_key(parent) not in working
            or working[_path_key(parent)].entry_type != "directory"
            or working[_path_key(parent)].location != parent
        ):
            raise ValueError("The simulated hierarchy has a missing or non-directory parent.")
    expected = _ordered(
        SimulatedEntry(node.source, node.location, node.entry_type, node.source_size)
        for node in proposal.nodes
    )
    if resulting != expected or len(working) != len(expected):
        raise ValueError("The move plan does not reproduce the proposed hierarchy.")

    return SimulationResult(original, resulting, tuple(steps), (), len(plan.folders), len(plan.moves))
