from dataclasses import dataclass, field
from datetime import datetime


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class WorkMemo:
    title: str
    content: str
    linked_folders: list[str] = field(default_factory=list)
    linked_files: list[str] = field(default_factory=list)
    linked_emails: list[str] = field(default_factory=list)
    linked_kakao_files: list[str] = field(default_factory=list)
    ai_result: dict[str, str] | None = None
    ai_result_content_hash: str | None = None
    createdat: str = field(default_factory=_now_iso)
    updatedat: str = field(default_factory=_now_iso)

    def __setattr__(self, name: str, value: object) -> None:
        object.__setattr__(self, name, value)
        if name in {
            "title",
            "content",
            "linked_folders",
            "linked_files",
            "linked_emails",
            "linked_kakao_files",
        } and hasattr(self, "createdat"):
            object.__setattr__(self, "updatedat", _now_iso())


@dataclass
class HandoverQA:
    answers: list[str] = field(default_factory=lambda: ["", "", "", "", ""])
    updatedat: str = field(default_factory=_now_iso)


@dataclass(frozen=True)
class FolderTreeNode:
    name: str
    relative_path: str
    children: list["FolderTreeNode"] = field(default_factory=list)


@dataclass(frozen=True)
class ExtensionStat:
    extension: str
    file_count: int


@dataclass(frozen=True)
class RecentModifiedFile:
    file_name: str
    relative_path: str
    modified_at: str


@dataclass(frozen=True)
class PriorityReviewFileCandidate:
    file_name: str
    relative_path: str
    modified_at: str
    size_bytes: int


@dataclass(frozen=True)
class AnalyzedFile:
    file_name: str
    relative_path: str
    modified_at: str
    modified_timestamp: float
    size_bytes: int
    is_hidden_or_system: bool = False


@dataclass(frozen=True)
class ChildFolderSummary:
    folder_name: str
    relative_path: str
    total_folder_count: int
    total_file_count: int
    total_size_bytes: int
    modified_within_30_days_count: int
    recent_modified_files: list[RecentModifiedFile]
    extension_stats: list[ExtensionStat]
    priority_review_file_candidates: list[PriorityReviewFileCandidate]


@dataclass(frozen=True)
class AnalysisResult:
    root_folder_path: str
    total_folder_count: int
    total_file_count: int
    total_size_bytes: int
    modified_within_7_days_count: int
    modified_within_30_days_count: int
    modified_within_90_days_count: int
    error_count: int
    child_folder_summaries: list[ChildFolderSummary]
    folder_tree: list[FolderTreeNode] = field(default_factory=list)
    memos: list[WorkMemo] = field(default_factory=list)
    all_files: list[AnalyzedFile] = field(default_factory=list)
    analysismode: str = "basic"
    handover_qa: HandoverQA = field(default_factory=HandoverQA)
