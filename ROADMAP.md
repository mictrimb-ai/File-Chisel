# File Chisel Roadmap

Status key: ✅ Complete | 📍 Current | ⬜ Not started

## Phase 1 — Project Foundation ✅

- [x] Define the project purpose and first-version scope
- [x] Establish non-negotiable safety rules
- [x] Create the local Git repository
- [x] Connect the repository to GitHub
- [x] Create `AGENTS.md`
- [x] Create `ROADMAP.md`
- [x] Create `README.md`
- [x] Create the initial project structure
- [x] Commit and push the foundation to `main`

## Phase 2 — Filesystem Scanner 📍

- [ ] Let the user select Documents, Downloads, and Desktop
- [x] Recursively scan files and folders
- [x] Collect metadata without reading file contents
- [ ] Detect empty folders and likely duplicate files
- [x] Add scanner tests

## Phase 3 — Inventory and Visualization ⬜

- [ ] Store each scan as a structured inventory
- [ ] Display the current folder hierarchy
- [ ] Summarize file types, sizes, and problem areas
- [ ] Export a privacy-conscious inventory for AI analysis

## Phase 4 — Reorganization Planner ⬜

- [ ] Generate a proposed folder structure
- [ ] Produce an exact move plan
- [ ] Detect conflicts and unsafe operations
- [ ] Explain the reasoning behind the proposal

## Phase 5 — Safe Simulation ⬜

- [ ] Simulate the complete move plan
- [ ] Preserve the original filesystem
- [ ] Compare current and proposed structures
- [ ] Block plans containing unresolved conflicts

## Phase 6 — Batch Approval and Execution ⬜

- [ ] Present the complete plan for one-action approval
- [ ] Require explicit confirmation
- [ ] Execute only the approved batch
- [ ] Never delete or overwrite files
- [ ] Record every completed operation

## Phase 7 — Undo and Recovery ⬜

- [ ] Generate a complete undo log
- [ ] Validate that reversal is safe
- [ ] Restore the previous structure on request
- [ ] Report incomplete or blocked reversals

## Phase 8 — Repeatable Refinement ⬜

- [ ] Rescan the reorganized filesystem
- [ ] Compare results between iterations
- [ ] Run another recommendation cycle
- [ ] Preserve the history of every iteration

## Phase 9 — Final Validation and Packaging ⬜

- [ ] Test the complete workflow
- [ ] Test failure and recovery scenarios
- [ ] Improve the visual interface
- [ ] Write installation and usage instructions
- [ ] Package File Chisel for local macOS use