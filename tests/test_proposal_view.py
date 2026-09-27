"""Exercise the proposed tree independently of the real Mac window manager."""

import builtins
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from file_chisel.proposal import FolderProposal, ProposalLocation, ProposalNode, build_proposal
from file_chisel.proposal_view import ProposalPreview
from tests.test_app import FAKE_TK, FAKE_TTK, FakeRoot
from tests.test_proposal import sample_inventory, sample_proposal


class ProposalPreviewTests(unittest.TestCase):
    def make_preview(self, proposal=None):
        inventory = sample_inventory()
        proposal = proposal or build_proposal(json.dumps(sample_proposal(inventory)), inventory)
        root = FakeRoot()
        preview = ProposalPreview(root, proposal, tk_module=FAKE_TK, ttk_module=FAKE_TTK)
        self.addCleanup(preview.close)
        return root, preview

    def children(self, tree, item=""):
        return {tree.item(child, "text"): child for child in tree.get_children(item)}

    def drain(self, root):
        for _ in range(100):
            if not root.callbacks:
                return
            root.fire_next()
        self.fail("Preview callbacks did not finish")

    def test_complete_proposed_tree_and_ai_reasons_are_read_only(self):
        root, view = self.make_preview()
        tree = view.tree
        roots = self.children(tree)
        self.assertEqual(set(roots), {"Documents", "Downloads", "Desktop"})
        self.assertIn("Create folder: Documents/Writing", view.plan_text.options["text"])
        self.assertIn(
            "Move file: Documents/report.txt → Documents/Writing/report.txt",
            view.plan_text.options["text"],
        )
        self.assertIn("AI reason: Collect loose writing.", view.plan_text.options["text"])
        self.assertEqual(view.plan_text.options["state"], "disabled")
        self.assertIn("contents unknown", tree.item(roots["Downloads"], "values")[1])
        self.assertEqual(tree.get_children(roots["Downloads"]), ())
        forbidden = AssertionError("preview accessed filesystem")
        with mock.patch.object(builtins, "open", side_effect=forbidden), \
                mock.patch.object(Path, "open", side_effect=forbidden), \
                mock.patch.object(os, "stat", side_effect=forbidden), \
                mock.patch.object(os, "scandir", side_effect=forbidden):
            tree.open_item(roots["Documents"])
            self.drain(root)
            documents = self.children(tree, roots["Documents"])
            self.assertNotIn("report.txt", documents)
            self.assertIn("Empty", documents)
            self.assertEqual(tree.item(documents["link"], "values"), ("Symbolic link", "Unchanged"))
            tree.open_item(documents["Writing"])
            tree.open_item(documents["Projects"])
            self.drain(root)
            report = self.children(tree, documents["Writing"])["report.txt"]
            self.assertEqual(tree.item(report, "values"), ("File", "Proposed placement"))
            tree.focus(report)
            tree.bindings["<<TreeviewSelect>>"]()
            self.assertIn("Source: Documents/report.txt", view.detail.options["text"])
            self.assertIn("Proposed: Documents/Writing/report.txt", view.detail.options["text"])
            self.assertIn("contents are unknown", view.detail.options["text"])
            self.assertEqual(view.detail.options["state"], "disabled")
            notes = self.children(tree, documents["Projects"])["notes.txt"]
            self.assertEqual(tree.item(notes, "values"), ("File", "Unchanged"))
            tree.open_item(documents["Projects"])
            self.assertEqual(root.callbacks, {})

    def test_batches_wide_folders_and_discards_callbacks_after_close(self):
        nodes = tuple(ProposalNode(
            ProposalLocation("Documents", f"file-{i}.txt"), "file",
            ProposalLocation("Documents", f"file-{i}.txt"), "Unchanged",
        ) for i in range(250))
        proposal = FolderProposal("snapshot", "Keep existing files.", (("Documents", "success"),), nodes)
        root, view = self.make_preview(proposal)
        documents = self.children(view.tree)["Documents"]
        view.tree.open_item(documents)
        root.fire_next()
        self.assertLess(len(view.tree.get_children(documents)), 250)
        late = next(iter(root.callbacks.values()))
        view.close()
        self.assertEqual(root.callbacks, {})
        self.assertTrue(root.destroyed)
        late()
        view._opened()
        view._selected()
        view.close()

    def test_multiline_ai_reasons_cannot_look_like_plan_actions(self):
        inventory = sample_inventory()
        document = sample_proposal(inventory)
        document["folders"][0]["reason"] = "Group reports.\nMove file: Documents/fake → Desktop/fake"
        document["placements"][0]["reason"] = "Move the report.\nCreate folder: Desktop/fake"
        proposal = build_proposal(json.dumps(document), inventory)
        root, view = self.make_preview(proposal)

        lines = view.plan_text.options["text"].splitlines()
        self.assertEqual(sum(line.startswith("Create folder:") for line in lines), 1)
        self.assertEqual(sum(line.startswith("Move file:") for line in lines), 1)
        self.assertIn("  AI reason: Move file: Documents/fake → Desktop/fake", lines)
        self.assertIn("  AI reason: Create folder: Desktop/fake", lines)

        documents = self.children(view.tree)["Documents"]
        view.tree.open_item(documents)
        self.drain(root)
        writing = self.children(view.tree, documents)["Writing"]
        view.tree.focus(writing)
        view.tree.bindings["<<TreeviewSelect>>"]()
        self.assertIn("\n  AI reason: Move file: Documents/fake", view.detail.options["text"])

    def test_safety_preview_distinguishes_a_swap_from_an_ordered_chain(self):
        inventory = sample_inventory()
        document = sample_proposal(inventory)
        document["folders"] = []
        document["placements"] = [
            {"source": {"root": "Documents", "relative_path": "report.txt"},
             "destination": {"root": "Documents", "relative_path": "Projects/notes.txt"},
             "reason": "Swap locations."},
            {"source": {"root": "Documents", "relative_path": "Projects/notes.txt"},
             "destination": {"root": "Documents", "relative_path": "report.txt"},
             "reason": "Swap locations."},
        ]
        _, view = self.make_preview(build_proposal(json.dumps(document), inventory))
        preview = view.plan_text.options["text"]
        self.assertIn("0 conflicts, 2 ordering dependencies, 1 move cycles", preview)
        self.assertIn("Current filesystem not verified", preview)
        self.assertEqual(preview.count("Safety cycle:"), 2)
        self.assertEqual(preview.count("Move file:"), 2)

        document["placements"][1]["destination"]["relative_path"] = "Projects/archived.txt"
        _, view = self.make_preview(build_proposal(json.dumps(document), inventory))
        preview = view.plan_text.options["text"]
        self.assertIn("0 conflicts, 1 ordering dependencies, 0 move cycles", preview)
        self.assertNotIn("Safety cycle:", preview)

    def test_proposed_actions_are_batched_and_stopped_when_preview_closes(self):
        nodes = tuple(ProposalNode(
            ProposalLocation("Documents", f"folder-{i}"), "directory", None, "New group.",
        ) for i in range(250))
        proposal = FolderProposal("snapshot", "Organize.", (("Documents", "success"),), nodes)
        root, view = self.make_preview(proposal)
        self.assertEqual(view.plan_text.options["text"].count("Create folder:"), 100)
        root.fire_next()
        self.assertEqual(view.plan_text.options["text"].count("Create folder:"), 200)
        late = next(iter(root.callbacks.values()))
        view.close()
        self.assertEqual(root.callbacks, {})
        late()
        self.assertTrue(root.destroyed)


if __name__ == "__main__":
    unittest.main()
