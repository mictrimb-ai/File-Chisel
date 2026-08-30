# File Chisel — Agent Instructions

## Project Purpose

File Chisel is a local macOS application that helps a user safely reorganize their Documents, Downloads, and Desktop folders through a repeatable scan, recommendation, simulation, approval, execution, and undo process.

## Non-Negotiable Safety Rules

1. Never delete user files or folders.
2. Never overwrite an existing file or folder.
3. Never read file contents; use metadata only.
4. Never make real filesystem changes without approval of the complete batch plan.
5. Simulate the complete plan before offering permanent implementation.
6. Record every executed operation in a complete undo log.
7. Stop and report any conflict or uncertain operation instead of guessing.

 ## Agent Workflow

1. Read this file and `ROADMAP.md` before making changes.
2. Work only on the feature or task assigned to the current branch.
3. Do not modify unrelated code or expand the task without approval.
4. Never work directly on `main`; use a dedicated feature branch.
5. Check the current branch and working-tree status before editing.
6. Add or update tests for every behavioral change.
7. Run the relevant tests before declaring work complete.
8. Summarize all changed files, test results, and unresolved risks.
9. Do not merge a feature branch into `main`; leave that decision for review.

## Pull Request Integration

1. Open or mark a pull request ready only after local validation passes.
2. Wait for all requested automated and human reviews to finish before merging.
3. Resolve or explicitly document every actionable review finding.
4. After merging, synchronize local `main` and rerun relevant tests before deleting branches.
