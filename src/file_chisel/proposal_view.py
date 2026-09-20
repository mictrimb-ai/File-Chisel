"""A separate read-only window for an imported proposal; Tk is loaded on demand."""

from collections import deque

from file_chisel.proposal import FolderProposal, ProposalLocation


class ProposalPreview:
    """Expand the proposed snapshot in small GUI batches, without filesystem reads."""

    BATCH_SIZE = 100
    TYPE_LABELS = {
        "file": "File", "directory": "Folder", "symlink": "Symbolic link", "other": "Other",
    }

    def __init__(self, root, proposal: FolderProposal, *, tk_module, ttk_module):
        self.root = root
        self._closed = False
        self._after_id = None
        self._pending = deque()
        self._unopened = {}
        self._details = {}
        self._children = {}
        for node in proposal.nodes:
            self._children.setdefault(node.location.parent, []).append(node)

        root.title("File Chisel — Proposed folder structure")
        root.minsize(760, 520)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame = ttk_module.Frame(root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk_module.Label(frame, text="Proposed folder structure", font=("TkDefaultFont", 16, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w",
        )
        ttk_module.Label(
            frame, text="Preview only. No files moved. Move planning and filesystem safety checks are still required.",
            wraplength=700, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 8))
        summary = ttk_module.Frame(frame)
        summary.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        summary.columnconfigure(0, weight=1)
        summary_text = tk_module.Text(summary, height=4, wrap="word")
        summary_text.insert("1.0", "AI explanation (unverified):\n" + proposal.summary)
        summary_text.configure(state="disabled")
        summary_scroll = ttk_module.Scrollbar(summary, orient="vertical", command=summary_text.yview)
        summary_text.configure(yscrollcommand=summary_scroll.set)
        summary_text.grid(row=0, column=0, sticky="ew")
        summary_scroll.grid(row=0, column=1, sticky="ns")

        self.tree = ttk_module.Treeview(
            frame, columns=("type", "status"), show="tree headings", selectmode="browse", height=12,
        )
        for column, title in (("#0", "Name"), ("type", "Type"), ("status", "Proposal")):
            self.tree.heading(column, text=title)
        self.tree.column("#0", width=300, minwidth=160)
        self.tree.column("type", width=110, stretch=False)
        self.tree.column("status", width=260, minwidth=200)
        vertical = ttk_module.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk_module.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=3, column=0, sticky="nsew")
        vertical.grid(row=3, column=1, sticky="ns")
        horizontal.grid(row=4, column=0, sticky="ew")
        detail_frame = ttk_module.Frame(frame)
        detail_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        detail_frame.columnconfigure(0, weight=1)
        self.detail = tk_module.Text(detail_frame, height=5, wrap="word")
        self.detail.insert("1.0", "Select a row to inspect its source and the AI's reason.")
        self.detail.configure(state="disabled")
        detail_scroll = ttk_module.Scrollbar(detail_frame, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.detail.grid(row=0, column=0, sticky="ew")
        detail_scroll.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<<TreeviewOpen>>", self._opened)
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        for name, status in proposal.roots:
            self._add(
                "", ProposalLocation(name, ""), "directory",
                "Scanned root" if status == "success" else "Scan failed; contents unknown",
                "Root retained. " + ("Expand to inspect the proposal." if status == "success"
                                     else "No proposal can reference this failed root."),
            )
        root.protocol("WM_DELETE_WINDOW", self.close)

    def _add(self, parent, location, entry_type, status, details):
        item = self.tree.insert(
            parent, "end", text=location.relative_path.rsplit("/", 1)[-1] or location.root,
            values=(self.TYPE_LABELS[entry_type], status),
        )
        self._details[item] = details
        if entry_type == "directory" and self._children.get(location):
            placeholder = self.tree.insert(item, "end", text="Expand to view proposal")
            self._unopened[item] = (location, placeholder)

    def _opened(self, event=None):
        if self._closed:
            return
        parent = self.tree.focus()
        pending = self._unopened.pop(parent, None)
        if pending is not None:
            location, placeholder = pending
            self._pending.append((parent, iter(self._children[location]), placeholder))
            self._schedule()

    def _schedule(self):
        if self._pending and self._after_id is None:
            self._after_id = self.root.after(1, self._insert_batch)

    def _insert_batch(self):
        if self._closed:
            return
        self._after_id = None
        for _ in range(self.BATCH_SIZE):
            if not self._pending:
                break
            parent, children, placeholder = self._pending.popleft()
            try:
                node = next(children)
            except StopIteration:
                self.tree.delete(placeholder)
                continue
            source = f"{node.source.root}/{node.source.relative_path}" if node.source else "New folder"
            self._add(
                parent, node.location, node.entry_type, node.status,
                f"Source: {source}\nProposed: {node.location.root}/{node.location.relative_path}\n"
                f"{node.reason}",
            )
            self._pending.append((parent, children, placeholder))
        self._schedule()

    def _selected(self, event=None):
        if self._closed:
            return
        selected = self.tree.selection()
        if selected:
            self.detail.configure(state="normal")
            self.detail.delete("1.0", "end")
            self.detail.insert("1.0", self._details.get(selected[0], "Expand the folder to load its entries."))
            self.detail.configure(state="disabled")

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
            self._after_id = None
        self._pending.clear()
        self.root.destroy()


def open_proposal_preview(parent, proposal: FolderProposal) -> ProposalPreview:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Toplevel(parent)
    try:
        return ProposalPreview(root, proposal, tk_module=tk, ttk_module=ttk)
    except Exception:
        root.destroy()
        raise
