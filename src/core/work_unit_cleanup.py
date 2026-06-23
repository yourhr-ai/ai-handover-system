from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.core.work_unit_resolver import FUNCTION, WorkUnit


@dataclass
class PromotedWorkUnit:
    original_name: str
    promoted_name: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class WorkUnitCleanupResult:
    before_cleanup: list[WorkUnit] = field(default_factory=list)
    clean_work_units: list[WorkUnit] = field(default_factory=list)
    removed_units: list[str] = field(default_factory=list)
    promoted_units: list[PromotedWorkUnit] = field(default_factory=list)


_CONTAINER_SIGNALS = {
    "고객사",
    "결과물",
    "회의록",
    "자료",
    "문서",
    "기타",
    "output",
    "outputs",
    "result",
    "results",
    "document",
    "documents",
    "misc",
}

_WORK_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "인사",
        (
            "인사",
            "채용",
            "채용공고",
            "면접",
            "면접평가",
            "입사서류",
            "성과평가",
            "평가가이드",
            "평가양식",
            "연봉",
            "연봉인상",
            "보상",
            "recruit",
            "interview",
            "performance",
            "evaluation",
            "salary",
            "compensation",
        ),
    ),
    (
        "교육",
        (
            "교육",
            "교육자료",
            "교육결과",
            "신입사원교육",
            "만족도조사",
            "training",
            "education",
        ),
    ),
    (
        "회계",
        (
            "회계",
            "결산",
            "결산보고",
            "재무제표",
            "부가세",
            "세무",
            "세금",
            "closing",
            "financial",
            "vat",
            "tax",
            "accounting",
        ),
    ),
    (
        "영업",
        (
            "영업",
            "제안서",
            "견적서",
            "고객미팅",
            "미팅메모",
            "제안",
            "견적",
            "sales",
            "proposal",
            "quotation",
            "quote",
            "estimate",
        ),
    ),
    (
        "총무",
        (
            "총무",
            "비품",
            "비품관리",
            "차량",
            "차량관리",
            "법인차량",
            "자산관리",
            "admin",
            "asset",
            "facility",
        ),
    ),
)

_PURPOSE_TO_WORK_UNIT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("인사", ("채용", "인력", "평가", "보상", "recruit", "hiring", "performance", "compensation")),
    ("교육", ("교육", "training", "education")),
    ("회계", ("결산", "세무", "재무", "회계", "closing", "tax", "finance", "accounting")),
    ("영업", ("영업", "제안", "견적", "sales", "proposal", "quotation")),
    ("총무", ("총무", "비품", "차량", "자산", "admin", "asset")),
)


class WorkUnitCleanup:
    """Remove container work units and promote them using nearby evidence."""

    def cleanup(
        self,
        resolved_work_units,
        category_discovery_results=None,
        business_context_v3_results=None,
    ) -> WorkUnitCleanupResult:
        before = _extract_work_units(resolved_work_units)
        evidence_by_unit = _collect_evidence_by_unit(category_discovery_results, business_context_v3_results)
        global_evidence = _collect_global_evidence(category_discovery_results, business_context_v3_results)

        clean: list[WorkUnit] = []
        removed: list[str] = []
        promoted: list[PromotedWorkUnit] = []

        for unit in before:
            unit_name = _unit_name(unit)
            if not _is_container_only(unit_name):
                clean.append(unit)
                continue

            evidence = evidence_by_unit.get(_key(unit_name), [])
            if not evidence and len(before) == 1:
                evidence = global_evidence
            evidence = _unique_limited(evidence, 30)
            promoted_name = _infer_work_unit(evidence)
            if promoted_name:
                clean.append(
                    WorkUnit(
                        unit_type=FUNCTION,
                        unit_name=promoted_name,
                        confidence=_promoted_confidence(unit, evidence),
                        source_reason=f"promoted from container unit: {unit_name}",
                    )
                )
                promoted.append(PromotedWorkUnit(unit_name, promoted_name, evidence[:10]))
            else:
                removed.append(unit_name)

        merged = _merge_work_units(clean)
        return WorkUnitCleanupResult(
            before_cleanup=before,
            clean_work_units=merged,
            removed_units=_unique_limited(removed, 100),
            promoted_units=_dedupe_promotions(promoted),
        )


def cleanup_work_units(
    resolved_work_units,
    category_discovery_results=None,
    business_context_v3_results=None,
) -> WorkUnitCleanupResult:
    return WorkUnitCleanup().cleanup(
        resolved_work_units,
        category_discovery_results=category_discovery_results,
        business_context_v3_results=business_context_v3_results,
    )


def write_work_unit_cleanup_report(
    result: WorkUnitCleanupResult,
    output_dir: str | None = None,
) -> str:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "output"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "work_unit_cleanup.txt"

    lines = [
        "# Work Unit Cleanup",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Before cleanup",
    ]
    _append_units(lines, result.before_cleanup)
    lines.extend(["", "After cleanup"])
    _append_units(lines, result.clean_work_units)
    lines.extend(["", "Removed units"])
    _append_names(lines, result.removed_units)
    lines.extend(["", "Promoted units"])
    if result.promoted_units:
        for item in result.promoted_units:
            lines.append(f"- {item.original_name} -> {item.promoted_name}")
            if item.evidence:
                lines.append(f"  evidence: {', '.join(item.evidence[:5])}")
    else:
        lines.append("- (none)")

    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return str(path)


def _extract_work_units(value) -> list[WorkUnit]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return list(getattr(value, "work_units", []) or [])


def _collect_evidence_by_unit(category_results, business_context_results) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = {}

    for category in _iter_categories(category_results):
        name = str(getattr(category, "category_name", "") or "")
        files = list(getattr(category, "evidence_files", []) or [])
        keywords = list(getattr(category, "evidence_keywords", []) or [])
        if name:
            evidence.setdefault(_key(name), []).extend(files + keywords)

    if isinstance(business_context_results, dict):
        for unit_name, context in business_context_results.items():
            evidence.setdefault(_key(str(unit_name)), []).extend(_context_evidence(context))
            evidence.setdefault(_key(str(unit_name)), []).extend(_context_purposes(context))
    else:
        for context in business_context_results or []:
            unit_name = str(getattr(context, "work_unit_name", "") or "")
            if unit_name:
                evidence.setdefault(_key(unit_name), []).extend(_context_evidence(context))
                evidence.setdefault(_key(unit_name), []).extend(_context_purposes(context))

    return {key: _unique_limited(values, 50) for key, values in evidence.items()}


def _collect_global_evidence(category_results, business_context_results) -> list[str]:
    values: list[str] = []
    for category in _iter_categories(category_results):
        values.append(str(getattr(category, "category_name", "") or ""))
        values.extend(str(item) for item in (getattr(category, "evidence_files", []) or []))
        values.extend(str(item) for item in (getattr(category, "evidence_keywords", []) or []))

    contexts = business_context_results.values() if isinstance(business_context_results, dict) else business_context_results
    for context in contexts or []:
        values.extend(_context_evidence(context))
        values.extend(str(item) for item in (getattr(context, "purpose_candidates", []) or []))

    return _unique_limited(values, 100)


def _iter_categories(category_results):
    if category_results is None:
        return []
    if isinstance(category_results, list):
        return category_results
    return list(getattr(category_results, "categories", []) or [])


def _context_evidence(context) -> list[str]:
    values = [str(item) for item in (getattr(context, "evidence", []) or [])]
    values.extend(str(item) for item in (getattr(context, "evidence_files", []) or []))
    return values


def _context_purposes(context) -> list[str]:
    return [
        str(item)
        for item in (getattr(context, "purpose_candidates", []) or [])
        if str(item).strip().lower() != "unknown"
    ]


def _infer_work_unit(evidence: list[str]) -> str:
    text = _normalize_text(" ".join(evidence))
    scores: dict[str, int] = {}

    for work_unit, keywords in _WORK_RULES:
        for keyword in keywords:
            if _normalize_text(keyword) in text:
                scores[work_unit] = scores.get(work_unit, 0) + 1

    for purpose in evidence:
        for work_unit, keywords in _PURPOSE_TO_WORK_UNIT:
            if any(_normalize_text(keyword) in _normalize_text(str(purpose)) for keyword in keywords):
                scores[work_unit] = scores.get(work_unit, 0) + 1

    if not scores:
        return ""
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _promoted_confidence(unit: WorkUnit, evidence: list[str]) -> int:
    base = int(getattr(unit, "confidence", 0) or 0)
    return _clamp(max(60, min(82, base + 8 + min(len(evidence), 5) * 2)))


def _merge_work_units(units: list[WorkUnit]) -> list[WorkUnit]:
    merged: dict[tuple[str, str], WorkUnit] = {}
    for unit in units:
        if _is_container_only(_unit_name(unit)):
            continue
        key = (str(getattr(unit, "unit_type", "") or FUNCTION), _key(_unit_name(unit)))
        if key not in merged:
            merged[key] = unit
            continue
        current = merged[key]
        current.confidence = max(current.confidence, unit.confidence)
        reason = str(getattr(unit, "source_reason", "") or "")
        if reason and reason not in current.source_reason:
            current.source_reason = f"{current.source_reason}; {reason}"
    return sorted(merged.values(), key=lambda item: (-item.confidence, item.unit_name))


def _dedupe_promotions(promotions: list[PromotedWorkUnit]) -> list[PromotedWorkUnit]:
    deduped: dict[tuple[str, str], PromotedWorkUnit] = {}
    for item in promotions:
        key = (_key(item.original_name), _key(item.promoted_name))
        if key not in deduped:
            deduped[key] = item
            continue
        deduped[key].evidence = _unique_limited(deduped[key].evidence + item.evidence, 10)
    return list(deduped.values())


def _is_container_only(value: str) -> bool:
    normalized = _key(value)
    if not normalized:
        return False
    signals = {_key(signal) for signal in _CONTAINER_SIGNALS}
    return normalized in signals or all(signal in signals for signal in re.split(r"[/\\]+", normalized) if signal)


def _unit_name(unit) -> str:
    return str(getattr(unit, "unit_name", "") or unit or "").strip()


def _key(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").lower())


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s_\-./\\]+", "", str(value or "").lower())


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


def _append_units(lines: list[str], units: list[WorkUnit]) -> None:
    if not units:
        lines.append("- (none)")
        return
    for unit in units:
        lines.append(f"- [{unit.unit_type}] {unit.unit_name} ({unit.confidence})")


def _append_names(lines: list[str], names: list[str]) -> None:
    if not names:
        lines.append("- (none)")
        return
    for name in names:
        lines.append(f"- {name}")


def _clamp(value: int | float) -> int:
    return int(max(0, min(100, value)))
