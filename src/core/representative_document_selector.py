from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.document_family import DocumentFamilyEngine
from src.core.document_value_score import calculate_document_value_score

if TYPE_CHECKING:
    from src.core.document_summarizer import DocumentSummary


@dataclass
class RepresentativeDocument:
    display_name: str
    file_name: str
    dvs: int
    modified_dt: str
    deliverable_score: int
    filename_signal_score: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class RepresentativeDocumentResult:
    project_key: str
    representative_docs: list[RepresentativeDocument] = field(default_factory=list)
    supporting_docs: list[RepresentativeDocument] = field(default_factory=list)
    reference_docs: list[RepresentativeDocument] = field(default_factory=list)


_CORE_OUTPUT_KEYWORDS = (
    "제안서", "기획안", "운영안", "평가기획안", "설계서", "요구사항", "명세서",
    "계약서", "회의록", "보고서", "결과보고서", "가이드", "정책", "규정",
    "proposal", "plan", "design", "requirement", "requirements", "spec",
    "contract", "minutes", "report", "guide", "policy",
)
_FINAL_KEYWORDS = ("최종", "확정", "final", "confirmed")
_NEGATIVE_KEYWORDS = (
    "temp", "backup", "copy", "복사본", "old", "test", "sample", "draft",
)
_DELIVERABLE_WEIGHTS = {
    ".docx": 30,
    ".xlsx": 25,
    ".pptx": 25,
    ".hwp": 25,
    ".hwpx": 25,
    ".pdf": 20,
    ".md": 15,
    ".txt": 5,
    ".csv": 5,
}


class RepresentativeDocumentSelector:
    """Select representative, supporting, and reference documents for one project."""

    def select_representative_documents(
        self,
        project_key: str,
        docs: list["DocumentSummary"],
    ) -> RepresentativeDocumentResult:
        families = DocumentFamilyEngine().group_document_families(docs)
        primary_docs = []
        previous_docs = []
        for family in families:
            primary_docs.append(family.latest_doc)
            promoted_previous = _promotable_previous_doc(family.previous_docs)
            if promoted_previous is not None:
                primary_docs.append(promoted_previous)
            for doc in family.previous_docs:
                if doc is not promoted_previous:
                    previous_docs.append(doc)

        scored = [
            self._score_document(project_key, doc)
            for doc in primary_docs
        ]
        previous_scored = [
            self._score_document(project_key, doc)
            for doc in previous_docs
        ]
        for doc in previous_scored:
            doc.reasons.append("previous document in same family")

        scored.sort(
            key=lambda d: (
                -d.dvs,
                -_date_ordinal(d.modified_dt),
                -d.deliverable_score,
                -d.filename_signal_score,
                d.file_name.lower(),
            )
        )
        representative_docs = [
            doc for doc in scored
            if doc.dvs >= 80 and "low-value filename signal" not in doc.reasons
        ][:3]
        representative_set = {doc.display_name for doc in representative_docs}
        supporting_docs = [
            doc for doc in scored
            if doc.display_name not in representative_set
            and doc.dvs >= 50
            and "low-value filename signal" not in doc.reasons
        ][:5]
        used = representative_set | {doc.display_name for doc in supporting_docs}
        reference_docs = [
            doc for doc in scored if doc.display_name not in used
        ] + previous_scored

        return RepresentativeDocumentResult(
            project_key=project_key,
            representative_docs=representative_docs,
            supporting_docs=supporting_docs,
            reference_docs=reference_docs,
        )

    def _score_document(
        self,
        project_key: str,
        doc: "DocumentSummary",
    ) -> RepresentativeDocument:
        display_name = doc.display_name
        file_name = Path(display_name.replace("\\", "/")).name
        deliverable_score = _deliverable_score(display_name)
        filename_signal_score, filename_reasons = _filename_signal_score(file_name)

        dvs_result = calculate_document_value_score(
            file_path=display_name,
            display_name=display_name,
            classifier_score=doc.score,
            deliverable_score=deliverable_score,
            modified_time=None,
            summary_text=doc.summary_text,
            metadata={
                "project_key": project_key,
                "modified_dt": doc.modified_dt,
            },
        )

        adjusted_dvs = _clamp(dvs_result.score + filename_signal_score, 0, 100)
        reasons = list(dvs_result.reasons)
        reasons.extend(filename_reasons)
        if adjusted_dvs >= 85:
            reasons.insert(0, "high document value")
        if deliverable_score >= 25:
            reasons.append("core project deliverable")

        return RepresentativeDocument(
            display_name=display_name,
            file_name=file_name,
            dvs=adjusted_dvs,
            modified_dt=doc.modified_dt,
            deliverable_score=deliverable_score,
            filename_signal_score=filename_signal_score,
            reasons=list(dict.fromkeys(reasons)),
        )


def write_representative_documents_report(
    results: dict[str, RepresentativeDocumentResult] | list[RepresentativeDocumentResult],
) -> str:
    """Write output/representative_documents.txt and return the path."""
    if isinstance(results, dict):
        result_list = list(results.values())
    else:
        result_list = list(results)

    output_dir = Path(__file__).resolve().parents[2] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "representative_documents.txt"

    lines = [
        "# Representative Documents",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for result in sorted(result_list, key=lambda r: r.project_key):
        lines.append(f"[{result.project_key}]")
        lines.append("")
        _append_group(lines, "대표문서", result.representative_docs)
        _append_group(lines, "보조문서", result.supporting_docs)
        _append_group(lines, "참고문서", result.reference_docs)
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return str(path)


def _append_group(
    lines: list[str],
    title: str,
    docs: list[RepresentativeDocument],
) -> None:
    lines.append(title)
    lines.append("")
    if not docs:
        lines.append("(없음)")
        lines.append("")
        return
    for doc in docs:
        lines.append(doc.file_name)
        lines.append(f"DVS {doc.dvs}")
        lines.append("선정사유:")
        for reason in doc.reasons[:6]:
            lines.append(f"- {reason}")
        lines.append("")


def _promotable_previous_doc(docs: list["DocumentSummary"]) -> "DocumentSummary | None":
    for doc in docs:
        stem = Path(doc.display_name.replace("\\", "/")).stem.lower()
        if any(keyword.lower() in stem for keyword in _NEGATIVE_KEYWORDS):
            continue
        if _version_family(doc.display_name):
            return doc
    return None


def _split_obsolete_versions(
    docs: list[RepresentativeDocument],
) -> tuple[list[RepresentativeDocument], list[RepresentativeDocument]]:
    family_counts: dict[str, int] = {}
    primary: list[RepresentativeDocument] = []
    obsolete: list[RepresentativeDocument] = []

    for doc in docs:
        family = _version_family(doc.file_name)
        if not family:
            primary.append(doc)
            continue
        family_counts[family] = family_counts.get(family, 0) + 1
        if family_counts[family] <= 2:
            primary.append(doc)
        else:
            doc.reasons.append("older version in same document family")
            obsolete.append(doc)

    return primary, obsolete


def _version_family(file_name: str) -> str:
    stem = Path(file_name).stem.lower()
    if not (
        re.search(r"(?:^|[^a-z0-9])v(?:er(?:sion)?)?\.?\s*\d+(?:\.\d+)*\b", stem)
        or any(keyword.lower() in stem for keyword in _FINAL_KEYWORDS)
    ):
        return ""
    family = re.sub(
        r"(?:^|[_\-\s])v(?:er(?:sion)?)?\.?\s*\d+(?:\.\d+)*\b",
        "",
        stem,
    )
    for keyword in _FINAL_KEYWORDS:
        family = family.replace(keyword.lower(), "")
    family = re.sub(r"[_\-\s]+", "", family)
    return family


def _deliverable_score(display_name: str) -> int:
    path = Path(display_name.replace("\\", "/"))
    stem = path.stem.lower()
    score = _DELIVERABLE_WEIGHTS.get(path.suffix.lower(), 0)
    if any(keyword.lower() in stem for keyword in _CORE_OUTPUT_KEYWORDS):
        score += 20
    if any(keyword.lower() in stem for keyword in _NEGATIVE_KEYWORDS):
        score -= 20
    return score


def _filename_signal_score(file_name: str) -> tuple[int, list[str]]:
    lower = Path(file_name).stem.lower()
    score = 0
    reasons: list[str] = []

    if any(keyword.lower() in lower for keyword in _CORE_OUTPUT_KEYWORDS):
        score += 8
        reasons.append("core filename signal")

    if any(keyword.lower() in lower for keyword in _FINAL_KEYWORDS):
        score += 12
        reasons.append("final document")

    versions = []
    for match in re.finditer(
        r"(?:^|[^a-z0-9])v(?:er(?:sion)?)?\.?\s*(\d+(?:\.\d+)*)\b",
        lower,
    ):
        parts = tuple(int(part) for part in match.group(1).split(".") if part.isdigit())
        if parts:
            versions.append(parts)
    if versions:
        version = max(versions)
        score += min(10, 4 + version[0] + (1 if len(version) > 1 else 0))
        reasons.append(f"version v{'.'.join(str(part) for part in version)}")

    if any(keyword.lower() in lower for keyword in _NEGATIVE_KEYWORDS):
        score -= 18
        reasons.append("low-value filename signal")

    return _clamp(score, -25, 25), reasons


def _date_ordinal(value: str) -> int:
    try:
        return _dt.date.fromisoformat(value).toordinal()
    except (TypeError, ValueError):
        return 0


def _clamp(value: int | float, low: int, high: int) -> int:
    return int(max(low, min(high, value)))
