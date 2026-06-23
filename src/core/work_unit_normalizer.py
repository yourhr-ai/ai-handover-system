from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.core.work_unit_resolver import DOCUMENT_SET, FUNCTION, PROJECT, WorkUnit, WorkUnitResolverResult


@dataclass
class WorkUnitNormalizerResult:
    work_units: list[WorkUnit] = field(default_factory=list)
    removed_containers: list[str] = field(default_factory=list)
    promoted_work_units: list[str] = field(default_factory=list)
    retained_low_confidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_CONTAINER_NAMES = {
    "결과물", "output", "outputs", "result", "results", "document", "documents",
    "자료", "문서", "기타", "misc", "archive", "old", "backup",
    "회의록", "meeting", "notes", "고객사", "client", "clients", "customers",
}

_REMOVE_CONTAINERS = {
    "결과물", "고객사", "회의록", "기타", "자료", "문서",
    "output", "outputs", "result", "results", "document", "documents", "misc",
}

_WORK_KEYWORDS = {
    "인사": ("인사", "평가", "채용", "면접", "연봉", "보상", "성과평가"),
    "교육": ("교육", "교안", "만족도", "훈련", "연수", "온보딩", "신입사원"),
    "회계": ("회계", "결산", "세무", "부가세", "재무", "정산"),
    "총무": ("총무", "구매", "비품", "자산", "행정"),
    "법무": ("계약", "법무", "NDA", "규정", "법률검토"),
    "영업": ("영업", "견적", "제안", "수주", "고객관리"),
    "마케팅": ("마케팅", "캠페인", "광고", "홍보", "브랜드"),
    "개발": ("개발", "구축", "시스템", "웹사이트", "ERP", "요구사항"),
}

_CUSTOMER_HINTS = ("고객", "고객사", "client", "customer", "미팅", "견적", "제안", "회의")
_MEETING_HINTS = ("회의록", "meeting", "notes", "미팅메모", "회의메모")


class WorkUnitNormalizer:
    """Post-process resolved work units and remove container folder leakage."""

    def normalize_work_units(
        self,
        resolver_result: WorkUnitResolverResult | list[WorkUnit],
        work_clusters=None,
        document_families=None,
    ) -> WorkUnitNormalizerResult:
        units = _extract_units(resolver_result)
        evidence_text = _build_evidence_text(work_clusters or [], document_families or [])
        removed: list[str] = []
        promoted: list[str] = []
        retained_low: list[str] = []
        warnings: list[str] = []
        normalized: list[WorkUnit] = []

        for unit in units:
            if not _is_container_name(unit.unit_name):
                normalized.append(unit)
                continue

            inferred = _infer_units_from_text(evidence_text)
            if inferred:
                removed.append(unit.unit_name)
                for name in inferred:
                    promoted.append(name)
                    normalized.append(
                        WorkUnit(
                            unit_type=FUNCTION,
                            unit_name=name,
                            confidence=max(65, unit.confidence + 10),
                            source_reason=f"promoted from container: {unit.unit_name}",
                        )
                    )
                continue

            if _is_removable_container(unit.unit_name):
                if _is_customer_container(unit.unit_name):
                    promoted.append("고객관리")
                    normalized.append(
                        WorkUnit(
                            unit_type=FUNCTION,
                            unit_name="고객관리",
                            confidence=45,
                            source_reason=f"low-confidence promotion from container: {unit.unit_name}",
                        )
                    )
                    retained_low.append("고객관리")
                else:
                    removed.append(unit.unit_name)
                    warnings.append(f"removed container work unit: {unit.unit_name}")
                continue

            retained = WorkUnit(
                unit_type=unit.unit_type,
                unit_name=unit.unit_name,
                confidence=min(unit.confidence, 35),
                source_reason=f"retained low confidence container: {unit.source_reason}",
            )
            normalized.append(retained)
            retained_low.append(unit.unit_name)

        if not normalized:
            inferred = _infer_units_from_text(evidence_text)
            for name in inferred:
                promoted.append(name)
                normalized.append(
                    WorkUnit(
                        unit_type=FUNCTION,
                        unit_name=name,
                        confidence=70,
                        source_reason="promoted from evidence files",
                    )
                )

        merged = _merge_units(normalized)
        return WorkUnitNormalizerResult(
            work_units=merged,
            removed_containers=list(dict.fromkeys(removed)),
            promoted_work_units=list(dict.fromkeys(promoted)),
            retained_low_confidence=list(dict.fromkeys(retained_low)),
            warnings=warnings,
        )


def normalize_work_units(
    resolver_result: WorkUnitResolverResult | list[WorkUnit],
    work_clusters=None,
    document_families=None,
) -> WorkUnitNormalizerResult:
    return WorkUnitNormalizer().normalize_work_units(resolver_result, work_clusters, document_families)


def write_work_unit_normalizer_report(
    result: WorkUnitNormalizerResult,
    output_dir: str | None = None,
) -> str:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "output"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "work_unit_normalizer.txt"

    lines = [
        "# Work Unit Normalizer",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Removed Containers",
    ]
    _append_lines(lines, result.removed_containers)
    lines.extend(["", "Promoted Work Units"])
    _append_lines(lines, result.promoted_work_units)
    lines.extend(["", "Normalized Work Units"])
    for unit in result.work_units:
        lines.append(f"- [{unit.unit_type}] {unit.unit_name} ({unit.confidence})")
    lines.extend(["", "Retained Low Confidence"])
    _append_lines(lines, result.retained_low_confidence)
    if result.warnings:
        lines.extend(["", "Warnings"])
        _append_lines(lines, result.warnings)

    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return str(path)


def _extract_units(resolver_result: WorkUnitResolverResult | list[WorkUnit]) -> list[WorkUnit]:
    if isinstance(resolver_result, list):
        return list(resolver_result)
    return list(getattr(resolver_result, "work_units", []) or [])


def _build_evidence_text(work_clusters, document_families) -> str:
    parts: list[str] = []
    for cluster in work_clusters or []:
        parts.append(str(getattr(cluster, "cluster_key", "") or ""))
        parts.extend(str(keyword) for keyword in (getattr(cluster, "keywords", []) or []))
        for doc in getattr(cluster, "documents", []) or []:
            parts.append(_doc_name(doc))

    families = document_families.values() if isinstance(document_families, dict) else document_families
    for family_group in families or []:
        if isinstance(family_group, list):
            family_iter = family_group
        else:
            family_iter = [family_group]
        for family in family_iter:
            parts.append(str(getattr(family, "family_key", "") or ""))
            for doc in getattr(family, "family_docs", []) or []:
                parts.append(_doc_name(doc))
    return " ".join(parts)


def _infer_units_from_text(text: str) -> list[str]:
    lower = text.lower()
    scores: dict[str, int] = {}
    for unit_name, keywords in _WORK_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in lower:
                scores[unit_name] = scores.get(unit_name, 0) + 1

    if not scores and any(hint.lower() in lower for hint in _CUSTOMER_HINTS):
        scores["고객관리"] = 1
    if not scores and any(hint.lower() in lower for hint in _MEETING_HINTS):
        return []

    return [
        name for name, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    ][:6]


def _merge_units(units: list[WorkUnit]) -> list[WorkUnit]:
    merged: dict[tuple[str, str], WorkUnit] = {}
    for unit in units:
        if _is_container_name(unit.unit_name):
            continue
        key = (unit.unit_type, _normalize(unit.unit_name))
        if key not in merged:
            merged[key] = unit
            continue
        current = merged[key]
        current.confidence = max(current.confidence, unit.confidence)
        if unit.source_reason not in current.source_reason:
            current.source_reason = f"{current.source_reason}; {unit.source_reason}"

    return sorted(
        merged.values(),
        key=lambda u: (_unit_type_order(u.unit_type), -u.confidence, u.unit_name),
    )


def _unit_type_order(unit_type: str) -> int:
    return {PROJECT: 0, FUNCTION: 1, DOCUMENT_SET: 3}.get(unit_type, 2)


def _doc_name(doc) -> str:
    if isinstance(doc, str):
        return doc
    return str(getattr(doc, "display_name", "") or getattr(doc, "file_name", "") or "")


def _is_container_name(value: str) -> bool:
    return _normalize(value) in {_normalize(name) for name in _CONTAINER_NAMES}


def _is_removable_container(value: str) -> bool:
    return _normalize(value) in {_normalize(name) for name in _REMOVE_CONTAINERS}


def _is_customer_container(value: str) -> bool:
    return _normalize(value) in {_normalize(name) for name in ("고객사", "client", "clients", "customers")}


def _normalize(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").lower())


def _append_lines(lines: list[str], items: list[str]) -> None:
    if not items:
        lines.append("- (none)")
        return
    for item in items:
        lines.append(f"- {item}")
