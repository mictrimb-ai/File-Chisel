"""A small folder-selection window; importing this module does not load Tk."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from file_chisel.export import save_export
from file_chisel.folder_selection import (
    BatchScanResult,
    BatchStatus,
    FolderScanCoordinator,
    FolderScanRunner,
    ScanAlreadyRunningError,
    ScanClosedError,
    ScanRunState,
)
from file_chisel.hierarchy import HierarchyIndex
from file_chisel.inventory import ScanInventory
from file_chisel.proposal import FolderProposal, build_ai_request, load_proposal
from file_chisel.proposal_view import open_proposal_preview
from file_chisel.summary import format_summary_lines


class ApplicationController:
    """Keep checkbox selection separate from the explicit scan action."""

    def __init__(
        self, home: Path | None = None, runner: FolderScanRunner | None = None,
    ) -> None:
        self.runner = runner if runner is not None else FolderScanRunner(
            FolderScanCoordinator(home)
        )
        self.options = self.runner.options
        self._selected: set[str] = set()
        self.result: BatchScanResult | None = None
        self.inventory: ScanInventory | None = None
        self.hierarchy: HierarchyIndex | None = None
        self.proposal: FolderProposal | None = None
        self.error: Exception | None = None
        self.status = "Select at least one folder to scan."

    @property
    def selected_keys(self) -> tuple[str, ...]:
        return tuple(option.key for option in self.options if option.key in self._selected)

    @property
    def can_scan(self) -> bool:
        return bool(self._selected) and self.runner.state is ScanRunState.IDLE

    @property
    def can_export(self) -> bool:
        return self.inventory is not None and self.runner.state is ScanRunState.IDLE

    def export_to(self, destination: Path) -> None:
        """Save the completed snapshot at an explicitly selected new path."""

        if not self.can_export:
            raise ValueError("Complete a scan before exporting its inventory.")
        save_export(self.inventory, destination)

    @property
    def can_propose(self) -> bool:
        return self.can_export and bool(self.inventory.successful_roots)

    def ai_request(self) -> str:
        if not self.can_propose:
            raise ValueError("Complete a successful scan before requesting a proposal.")
        return build_ai_request(self.inventory)

    def import_proposal(self, source: Path) -> None:
        if not self.can_propose:
            raise ValueError("Complete a successful scan before importing a proposal.")
        self.proposal = None
        self.proposal = load_proposal(source, self.inventory)

    def set_selected(self, key: str, selected: bool) -> None:
        if self.runner.state is ScanRunState.CLOSED:
            raise ScanClosedError("The scan window is closed.")
        if self.runner.state is ScanRunState.SCANNING:
            raise ScanAlreadyRunningError("Selection is locked during a scan.")
        if key not in {option.key for option in self.options}:
            raise ValueError("Unknown folder selection.")
        if selected:
            self._selected.add(key)
        else:
            self._selected.discard(key)
        self.result = None
        self.inventory = None
        self.hierarchy = None
        self.proposal = None
        self.error = None
        self.status = (
            "Ready to scan selected folders." if self._selected
            else "Select at least one folder to scan."
        )

    def start_scan(self) -> None:
        try:
            self.runner.start(self.selected_keys)
        finally:
            self.result = None
            self.inventory = None
            self.hierarchy = None
            self.proposal = None
            self.error = None
        self.status = "Scanning selected folders…"

    def poll(self) -> bool:
        completion = self.runner.poll_completion()
        if completion is None:
            return False
        self.result = completion.result
        self.inventory = completion.inventory
        self.hierarchy = completion.hierarchy
        self.error = completion.error
        if completion.error is not None:
            self.status = "Scan could not complete because of an unexpected error."
        else:
            self.status = {
                BatchStatus.SUCCESS: "Scan completed successfully.",
                BatchStatus.PARTIAL: "Scan partially completed.",
                BatchStatus.FAILED: "Scan failed.",
            }[completion.result.status]
        return True

    @property
    def result_rows(self) -> tuple[str, ...]:
        if self.result is None:
            return ()
        successes = {item.root: item for item in self.result.successes}
        failures = {item.root: item for item in self.result.failures}
        rows = []
        for option in self.options:
            if option.path in successes:
                count = successes[option.path].entry_count
                noun = "entry" if count == 1 else "entries"
                rows.append(f"{option.label} — {count} {noun}")
            elif option.path in failures:
                rows.append(
                    f"{option.label} ({option.path}) — {failures[option.path].message}"
                )
        return tuple(rows)

    @property
    def summary_lines(self) -> tuple[str, ...]:
        if self.inventory is None:
            return ()
        return format_summary_lines(self.inventory.summary)

    def close(self) -> None:
        self.runner.close()
        self.result = None
        self.inventory = None
        self.hierarchy = None
        self.proposal = None
        self.error = None
        self.status = "Closed."


class InventoryHierarchyView:
    """Render only expanded snapshot branches, yielding between small batches."""

    BATCH_SIZE = 100
    TYPE_LABELS = {
        "directory": "Folder", "file": "File",
        "symlink": "Symbolic link", "other": "Other",
    }

    def __init__(self, root, parent, options, ttk_module) -> None:
        self.root = root
        self.options = options
        self._inventory = None
        self._index = None
        self._closed = False
        self._generation = 0
        self._after_id = None
        self._unopened = {}
        self._pending = deque()

        frame = ttk_module.Frame(parent)
        frame.grid(row=10, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk_module.Label(frame, text="Scanned folder hierarchy").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4),
        )
        self.tree = ttk_module.Treeview(
            frame, columns=("type", "status"), show="tree headings", height=9,
        )
        self.tree.heading("#0", text="Name")
        self.tree.heading("type", text="Type")
        self.tree.heading("status", text="Status")
        self.tree.column("#0", width=280, minwidth=180)
        self.tree.column("type", width=100, minwidth=100, stretch=False)
        self.tree.column("status", width=400, minwidth=220)
        vertical = ttk_module.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk_module.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        vertical.grid(row=1, column=1, sticky="ns")
        horizontal.grid(row=2, column=0, sticky="ew")
        self.tree.bind("<<TreeviewOpen>>", self._opened)

    def show(self, inventory, index) -> None:
        if self._closed or inventory is self._inventory:
            return
        self._clear()
        self._inventory, self._index = inventory, index
        if inventory is None:
            return
        successes = {item.root: item for item in inventory.successful_roots}
        failures = {item.root: item for item in inventory.failed_roots}
        for option in self.options:
            if option.path in successes:
                self._add_entry("", option.label, option.path, "directory")
            elif option.path in failures:
                self.tree.insert(
                    "", "end", text=option.label,
                    values=("Folder", f"Scan failed: {failures[option.path].message} Contents unknown."),
                )

    def _add_entry(self, parent, name, path, entry_type) -> None:
        is_folder = entry_type == "directory"
        has_children = is_folder and bool(self._index.children.get(path))
        status = "Empty folder" if is_folder and not has_children else ""
        item = self.tree.insert(
            parent, "end", text=name,
            values=(self.TYPE_LABELS[entry_type], status),
        )
        if has_children:
            placeholder = self.tree.insert(item, "end", text="Expand to view contents")
            self._unopened[item] = (path, placeholder)

    def _opened(self, event=None) -> None:
        if self._closed:
            return
        # Tk sends this event before setting the focused item's open flag.
        item = self.tree.focus()
        unopened = self._unopened.pop(item, None)
        if unopened is None:
            return
        path, placeholder = unopened
        self.tree.item(placeholder, text="Loading contents…")
        self._pending.append((item, iter(self._index.children[path]), placeholder))
        self._schedule_batch()

    def _schedule_batch(self) -> None:
        if self._pending and self._after_id is None:
            generation = self._generation
            self._after_id = self.root.after(1, lambda: self._insert_batch(generation))

    def _insert_batch(self, generation) -> None:
        if self._closed or generation != self._generation:
            return
        self._after_id = None
        for _ in range(self.BATCH_SIZE):
            if not self._pending:
                break
            item, children, placeholder = self._pending.popleft()
            if not self.tree.exists(item):
                continue
            try:
                entry = next(children)
            except StopIteration:
                self.tree.delete(placeholder)
                continue
            self._add_entry(item, entry.name, entry.path, entry.entry_type)
            # Round-robin keeps another expanded folder from waiting for a huge one.
            self._pending.append((item, children, placeholder))
        self._schedule_batch()

    def _clear(self) -> None:
        self._generation += 1
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
            self._after_id = None
        self._pending.clear()
        self._unopened.clear()
        roots = self.tree.get_children()
        if roots:
            self.tree.delete(*roots)
        self._inventory = self._index = None

    def close(self) -> None:
        if not self._closed:
            self._clear()
            self._closed = True


class FolderSelectionView:
    """Translate Tk events into controller actions on the GUI thread."""

    def __init__(
        self, root, controller, *, tk_module=None, ttk_module=None, save_dialog=None,
        open_dialog=None, preview_factory=open_proposal_preview,
    ) -> None:
        if tk_module is None or ttk_module is None:
            import tkinter as tk_module
            from tkinter import ttk as ttk_module

        self.root = root
        self.controller = controller
        self._save_dialog = save_dialog
        self._open_dialog = open_dialog
        self._preview_factory = preview_factory
        self._proposal_preview = None
        self._preview_proposal = None
        self._closed = False
        self._after_id = None
        root.title("File Chisel")
        root.minsize(620, 680)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame = ttk_module.Frame(root, padding=20)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(10, weight=1)
        ttk_module.Label(
            frame, text="Choose folders to scan", font=("TkDefaultFont", 18, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk_module.Label(
            frame, text="Read-only scan of file and folder metadata. File contents stay unopened.",
            wraplength=580,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 16))

        self.variables = {}
        self.checkboxes = []
        for row, option in enumerate(controller.options, start=2):
            variable = tk_module.BooleanVar(master=root, value=False)
            self.variables[option.key] = variable
            checkbox = ttk_module.Checkbutton(
                frame, text=option.label, variable=variable,
                command=lambda key=option.key: self._selection_changed(key),
            )
            checkbox.grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=6)
            self.checkboxes.append(checkbox)
            ttk_module.Label(frame, text=str(option.path), wraplength=440).grid(
                row=row, column=1, sticky="w", pady=6,
            )

        self.scan_button = ttk_module.Button(
            frame, text="Scan selected folders", command=self.start_scan,
        )
        self.scan_button.grid(row=5, column=0, columnspan=2, sticky="w", pady=(16, 12))
        self.status_label = ttk_module.Label(frame, wraplength=580, justify="left")
        self.status_label.grid(row=6, column=0, columnspan=2, sticky="w")
        self.results_label = ttk_module.Label(frame, wraplength=580, justify="left")
        self.results_label.grid(row=7, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk_module.Label(frame, text="Inventory summary").grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(12, 0),
        )
        self.summary_label = ttk_module.Label(
            frame, wraplength=580, justify="left",
        )
        self.summary_label.grid(row=9, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.hierarchy_view = InventoryHierarchyView(root, frame, controller.options, ttk_module)
        actions = ttk_module.Frame(frame)
        actions.grid(row=11, column=0, columnspan=2, sticky="w", pady=(12, 4))
        self.export_button = ttk_module.Button(
            actions, text="Save inventory JSON…", command=self.save_inventory,
        )
        self.export_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.request_button = ttk_module.Button(
            actions, text="Copy AI request", command=self.copy_ai_request,
        )
        self.request_button.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.import_button = ttk_module.Button(
            actions, text="Import proposal JSON…", command=self.import_proposal,
        )
        self.import_button.grid(row=0, column=2, sticky="w")
        ttk_module.Label(
            frame,
            text="Exports and AI requests include file and folder names. Review before sharing. "
                 "Paste the request into your AI chat, discuss revisions, then import its JSON reply.",
            wraplength=580, justify="left",
        ).grid(row=12, column=0, columnspan=2, sticky="w")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._render()

    def _selection_changed(self, key: str) -> None:
        if self._closed or self.controller.runner.state is not ScanRunState.IDLE:
            return
        self.controller.set_selected(key, self.variables[key].get())
        self._render()

    def _render(self) -> None:
        if self._closed:
            return
        busy = self.controller.runner.state is not ScanRunState.IDLE
        for key, variable in self.variables.items():
            variable.set(key in self.controller.selected_keys)
        for checkbox in self.checkboxes:
            checkbox.configure(state="disabled" if busy else "normal")
        self.scan_button.configure(state="normal" if self.controller.can_scan else "disabled")
        self.export_button.configure(state="normal" if self.controller.can_export else "disabled")
        for button in (self.request_button, self.import_button):
            button.configure(state="normal" if self.controller.can_propose else "disabled")
        if self._preview_proposal is not self.controller.proposal:
            self._close_proposal_preview()
        self.status_label.configure(text=self.controller.status)
        self.results_label.configure(text="\n".join(self.controller.result_rows))
        self.summary_label.configure(text="\n".join(self.controller.summary_lines))
        self.hierarchy_view.show(self.controller.inventory, self.controller.hierarchy)

    def _close_proposal_preview(self) -> None:
        if self._proposal_preview is not None:
            self._proposal_preview.close()
        self._proposal_preview = self._preview_proposal = None

    def copy_ai_request(self) -> None:
        if self._closed or not self.controller.can_propose:
            return
        try:
            request = self.controller.ai_request()
        except ValueError as error:
            self.controller.status = f"Request not copied: {error}"
        else:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(request)
            except Exception:
                self.controller.status = "Clipboard unavailable. Try copying the request again."
            else:
                self.controller.status = "AI request copied with inventory. Review it, then paste into your AI chat."
        self._render()

    def import_proposal(self) -> None:
        if self._closed or not self.controller.can_propose:
            return
        if self._open_dialog is None:
            from tkinter import filedialog

            dialog = filedialog.askopenfilename
        else:
            dialog = self._open_dialog
        try:
            source = dialog(
                parent=self.root, title="Import AI proposal JSON",
                filetypes=[("JSON files", "*.json")],
            )
            if not source or self._closed:
                return
            self.controller.import_proposal(Path(source))
        except OSError:
            self.controller.status = "Proposal not imported. Select a readable regular JSON file."
        except ValueError as error:
            self.controller.status = f"Proposal not imported: {error}"
        else:
            self._close_proposal_preview()
            try:
                self._proposal_preview = self._preview_factory(self.root, self.controller.proposal)
            except Exception:
                self.controller.proposal = None
                self.controller.status = "The proposal window could not open. Try importing it again."
            else:
                self._preview_proposal = self.controller.proposal
                self.controller.status = "Proposal imported for preview. No files moved."
        self._render()

    def save_inventory(self) -> None:
        if self._closed or not self.controller.can_export:
            return
        if self._save_dialog is None:
            from tkinter import filedialog

            dialog = filedialog.asksaveasfilename
        else:
            dialog = self._save_dialog
        try:
            destination = dialog(
                parent=self.root, title="Save inventory JSON",
                defaultextension=".json", filetypes=[("JSON files", "*.json")],
            )
            if not destination:
                return
            self.controller.export_to(Path(destination))
        except FileExistsError:
            self.controller.status = "Export not saved: that file already exists. Choose a new name."
        except (OSError, ValueError):
            self.controller.status = "Export not saved. Choose another location and try again."
        else:
            self.controller.status = "Inventory JSON saved. Review its names before sharing."
        self._render()

    def start_scan(self) -> None:
        if self._closed or not self.controller.can_scan:
            return
        try:
            self.controller.start_scan()
        except Exception:
            # A thread-start failure must leave the window usable for another try.
            self.controller.result = None
            self.controller.status = "The scan could not start. Please try again."
            self._render()
            return
        self._render()
        self._after_id = self.root.after(50, self._poll)

    def _poll(self) -> None:
        self._after_id = None
        if self._closed:
            return
        self.controller.poll()
        self._render()
        if self.controller.runner.state is ScanRunState.SCANNING:
            self._after_id = self.root.after(50, self._poll)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
            self._after_id = None
        self.controller.close()
        self.hierarchy_view.close()
        self._close_proposal_preview()
        self.root.destroy()


def main(home: Path | None = None) -> None:
    """Launch normally, or inject a temporary home for a manual GUI check."""

    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as error:
        raise SystemExit("Tkinter is unavailable. Use a Python installation with Tk support.") from error
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise SystemExit(f"Could not open the File Chisel window: {error}") from error

    view = None
    try:
        view = FolderSelectionView(
            root, ApplicationController(home), tk_module=tk, ttk_module=ttk,
        )
        root.mainloop()
    finally:
        if view is not None:
            view.close()
        else:
            root.destroy()


if __name__ == "__main__":
    main()
