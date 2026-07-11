# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Windows desktop app (PySide6) that scans a folder, lets the user write "handover" work memos linking specific subfolders, and exports a JSON report and/or a Word (`.docx`) handover document (인수인계서) summarizing the folder's files and the memos. UI text and generated documents are in Korean.

## Commands

```powershell
pip install -r requirements.txt
python app/main.py
```

There is no test suite, linter, or build step configured in this repo.

## Architecture

The app follows a strict one-way data flow: scan → result → mutate (memos) → report.

- `app/services/analysis_result.py` — plain dataclasses that are the shared vocabulary across the app: `AnalysisResult` (root scan output), `ChildFolderSummary` (per top-level subfolder stats), `AnalyzedFile`, `FolderTreeNode`, `WorkMemo`. `AnalysisResult` is frozen except for `analysismode`, which is set via `dataclasses.replace(...)` after scanning (see `main_window.py:_start_analysis`). `WorkMemo` is mutable and auto-updates `updatedat` via a custom `__setattr__` whenever `title`/`content`/`linked_folders` change.
- `app/services/folder_scanner.py` — `scan_folder(root_path)` walks the tree once for root-level aggregate stats (`all_files`, totals, modified-within-N-days counts) and once per immediate child folder for richer per-folder stats (recent files, extension histogram, "priority review" candidates). Two paths exist for a reason: the root-level walk needs `all_files` for memo/report cross-referencing later, while the per-child walk needs different derived stats — don't try to merge them into a single traversal without preserving both outputs. "Priority review" candidate scoring (boost office-document extensions, penalize temp/lock files, hidden/system files, zero-byte files) is duplicated almost identically in `_score_priority_review_candidate` here and `_score_priority_review_file` in `report_writer.py` — keep both in sync if the heuristic changes.
- `app/ui/main_window.py` — the `MainWindow` owns the current `AnalysisResult` and `analyzed_at` timestamp as session state (no persistence beyond the auto-saved `output/analysis_result.json`). Every analysis run auto-saves JSON to `output/analysis_result.json` (`_auto_save_json`); explicit "JSON 저장"/"Word 저장" buttons additionally validate that all memos are complete and prompt the user if recently-modified subfolders have no memo linking to them (`_handle_recent_activity_unlinked_folders`) before exporting.
- `app/ui/memodialog.py` — `MemoDialog` edits the live `memos` list (and `folder_tree`) in place, including a tri-state checkable `QTreeWidget` for picking `linked_folders` (parent/child check state propagation lives in `_update_parent_check_states`/`_set_child_check_states`). Closing the dialog or switching memos triggers unsaved-changes confirmation and completeness validation, mirroring the same validation rules enforced again before export in `main_window.py`.
- `app/services/report_writer.py` — pure functions that turn an `AnalysisResult` + `analyzed_at` into either a JSON-serializable dict (`save_analysis_result_as_json`) or a `python-docx` `Document` (`save_analysis_result_as_word`). Memos are grouped by top-level linked folder (`_group_linked_folders_by_top_level`), and near-duplicate filenames (e.g. versioned drafts) are collapsed via `difflib.SequenceMatcher` (`_deduplicate_similar_file_names`) before picking the top priority-review candidates per folder. `EXTENSION_GROUPS` drives the Korean-labeled file-type statistics table in the Word doc.

### Analysis modes

`MainWindow` exposes three radio-button modes (basic/lite/pro) but only "basic" (`scan_folder` + the report writers above) is implemented. Lite and Pro modes are intentionally unimplemented placeholders — selecting them shows a Korean notice that the run will fall back to basic-mode analysis (`_update_analysis_mode_notice`). Don't implement AI-based lite/pro analysis without explicit instruction; it's out of scope for the current MVP.

### Relative paths

All `relative_path` fields use `/` as separator regardless of OS (`_get_relative_path` replaces `os.sep`). Top-level linked folder grouping throughout `report_writer.py` relies on `relative_path.split("/", 1)[0]`.
