from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path


USER_DEFINED = "USER_DEFINED"
AUTO_DISCOVERED = "AUTO_DISCOVERED"
HYBRID = "HYBRID"


@dataclass
class DiscoveredCategory:
    category_name: str
    source: str
    confidence: int
    evidence_files: list[str] = field(default_factory=list)
    evidence_keywords: list[str] = field(default_factory=list)


@dataclass
class CategoryDiscoveryResult:
    categories: list[DiscoveredCategory] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_CONTAINER_NAMES = {
    "결과물", "자료", "문서", "misc", "기타", "output", "outputs",
    "result", "results", "documents", "document",
}

_CATEGORY_FINGERPRINTS = {
    "인사": (
        "인사", "채용", "채용공고", "면접", "면접평가표", "성과평가", "평가표",
        "평가제도", "급여", "연봉", "보상", "hr", "recruit", "interview",
        "evaluation", "compensation",
    ),
    "회계": (
        "회계", "부가세", "결산", "재무제표", "재무", "세금", "세무", "정산",
        "invoice", "tax", "vat", "finance", "closing", "accounting",
    ),
    "법무": (
        "법무", "계약", "계약서", "nda", "법률검토", "검토의견", "규정",
        "정책", "legal", "contract", "agreement",
    ),
    "교육": (
        "교육", "교안", "교육자료", "만족도조사", "훈련", "연수", "온보딩",
        "신입사원", "training", "education", "survey", "onboarding",
    ),
    "영업": (
        "영업", "제안", "제안서", "견적", "견적서", "고객", "수주", "매출",
        "sales", "proposal", "quotation", "estimate",
    ),
    "마케팅": (
        "마케팅", "캠페인", "광고", "홍보", "브랜드", "콘텐츠", "이벤트",
        "marketing", "campaign", "promotion", "brand",
    ),
    "개발": (
        "개발", "구축", "시스템", "웹사이트", "홈페이지", "쇼핑몰", "erp",
        "api", "설계서", "요구사항", "development", "build", "website",
    ),
    "총무": (
        "총무", "행정", "비품", "자산", "관리", "운영", "admin", "operation",
    ),
}

_WORK_CLUSTER_CATEGORY_MAP = {
    "HR": "인사",
    "Finance": "회계",
    "Legal": "법무",
    "Education": "교육",
    "Sales": "영업",
    "Marketing": "마케팅",
    "Development": "개발",
    "Admin": "총무",
}


class CategoryDiscoveryEngine:
    """Discover missing business categories from uploaded files and analysis artifacts."""

    def discover(
        self,
        user_job_categories=None,
        document_summaries=None,
        work_clusters=None,
        representative_docs=None,
        project_summaries=None,
    ) -> CategoryDiscoveryResult:
        user_categories = _parse_user_categories(user_job_categories)
        user_map = {_normalize(category): category for category in user_categories}
        discovered = _discover_candidates(
            document_summaries=document_summaries or [],
            work_clusters=work_clusters or [],
            representative_docs=representative_docs or {},
            project_summaries=project_summaries or [],
        )

        categories: list[DiscoveredCategory] = []
        warnings: list[str] = []
        used_discovered: set[str] = set()

        for norm_name, user_name in user_map.items():
            if _is_container_name(user_name):
                warnings.append(f"container category ignored: {user_name}")
                continue
            match = discovered.get(norm_name)
            if match:
                categories.append(
                    DiscoveredCategory(
                        category_name=user_name,
                        source=HYBRID,
                        confidence=max(70, match.confidence),
                        evidence_files=match.evidence_files,
                        evidence_keywords=match.evidence_keywords,
                    )
                )
                used_discovered.add(norm_name)
            else:
                categories.append(
                    DiscoveredCategory(
                        category_name=user_name,
                        source=USER_DEFINED,
                        confidence=70,
                        evidence_files=[],
                        evidence_keywords=[],
                    )
                )

        for norm_name, category in discovered.items():
            if norm_name in used_discovered:
                continue
            if _is_container_name(category.category_name):
                warnings.append(f"container category ignored: {category.category_name}")
                continue
            categories.append(category)

        categories.sort(key=lambda c: (_source_order(c.source), -c.confidence, c.category_name))
        return CategoryDiscoveryResult(categories=categories, warnings=warnings)


def write_category_discovery_report(
    result: CategoryDiscoveryResult,
    output_dir: str | None = None,
) -> str:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "output"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "category_discovery.txt"

    lines = [
        "# Category Discovery",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for source in (USER_DEFINED, AUTO_DISCOVERED, HYBRID):
        lines.append(source)
        lines.append("")
        source_categories = [c for c in result.categories if c.source == source]
        if source_categories:
            for category in source_categories:
                lines.append(f"* {category.category_name}")
        else:
            lines.append("* (none)")
        lines.append("")

    lines.append("evidence")
    lines.append("")
    for category in result.categories:
        lines.append(category.category_name)
        lines.append("")
        lines.append("files:")
        _append_bullets(lines, category.evidence_files)
        lines.append("")
        lines.append("keywords:")
        _append_bullets(lines, category.evidence_keywords)
        lines.append("")
        lines.append("confidence:")
        lines.append(str(category.confidence))
        lines.append("")

    if result.warnings:
        lines.append("warnings:")
        for warning in result.warnings:
            lines.append(f"* {warning}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _discover_candidates(
    document_summaries,
    work_clusters,
    representative_docs,
    project_summaries,
) -> dict[str, DiscoveredCategory]:
    evidence: dict[str, dict[str, object]] = {}

    def add(category_name: str, keyword: str, file_name: str = "", weight: int = 1) -> None:
        if not category_name or _is_container_name(category_name):
            return
        norm = _normalize(category_name)
        row = evidence.setdefault(norm, {"name": category_name, "files": [], "keywords": {}, "weight": 0, "rep_hits": 0})
        if file_name and file_name not in row["files"]:
            row["files"].append(file_name)
        if keyword:
            keywords = row["keywords"]
            keywords[keyword] = keywords.get(keyword, 0) + weight
        row["weight"] = int(row["weight"]) + weight

    for doc in document_summaries or []:
        name = _doc_name(doc)
        _add_text_matches(add, " ".join([name, getattr(doc, "summary_text", "") or ""]), name, 2)

    for cluster in work_clusters or []:
        cluster_category = getattr(cluster, "category", "")
        mapped = _WORK_CLUSTER_CATEGORY_MAP.get(cluster_category)
        cluster_name = getattr(cluster, "cluster_key", "")
        if mapped:
            add(mapped, cluster_category, cluster_name, 3)
        _add_text_matches(add, " ".join([cluster_name, " ".join(getattr(cluster, "keywords", []) or [])]), cluster_name, 3)
        for doc in getattr(cluster, "documents", []) or []:
            _add_text_matches(add, _doc_name(doc), _doc_name(doc), 1)

    for doc in _iter_representative_docs(representative_docs):
        name = _doc_name(doc)
        before = set(evidence)
        _add_text_matches(add, name, name, 4)
        for key in set(evidence) - before:
            evidence[key]["rep_hits"] = int(evidence[key].get("rep_hits", 0)) + 1

    for summary in project_summaries or []:
        text = " ".join(
            str(getattr(summary, attr, "") or "")
            for attr in ("project_key", "project_name", "client_name", "key_outputs", "summary_text")
        )
        text = " ".join([text, " ".join(getattr(summary, "related_files", []) or [])])
        _add_text_matches(add, text, getattr(summary, "project_key", ""), 2)

    result: dict[str, DiscoveredCategory] = {}
    for norm, row in evidence.items():
        files = list(row["files"])[:8]
        keywords = [
            keyword for keyword, _ in sorted(row["keywords"].items(), key=lambda item: (-item[1], item[0]))
        ][:8]
        doc_count = len(files)
        keyword_count = len(keywords)
        rep_hits = int(row.get("rep_hits", 0))
        confidence = min(100, 35 + doc_count * 10 + keyword_count * 8 + rep_hits * 12 + int(row["weight"]) * 2)
        result[norm] = DiscoveredCategory(
            category_name=str(row["name"]),
            source=AUTO_DISCOVERED,
            confidence=confidence,
            evidence_files=files,
            evidence_keywords=keywords,
        )
    return result


def _add_text_matches(add_fn, text: str, file_name: str, weight: int) -> None:
    lower = text.lower()
    for category_name, keywords in _CATEGORY_FINGERPRINTS.items():
        for keyword in keywords:
            if keyword.lower() in lower:
                add_fn(category_name, keyword, _file_name(file_name), weight)


def _parse_user_categories(user_job_categories) -> list[str]:
    if user_job_categories is None:
        return []
    if isinstance(user_job_categories, str):
        lines = re.split(r"[\n,;/]+", user_job_categories)
    else:
        lines = list(user_job_categories)

    categories: list[str] = []
    for raw in lines:
        text = str(raw or "").strip()
        text = re.sub(r"^[#*\-\d.\s]+", "", text).strip()
        text = re.split(r"[:：\-–—]", text, maxsplit=1)[0].strip()
        if text and not _is_container_name(text):
            categories.append(text)
    return list(dict.fromkeys(categories))


def _iter_representative_docs(representative_docs):
    if isinstance(representative_docs, dict):
        values = representative_docs.values()
    else:
        values = representative_docs or []
    for item in values:
        if hasattr(item, "representative_docs"):
            for doc in getattr(item, "representative_docs", []) or []:
                yield doc
        else:
            yield item


def _doc_name(doc) -> str:
    if isinstance(doc, str):
        return doc
    return str(getattr(doc, "display_name", "") or getattr(doc, "file_name", "") or "")


def _file_name(value: str) -> str:
    if not value:
        return ""
    return Path(str(value).replace("\\", "/")).name


def _source_order(source: str) -> int:
    return {USER_DEFINED: 0, AUTO_DISCOVERED: 1, HYBRID: 2}.get(source, 9)


def _is_container_name(value: str) -> bool:
    normalized = _normalize(value)
    return normalized in {_normalize(name) for name in _CONTAINER_NAMES}


def _normalize(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").lower())


def _append_bullets(lines: list[str], items: list[str]) -> None:
    if not items:
        lines.append("* (none)")
        return
    for item in items:
        lines.append(f"* {item}")
