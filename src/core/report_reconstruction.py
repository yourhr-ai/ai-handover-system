from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ReportUnit:
    unit_name: str
    unit_type: str
    confidence: int
    projects: list = field(default_factory=list)
    document_names: list[str] = field(default_factory=list)


@dataclass
class ReportReconstructionResult:
    original_units: list[str] = field(default_factory=list)
    normalized_units: list[ReportUnit] = field(default_factory=list)
    suppressed_containers: list[str] = field(default_factory=list)
    fallback_used: bool = False


_CONTAINER_NAMES = {
    "고객사", "결과물", "회의록", "기타", "자료", "문서",
    "documents", "document", "results", "result", "outputs", "output",
    "misc", "meeting", "notes",
}

_UNIT_KEYWORDS = {
    "인사": ("인사", "평가", "성과평가", "채용", "면접", "연봉", "보상"),
    "교육": ("교육", "교안", "만족도", "훈련", "연수", "온보딩", "신입사원"),
    "영업": ("영업", "견적", "제안", "수주", "고객관리"),
    "회계": ("회계", "결산", "세무", "부가세", "재무", "정산"),
    "총무": ("총무", "구매", "비품", "자산", "행정"),
    "법무": ("법무", "계약", "규정", "NDA", "법률검토"),
    "마케팅": ("마케팅", "캠페인", "광고", "홍보", "브랜드"),
    "개발": ("개발", "구축", "시스템", "웹사이트", "ERP", "요구사항"),
}


def reconstruct_report_units(
    resolver_result,
    normalizer_result,
    project_summaries=None,
    doc_summaries=None,
) -> ReportReconstructionResult:
    project_summaries = list(project_summaries or [])
    doc_summaries = list(doc_summaries or [])
    original_names = [_unit_name(unit) for unit in getattr(resolver_result, "work_units", []) or []]
    suppressed = [
        name for name in original_names
        if _is_container_name(name)
    ]
    suppressed.extend(getattr(normalizer_result, "removed_containers", []) or [])
    suppressed = list(dict.fromkeys(name for name in suppressed if name))

    report_units: list[ReportUnit] = []
    for unit in getattr(normalizer_result, "work_units", []) or []:
        name = _unit_name(unit)
        if _is_container_name(name):
            suppressed.append(name)
            continue
        matched_projects = _match_projects(name, project_summaries)
        matched_docs = _match_docs(name, doc_summaries)
        report_units.append(
            ReportUnit(
                unit_name=name,
                unit_type=getattr(unit, "unit_type", ""),
                confidence=getattr(unit, "confidence", 0),
                projects=matched_projects,
                document_names=matched_docs,
            )
        )

    fallback_used = False
    if not report_units:
        fallback_used = True
        for ps in project_summaries:
            name = getattr(ps, "project_name", "") or getattr(ps, "project_key", "")
            if not name or _is_container_name(name):
                name = getattr(ps, "project_key", "")
            if not name:
                continue
            report_units.append(
                ReportUnit(
                    unit_name=name,
                    unit_type="PROJECT",
                    confidence=35,
                    projects=[ps],
                    document_names=list(getattr(ps, "related_files", []) or []),
                )
            )

    if report_units and project_summaries:
        _attach_unmatched_projects(report_units, project_summaries)

    return ReportReconstructionResult(
        original_units=list(dict.fromkeys(original_names)),
        normalized_units=_merge_report_units(report_units),
        suppressed_containers=list(dict.fromkeys(suppressed)),
        fallback_used=fallback_used,
    )


def write_report_reconstruction_report(
    result: ReportReconstructionResult,
    output_dir: str | None = None,
) -> str:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "output"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "report_reconstruction.txt"

    lines = [
        "# Report Reconstruction",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Original Units",
    ]
    _append_list(lines, result.original_units)
    lines.extend(["", "Normalized Units"])
    _append_list(lines, [unit.unit_name for unit in result.normalized_units])
    lines.extend(["", "Suppressed Containers"])
    _append_list(lines, result.suppressed_containers)
    lines.extend(["", "Fallback Used", str(result.fallback_used)])

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _match_projects(unit_name: str, project_summaries: list) -> list:
    keywords = _keywords_for_unit(unit_name)
    matched = []
    for ps in project_summaries:
        text = " ".join(
            str(value or "")
            for value in [
                getattr(ps, "project_key", ""),
                getattr(ps, "project_name", ""),
                getattr(ps, "key_outputs", ""),
                " ".join(getattr(ps, "related_files", []) or []),
                " ".join(getattr(ps, "representative_docs", []) or []),
            ]
        )
        if _matches_keywords(text, keywords):
            matched.append(ps)
    return matched


def _match_docs(unit_name: str, doc_summaries: list) -> list[str]:
    keywords = _keywords_for_unit(unit_name)
    names: list[str] = []
    for doc in doc_summaries:
        name = str(getattr(doc, "display_name", "") or "")
        summary = str(getattr(doc, "summary_text", "") or "")
        if _matches_keywords(" ".join([name, summary]), keywords):
            names.append(name)
    return names


def _attach_unmatched_projects(report_units: list[ReportUnit], project_summaries: list) -> None:
    used = {id(ps) for unit in report_units for ps in unit.projects}
    unmatched = [ps for ps in project_summaries if id(ps) not in used]
    if not unmatched:
        return
    target = max(report_units, key=lambda unit: (unit.confidence, len(unit.projects)))
    target.projects.extend(unmatched)


def _merge_report_units(report_units: list[ReportUnit]) -> list[ReportUnit]:
    merged: dict[str, ReportUnit] = {}
    for unit in report_units:
        key = _normalize(unit.unit_name)
        if key not in merged:
            merged[key] = unit
            continue
        current = merged[key]
        current.confidence = max(current.confidence, unit.confidence)
        current.projects.extend(ps for ps in unit.projects if ps not in current.projects)
        current.document_names.extend(name for name in unit.document_names if name not in current.document_names)
    return sorted(
        merged.values(),
        key=lambda unit: (_unit_type_order(unit.unit_type), -unit.confidence, unit.unit_name),
    )


def _keywords_for_unit(unit_name: str) -> tuple[str, ...]:
    for key, keywords in _UNIT_KEYWORDS.items():
        if key in unit_name:
            return keywords
    return tuple(token for token in re.split(r"[^0-9A-Za-z가-힣]+", unit_name) if len(token) >= 2)


def _matches_keywords(text: str, keywords: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(keyword.lower() in lower for keyword in keywords)


def _unit_name(unit) -> str:
    return str(getattr(unit, "unit_name", "") or "")


def _is_container_name(value: str) -> bool:
    return _normalize(value) in {_normalize(name) for name in _CONTAINER_NAMES}


def _normalize(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").lower())


def _unit_type_order(unit_type: str) -> int:
    return {"PROJECT": 0, "FUNCTION": 1, "CUSTOMER": 2, "DOCUMENT_SET": 3}.get(unit_type, 9)


def _append_list(lines: list[str], items: list[str]) -> None:
    if not items:
        lines.append("- (none)")
        return
    for item in items:
        lines.append(f"- {item}")
