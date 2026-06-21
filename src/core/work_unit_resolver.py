from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path


PROJECT = "PROJECT"
CUSTOMER = "CUSTOMER"
FUNCTION = "FUNCTION"
DOCUMENT_SET = "DOCUMENT_SET"


@dataclass
class WorkUnit:
    unit_type: str
    unit_name: str
    confidence: int
    source_reason: str


@dataclass
class WorkUnitResolverResult:
    work_units: list[WorkUnit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_CONTAINER_NAMES = {
    "결과물", "문서", "자료", "misc", "기타", "documents", "results",
    "output", "outputs", "document", "result",
}

_PROJECT_KEYWORDS = (
    "구축", "개발", "도입", "제안", "제안서", "리뉴얼", "프로젝트",
    "implementation", "build", "proposal", "renewal", "development",
)
_FUNCTION_KEYWORDS = {
    "채용": ("채용", "recruit", "recruitment"),
    "평가": ("평가", "성과평가", "평가제도", "evaluation"),
    "급여": ("급여", "연봉", "보상", "compensation", "payroll"),
    "교육": ("교육", "훈련", "신입사원", "온보딩", "training", "education"),
    "회계결산": ("회계", "결산", "재무", "finance", "closing", "accounting"),
    "계약검토": ("계약", "계약서", "법무", "검토", "legal", "contract"),
    "마케팅": ("마케팅", "캠페인", "광고", "marketing", "campaign"),
}
_CUSTOMER_SUFFIXES = (
    "국제", "전자", "산업", "테크", "기술", "시스템", "솔루션", "은행",
    "보험", "증권", "병원", "학교", "대학교", "그룹", "세무법인", "법인",
    "주식회사", "회사",
)
_DOC_SET_KEYWORDS = ("견적서", "평가기획안", "계약서", "제안서", "회의록", "보고서", "양식")


class WorkUnitResolver:
    """Resolve the actual execution unit from inferred analysis artifacts."""

    def resolve(
        self,
        work_clusters=None,
        document_families=None,
        representative_documents=None,
        project_summaries=None,
    ) -> WorkUnitResolverResult:
        candidates: list[_Candidate] = []
        warnings: list[str] = []

        for cluster in work_clusters or []:
            source_name = str(getattr(cluster, "cluster_key", "") or "")
            if _is_container_name(source_name):
                warnings.append(f"container name rejected: {source_name}")

        candidates.extend(_candidates_from_clusters(work_clusters or []))
        candidates.extend(_candidates_from_families(document_families or {}))
        candidates.extend(_candidates_from_representatives(representative_documents or {}))
        candidates.extend(_candidates_from_project_summaries(project_summaries or []))

        filtered: list[_Candidate] = []
        for candidate in candidates:
            if _is_container_name(candidate.name):
                warnings.append(f"container name rejected: {candidate.name}")
                continue
            if not candidate.name:
                continue
            filtered.append(candidate)

        merged = _merge_candidates(filtered)
        if not merged:
            return WorkUnitResolverResult(
                work_units=[
                    WorkUnit(
                        unit_type=DOCUMENT_SET,
                        unit_name="문서 묶음",
                        confidence=30,
                        source_reason="fallback",
                    )
                ],
                warnings=warnings,
            )

        ordered = sorted(
            merged,
            key=lambda c: (_type_priority(c.unit_type), -c.confidence, c.name),
        )
        work_units = [
            WorkUnit(
                unit_type=c.unit_type,
                unit_name=c.name,
                confidence=_clamp(c.confidence),
                source_reason=c.reason,
            )
            for c in ordered
        ]
        return WorkUnitResolverResult(work_units=work_units, warnings=warnings)


def write_work_unit_resolver_report(
    result: WorkUnitResolverResult,
    output_dir: str | None = None,
) -> str:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "output"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "work_unit_resolver.txt"

    lines = [
        "# Work Unit Resolver",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Detected Work Units",
        "",
    ]
    for unit in result.work_units:
        lines.extend([
            f"[{unit.unit_type}]",
            unit.unit_name,
            "",
            "reason:",
            unit.source_reason,
            "",
            "confidence:",
            str(unit.confidence),
            "",
        ])

    if result.warnings:
        lines.append("Warnings:")
        for warning in result.warnings:
            lines.append(f"* {warning}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


@dataclass
class _Candidate:
    unit_type: str
    name: str
    confidence: int
    reason: str


def _candidates_from_clusters(work_clusters) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for cluster in work_clusters:
        name = str(getattr(cluster, "cluster_key", "") or "").strip()
        docs = getattr(cluster, "documents", []) or []
        text = " ".join([name, *[_doc_name(doc) for doc in docs]])
        unit_type, resolved_name, reason, base = _classify_text(text, preferred_name=name)
        confidence = base + min(15, len(docs) * 4)
        candidates.append(_Candidate(unit_type, resolved_name, confidence, reason))
    return candidates


def _candidates_from_families(document_families) -> list[_Candidate]:
    families = _flatten_families(document_families)
    candidates: list[_Candidate] = []
    for family in families:
        family_key = str(getattr(family, "family_key", "") or "").strip()
        docs = getattr(family, "family_docs", []) or []
        text = " ".join([family_key, *[_doc_name(doc) for doc in docs]])
        if _best_doc_keyword(family_key):
            candidates.append(
                _Candidate(
                    DOCUMENT_SET,
                    _document_set_name(family_key),
                    72,
                    "representative document family",
                )
            )
            continue
        unit_type, resolved_name, reason, base = _classify_text(text, preferred_name=family_key)
        if unit_type == CUSTOMER:
            unit_type = DOCUMENT_SET
            reason = "representative document family"
        if unit_type == DOCUMENT_SET:
            resolved_name = _document_set_name(family_key)
        candidates.append(_Candidate(unit_type, resolved_name, base + 5, reason))
    return candidates


def _candidates_from_representatives(representative_documents) -> list[_Candidate]:
    result_list = representative_documents.values() if isinstance(representative_documents, dict) else representative_documents
    candidates: list[_Candidate] = []
    for result in result_list or []:
        docs = (
            list(getattr(result, "representative_docs", []) or [])
            + list(getattr(result, "supporting_docs", []) or [])
        )
        text = " ".join(_doc_name(doc) for doc in docs)
        unit_type, resolved_name, reason, base = _classify_text(text)
        if not resolved_name:
            continue
        candidates.append(_Candidate(unit_type, resolved_name, base, reason))
    return candidates


def _candidates_from_project_summaries(project_summaries) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for summary in project_summaries or []:
        project_name = _info_value(getattr(summary, "project_name", ""))
        client_name = _info_value(getattr(summary, "client_name", ""))
        project_key = _info_value(getattr(summary, "project_key", ""))
        text = " ".join([
            project_name,
            client_name,
            project_key,
            *list(getattr(summary, "related_files", []) or []),
        ])
        unit_type, resolved_name, reason, base = _classify_text(
            text,
            preferred_name=project_name or project_key,
        )
        if unit_type == CUSTOMER and client_name:
            resolved_name = client_name
            base += 5
            reason = "customer signal from project summary"
        candidates.append(_Candidate(unit_type, resolved_name, base, reason))
    return candidates


def _classify_text(text: str, preferred_name: str = "") -> tuple[str, str, str, int]:
    normalized_preferred = _clean_name(preferred_name)
    project_name = _project_name(text, normalized_preferred)
    if project_name:
        return PROJECT, project_name, "project signals", 88

    function_name = _function_name(text, normalized_preferred)
    if function_name:
        return FUNCTION, function_name, "shared keywords", 82

    customer_name = _customer_name(text, normalized_preferred)
    if customer_name:
        return CUSTOMER, customer_name, "shared organization/customer name", 74

    doc_set = _document_set_name(normalized_preferred or _best_doc_keyword(text))
    return DOCUMENT_SET, doc_set, "representative document family", 58


def _project_name(text: str, preferred_name: str) -> str:
    lower = text.lower()
    if any(keyword.lower() in lower for keyword in _PROJECT_KEYWORDS):
        name = preferred_name if preferred_name and not _is_container_name(preferred_name) else ""
        if not name:
            name = _phrase_around_keyword(text, _PROJECT_KEYWORDS)
        return _clean_project_name(name)
    return ""


def _function_name(text: str, preferred_name: str) -> str:
    lower = text.lower()
    for function_name, keywords in _FUNCTION_KEYWORDS.items():
        if any(keyword.lower() in lower for keyword in keywords):
            return _clean_name(preferred_name) if _is_functionish(preferred_name, keywords) else function_name
    return ""


def _customer_name(text: str, preferred_name: str) -> str:
    if preferred_name and _looks_like_customer(preferred_name):
        return preferred_name
    parts = re.split(r"[/\\_\-\s]+", text)
    for part in parts:
        clean = _clean_name(part)
        if _looks_like_customer(clean):
            return clean
    return ""


def _document_set_name(value: str) -> str:
    clean = _clean_name(value)
    keyword = _best_doc_keyword(clean)
    if keyword:
        return f"{keyword} 묶음"
    if clean and not _is_container_name(clean):
        return f"{clean} 묶음"
    return "문서 묶음"


def _best_doc_keyword(text: str) -> str:
    for keyword in _DOC_SET_KEYWORDS:
        if keyword.lower() in text.lower():
            return keyword
    return ""


def _merge_candidates(candidates: list[_Candidate]) -> list[_Candidate]:
    merged: dict[tuple[str, str], _Candidate] = {}
    for candidate in candidates:
        key = (candidate.unit_type, _normalize(candidate.name))
        if key not in merged:
            merged[key] = candidate
            continue
        current = merged[key]
        current.confidence = max(current.confidence, candidate.confidence) + 3
        if candidate.reason not in current.reason:
            current.reason = f"{current.reason}; {candidate.reason}"
    return list(merged.values())


def _type_priority(unit_type: str) -> int:
    return {
        PROJECT: 0,
        FUNCTION: 1,
        CUSTOMER: 2,
        DOCUMENT_SET: 3,
    }.get(unit_type, 9)


def _flatten_families(document_families) -> list:
    if isinstance(document_families, dict):
        return [family for families in document_families.values() for family in (families or [])]
    return list(document_families or [])


def _doc_name(doc) -> str:
    if isinstance(doc, str):
        return doc
    return str(getattr(doc, "display_name", "") or getattr(doc, "file_name", "") or "")


def _info_value(value: str) -> str:
    text = str(value or "").strip()
    return "" if text == "[정보 부족]" else text


def _clean_project_name(name: str) -> str:
    clean = _clean_name(name)
    clean = re.sub(r"(?i)\.(docx|xlsx|pptx|pdf|hwp|hwpx|txt|md)$", "", clean)
    return clean


def _clean_name(name: str) -> str:
    value = Path(str(name).replace("\\", "/")).stem
    value = re.sub(r"(?i)(^|[_\-\s])v\d+(?:\.\d+)?\b", " ", value)
    value = re.sub(r"(?i)(final|최종|확정|draft|초안|old|backup|copy|복사본)", " ", value)
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return "" if _is_container_name(value) else value


def _phrase_around_keyword(text: str, keywords: tuple[str, ...]) -> str:
    parts = [part for part in re.split(r"[/\\_\-\s]+", text) if part]
    for idx, part in enumerate(parts):
        if any(keyword.lower() in part.lower() for keyword in keywords):
            start = max(0, idx - 1)
            end = min(len(parts), idx + 2)
            phrase = " ".join(parts[start:end])
            cleaned = _clean_name(phrase)
            if cleaned and not _is_container_name(cleaned):
                return cleaned
    return ""


def _is_functionish(value: str, keywords: tuple[str, ...]) -> bool:
    if not value or _is_container_name(value):
        return False
    lower = value.lower()
    return any(keyword.lower() in lower for keyword in keywords)


def _looks_like_customer(value: str) -> bool:
    if not value or _is_container_name(value):
        return False
    return value.endswith(_CUSTOMER_SUFFIXES) or bool(re.search(r"(?i)\b(inc|corp|co|ltd)\b", value))


def _is_container_name(value: str) -> bool:
    normalized = _normalize(value)
    return normalized in {_normalize(name) for name in _CONTAINER_NAMES}


def _normalize(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").lower())


def _clamp(value: int | float) -> int:
    return int(max(0, min(100, value)))
