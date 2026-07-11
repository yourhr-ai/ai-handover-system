import os
import stat
import time
from dataclasses import dataclass, field

from app.services.analysis_result import (
    AnalyzedFile,
    AnalysisResult,
    ChildFolderSummary,
    ExtensionStat,
    FolderTreeNode,
    PriorityReviewFileCandidate,
    RecentModifiedFile,
)


SECONDS_PER_DAY = 24 * 60 * 60
DOCUMENT_EXTENSIONS = {
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".pdf",
    ".ppt",
    ".pptx",
    ".hwp",
}
SUPPORTING_EXTENSIONS = {".tmp", ".log"}
SMALL_FILE_SIZE_BYTES = 1024
WINDOWS_HIDDEN_OR_SYSTEM_ATTRIBUTES = (
    getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0)
    | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0)
)


@dataclass
class _ScanStats:
    total_folder_count: int = 0
    total_file_count: int = 0
    total_size_bytes: int = 0
    modified_within_7_days_count: int = 0
    modified_within_30_days_count: int = 0
    modified_within_90_days_count: int = 0
    error_count: int = 0
    recent_modified_files: list[tuple[float, str, str]] = field(default_factory=list)
    extension_counts: dict[str, int] = field(default_factory=dict)
    priority_review_file_candidates: list[
        tuple[float, str, str, int, str, bool]
    ] = field(default_factory=list)
    all_files: list[AnalyzedFile] = field(default_factory=list)


def scan_folder(root_folder_path: str) -> AnalysisResult:
    now = time.time()
    thresholds = {
        7: now - (7 * SECONDS_PER_DAY),
        30: now - (30 * SECONDS_PER_DAY),
        90: now - (90 * SECONDS_PER_DAY),
    }

    root_stats = _scan_tree(
        root_folder_path,
        root_folder_path,
        thresholds,
        collect_recent_files=False,
        collect_extension_stats=False,
        collect_priority_review_candidates=False,
        collect_all_files=True,
    )
    child_folder_summaries, child_listing_error_count = _scan_child_folders(
        root_folder_path,
        thresholds,
    )
    root_stats.error_count += child_listing_error_count

    return AnalysisResult(
        root_folder_path=root_folder_path,
        total_folder_count=root_stats.total_folder_count,
        total_file_count=root_stats.total_file_count,
        total_size_bytes=root_stats.total_size_bytes,
        modified_within_7_days_count=root_stats.modified_within_7_days_count,
        modified_within_30_days_count=root_stats.modified_within_30_days_count,
        modified_within_90_days_count=root_stats.modified_within_90_days_count,
        error_count=root_stats.error_count,
        child_folder_summaries=child_folder_summaries,
        folder_tree=_scan_folder_tree(root_folder_path),
        all_files=root_stats.all_files,
    )


def _scan_tree(
    root_folder_path: str,
    analysis_root_folder_path: str,
    thresholds: dict[int, float],
    collect_recent_files: bool,
    collect_extension_stats: bool,
    collect_priority_review_candidates: bool,
    collect_all_files: bool,
) -> _ScanStats:
    stats = _ScanStats()
    folders_to_scan = [root_folder_path]

    while folders_to_scan:
        current_folder = folders_to_scan.pop()

        try:
            with os.scandir(current_folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stats.total_folder_count += 1
                            folders_to_scan.append(entry.path)
                            continue

                        if not entry.is_file(follow_symlinks=False):
                            continue

                        file_stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        stats.error_count += 1
                        continue

                    stats.total_file_count += 1
                    stats.total_size_bytes += file_stat.st_size
                    if collect_recent_files:
                        stats.recent_modified_files.append(
                            (
                                file_stat.st_mtime,
                                entry.name,
                                _get_relative_path(
                                    analysis_root_folder_path,
                                    entry.path,
                                ),
                            )
                        )
                    if collect_extension_stats:
                        extension = _get_extension_label(entry.name)
                        stats.extension_counts[extension] = (
                            stats.extension_counts.get(extension, 0) + 1
                        )
                    if collect_priority_review_candidates:
                        stats.priority_review_file_candidates.append(
                            (
                                file_stat.st_mtime,
                                entry.name,
                                _get_relative_path(
                                    analysis_root_folder_path,
                                    entry.path,
                                ),
                                file_stat.st_size,
                                _get_extension_label(entry.name),
                                _is_hidden_or_system_file(file_stat),
                            )
                        )
                    if collect_all_files:
                        stats.all_files.append(
                            AnalyzedFile(
                                file_name=entry.name,
                                relative_path=_get_relative_path(
                                    analysis_root_folder_path,
                                    entry.path,
                                ),
                                modified_at=time.strftime(
                                    "%Y-%m-%d %H:%M:%S",
                                    time.localtime(file_stat.st_mtime),
                                ),
                                modified_timestamp=file_stat.st_mtime,
                                size_bytes=file_stat.st_size,
                                is_hidden_or_system=_is_hidden_or_system_file(
                                    file_stat
                                ),
                            )
                        )

                    if file_stat.st_mtime >= thresholds[7]:
                        stats.modified_within_7_days_count += 1
                    if file_stat.st_mtime >= thresholds[30]:
                        stats.modified_within_30_days_count += 1
                    if file_stat.st_mtime >= thresholds[90]:
                        stats.modified_within_90_days_count += 1
        except OSError:
            stats.error_count += 1

    return stats


def _scan_child_folders(
    root_folder_path: str,
    thresholds: dict[int, float],
) -> tuple[list[ChildFolderSummary], int]:
    child_folders: list[os.DirEntry[str]] = []
    error_count = 0

    try:
        with os.scandir(root_folder_path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        child_folders.append(entry)
                except OSError:
                    error_count += 1
    except OSError:
        return [], error_count + 1

    summaries = []
    for child_folder in sorted(child_folders, key=lambda entry: entry.name.lower()):
        stats = _scan_tree(
            child_folder.path,
            root_folder_path,
            thresholds,
            collect_recent_files=True,
            collect_extension_stats=True,
            collect_priority_review_candidates=True,
            collect_all_files=False,
        )
        recent_modified_files = _format_recent_modified_files(
            stats.recent_modified_files,
        )
        extension_stats = _format_extension_stats(stats.extension_counts)
        priority_review_file_candidates = _format_priority_review_file_candidates(
            stats.priority_review_file_candidates,
        )
        summaries.append(
            ChildFolderSummary(
                folder_name=child_folder.name,
                relative_path=_get_relative_path(root_folder_path, child_folder.path),
                total_folder_count=stats.total_folder_count,
                total_file_count=stats.total_file_count,
                total_size_bytes=stats.total_size_bytes,
                modified_within_30_days_count=stats.modified_within_30_days_count,
                recent_modified_files=recent_modified_files,
                extension_stats=extension_stats,
                priority_review_file_candidates=priority_review_file_candidates,
            )
        )

    return summaries, error_count


def _format_recent_modified_files(
    recent_modified_files: list[tuple[float, str, str]],
) -> list[RecentModifiedFile]:
    sorted_files = sorted(
        recent_modified_files,
        key=lambda file_info: file_info[0],
        reverse=True,
    )
    return [
        RecentModifiedFile(
            file_name=file_name,
            relative_path=relative_path,
            modified_at=time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(modified_at),
            ),
        )
        for modified_at, file_name, relative_path in sorted_files[:10]
    ]


def _format_extension_stats(extension_counts: dict[str, int]) -> list[ExtensionStat]:
    sorted_stats = sorted(
        extension_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )
    return [
        ExtensionStat(extension=extension, file_count=file_count)
        for extension, file_count in sorted_stats[:10]
    ]


def _format_priority_review_file_candidates(
    candidates: list[tuple[float, str, str, int, str, bool]],
) -> list[PriorityReviewFileCandidate]:
    sorted_candidates = sorted(
        candidates,
        key=lambda candidate: (
            _score_priority_review_candidate(candidate),
            candidate[0],
        ),
        reverse=True,
    )
    return [
        PriorityReviewFileCandidate(
            file_name=file_name,
            relative_path=relative_path,
            modified_at=time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(modified_at),
            ),
            size_bytes=size_bytes,
        )
        for (
            modified_at,
            file_name,
            relative_path,
            size_bytes,
            _extension,
            _is_hidden_or_system,
        ) in sorted_candidates[:5]
    ]


def _score_priority_review_candidate(
    candidate: tuple[float, str, str, int, str, bool],
) -> int:
    (
        _modified_at,
        file_name,
        _relative_path,
        size_bytes,
        extension,
        is_hidden_or_system,
    ) = candidate
    score = 0

    if extension in DOCUMENT_EXTENSIONS:
        score += 40
    if size_bytes >= SMALL_FILE_SIZE_BYTES:
        score += 10
    if size_bytes == 0:
        score -= 50
    if file_name.startswith("~$"):
        score -= 40
    if extension in SUPPORTING_EXTENSIONS:
        score -= 20
    if file_name.startswith(".") or is_hidden_or_system:
        score -= 20

    return score


def _is_hidden_or_system_file(file_stat: os.stat_result) -> bool:
    file_attributes = getattr(file_stat, "st_file_attributes", 0)
    return bool(file_attributes & WINDOWS_HIDDEN_OR_SYSTEM_ATTRIBUTES)


def _get_extension_label(file_name: str) -> str:
    _, extension = os.path.splitext(file_name)
    if not extension:
        return "[no extension]"

    return extension.lower()


def _get_relative_path(root_folder_path: str, target_path: str) -> str:
    return os.path.relpath(target_path, root_folder_path).replace(os.sep, "/")


def _scan_folder_tree(root_folder_path: str) -> list[FolderTreeNode]:
    try:
        with os.scandir(root_folder_path) as entries:
            child_folders = [
                entry
                for entry in entries
                if entry.is_dir(follow_symlinks=False)
            ]
    except OSError:
        return []

    nodes = []
    for folder in sorted(child_folders, key=lambda entry: entry.name.lower()):
        nodes.append(_build_folder_tree_node(root_folder_path, folder.path))

    return nodes


def _build_folder_tree_node(
    root_folder_path: str,
    folder_path: str,
) -> FolderTreeNode:
    children = []
    try:
        with os.scandir(folder_path) as entries:
            child_folders = [
                entry
                for entry in entries
                if entry.is_dir(follow_symlinks=False)
            ]
    except OSError:
        child_folders = []

    for child in sorted(child_folders, key=lambda entry: entry.name.lower()):
        children.append(_build_folder_tree_node(root_folder_path, child.path))

    return FolderTreeNode(
        name=os.path.basename(folder_path),
        relative_path=_get_relative_path(root_folder_path, folder_path),
        children=children,
    )
