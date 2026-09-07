"""A small folder-selection window; importing this module does not load Tk."""

from __future__ import annotations

from pathlib import Path

from file_chisel.folder_selection import (
    BatchScanResult,
    BatchStatus,
    FolderScanCoordinator,
    FolderScanRunner,
    ScanAlreadyRunningError,
    ScanClosedError,
    ScanRunState,
)
from file_chisel.inventory import ScanInventory


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
        self.error: Exception | None = None
        self.status = "Select at least one folder to scan."

    @property
    def selected_keys(self) -> tuple[str, ...]:
        return tuple(option.key for option in self.options if option.key in self._selected)

    @property
    def can_scan(self) -> bool:
        return bool(self._selected) and self.runner.state is ScanRunState.IDLE

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
        self.error = None
        self.status = (
            "Ready to scan selected folders." if self._selected
            else "Select at least one folder to scan."
        )

    def start_scan(self) -> None:
        self.runner.start(self.selected_keys)
        self.result = None
        self.inventory = None
        self.error = None
        self.status = "Scanning selected folders…"

    def poll(self) -> bool:
        completion = self.runner.poll_completion()
        if completion is None:
            return False
        self.result = completion.result
        self.inventory = completion.inventory
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

    def close(self) -> None:
        self.runner.close()
        self.result = None
        self.inventory = None
        self.error = None
        self.status = "Closed."


class FolderSelectionView:
    """Translate Tk events into controller actions on the GUI thread."""

    def __init__(self, root, controller, *, tk_module=None, ttk_module=None) -> None:
        if tk_module is None or ttk_module is None:
            import tkinter as tk_module
            from tkinter import ttk as ttk_module

        self.root = root
        self.controller = controller
        self._closed = False
        self._after_id = None
        root.title("File Chisel")
        root.minsize(620, 360)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame = ttk_module.Frame(root, padding=20)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
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
        self.status_label.configure(text=self.controller.status)
        self.results_label.configure(text="\n".join(self.controller.result_rows))

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
