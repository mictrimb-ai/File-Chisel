# File Chisel

File Chisel is a local macOS application that helps users safely reorganize their Documents, Downloads, and Desktop folders.

It guides the user through a repeatable process:

1. Scan the selected folders without reading file contents.
2. Build a structured inventory from file and folder metadata.
3. Generate a proposed reorganization plan.
4. Simulate and preview the complete plan without changing the original structure.
5. Apply the entire plan only after explicit batch approval.
6. Record every operation so the changes can be reversed.
7. Repeat the process to refine the organization further.

## Project Status

File Chisel is currently in early development and is not ready for real filesystem use.

## Inventory JSON export

After an explicit scan, choose **Save inventory JSON…** to create a new local file.
The export includes root-relative file and folder names, entry types and sizes,
summary totals, empty folders, likely duplicate groups, and failed-root categories.
It omits absolute paths, modification times, file contents, and detailed errors.
Saving never overwrites an existing file or sends data to an AI service.

**Review the JSON before sharing it.** Filenames and folder names may contain
personal information even without an absolute path. Likely duplicates are based
on names and sizes; they do not establish identical contents.
