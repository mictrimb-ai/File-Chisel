# AI folder proposal format (version 1)

The app supplies a complete request through **Copy AI request**. It contains
metadata and asks the AI to propose its own initial organization. Revisions happen
in that conversation. The final response must be a plain JSON object, without
Markdown fences or surrounding prose.

This example describes a new folder and one file placement. Substitute actual
scanned locations and the exact `inventory_id` from the copied request:

```json
{
  "schema_version": 1,
  "inventory_id": "COPY THE ID FROM YOUR REQUEST",
  "summary": "Group this loose report. Its contents and purpose remain uncertain.",
  "folders": [
    {
      "root": "Documents",
      "relative_path": "Writing",
      "reason": "Collect loose written material."
    }
  ],
  "placements": [
    {
      "source": {"root": "Documents", "relative_path": "report.txt"},
      "destination": {"root": "Documents", "relative_path": "Writing/report.txt"},
      "reason": "The filename suggests a report."
    }
  ]
}
```

## Meaning

- `schema_version` is the integer `1`.
- `inventory_id` identifies the exported metadata used by this proposal. It is
  computed locally from the deterministic export; copy it unchanged. It is not a
  signature, AI authentication, or proof that the filesystem has stayed unchanged.
- `summary` contains the AI's overall explanation and uncertainty.
- `folders` lists only new folders and a reason for each. Nested parents must
  exist in the scanned inventory or also be explicitly listed here.
- `placements` assigns scanned regular files to proposed locations, once per
  source. Each destination has a reason. Omitted entries retain their locations.
- Either list may be empty. Reasons and summary must contain 1–4,000 characters.
- Only successfully scanned Documents, Downloads, and Desktop roots may appear
  as sources or destinations. Cross-root file placement is supported.
- Existing directories, symbolic links, and other entry types stay in place in
  version 1. Empty directories are preserved. There is no deletion instruction.

## Import and preview boundaries

The importer rejects unsupported or missing fields, duplicate JSON keys, invalid
types, unknown or repeated sources, mismatched inventories, unsafe relative path
syntax, conflicting projected locations, and missing or non-directory parents.
It conservatively rejects case-insensitive and Unicode-equivalent collisions,
including on case-sensitive volumes; it never silently renames a destination.
An existing directory should not be listed again as a new folder.

Paths use `/`, with no leading slash, empty components, `.` or `..`, backslashes,
colons, control/format characters, or more than 64 components. Paths are limited
to 4,096 characters. Parent spelling must match exactly. Locations are compared
against metadata only: paths are never resolved, traversed, or created on disk.

Imports are limited to 2 MiB and must be regular UTF-8 JSON files (a UTF-8 BOM is
accepted). Symbolic links and special files are refused. Inventories larger than
2 MiB of exported JSON are currently refused for the proposal workflow; scan fewer
folders if necessary. This limit also bounds work done during import on the GUI
thread. The tree inserts expanded children in small batches to remain responsive.

No source contents are opened. Only the proposal JSON explicitly selected in the
file dialog is read. The app neither sends a network request nor executes text
returned by AI. File and folder names are included in the copied request, just as
in the JSON export, and should be reviewed before sharing.

The preview represents a possible final hierarchy, not an executable move plan.
For example, two files exchanging destinations can have an unambiguous final
hierarchy but still need a safe operation sequence. Current filesystem changes,
permissions, links, filesystem-specific naming rules, and operation sequencing
must be checked by the later planner and simulator before execution is offered.
Proposals are held in memory; keep the returned JSON to import it again after a
new matching scan. A new scan always clears the displayed proposal.
