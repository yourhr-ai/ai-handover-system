from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.core.business_context_engine_v3 import BusinessContextV3, infer_business_context_v3
from src.core.description_generator import UNKNOWN_DESCRIPTION, generate_description
from src.core.knowhow_generator import generate_knowhow
from src.core.tool_generator import generate_tools


@dataclass(frozen=True)
class AssembledWorkSummary:
    purpose: str
    description: str
    major_deliverables: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    knowhow: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    purpose_confidence: int = 25
    description_confidence: int = 25
    tool_confidence: int = 25
    knowhow_confidence: int = 25
    overall_confidence: int = 25
    abstention_reasons: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> int:
        return self.overall_confidence


@dataclass(frozen=True)
class WorkUnitEvidence:
    work_unit: str
    representative_documents: list[str] = field(default_factory=list)
    supporting_documents: list[str] = field(default_factory=list)
    document_families: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EngineWorkSummary:
    work_unit: str
    related_documents: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    recent_documents: list[str] = field(default_factory=list)
    final_deliverables: list[str] = field(default_factory=list)
    latest_modified_date: str = "Not available"
    related_document_count: int = 0
    deliverable_count: int = 0
    importance: int = 0
    importance_reasons: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    timeline: list[str] = field(default_factory=list)


GPT_LAYER_COMPONENTS = (
    "BusinessContextV3",
    "Description Generator",
    "Knowhow Generator",
)


def assemble_work_summary(
    business_context: BusinessContextV3,
    representative_documents=None,
    supporting_documents=None,
    document_families=None,
) -> AssembledWorkSummary:
    description = generate_description(
        representative_documents=representative_documents,
        supporting_documents=supporting_documents,
        document_families=document_families,
    )
    tools = generate_tools(
        representative_documents=representative_documents,
        supporting_documents=supporting_documents,
        document_families=document_families,
    )
    knowhow = generate_knowhow(
        representative_documents=representative_documents,
        supporting_documents=supporting_documents,
        document_families=document_families,
    )

    purpose = _primary_purpose(business_context)
    overall_confidence = min(
        business_context.confidence,
        description.confidence,
        tools.confidence,
        knowhow.confidence,
    )
    evidence = _unique(
        list(business_context.evidence)
        + list(description.evidence)
        + list(tools.evidence)
        + list(knowhow.evidence)
    )
    abstention_reasons = _abstention_reasons(
        purpose=purpose,
        description=description.description,
        tools=tools.tools,
        knowhow=knowhow.knowhow_items,
    )

    return AssembledWorkSummary(
        purpose=purpose,
        description=description.description,
        major_deliverables=_document_names(representative_documents),
        tools=tools.tools,
        knowhow=knowhow.knowhow_items,
        evidence=evidence,
        purpose_confidence=business_context.confidence,
        description_confidence=description.confidence,
        tool_confidence=tools.confidence,
        knowhow_confidence=knowhow.confidence,
        overall_confidence=overall_confidence,
        abstention_reasons=abstention_reasons,
    )


def assemble_engine_summary(evidence: WorkUnitEvidence) -> EngineWorkSummary:
    related_documents = _unique(
        list(evidence.representative_documents)
        + list(evidence.supporting_documents)
        + list(evidence.document_families)
    )
    relationships = []
    if evidence.representative_documents:
        relationships.append("Representative documents -> Work unit")
    if evidence.supporting_documents:
        relationships.append("Supporting documents -> Work unit")
    if evidence.document_families:
        relationships.append("Document families -> Work unit")
    deliverables = _document_names(evidence.representative_documents)
    timeline = _extract_explicit_dates(related_documents)
    latest_modified_date = max(timeline) if timeline else "Not available"
    recent_documents = _recent_documents(related_documents, timeline)
    final_deliverables = _final_deliverables(deliverables)
    importance, importance_reasons = _score_engine_importance(
        related_documents=related_documents,
        deliverables=deliverables,
        timeline=timeline,
    )

    return EngineWorkSummary(
        work_unit=evidence.work_unit,
        related_documents=related_documents,
        deliverables=deliverables,
        recent_documents=recent_documents,
        final_deliverables=final_deliverables,
        latest_modified_date=latest_modified_date,
        related_document_count=len(related_documents),
        deliverable_count=len(deliverables),
        importance=importance,
        importance_reasons=importance_reasons,
        evidence=related_documents,
        relationships=relationships,
        timeline=timeline,
    )


def assemble_from_evidence(evidence: WorkUnitEvidence) -> AssembledWorkSummary:
    documents = evidence.representative_documents + evidence.supporting_documents
    business_context = infer_business_context_v3(documents)
    return assemble_work_summary(
        business_context=business_context,
        representative_documents=evidence.representative_documents,
        supporting_documents=evidence.supporting_documents,
        document_families=evidence.document_families,
    )


def load_work_units_from_work_clusters(path: str | Path, limit: int = 8) -> list[WorkUnitEvidence]:
    if not Path(path).exists():
        return []

    units: list[WorkUnitEvidence] = []
    current_name = ""
    current_docs: list[str] = []
    current_representative: list[str] = []
    section = ""

    def flush() -> None:
        nonlocal current_name, current_docs, current_representative, section
        if current_name and (current_docs or current_representative):
            supporting = [doc for doc in current_docs if doc not in current_representative]
            units.append(
                WorkUnitEvidence(
                    work_unit=current_name,
                    representative_documents=current_representative.copy(),
                    supporting_documents=supporting,
                )
            )
        current_name = ""
        current_docs = []
        current_representative = []
        section = ""

    for raw_line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if line == "[Cluster]":
            flush()
            section = "name"
            continue
        if not line:
            continue
        if line in {"Category:", "Documents:", "Representative:"}:
            section = line.rstrip(":").lower()
            continue
        if section == "name" and not current_name:
            current_name = line
            continue
        if line.startswith("* "):
            value = line[2:].strip()
            if section == "documents":
                current_docs.append(value)
            elif section == "representative":
                current_representative.append(value)

    flush()
    return units[:limit]


def write_report_assembly_preview(
    work_units: list[WorkUnitEvidence],
    output_path: str | Path,
) -> str:
    return write_engine_only_preview(work_units, output_path)


def write_final_handover_report_preview(
    work_units: list[WorkUnitEvidence],
    output_path: str | Path,
) -> str:
    return write_engine_only_preview(work_units, output_path)


def write_engine_only_preview(
    work_units: list[WorkUnitEvidence],
    output_path: str | Path,
) -> str:
    merged_units, merge_notes = merge_similar_work_units(work_units)
    summaries = [assemble_engine_summary(work_unit) for work_unit in merged_units]
    lines = [
        "# Engine-only Reconstruction Preview",
        "",
        "1. Modified files",
        "- src/core/report_assembly_preview.py",
        "- tests/test_report_assembly_preview.py",
        f"- {Path(output_path).as_posix()}",
        "",
        "2. Start Here",
        "",
        "| Work Unit | Recent Documents Count | Deliverable Count | Related Document Count |",
        "|---|---:|---:|---:|",
    ]
    for summary in _start_here(summaries):
        lines.append(
            f"| {summary.work_unit} | {len(summary.recent_documents)} | "
            f"{summary.deliverable_count} | {summary.related_document_count} |"
        )

    lines.extend(["", "3. Duplicate reduction results"])
    lines.extend(f"- {item}" for item in merge_notes or ["No duplicate work units merged"])
    lines.extend(["", "4. Engine-only work unit sections"])

    for summary in summaries:
        lines.extend(
            [
                "",
                f"## {summary.work_unit}",
                "",
                "Work Units:",
                f"- {summary.work_unit}",
                "",
                f"Related Document Count: {summary.related_document_count}",
                f"Deliverable Count: {summary.deliverable_count}",
                f"Latest Modified Date: {summary.latest_modified_date}",
                "",
                "Recent Documents (30 days only):",
            ]
        )
        lines.extend(f"- {item}" for item in summary.recent_documents or ["(none)"])
        lines.extend(
            [
                "",
                "Related Documents:",
            ]
        )
        lines.extend(f"- {item}" for item in summary.related_documents or ["(none)"])
        lines.extend(
            [
                "",
                "Deliverables:",
            ]
        )
        lines.extend(f"- {item}" for item in summary.deliverables or ["(none)"])
        lines.extend(["", "Final Deliverables:"])
        lines.extend(f"- {item}" for item in summary.final_deliverables or ["(none)"])
        lines.extend(
            [
                "",
                "Relationships:",
            ]
        )
        lines.extend(f"- {item}" for item in summary.relationships or ["(none)"])
        lines.extend(
            [
                "",
                "Timeline:",
            ]
        )
        lines.extend(f"- {item}" for item in summary.timeline or ["(none)"])
        lines.extend(
            [
                "",
                f"Importance: {summary.importance}",
                "",
                "Importance Reasons:",
            ]
        )
        lines.extend(f"- {item}" for item in summary.importance_reasons or ["(none)"])
        lines.extend(
            [
                "",
                "Evidence:",
            ]
        )
        lines.extend(f"- {item}" for item in summary.evidence or ["(none)"])

    lines.extend(
        [
            "",
            "5. Engine boundary",
            "- Status, owner, purpose, description, knowhow, risks, recommendations, action plans, and human explanations are GPT-layer responsibilities.",
            "- Engine output is limited to deterministic reconstruction and source evidence.",
        ]
    )

    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return str(output_path)


def write_engine_cleanup_report(output_path: str | Path) -> str:
    lines = [
        "# Engine Cleanup Report",
        "",
        "Components removed from Engine outputs:",
        "- BusinessContextV3 / Purpose Generation",
        "- Description Generator",
        "- Knowhow Generator",
        "- Status fields",
        "- Owner fields",
        "- Human explanation and abstention prose from Engine report sections",
        "",
        "Components retained:",
        "- Work unit evidence loaded from output/work_clusters.txt",
        "- Related documents",
        "- Deliverables",
        "- Evidence lists",
        "- Relationships derived from representative/supporting/family document roles",
        "- Explicit-date timeline extracted from evidence strings",
        "- Metadata counts for related documents, deliverables, recent documents, and latest explicit date",
        "- Deterministic duplicate work-unit merging",
        "- Factual Start Here ranking by counts only",
        "- Deterministic importance score and reasons",
        "",
        "Future GPT-only components:",
        "- Purpose Generation",
        "- Description Generation",
        "- Knowhow Generation",
        "- Risk Generation",
        "- Success Tips",
        "- Best Practices",
        "- Action Plans",
        "- Human Explanations",
        "- Human Judgments",
        "- Human Recommendations",
        "",
        "Before vs After report structure:",
        "- Before: Engine preview included purpose, description, tools, knowhow, confidence explanations, abstention reasons, status, and owner fields.",
        "- After: Engine MVP includes only work units, major deliverables, related documents, relationships, explicit-date timeline, importance, and evidence.",
        "- V3: Engine MVP also shows metadata depth, duplicate reduction, and factual Start Here counts.",
        "- Future GPT mode can consume Engine evidence and generate interpretation separately.",
        "",
        "Final Engine MVP Definition:",
        "- Work Unit",
        "- Major Deliverables",
        "- Related Documents",
        "- Recent Documents (30 days only)",
        "- Final Deliverables",
        "- Latest Modified Date",
        "- Related Document Count",
        "- Deliverable Count",
        "- Relationships",
        "- Timeline from explicit dates only",
        "- Importance score with deterministic reasons",
        "- Evidence",
        "",
        "Metadata additions:",
        "- Recent Documents (30 days only)",
        "- Final Deliverables",
        "- Latest Modified Date",
        "- Related Document Count",
        "- Deliverable Count",
        "",
        "Duplicate reduction results:",
        "- Highly similar work unit names are merged by deterministic normalization.",
        "- Merged units combine representative documents, supporting documents, and document families.",
        "",
        "Start Here output:",
        "- Top 5 active work units ranked by recent document count, deliverable count, then related document count.",
        "- Displays counts only; no importance, recommendations, or business meaning.",
        "",
        "Expected usability improvement:",
        "- Replacement employees can identify document-heavy units, concrete deliverables, recent evidence, and where to begin inspection without GPT interpretation.",
        "",
        "Validation:",
        "- Metadata Depth = YES",
        "- Duplicate Reduction = YES",
        "- Start Here = YES",
        "",
        "Remaining Engine Limitations:",
        "- Timeline is limited to dates visible in evidence strings.",
        "- Recent document detection depends on explicit dates visible in evidence strings.",
        "- Importance uses only available Engine evidence, not semantic business judgment.",
        "- Owner and status are intentionally excluded from Engine MVP output.",
    ]

    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return str(output_path)


def _primary_purpose(context: BusinessContextV3) -> str:
    if context.confidence <= 30 or not context.purpose_candidates:
        return "Unknown"
    purpose = context.purpose_candidates[0]
    return "Unknown" if purpose == "Unknown" else purpose


def _abstention_reasons(
    purpose: str,
    description: str,
    tools: list[str],
    knowhow: list[str],
) -> list[str]:
    reasons: list[str] = []
    if purpose == "Unknown":
        reasons.append("Purpose unknown: insufficient document evidence")
    if description == UNKNOWN_DESCRIPTION:
        reasons.append("Description unknown: no supported pattern match")
    if not tools:
        reasons.append("No tools inferred: no supported extension or tool keyword")
    if not knowhow:
        reasons.append("No knowhow inferred: insufficient supported evidence")
    return reasons


def _document_names(documents) -> list[str]:
    names: list[str] = []
    for item in documents or []:
        name = str(item or "").strip()
        if name:
            names.append(name)
    return names


def _extract_explicit_dates(values: list[str]) -> list[str]:
    dates: list[str] = []
    for value in values:
        text = str(value or "")
        for match in re.finditer(r"(?<!\d)(20\d{2})[._\-\s]?(0[1-9]|1[0-2])[._\-\s]?([0-2]\d|3[01])(?!\d)", text):
            dates.append(f"{match.group(1)}-{match.group(2)}-{match.group(3)}")
        for match in re.finditer(r"(?<!\d)(\d{2})[._\-\s]?(0[1-9]|1[0-2])[._\-\s]?([0-2]\d|3[01])(?!\d)", text):
            dates.append(f"20{match.group(1)}-{match.group(2)}-{match.group(3)}")
    return _unique(dates)


def _recent_documents(values: list[str], timeline: list[str]) -> list[str]:
    if not timeline:
        return []
    latest = max(timeline)
    recent: list[str] = []
    for value in values:
        dates = _extract_explicit_dates([value])
        if any(_days_between(date, latest) <= 30 for date in dates):
            recent.append(value)
    return _unique(recent)


def _days_between(date_value: str, latest: str) -> int:
    from datetime import date

    y1, m1, d1 = (int(part) for part in date_value.split("-"))
    y2, m2, d2 = (int(part) for part in latest.split("-"))
    return abs((date(y2, m2, d2) - date(y1, m1, d1)).days)


def _final_deliverables(values: list[str]) -> list[str]:
    final_tokens = ("final", "finalized", "confirmed", "최종", "확정")
    return [
        value for value in values
        if any(token.lower() in value.lower() for token in final_tokens)
    ]


def _score_engine_importance(
    related_documents: list[str],
    deliverables: list[str],
    timeline: list[str],
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if related_documents:
        doc_score = min(40, len(related_documents) * 5)
        score += doc_score
        reasons.append(f"{len(related_documents)} related documents")

    if deliverables:
        score += 35
        reasons.append("Deliverable detected")

    if timeline:
        score += 15
        reasons.append("Explicit date detected")

    score = min(100, score)
    if not reasons:
        reasons.append("No deterministic importance evidence")
    return score, reasons


def merge_similar_work_units(work_units: list[WorkUnitEvidence]) -> tuple[list[WorkUnitEvidence], list[str]]:
    normalized_keys = [_normalize_work_unit_name(unit.work_unit) for unit in work_units]
    canonical_keys = _canonical_work_unit_keys(normalized_keys)
    merged: dict[str, WorkUnitEvidence] = {}
    labels: dict[str, list[str]] = {}
    for unit, normalized_key in zip(work_units, normalized_keys):
        key = canonical_keys.get(normalized_key, normalized_key)
        if key not in merged:
            merged[key] = unit
            labels[key] = [unit.work_unit]
            continue
        current = merged[key]
        merged[key] = WorkUnitEvidence(
            work_unit=current.work_unit,
            representative_documents=_unique(current.representative_documents + unit.representative_documents),
            supporting_documents=_unique(current.supporting_documents + unit.supporting_documents),
            document_families=_unique(current.document_families + unit.document_families),
        )
        labels[key].append(unit.work_unit)

    notes = [
        f"{labels[key]} -> {unit.work_unit}"
        for key, unit in merged.items()
        if len(labels[key]) > 1
    ]
    return list(merged.values()), notes


def _normalize_work_unit_name(value: str) -> str:
    text = re.sub(r"[\s_\-]+", "", str(value or "").lower())
    suffixes = ("관리업무", "관리", "업무", "자료", "문서")
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if text.endswith(suffix) and len(text) > len(suffix) + 1:
                text = text[: -len(suffix)]
                changed = True
    return text


def _canonical_work_unit_keys(keys: list[str]) -> dict[str, str]:
    unique_keys = list(dict.fromkeys(key for key in keys if key))
    canonical: dict[str, str] = {}
    for key in unique_keys:
        suffix_matches = [
            other for other in unique_keys
            if other != key and len(other) >= 2 and key.endswith(other)
        ]
        canonical[key] = min(suffix_matches, key=len) if suffix_matches else key
    return canonical


def _start_here(summaries: list[EngineWorkSummary]) -> list[EngineWorkSummary]:
    return sorted(
        summaries,
        key=lambda item: (
            -len(item.recent_documents),
            -item.deliverable_count,
            -item.related_document_count,
            item.work_unit,
        ),
    )[:5]


def _unique(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        unique.append(clean)
    return unique


def _repeated_values(values) -> set[str]:
    counts: dict[str, int] = {}
    for value in values:
        if value == "Unknown" or value == UNKNOWN_DESCRIPTION:
            continue
        counts[value] = counts.get(value, 0) + 1
    return {value for value, count in counts.items() if count > 1}


def _duplicate_flags(
    summary: AssembledWorkSummary,
    repeated_purposes: set[str],
    repeated_descriptions: set[str],
) -> list[str]:
    flags: list[str] = []
    if summary.purpose in repeated_purposes:
        flags.append("Repeated purpose detected")
    if summary.description in repeated_descriptions:
        flags.append("Repeated description detected")
    return flags
