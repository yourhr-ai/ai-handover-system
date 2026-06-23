from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path

from src.core.work_unit_resolver import WorkUnit


@dataclass
class ContextCandidate:
    name: str
    confidence: int
    evidence_files: list[str] = field(default_factory=list)


@dataclass
class WorkflowCandidate:
    name: str
    steps: list[str] = field(default_factory=list)
    confidence: int = 0
    evidence_files: list[str] = field(default_factory=list)


@dataclass
class BusinessContext:
    work_unit_name: str
    purpose_candidates: list[ContextCandidate] = field(default_factory=list)
    workflow_candidates: list[WorkflowCandidate] = field(default_factory=list)
    tool_candidates: list[ContextCandidate] = field(default_factory=list)
    deliverable_candidates: list[ContextCandidate] = field(default_factory=list)
    confidence: int = 0
    evidence_files: list[str] = field(default_factory=list)


_PURPOSE_RULES: tuple[dict, ...] = (
    {
        "name": "채용 운영",
        "keywords": ("채용공고", "채용", "면접", "면접평가", "입사서류", "입사", "지원자", "recruit", "interview", "onboarding"),
    },
    {
        "name": "평가 운영",
        "keywords": ("성과평가", "평가기획", "평가양식", "평가표", "인사평가", "performance", "evaluation", "review"),
    },
    {
        "name": "영업 제안",
        "keywords": ("견적서", "제안서", "견적", "제안", "영업", "수주", "proposal", "quotation", "quote", "sales"),
    },
    {
        "name": "재무 관리",
        "keywords": ("정산", "청구", "세금계산서", "매출", "매입", "예산", "결산", "회계", "invoice", "finance", "budget"),
    },
    {
        "name": "개발 운영",
        "keywords": ("개발", "요구사항", "설계서", "api", "코드", "배포", "버그", "개선", "python", "typescript", "database"),
    },
    {
        "name": "마케팅 운영",
        "keywords": ("마케팅", "캠페인", "광고", "콘텐츠", "프로모션", "홍보", "브랜드", "marketing", "campaign", "promotion"),
    },
)

_WORKFLOW_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("채용공고", "채용 공고", "job posting", "recruit"), "채용공고 작성"),
    (("면접평가표", "면접", "interview"), "면접 진행"),
    (("입사서류", "입사", "onboarding"), "입사 처리"),
    (("성과평가", "평가기획", "평가 계획", "performance"), "평가 기획"),
    (("평가양식", "평가표", "evaluation form"), "평가 양식 작성"),
    (("평가결과", "결과보고", "feedback"), "평가 결과 정리"),
    (("견적서", "견적", "quotation", "quote"), "견적 작성"),
    (("제안서", "제안", "proposal"), "제안서 작성"),
    (("계약", "contract"), "계약 검토"),
    (("청구", "세금계산서", "invoice"), "청구 처리"),
    (("정산", "결산", "closing"), "정산 처리"),
    (("요구사항", "requirements"), "요구사항 정리"),
    (("설계", "design", "spec"), "설계"),
    (("개발", "구현", "code", "api"), "개발"),
    (("테스트", "test", "qa"), "테스트"),
    (("배포", "deploy", "release"), "배포"),
    (("캠페인", "campaign"), "캠페인 기획"),
    (("성과", "리포트", "report"), "성과 분석"),
    (("광고", "ad "), "광고 집행"),
)

_TOOL_BY_EXTENSION = {
    ".xlsx": "Excel",
    ".xls": "Excel",
    ".xlsm": "Excel",
    ".csv": "Excel",
    ".docx": "Word",
    ".doc": "Word",
    ".pptx": "PowerPoint",
    ".ppt": "PowerPoint",
    ".sql": "Database",
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
}


class BusinessContextEngineV2:
    """Infer business context from document evidence using deterministic rules."""

    def build_context(
        self,
        work_unit: WorkUnit,
        representative_documents=None,
        supporting_documents=None,
        document_families=None,
        work_status=None,
    ) -> BusinessContext:
        representative_documents = list(representative_documents or [])
        supporting_documents = list(supporting_documents or [])
        family_names = _document_family_names(document_families or [])

        evidence_docs = _unique_docs(
            representative_documents
            + supporting_documents
            + _documents_from_families(document_families or [])
        )
        evidence_files = _unique_limited([_file_name(_doc_name(doc)) for doc in evidence_docs], 30)

        purpose_inputs = [_work_unit_name(work_unit)]
        purpose_inputs.extend(_doc_name(doc) for doc in representative_documents)
        purpose_inputs.extend(family_names)
        purpose_candidates = _purpose_candidates(
            purpose_inputs,
            evidence_files,
            _work_unit_confidence(work_unit),
        )

        workflow_candidates = _workflow_candidates(evidence_docs)
        tool_candidates = _tool_candidates(evidence_docs)
        deliverable_candidates = _deliverable_candidates(representative_documents)

        confidence = _overall_confidence(
            purpose_candidates=purpose_candidates,
            workflow_candidates=workflow_candidates,
            tool_candidates=tool_candidates,
            deliverable_candidates=deliverable_candidates,
            evidence_count=len(evidence_files),
            family_names=family_names,
            work_unit_confidence=_work_unit_confidence(work_unit),
            work_status_confidence=_work_status_confidence(work_status),
        )

        return BusinessContext(
            work_unit_name=_work_unit_name(work_unit),
            purpose_candidates=purpose_candidates,
            workflow_candidates=workflow_candidates,
            tool_candidates=tool_candidates,
            deliverable_candidates=deliverable_candidates,
            confidence=confidence,
            evidence_files=evidence_files,
        )

    def build_contexts(
        self,
        work_units,
        representative_results=None,
        document_families=None,
        work_statuses=None,
    ) -> dict[str, BusinessContext]:
        representative_results = representative_results or {}
        document_families = document_families or {}
        work_statuses = work_statuses or {}
        contexts: dict[str, BusinessContext] = {}

        for work_unit in work_units or []:
            key = _work_unit_name(work_unit)
            rep_result = _lookup_by_key(representative_results, key)
            families = _lookup_by_key(document_families, key, default=[])
            status = _lookup_status(work_statuses, key)
            contexts[key] = self.build_context(
                work_unit,
                representative_documents=getattr(rep_result, "representative_docs", []) if rep_result else [],
                supporting_documents=getattr(rep_result, "supporting_docs", []) if rep_result else [],
                document_families=families,
                work_status=status,
            )

        return contexts


def write_business_context_v2_report(
    contexts: dict[str, BusinessContext] | list[BusinessContext],
    output_dir: str | None = None,
) -> str:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "output"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "business_context_v2.txt"

    context_list = list(contexts.values()) if isinstance(contexts, dict) else list(contexts)
    lines = [
        "# Business Context V2",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    for context in context_list:
        lines.extend(["[Work Unit]", context.work_unit_name, ""])
        lines.append("Purpose Candidates")
        _append_candidates(lines, context.purpose_candidates)
        lines.extend(["", "Workflow Candidates"])
        _append_workflows(lines, context.workflow_candidates)
        lines.extend(["", "Tool Candidates"])
        _append_names(lines, context.tool_candidates)
        lines.extend(["", "Deliverables"])
        _append_candidates(lines, context.deliverable_candidates)
        lines.extend(["", "Evidence"])
        _append_plain(lines, context.evidence_files)
        lines.extend(["", "Confidence", str(context.confidence), ""])

    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return str(path)


def _purpose_candidates(
    inputs: list[str],
    evidence_files: list[str],
    work_unit_confidence: int,
) -> list[ContextCandidate]:
    scored: list[ContextCandidate] = []
    evidence_texts = [(text or "", (text or "").lower()) for text in inputs]

    for rule in _PURPOSE_RULES:
        matches = 0
        matched_files: list[str] = []
        for original, lower in evidence_texts:
            if any(keyword.lower() in lower for keyword in rule["keywords"]):
                matches += 1
                if Path(original.replace("\\", "/")).suffix:
                    matched_files.append(_file_name(original))

        if matches == 0:
            continue

        consistency_bonus = min(18, max(0, matches - 1) * 6)
        confidence = _clamp(42 + matches * 14 + consistency_bonus + work_unit_confidence // 10, 0, 100)
        scored.append(
            ContextCandidate(
                name=rule["name"],
                confidence=confidence,
                evidence_files=_unique_limited(matched_files or evidence_files, 8),
            )
        )

    scored.sort(key=lambda candidate: (-candidate.confidence, candidate.name))
    return scored[:5]


def _workflow_candidates(docs: list[object]) -> list[WorkflowCandidate]:
    steps: list[str] = []
    evidence: list[str] = []

    for doc in docs:
        name = _doc_name(doc)
        lower = name.lower()
        for keywords, step in _WORKFLOW_RULES:
            if any(keyword.lower() in lower for keyword in keywords):
                if step not in steps:
                    steps.append(step)
                evidence.append(_file_name(name))
                break

    if not steps:
        return []

    confidence = _clamp(38 + len(steps) * 13 + min(len(evidence), 5) * 4, 0, 100)
    return [
        WorkflowCandidate(
            name=" → ".join(steps),
            steps=steps,
            confidence=confidence,
            evidence_files=_unique_limited(evidence, 10),
        )
    ]


def _tool_candidates(docs: list[object]) -> list[ContextCandidate]:
    evidence_by_tool: dict[str, list[str]] = {}
    for doc in docs:
        name = _doc_name(doc)
        tool = _TOOL_BY_EXTENSION.get(Path(name.replace("\\", "/")).suffix.lower())
        if tool:
            evidence_by_tool.setdefault(tool, []).append(_file_name(name))

    candidates = [
        ContextCandidate(
            name=tool,
            confidence=_clamp(55 + min(len(files), 5) * 8, 0, 100),
            evidence_files=_unique_limited(files, 8),
        )
        for tool, files in evidence_by_tool.items()
    ]
    candidates.sort(key=lambda candidate: (-candidate.confidence, candidate.name))
    return candidates


def _deliverable_candidates(representative_documents: list[object]) -> list[ContextCandidate]:
    candidates: list[ContextCandidate] = []
    for doc in representative_documents:
        name = _doc_name(doc)
        if not name:
            continue
        confidence = getattr(doc, "dvs", None)
        if confidence is None:
            confidence = getattr(doc, "deliverable_score", 0) + getattr(doc, "filename_signal_score", 0) + 45
        candidates.append(
            ContextCandidate(
                name=_file_name(name),
                confidence=_clamp(int(confidence), 0, 100),
                evidence_files=[_file_name(name)],
            )
        )

    candidates.sort(key=lambda candidate: (-candidate.confidence, candidate.name))
    return candidates


def _overall_confidence(
    purpose_candidates: list[ContextCandidate],
    workflow_candidates: list[WorkflowCandidate],
    tool_candidates: list[ContextCandidate],
    deliverable_candidates: list[ContextCandidate],
    evidence_count: int,
    family_names: list[str],
    work_unit_confidence: int,
    work_status_confidence: int,
) -> int:
    if not purpose_candidates and not workflow_candidates:
        return _clamp(8 + min(evidence_count, 3) * 4 + work_unit_confidence // 12, 0, 35)

    best_purpose = purpose_candidates[0].confidence if purpose_candidates else 0
    best_workflow = workflow_candidates[0].confidence if workflow_candidates else 0
    score = 20
    score += min(evidence_count, 8) * 4
    score += min(len(family_names), 5) * 3
    score += work_unit_confidence // 8
    score += work_status_confidence // 12
    score += best_purpose // 4
    score += best_workflow // 5
    score += min(len(tool_candidates), 3) * 3
    score += min(len(deliverable_candidates), 3) * 4
    return _clamp(score, 0, 100)


def _document_family_names(document_families) -> list[str]:
    names: list[str] = []
    families = _iter_values(document_families)
    for family in families:
        family_key = str(getattr(family, "family_key", "") or "")
        if family_key:
            names.append(family_key)
    return _unique_limited(names, 30)


def _documents_from_families(document_families) -> list[object]:
    docs: list[object] = []
    for family in _iter_values(document_families):
        docs.extend(list(getattr(family, "family_docs", []) or []))
        latest = getattr(family, "latest_doc", None)
        if latest is not None:
            docs.append(latest)
    return docs


def _unique_docs(docs: list[object]) -> list[object]:
    seen: set[str] = set()
    unique: list[object] = []
    for doc in docs:
        key = _doc_name(doc)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(doc)
    return unique


def _doc_name(doc: object) -> str:
    if isinstance(doc, str):
        return doc
    for attr in ("display_name", "file_name", "name", "path"):
        value = getattr(doc, attr, "")
        if value:
            return str(value)
    return str(doc) if doc is not None else ""


def _file_name(value: str) -> str:
    return Path(str(value).replace("\\", "/")).name


def _work_unit_name(work_unit: WorkUnit) -> str:
    return str(getattr(work_unit, "unit_name", "") or "")


def _work_unit_confidence(work_unit: WorkUnit) -> int:
    return _clamp(int(getattr(work_unit, "confidence", 0) or 0), 0, 100)


def _work_status_confidence(work_status) -> int:
    return _clamp(int(getattr(work_status, "confidence", 0) or 0), 0, 100)


def _lookup_by_key(mapping, key: str, default=None):
    if isinstance(mapping, dict):
        return mapping.get(key, default)
    return default


def _lookup_status(work_statuses, key: str):
    if isinstance(work_statuses, dict):
        return work_statuses.get(key)
    for status in work_statuses or []:
        if getattr(status, "work_unit_name", None) == key:
            return status
    return None


def _iter_values(value) -> list:
    if isinstance(value, dict):
        values: list = []
        for item in value.values():
            if isinstance(item, list):
                values.extend(item)
            else:
                values.append(item)
        return values
    return list(value or [])


def _unique_limited(values: list[str], limit: int) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        unique.append(clean)
        if len(unique) >= limit:
            break
    return unique


def _append_candidates(lines: list[str], candidates: list[ContextCandidate]) -> None:
    if not candidates:
        lines.append("- (none)")
        return
    for candidate in candidates:
        lines.append(f"- {candidate.name} ({candidate.confidence})")


def _append_workflows(lines: list[str], candidates: list[WorkflowCandidate]) -> None:
    if not candidates:
        lines.append("- (none)")
        return
    for candidate in candidates:
        lines.append(f"- {candidate.name} ({candidate.confidence})")


def _append_names(lines: list[str], candidates: list[ContextCandidate]) -> None:
    if not candidates:
        lines.append("- (none)")
        return
    for candidate in candidates:
        lines.append(f"- {candidate.name}")


def _append_plain(lines: list[str], values: list[str]) -> None:
    if not values:
        lines.append("- (none)")
        return
    for value in values:
        lines.append(f"- {value}")


def _clamp(value: int | float, low: int, high: int) -> int:
    return int(max(low, min(high, value)))
