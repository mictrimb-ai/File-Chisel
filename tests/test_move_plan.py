"""Checks that a proposal becomes a precise, read-only list of changes."""

import builtins
import json
import os
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from file_chisel.move_plan import FolderCreation, FileMove, MovePlan, build_move_plan
from file_chisel.proposal import FolderProposal, ProposalLocation, ProposalNode, build_proposal
from tests.test_proposal import sample_inventory, sample_proposal


class MovePlanTests(unittest.TestCase):
    def setUp(self):
        self.inventory = sample_inventory()
        self.document = sample_proposal(self.inventory)

    def build(self):
        return build_proposal(json.dumps(self.document), self.inventory)

    def test_new_folder_and_move_are_precise_and_unchanged_entries_are_omitted(self):
        proposal = self.build()
        expected = MovePlan(
            proposal.inventory_id,
            (FolderCreation(ProposalLocation("Documents", "Writing"), "Collect loose writing."),),
            (FileMove(
                ProposalLocation("Documents", "report.txt"),
                ProposalLocation("Documents", "Writing/report.txt"),
                "This name suggests a report; its contents are unknown.",
            ),),
        )
        self.assertEqual(build_move_plan(proposal), expected)
        self.assertEqual(len(expected.folders), 1)
        self.assertEqual(len(expected.moves), 1)

    def test_nested_folders_precede_children_and_cross_root_moves_are_preserved(self):
        self.document["folders"] = [
            {"root": "Documents", "relative_path": "Writing/Reports", "reason": "Reports"},
            {"root": "Documents", "relative_path": "Writing", "reason": "Writing"},
        ]
        self.document["placements"][0]["destination"] = {
            "root": "Desktop", "relative_path": "report.txt",
        }
        proposal = self.build()
        plan = build_move_plan(proposal)
        self.assertEqual(
            tuple(item.destination.relative_path for item in plan.folders),
            ("Writing", "Writing/Reports"),
        )
        self.assertEqual(plan.moves[0].destination, ProposalLocation("Desktop", "report.txt"))
        self.assertEqual(build_move_plan(FolderProposal(
            proposal.inventory_id, proposal.summary, proposal.roots,
            tuple(reversed(proposal.nodes)),
        )), plan)

    def test_swapped_file_destinations_are_reported_as_requests_without_execution(self):
        self.document["folders"] = []
        self.document["placements"] = [
            {"source": {"root": "Documents", "relative_path": "report.txt"},
             "destination": {"root": "Documents", "relative_path": "Projects/notes.txt"},
             "reason": "Trade locations."},
            {"source": {"root": "Documents", "relative_path": "Projects/notes.txt"},
             "destination": {"root": "Documents", "relative_path": "report.txt"},
             "reason": "Trade locations."},
        ]
        plan = build_move_plan(self.build())
        self.assertEqual(len(plan.moves), 2)
        self.assertEqual(plan.moves[0].source.relative_path, "Projects/notes.txt")
        self.assertEqual(plan.moves[1].destination.relative_path, "Projects/notes.txt")

    def test_empty_proposal_is_immutable_and_plan_never_reads_or_modifies_disk(self):
        self.document["folders"] = []
        self.document["placements"] = []
        proposal = self.build()
        blocked = AssertionError("move planning accessed the filesystem")
        with mock.patch.object(builtins, "open", side_effect=blocked), \
                mock.patch.object(Path, "open", side_effect=blocked), \
                mock.patch.object(os, "scandir", side_effect=blocked), \
                mock.patch.object(os, "stat", side_effect=blocked), \
                mock.patch.object(os, "rename", side_effect=blocked), \
                mock.patch.object(os, "mkdir", side_effect=blocked):
            plan = build_move_plan(proposal)
        self.assertEqual(plan.folders, ())
        self.assertEqual(plan.moves, ())
        with self.assertRaises(FrozenInstanceError):
            plan.moves = ()

    def test_unsupported_proposal_node_cannot_turn_into_an_operation(self):
        proposal = self.build()
        bad = ProposalNode(ProposalLocation("Documents", "link"), "symlink", None, "Invalid")
        with self.assertRaisesRegex(ValueError, "Only folders"):
            build_move_plan(FolderProposal(
                proposal.inventory_id, proposal.summary, proposal.roots, (bad,),
            ))


if __name__ == "__main__":
    unittest.main()
