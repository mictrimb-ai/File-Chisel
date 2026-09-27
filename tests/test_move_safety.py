"""Assess only the scanned snapshot; no finding authorizes filesystem changes."""

import builtins
import os
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from file_chisel.move_plan import FileMove, FolderCreation, MovePlan
from file_chisel.move_safety import assess_move_plan, moves_into_moving_sources
from file_chisel.proposal import FolderProposal, ProposalLocation, ProposalNode


def location(name, root="Documents"):
    return ProposalLocation(root, name)


def example(moves=(), folders=(), stationary=()):
    plan = MovePlan("snapshot", tuple(
        FolderCreation(location(name), "Group files.") for name in folders
    ), tuple(
        FileMove(location(source), location(destination), "Reorganize.")
        for source, destination in moves
    ))
    nodes = tuple(
        ProposalNode(move.destination, "file", move.source, move.reason)
        for move in plan.moves
    ) + tuple(
        ProposalNode(folder.destination, "directory", None, folder.reason)
        for folder in plan.folders
    ) + tuple(
        ProposalNode(location(name), kind, location(name), "Unchanged.")
        for name, kind in stationary
    )
    proposal = FolderProposal("snapshot", "Review only.", (("Documents", "success"),), nodes)
    return plan, proposal


class MoveSafetyTests(unittest.TestCase):
    def test_chain_has_a_dependency_without_a_cycle(self):
        plan, proposal = example(moves=(("A.txt", "B.txt"), ("B.txt", "C.txt")))
        self.assertEqual(moves_into_moving_sources(plan), (plan.moves[0],))
        report = assess_move_plan(plan, proposal)
        self.assertEqual(report.conflict_count, 0)
        self.assertEqual(report.dependency_count, 1)
        self.assertEqual(report.findings[0].destination, location("B.txt"))
        self.assertEqual(report.cycles, ())

    def test_swaps_and_longer_cycles_require_an_unplanned_holding_step(self):
        plan, proposal = example(moves=(
            ("A.txt", "B.txt"), ("B.txt", "A.txt"),
            ("C.txt", "D.txt"), ("D.txt", "E.txt"), ("E.txt", "C.txt"),
        ))
        report = assess_move_plan(plan, proposal)
        self.assertEqual(report.dependency_count, 5)
        self.assertEqual(report.cycles, (
            (location("A.txt"), location("B.txt")),
            (location("C.txt"), location("D.txt"), location("E.txt")),
        ))

    def test_folder_reusing_a_moving_file_location_is_a_dependency(self):
        plan, proposal = example(moves=(("Loose", "Filed.txt"),), folders=("Loose",))
        report = assess_move_plan(plan, proposal)
        self.assertEqual(report.dependency_count, 1)
        self.assertEqual(report.findings[0].destination, plan.folders[0].destination)
        self.assertEqual(report.cycles, ())

    def test_stationary_entries_and_duplicate_targets_are_conflicts(self):
        plan, proposal = example(
            moves=(("A.txt", "Occupied"), ("B.txt", "occupied")),
            stationary=(("OCCUPIED", "symlink"),),
        )
        report = assess_move_plan(plan, proposal)
        self.assertEqual(report.conflict_count, 3)
        self.assertEqual(report.dependency_count, 0)
        self.assertTrue(all("overwritten" in finding.explanation or "same location" in finding.explanation
                            for finding in report.findings))

    def test_case_and_unicode_variants_collide_but_different_roots_do_not(self):
        plan, proposal = example(moves=(
            ("A.txt", "Caf\u00e9.txt"), ("B.txt", "Cafe\u0301.txt"),
        ))
        self.assertEqual(assess_move_plan(plan, proposal).conflict_count, 1)

        cross_root = MovePlan("snapshot", (), (FileMove(
            location("A.txt"), location("A.txt", "Desktop"), "Different root.",
        ),))
        cross_proposal = FolderProposal(
            "snapshot", "Review only.", (("Documents", "success"), ("Desktop", "success")),
            (ProposalNode(cross_root.moves[0].destination, "file", cross_root.moves[0].source,
                          "Different root."),),
        )
        self.assertEqual(assess_move_plan(cross_root, cross_proposal).findings, ())

    def test_independent_moves_and_no_actions_have_no_known_snapshot_findings(self):
        for plan, proposal in (
            example(moves=(("A.txt", "Empty/new.txt"),)), example(),
        ):
            with self.subTest(moves=len(plan.moves)):
                report = assess_move_plan(plan, proposal)
                self.assertEqual((report.findings, report.cycles), ((), ()))
                with self.assertRaises(FrozenInstanceError):
                    report.cycles = ()

    def test_analysis_does_not_access_disk_and_rejects_mismatched_snapshot(self):
        plan, proposal = example(moves=(("A.txt", "B.txt"),))
        blocked = AssertionError("safety assessment accessed the filesystem")
        with mock.patch.object(builtins, "open", side_effect=blocked), \
                mock.patch.object(Path, "open", side_effect=blocked), \
                mock.patch.object(os, "stat", side_effect=blocked), \
                mock.patch.object(os, "scandir", side_effect=blocked), \
                mock.patch.object(os, "rename", side_effect=blocked), \
                mock.patch.object(os, "mkdir", side_effect=blocked):
            self.assertEqual(assess_move_plan(plan, proposal).findings, ())
        other = MovePlan("different snapshot", plan.folders, plan.moves)
        with self.assertRaisesRegex(ValueError, "different inventories"):
            assess_move_plan(other, proposal)


if __name__ == "__main__":
    unittest.main()
