from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.document_family import DocumentFamilyEngine

if TYPE_CHECKING:
    from src.core.document_summarizer import DocumentSummary


@dataclass
class WorkCluster:
    cluster_key: str
    category: str
    documents: list["DocumentSummary"] = field(default_factory=list)
    representative_docs: list["DocumentSummary"] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    latest_modified: _dt.datetime | None = None


@dataclass
class WorkUnitDetectionResult:
    mode: str
    project_score: int
    work_cluster_score: int
    reasons: list[str] = field(default_factory=list)
    project_groups: dict[str, list["DocumentSummary"]] = field(default_factory=dict)
    work_clusters: list[WorkCluster] = field(default_factory=list)


PROJECT_MODE = "PROJECT_MODE"
WORK_CLUSTER_MODE = "WORK_CLUSTER_MODE"
MIXED_MODE = "MIXED_MODE"

_CONTAINER_FOLDERS = {
    "result", "results", "output", "outputs", "document", "documents",
    "자료", "문서", "기타", "misc",
}

_STOPWORDS = _CONTAINER_FOLDERS | {
    "final", "최종", "확정", "v1", "v2", "v3", "copy", "복사본", "old",
    "backup", "draft", "sample", "test", "보고서", "결과보고서", "회의록",
}

_CATEGORY_KEYWORDS = {
    "HR": (
        "채용", "평가", "성과평가", "평가제도", "보상", "연봉", "인사",
        "급여", "직무", "조직", "hr", "recruit", "recruitment",
        "evaluation", "compensation",
    ),
    "Development": (
        "개발", "구축", "웹사이트", "홈페이지", "쇼핑몰", "erp", "시스템",
        "앱", "서비스", "api", "program", "development", "website",
        "build",
    ),
    "Sales": (
        "영업", "제안", "제안서", "견적", "고객", "수주", "매출",
        "sales", "proposal", "quotation",
    ),
    "Marketing": (
        "마케팅", "캠페인", "광고", "브랜드", "홍보", "콘텐츠", "이벤트",
        "marketing", "campaign", "promotion",
    ),
    "Legal": (
        "법무", "계약", "계약서", "검토", "규정", "정책", "소송", "legal",
        "contract", "review",
    ),
    "Finance": (
        "재무", "회계", "결산", "정산", "세금", "세무", "예산", "비용",
        "finance", "closing", "accounting",
    ),
    "Education": (
        "교육", "훈련", "연수", "온보딩", "신입사원", "가이드", "매뉴얼",
        "training", "education", "onboarding",
    ),
    "Admin": (
        "총무", "행정", "관리", "비품", "자산", "운영", "admin", "operation",
    ),
}

_OPERATIONAL_CATEGORIES = {"HR", "Finance", "Legal", "Marketing", "Education", "Admin"}
_PROJECT_STYLE_KEYWORDS = (
    "프로젝트", "제안", "제안서", "구축", "개발", "도입", "구현", "착수",
    "완료보고", "implementation", "proposal", "build", "development",
)

_WORK_PHRASES = (
    "평가제도", "성과평가", "채용", "연봉인상", "보상", "ERP 제안",
    "ERP", "쇼핑몰 구축", "웹사이트 구축", "홈페이지 구축", "계약서 검토",
    "계약 검토", "신입사원 교육", "직원 교육", "교육", "재무 결산",
    "결산", "마케팅 캠페인", "캠페인",
)

_ORG_PATTERN = re.compile(
    r"([가-힣A-Za-z0-9]+(?:국제|전자|산업|테크|기술|시스템|솔루션|은행|보험|증권|병원|학교|대학교|그룹|주식회사|㈜|회사|inc|corp|co))",
    re.IGNORECASE,
)


class WorkClusterEngine:
    """Group documents into real work units without relying on folder names."""

    def group_work_clusters(self, doc_summaries: list["DocumentSummary"]) -> list[WorkCluster]:
        clusters: list[WorkCluster] = []
        profiles: dict[int, _DocProfile] = {
            id(doc): _profile_doc(doc) for doc in doc_summaries
        }

        for doc in doc_summaries:
            profile = profiles[id(doc)]
            best_cluster: WorkCluster | None = None
            best_score = 0
            for cluster in clusters:
                score = _cluster_match_score(profile, cluster, profiles)
                if score > best_score:
                    best_score = score
                    best_cluster = cluster

            if best_cluster is not None and best_score >= 45:
                best_cluster.documents.append(doc)
                _refresh_cluster(best_cluster, profiles)
            else:
                clusters.append(_new_cluster(doc, profile))

        for cluster in clusters:
            _refresh_cluster(cluster, profiles)

        clusters.sort(
            key=lambda c: (
                c.category,
                -(c.latest_modified.timestamp() if c.latest_modified else 0),
                c.cluster_key,
            )
        )
        return clusters

    def detect_work_unit_mode(
        self,
        doc_summaries: list["DocumentSummary"],
        work_clusters: list[WorkCluster] | None = None,
    ) -> WorkUnitDetectionResult:
        clusters = work_clusters or self.group_work_clusters(doc_summaries)
        profiles = [_profile_doc(doc) for doc in doc_summaries]
        project_groups = _project_groups(doc_summaries)
        project_score, project_reasons = _project_signal_score(profiles, project_groups)
        cluster_score, cluster_reasons = _cluster_signal_score(profiles, clusters)

        if project_score >= 35 and cluster_score >= 40:
            mode = MIXED_MODE
            mode_reason = "project and operational cluster signals are both strong"
        elif project_score >= cluster_score + 12 and project_score >= 45:
            mode = PROJECT_MODE
            mode_reason = "project folder/customer/proposal signals are dominant"
        elif cluster_score >= 45:
            mode = WORK_CLUSTER_MODE
            mode_reason = "operational work cluster signals are dominant"
        else:
            mode = PROJECT_MODE if project_score >= cluster_score else WORK_CLUSTER_MODE
            mode_reason = "fallback to stronger natural structure signal"

        return WorkUnitDetectionResult(
            mode=mode,
            project_score=project_score,
            work_cluster_score=cluster_score,
            reasons=[mode_reason, *project_reasons, *cluster_reasons],
            project_groups=project_groups,
            work_clusters=clusters,
        )


def group_work_clusters(doc_summaries: list["DocumentSummary"]) -> list[WorkCluster]:
    return WorkClusterEngine().group_work_clusters(doc_summaries)


def detect_work_unit_mode(
    doc_summaries: list["DocumentSummary"],
    work_clusters: list[WorkCluster] | None = None,
) -> WorkUnitDetectionResult:
    return WorkClusterEngine().detect_work_unit_mode(doc_summaries, work_clusters)


def write_work_clusters_report(
    clusters: list[WorkCluster],
    output_dir: str | None = None,
) -> str:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "output"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "work_clusters.txt"

    lines = [
        "# Work Clusters",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for cluster in clusters:
        lines.extend([
            "[Cluster]",
            cluster.cluster_key,
            "",
            "Category:",
            cluster.category,
            "",
            "Documents:",
            "",
        ])
        for doc in cluster.documents:
            lines.append(f"* {_file_stem(doc.display_name)}")
        lines.extend(["", "Representative:", ""])
        for doc in cluster.representative_docs:
            lines.append(f"* {_file_name(doc.display_name)}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return str(path)


def write_work_unit_detection_report(
    detection: WorkUnitDetectionResult,
    output_dir: str | None = None,
) -> str:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "output"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "work_unit_detection.txt"

    lines = [
        "# Work Unit Detection",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Detected Mode:",
        detection.mode,
        "",
        "Scores:",
        f"* Project signals: {detection.project_score}",
        f"* Work cluster signals: {detection.work_cluster_score}",
        "",
        "Scoring Logic:",
        "* Project: non-container project/customer folders, proposal/build/implementation keywords, folder cohesion",
        "* Work Cluster: operational categories, category diversity, cluster cohesion, container-folder penalty",
        "* Mixed: both project and operational cluster signals are strong",
        "",
        "Reasons:",
    ]
    for reason in detection.reasons:
        lines.append(f"* {reason}")

    lines.extend(["", "Project Groups:", ""])
    for project_key, docs in sorted(detection.project_groups.items()):
        lines.append(f"* {project_key}: {len(docs)} docs")

    lines.extend(["", "Work Clusters:", ""])
    for cluster in detection.work_clusters:
        lines.append(f"* {cluster.cluster_key} [{cluster.category}]: {len(cluster.documents)} docs")

    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return str(path)


@dataclass
class _DocProfile:
    doc: "DocumentSummary"
    name: str
    stem: str
    family_key: str
    tokens: set[str]
    keywords: set[str]
    org_names: set[str]
    folders: list[str]
    category: str
    modified: _dt.datetime | None
    work_phrase: str


def _new_cluster(doc: "DocumentSummary", profile: _DocProfile) -> WorkCluster:
    cluster = WorkCluster(
        cluster_key=_name_from_profiles([profile]),
        category=profile.category,
        documents=[doc],
        representative_docs=[doc],
        keywords=sorted(profile.keywords)[:8],
        latest_modified=profile.modified,
    )
    return cluster


def _refresh_cluster(cluster: WorkCluster, profiles: dict[int, _DocProfile]) -> None:
    cluster_profiles = [profiles[id(doc)] for doc in cluster.documents]
    cluster.cluster_key = _name_from_profiles(cluster_profiles)
    cluster.category = _majority_category(cluster_profiles)
    cluster.keywords = _top_keywords(cluster_profiles)
    cluster.latest_modified = _latest_modified(cluster_profiles)
    cluster.representative_docs = _representative_docs(cluster.documents)


def _cluster_match_score(
    profile: _DocProfile,
    cluster: WorkCluster,
    profiles: dict[int, _DocProfile],
) -> int:
    cluster_profiles = [profiles[id(doc)] for doc in cluster.documents]
    cluster_tokens = set().union(*(p.tokens for p in cluster_profiles)) if cluster_profiles else set()
    cluster_keywords = set().union(*(p.keywords for p in cluster_profiles)) if cluster_profiles else set()
    cluster_orgs = set().union(*(p.org_names for p in cluster_profiles)) if cluster_profiles else set()
    cluster_families = {p.family_key for p in cluster_profiles}
    cluster_folders = {folder for p in cluster_profiles for folder in p.folders}

    score = 0
    if cluster_tokens:
        token_jaccard = _jaccard(profile.tokens, cluster_tokens)
        score += round(token_jaccard * 25)

    if profile.family_key in cluster_families:
        score += 22
    elif any(_similar_text(profile.family_key, family) >= 0.72 for family in cluster_families):
        score += 14

    score += min(18, len(profile.keywords & cluster_keywords) * 6)
    score += min(15, len(profile.org_names & cluster_orgs) * 15)

    shared_folders = set(profile.folders) & cluster_folders
    if shared_folders:
        if shared_folders <= _CONTAINER_FOLDERS:
            score -= 12
        else:
            score += 10

    if profile.category == cluster.category and profile.category != "General":
        score += 10

    latest = cluster.latest_modified
    if profile.modified and latest:
        days = abs((profile.modified.date() - latest.date()).days)
        if days <= 7:
            score += 10
        elif days <= 30:
            score += 6

    if _is_container_name(cluster.cluster_key):
        score -= 20

    return score


def _profile_doc(doc: "DocumentSummary") -> _DocProfile:
    name = doc.display_name
    stem = _file_stem(name)
    family_key = DocumentFamilyEngine().group_document_families([doc])[0].family_key
    tokens = _tokenize(" ".join([stem, family_key]))
    keywords = _extract_keywords(stem)
    org_names = _extract_org_names(name)
    folders = _folders(name)
    category = _detect_category(" ".join([name, getattr(doc, "summary_text", "")]))
    modified = _parse_datetime(getattr(doc, "modified_dt", ""))
    work_phrase = _detect_work_phrase(" ".join([name, getattr(doc, "summary_text", "")]))

    return _DocProfile(
        doc=doc,
        name=name,
        stem=stem,
        family_key=family_key,
        tokens=tokens,
        keywords=keywords,
        org_names=org_names,
        folders=folders,
        category=category,
        modified=modified,
        work_phrase=work_phrase,
    )


def _project_groups(doc_summaries: list["DocumentSummary"]) -> dict[str, list["DocumentSummary"]]:
    groups: dict[str, list["DocumentSummary"]] = {}
    for doc in doc_summaries:
        folders = _folders(doc.display_name)
        key = _best_project_folder(folders)
        groups.setdefault(key, []).append(doc)
    return groups


def _best_project_folder(folders: list[str]) -> str:
    for folder in folders:
        if not _is_container_name(folder):
            return folder
    return folders[0] if folders else "기타"


def _project_signal_score(
    profiles: list[_DocProfile],
    project_groups: dict[str, list["DocumentSummary"]],
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    doc_count = len(profiles)
    meaningful_groups = {
        key: docs for key, docs in project_groups.items()
        if not _is_container_name(key) and key != "기타" and len(docs) >= 2
    }
    if meaningful_groups:
        group_ratio = sum(len(docs) for docs in meaningful_groups.values()) / max(1, doc_count)
        add = round(min(30, group_ratio * 30))
        score += add
        reasons.append(f"meaningful project/customer folders detected (+{add})")

    org_docs = sum(1 for profile in profiles if profile.org_names)
    if org_docs:
        add = round(min(20, org_docs / max(1, doc_count) * 20))
        score += add
        reasons.append(f"organization/customer name signals detected (+{add})")

    project_style_docs = [
        profile for profile in profiles
        if any(keyword.lower() in profile.name.lower() for keyword in _PROJECT_STYLE_KEYWORDS)
    ]
    if project_style_docs:
        add = round(min(25, len(project_style_docs) / max(1, doc_count) * 25))
        score += add
        reasons.append(f"proposal/build/implementation style documents detected (+{add})")

    cohesive_groups = sum(1 for docs in meaningful_groups.values() if len(docs) >= 3)
    if cohesive_groups:
        add = min(15, cohesive_groups * 5)
        score += add
        reasons.append(f"folder cohesion detected (+{add})")

    container_groups = {
        key: docs for key, docs in project_groups.items()
        if _is_container_name(key) and len(docs) >= 2
    }
    if container_groups:
        score -= 18
        reasons.append("container folders are weak project names (-18)")

    return _clamp(score, 0, 100), reasons


def _cluster_signal_score(
    profiles: list[_DocProfile],
    clusters: list[WorkCluster],
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    doc_count = len(profiles)
    operational_docs = [
        profile for profile in profiles
        if profile.category in _OPERATIONAL_CATEGORIES
    ]
    if operational_docs:
        add = round(min(35, len(operational_docs) / max(1, doc_count) * 35))
        score += add
        reasons.append(f"operational category documents detected (+{add})")

    operational_categories = {profile.category for profile in operational_docs}
    if len(operational_categories) >= 2:
        add = min(20, len(operational_categories) * 5)
        score += add
        reasons.append(f"multiple operational categories detected (+{add})")

    cohesive_clusters = [cluster for cluster in clusters if len(cluster.documents) >= 2]
    if cohesive_clusters:
        add = min(25, len(cohesive_clusters) * 8)
        score += add
        reasons.append(f"work cluster cohesion detected (+{add})")

    container_folder_docs = sum(
        1 for profile in profiles
        if profile.folders and _is_container_name(profile.folders[0])
    )
    if container_folder_docs:
        add = round(min(15, container_folder_docs / max(1, doc_count) * 15))
        score += add
        reasons.append(f"container folders suggest non-project organization (+{add})")

    project_style_docs = sum(
        1 for profile in profiles
        if any(keyword.lower() in profile.name.lower() for keyword in _PROJECT_STYLE_KEYWORDS)
    )
    if project_style_docs >= max(2, doc_count // 2):
        score -= 15
        reasons.append("project-style documents reduce pure work-cluster confidence (-15)")

    return _clamp(score, 0, 100), reasons


def _name_from_profiles(profiles: list[_DocProfile]) -> str:
    orgs = _ordered_common_values([p.org_names for p in profiles])
    phrases = [p.work_phrase for p in profiles if p.work_phrase]
    phrase = _most_common(phrases)
    if phrase:
        if orgs and phrase not in orgs[0]:
            return f"{orgs[0]} {phrase}".strip()
        return phrase

    common = _meaningful_common_phrase([p.family_key for p in profiles])
    if common:
        return common

    family = _best_family_key(profiles)
    if family and not _is_container_name(family):
        return family

    folder = _best_folder(profiles)
    if folder:
        return folder

    return "기타"


def _detect_category(text: str) -> str:
    lower = text.lower()
    best_category = "General"
    best_score = 0
    for category, keywords in _CATEGORY_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword.lower() in lower)
        if score > best_score:
            best_score = score
            best_category = category
    return best_category


def _detect_work_phrase(stem: str) -> str:
    compact = re.sub(r"[\s_\-]+", "", stem.lower())
    for phrase in sorted(_WORK_PHRASES, key=len, reverse=True):
        if re.sub(r"\s+", "", phrase.lower()) in compact:
            return phrase
    if "평가" in stem and ("동우" in stem or "제도" in stem):
        return "평가제도"
    return ""


def _extract_keywords(text: str) -> set[str]:
    tokens = _tokenize(text)
    category_terms = {
        keyword
        for keywords in _CATEGORY_KEYWORDS.values()
        for keyword in keywords
        if keyword.lower() in text.lower()
    }
    return {t for t in tokens if t not in _STOPWORDS and len(t) >= 2} | category_terms


def _extract_org_names(text: str) -> set[str]:
    names = {match.group(1).strip(" _-") for match in _ORG_PATTERN.finditer(text)}
    parts = re.split(r"[/\\_\-\s]+", text)
    for part in parts:
        if part.endswith(("국제", "전자", "테크", "그룹")) and len(part) >= 3:
            names.add(part)
    return {name for name in names if not _is_container_name(name)}


def _folders(display_name: str) -> list[str]:
    parts = display_name.replace("\\", "/").split("/")[:-1]
    return [part.strip() for part in parts if part.strip()]


def _tokenize(text: str) -> set[str]:
    raw_tokens = re.split(r"[^0-9A-Za-z가-힣]+", text.lower())
    tokens = {
        token for token in raw_tokens
        if len(token) >= 2 and token not in _STOPWORDS and not re.fullmatch(r"v\d+(?:\.\d+)?", token)
    }
    return tokens


def _representative_docs(docs: list["DocumentSummary"]) -> list["DocumentSummary"]:
    families = DocumentFamilyEngine().group_document_families(docs)
    latest_docs = [family.latest_doc for family in families]
    latest_docs.sort(
        key=lambda doc: (
            -getattr(doc, "score", 0),
            -_timestamp(_parse_datetime(getattr(doc, "modified_dt", ""))),
            _file_name(doc.display_name),
        )
    )
    return latest_docs[:3]


def _majority_category(profiles: list[_DocProfile]) -> str:
    categories = [p.category for p in profiles if p.category != "General"]
    return _most_common(categories) or "General"


def _top_keywords(profiles: list[_DocProfile]) -> list[str]:
    counts: dict[str, int] = {}
    for profile in profiles:
        for keyword in profile.keywords:
            if _is_container_name(keyword):
                continue
            counts[keyword] = counts.get(keyword, 0) + 1
    return [
        keyword for keyword, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ][:10]


def _latest_modified(profiles: list[_DocProfile]) -> _dt.datetime | None:
    values = [profile.modified for profile in profiles if profile.modified is not None]
    return max(values) if values else None


def _ordered_common_values(value_sets: list[set[str]]) -> list[str]:
    counts: dict[str, int] = {}
    for values in value_sets:
        for value in values:
            counts[value] = counts.get(value, 0) + 1
    return [
        value for value, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _meaningful_common_phrase(values: list[str]) -> str:
    clean_values = [value for value in values if value and not _is_container_name(value)]
    if not clean_values:
        return ""
    if len(clean_values) == 1:
        return clean_values[0]

    token_sets = [_tokenize(value) for value in clean_values]
    common = set.intersection(*token_sets) if token_sets else set()
    common = {token for token in common if not _is_container_name(token)}
    if common:
        return " ".join(sorted(common))
    return _longest_common_prefix_words(clean_values)


def _longest_common_prefix_words(values: list[str]) -> str:
    split_values = [re.split(r"[\s_\-]+", value.strip()) for value in values]
    prefix: list[str] = []
    for words in zip(*split_values):
        normalized = {word.lower() for word in words}
        if len(normalized) != 1:
            break
        if _is_container_name(words[0]):
            break
        prefix.append(words[0])
    return " ".join(prefix).strip()


def _best_family_key(profiles: list[_DocProfile]) -> str:
    values = [p.family_key for p in profiles if p.family_key and not _is_container_name(p.family_key)]
    return _most_common(values)


def _best_folder(profiles: list[_DocProfile]) -> str:
    folders = [
        folder for profile in profiles for folder in profile.folders
        if not _is_container_name(folder)
    ]
    return _most_common(folders)


def _most_common(values: list[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _is_container_name(value: str) -> bool:
    normalized = re.sub(r"[\s_\-]+", "", str(value).lower())
    return normalized in {re.sub(r"[\s_\-]+", "", item.lower()) for item in _CONTAINER_FOLDERS}


def _similar_text(left: str, right: str) -> float:
    return SequenceMatcher(None, left.lower(), right.lower()).ratio()


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _clamp(value: int | float, low: int, high: int) -> int:
    return int(max(low, min(high, value)))


def _parse_datetime(value: str) -> _dt.datetime | None:
    try:
        return _dt.datetime.combine(_dt.date.fromisoformat(str(value)), _dt.time.min)
    except (TypeError, ValueError):
        return None


def _timestamp(value: _dt.datetime | None) -> float:
    return value.timestamp() if value else 0.0


def _file_name(display_name: str) -> str:
    return Path(display_name.replace("\\", "/")).name


def _file_stem(display_name: str) -> str:
    return Path(display_name.replace("\\", "/")).stem
