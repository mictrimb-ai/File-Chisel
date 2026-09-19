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

## AI folder proposals

After a successful scan, choose **Copy AI request**. This copies the inventory
metadata and proposal instructions to your clipboard; it does not contact an AI.
Review the names, then paste the request into your chosen AI chat. The AI proposes
an initial organization and explains its assumptions. Discuss revisions there if
you wish, then save its final plain JSON response as a UTF-8 `.json` file.

Choose **Import proposal JSON…** to open that file. A separate window displays the
proposed hierarchy; expand folders and select rows to inspect sources and reasons.
The current scanned hierarchy stays available in the main window. Importing a
revision replaces the earlier preview. Cancellation leaves the current preview
alone; an invalid file clears it and reports the problem. Changing selections,
starting another scan, or closing the app clears the proposal.

This first version supports new folders and regular-file placements between
successfully scanned roots. Unmentioned entries stay in place. Existing folders,
symbolic links, and other entry types are retained, and failed roots remain unknown.
Nothing is moved, created, deleted, or overwritten by proposal import or preview.
The explicitly chosen proposal JSON is read; scanned file contents remain unopened.

See [the proposal format](docs/AI_PROPOSALS.md) for the contract and limits.
Exact move planning, current-filesystem conflict checks, simulation, execution,
and undo remain later work. AI reasons are suggestions, not verified facts.
