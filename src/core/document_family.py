from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.document_value_score import calculate_document_value_score

if TYPE_CHECKING:
    from src.core.document_summarizer import DocumentSummary


@dataclass
class DocumentFamily:
    family_key: str
    latest_doc: "DocumentSummary"
    previous_docs: list["DocumentSummary"] = field(default_factory=list)
    family_docs: list["DocumentSummary"] = field(default_factory=list)


_FINAL_TOKENS = ("final", "최종", "확정")
_DERIVATIVE_TOKENS = ("copy", "복사본", "old", "backup", "draft", "sample")
_SERIES_SUFFIXES = (
    "팀원", "팀장", "부서장", "담당자", "관리자", "실무자", "임원", "리더",
    "member", "leader", "manager", "admin",
)


class DocumentFamilyEngine:
    """Group versions and derivative documents into document families."""

    def group_document_families(
        self,
        docs: list["DocumentSummary"],
    ) -> list[DocumentFamily]:
        grouped: dict[str, list["DocumentSummary"]] = {}
        for doc in docs:
            grouped.setdefault(_family_key(doc.display_name), []).append(doc)

        families: list[DocumentFamily] = []
        for family_key, family_docs in grouped.items():
            ordered = sorted(
                family_docs,
                key=lambda d: _latest_sort_key(d, family_key),
                reverse=True,
            )
            latest = ordered[0]
            families.append(
                DocumentFamily(
                    family_key=family_key,
                    latest_doc=latest,
                    previous_docs=ordered[1:],
                    family_docs=ordered,
                )
            )

        families.sort(key=lambda f: f.family_key)
        return families


def write_document_families_report(
    results: dict[str, list[DocumentFamily]] | list[DocumentFamily],
) -> str:
    """Write output/document_families.txt and return the path."""
    if isinstance(results, dict):
        families = [
            family
            for project_families in results.values()
            for family in project_families
        ]
    else:
        families = list(results)

    output_dir = Path(__file__).resolve().parents[2] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "document_families.txt"

    lines = [
        "# Document Families",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for family in sorted(families, key=lambda f: f.family_key):
        lines.append(f"[{family.family_key}]")
        lines.append("")
        lines.append("latest:")
        lines.append(_file_name(family.latest_doc.display_name))
        lines.append("")
        lines.append("family:")
        for doc in family.family_docs:
            lines.append(_file_name(doc.display_name))
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _family_key(display_name: str) -> str:
    stem = Path(display_name.replace("\\", "/")).stem
    key = stem.strip()

    # Dates: 20260601, 2026-06-01, 2026_06_01, 2026.06.01
    key = re.sub(r"(?<!\d)(?:19|20)\d{2}[-_.]?\d{2}[-_.]?\d{2}(?!\d)", "", key)

    # Versions: v1, v1.0, v2, v2.1, version3
    key = re.sub(
        r"(?:^|[_\-\s])v(?:er(?:sion)?)?\.?\s*\d+(?:\.\d+)*\b",
        " ",
        key,
        flags=re.IGNORECASE,
    )

    # Final states and derivative markers.
    for token in _FINAL_TOKENS + _DERIVATIVE_TOKENS:
        key = re.sub(
            rf"(^|[_\-\s]){re.escape(token)}([_\-\s]|$)",
            " ",
            key,
            flags=re.IGNORECASE,
        )

    # Prefix markers such as old_평가기획안.
    key = re.sub(
        rf"^({'|'.join(re.escape(t) for t in _DERIVATIVE_TOKENS)})[_\-\s]+",
        "",
        key,
        flags=re.IGNORECASE,
    )

    # Series suffixes: 성과평가 가이드_팀장 -> 성과평가 가이드.
    suffix_pattern = "|".join(re.escape(s) for s in _SERIES_SUFFIXES)
    key = re.sub(rf"[_\-\s]+(?:{suffix_pattern})$", "", key, flags=re.IGNORECASE)

    key = re.sub(r"[_\-]+", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key or stem.strip()


def _latest_sort_key(doc: "DocumentSummary", family_key: str) -> tuple:
    stem = Path(doc.display_name.replace("\\", "/")).stem
    lower = stem.lower()
    final_rank = 1 if any(t.lower() in lower for t in _FINAL_TOKENS) else 0
    version = _version_tuple(lower)
    modified = _date_ordinal(getattr(doc, "modified_dt", ""))
    dvs = calculate_document_value_score(
        file_path=doc.display_name,
        display_name=doc.display_name,
        classifier_score=getattr(doc, "score", 0),
        modified_time=None,
        summary_text=getattr(doc, "summary_text", ""),
        metadata={"modified_dt": getattr(doc, "modified_dt", "")},
    ).score
    base_rank = 1 if _normalize_for_compare(stem) == _normalize_for_compare(family_key) else 0
    derivative_penalty = 1 if any(t.lower() in lower for t in _DERIVATIVE_TOKENS) else 0
    return (
        final_rank,
        version,
        modified,
        dvs,
        base_rank,
        -derivative_penalty,
    )


def _version_tuple(text: str) -> tuple[int, ...]:
    versions: list[tuple[int, ...]] = []
    for match in re.finditer(
        r"(?:^|[^a-z0-9])v(?:er(?:sion)?)?\.?\s*(\d+(?:\.\d+)*)\b",
        text,
        flags=re.IGNORECASE,
    ):
        parts = tuple(int(p) for p in match.group(1).split(".") if p.isdigit())
        if parts:
            versions.append(parts)
    return max(versions) if versions else tuple()


def _date_ordinal(value: str) -> int:
    try:
        return _dt.date.fromisoformat(value).toordinal()
    except (TypeError, ValueError):
        return 0


def _normalize_for_compare(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value).lower()


def _file_name(display_name: str) -> str:
    return Path(display_name.replace("\\", "/")).name
