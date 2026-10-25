"""A simulation must replay the complete plan without touching the disk."""

import builtins
import json
import os
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from file_chisel.move_plan import MovePlan, build_move_plan
from file_chisel.proposal import ProposalLocation, build_proposal
from file_chisel.simulation import simulate_move_plan
from tests.test_move_safety import example
from tests.test_proposal import sample_inventory, sample_proposal


class SimulationTests(unittest.TestCase):
    def test_replays_folder_and_move_while_preserving_original_snapshot(self):
        inventory = sample_inventory()
        proposal = build_proposal(json.dumps(sample_proposal(inventory)), inventory)
        plan = build_move_plan(proposal)
        blocked = AssertionError("simulation accessed the filesystem")
        with mock.patch.object(builtins, "open", side_effect=blocked), \
                mock.patch.object(Path, "open", side_effect=blocked), \
                mock.patch.object(os, "stat", side_effect=blocked), \
                mock.patch.object(os, "scandir", side_effect=blocked), \
                mock.patch.object(os, "rename", side_effect=blocked), \
                mock.patch.object(os, "mkdir", side_effect=blocked):
            result = simulate_move_plan(plan, proposal)

        self.assertFalse(result.blocked)
        self.assertEqual((result.folder_count, result.move_count), (1, 1))
        self.assertEqual([step.kind for step in result.steps], ["create_folder", "move_file"])
        original = {entry.location: entry for entry in result.original}
        resulting = {entry.location: entry for entry in result.resulting}
        report = ProposalLocation("Documents", "report.txt")
        relocated = ProposalLocation("Documents", "Writing/report.txt")
        self.assertIn(report, original)
        self.assertNotIn(report, resulting)
        self.assertEqual(resulting[relocated].source, report)
        self.assertEqual(resulting[relocated].source_size, 12)
        self.assertIsNone(resulting[ProposalLocation("Documents", "Writing")].source)
        self.assertIn(ProposalLocation("Documents", "Projects/notes.txt"), resulting)
        with self.assertRaises(FrozenInstanceError):
            result.original = ()

    def test_ordering_dependency_can_have_a_logical_final_state(self):
        plan, proposal = example(moves=(("A.txt", "B.txt"), ("B.txt", "C.txt")))
        result = simulate_move_plan(plan, proposal)

        self.assertFalse(result.blocked)
        self.assertEqual(
            [(step.source.relative_path, step.destination.relative_path)
             for step in result.steps],
            [("B.txt", "C.txt"), ("A.txt", "B.txt")],
        )
        self.assertEqual(
            {(entry.source.relative_path, entry.location.relative_path)
             for entry in result.resulting},
            {("A.txt", "B.txt"), ("B.txt", "C.txt")},
        )

    def test_cross_root_move_and_empty_plan_preserve_entry_identity(self):
        inventory = sample_inventory()
        document = sample_proposal(inventory)
        document["folders"] = []
        document["placements"][0]["destination"] = {
            "root": "Desktop", "relative_path": "report.txt",
        }
        proposal = build_proposal(json.dumps(document), inventory)
        result = simulate_move_plan(build_move_plan(proposal), proposal)
        self.assertFalse(result.blocked)
        self.assertIn(
            (ProposalLocation("Documents", "report.txt"),
             ProposalLocation("Desktop", "report.txt")),
            {(entry.source, entry.location) for entry in result.resulting},
        )

        document["placements"] = []
        unchanged = build_proposal(json.dumps(document), inventory)
        empty = simulate_move_plan(build_move_plan(unchanged), unchanged)
        self.assertEqual(empty.original, empty.resulting)
        self.assertEqual((empty.folder_count, empty.move_count), (0, 0))

    def test_cycles_and_stationary_occupants_block_a_result(self):
        for plan, proposal in (
            example(moves=(("A.txt", "B.txt"), ("B.txt", "A.txt"))),
            example(moves=(("A.txt", "Occupied"),), stationary=(("Occupied", "file"),)),
        ):
            with self.subTest(moves=plan.moves):
                result = simulate_move_plan(plan, proposal)
                self.assertTrue(result.blocked)
                self.assertIsNone(result.resulting)
                self.assertEqual(result.steps, ())
                self.assertTrue(result.blocked_reasons)
                self.assertTrue(result.original)

    def test_rejects_a_plan_that_does_not_match_its_proposal(self):
        plan, proposal = example(moves=(("A.txt", "B.txt"),))
        wrong = MovePlan("different", plan.folders, plan.moves)
        with self.assertRaisesRegex(ValueError, "does not match"):
            simulate_move_plan(wrong, proposal)

    def test_blocks_a_projected_file_without_a_directory_parent(self):
        plan, proposal = example(moves=(("A.txt", "Missing/A.txt"),))
        result = simulate_move_plan(plan, proposal)
        self.assertTrue(result.blocked)
        self.assertIn("parent", result.blocked_reasons[0])

    def test_folder_target_occupied_by_file_can_be_vacated_before_creation(self):
        plan, proposal = example(
            moves=(("Loose", "Filed.txt"), ("Other.txt", "Loose/Other.txt")),
            folders=("Loose",),
        )
        result = simulate_move_plan(plan, proposal)
        self.assertFalse(result.blocked)
        self.assertEqual(
            [(step.kind, step.destination.relative_path) for step in result.steps],
            [("move_file", "Filed.txt"), ("create_folder", "Loose"),
             ("move_file", "Loose/Other.txt")],
        )

    def test_move_into_a_folder_replacing_its_source_is_blocked(self):
        plan, proposal = example(moves=(("Loose", "Loose/file.txt"),), folders=("Loose",))
        result = simulate_move_plan(plan, proposal)
        self.assertTrue(result.blocked)
        self.assertIn("ordering cycle", result.blocked_reasons[0])
        self.assertEqual(result.steps, ())


if __name__ == "__main__":
    unittest.main()
