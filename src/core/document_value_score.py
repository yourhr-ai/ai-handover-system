from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DocumentValueScore:
    """Document-level value score used before representative document selection."""

    project_key: str
    file_name: str
    display_name: str
    score: int
    reasons: list[str] = field(default_factory=list)
    breakdown: dict[str, int] = field(default_factory=dict)


_FINAL_WORDS = (
    "final", "finalized", "confirmed", "release",
    "최종", "확정", "운영안",
)
_POSITIVE_FOLDERS = ("deliverables", "deliverable", "result", "results", "final")
_NEGATIVE_FOLDERS = ("temp", "tmp", "cache", "backup", "output", "outputs")
_NOISE_PATTERNS = (
    "test_report", "customer_count", "noise_filter", "analysis_summary",
    "category_score", "project_mapping", "project_priority", "project_importance",
    "debug_prompt", "document_summaries", "project_summaries", "customer_summaries",
    "extracted_files", "excluded_files", "extraction_errors", "final_report",
    "final_eval_input", "report_validation",
)
_NOISE_WORDS = ("report", "debug", "analysis")


def calculate_document_value_score(
    file_path: str,
    display_name: str | None = None,
    classifier_score: int = 0,
    deliverable_score: int | None = None,
    modified_time: float | None = None,
    summary_text: str = "",
    metadata: dict | None = None,
) -> DocumentValueScore:
    """Calculate DVS in the 0-100 range.

    Components:
      - document importance: 45 pts, based on FileClassifier.score()
      - deliverable value: 20 pts, based on the existing deliverable score
      - recency: 15 pts
      - final/version signal: 10 pts
      - location adjustment: -10 to +10 pts
      - noise penalty: up to -30 pts
    """
    metadata = metadata or {}
    display = display_name or str(file_path)
    path = Path(file_path)
    file_name = Path(display.replace("\\", "/")).name or path.name
    project_key = metadata.get("project_key") or _project_key(display)

    importance_points = round(_clamp(classifier_score, 0, 100) * 0.45)
    deliverable_points = round(_normalize_deliverable(deliverable_score) * 0.20)
    recency_points, recency_reason = _recency_points(modified_time, metadata)
    final_points, final_reason = _final_version_points(file_name, summary_text)
    location_points, location_reasons = _location_points(display)
    noise_penalty, noise_reasons = _noise_penalty(display, file_name)

    raw_score = (
        importance_points
        + deliverable_points
        + recency_points
        + final_points
        + location_points
        - noise_penalty
    )
    score = _clamp(round(raw_score), 0, 100)

    reasons: list[str] = []
    if importance_points >= 36:
        reasons.append("high document relevance")
    elif importance_points >= 25:
        reasons.append("medium document relevance")
    if deliverable_points >= 14:
        reasons.append("core deliverable")
    elif deliverable_points >= 8:
        reasons.append("deliverable candidate")
    if recency_reason:
        reasons.append(recency_reason)
    if final_reason:
        reasons.append(final_reason)
    reasons.extend(location_reasons)
    reasons.extend(noise_reasons)
    if not reasons:
        reasons.append("baseline document value")

    return DocumentValueScore(
        project_key=project_key,
        file_name=file_name,
        display_name=display,
        score=score,
        reasons=reasons,
        breakdown={
            "document_importance": importance_points,
            "deliverable_value": deliverable_points,
            "recency": recency_points,
            "final_version": final_points,
            "location": location_points,
            "noise_penalty": -noise_penalty,
        },
    )


def write_document_value_score_report(rows: list[DocumentValueScore]) -> str:
    """Write output/document_value_score.txt and return the path."""
    output_dir = Path(__file__).resolve().parents[2] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "document_value_score.txt"

    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Document Value Score",
        f"# Generated: {now}",
        "# Formula: importance(45) + deliverable(20) + recency(15) + final/version(10) + location(-10..10) - noise(0..30)",
        "",
    ]

    for row in sorted(rows, key=lambda r: (r.project_key, -r.score, r.file_name.lower())):
        lines.append(f"[{row.project_key}]")
        lines.append(f"{row.file_name}")
        lines.append(f"{row.score} points")
        bd = row.breakdown
        lines.append(
            "breakdown: "
            f"importance {bd.get('document_importance', 0)}, "
            f"deliverable {bd.get('deliverable_value', 0)}, "
            f"recency {bd.get('recency', 0)}, "
            f"final {bd.get('final_version', 0)}, "
            f"location {bd.get('location', 0)}, "
            f"noise {bd.get('noise_penalty', 0)}"
        )
        for reason in row.reasons:
            lines.append(f"- {reason}")
        lines.append(f"path: {row.display_name}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return str(path)


def _project_key(display_name: str) -> str:
    parts = display_name.replace("\\", "/").split("/")
    return parts[0] if len(parts) >= 2 else "Other"


def _normalize_deliverable(deliverable_score: int | None) -> int:
    if deliverable_score is None:
        return 0
    if deliverable_score <= -1000:
        return 0
    return _clamp(round((deliverable_score + 30) / 80 * 100), 0, 100)


def _recency_points(modified_time: float | None, metadata: dict) -> tuple[int, str]:
    days = None
    if modified_time is not None:
        days = (_dt.datetime.now().timestamp() - modified_time) / 86400
    else:
        modified_dt = metadata.get("modified_dt")
        if modified_dt:
            try:
                dt = _dt.date.fromisoformat(str(modified_dt))
                days = (_dt.date.today() - dt).days
            except ValueError:
                days = None

    if days is None:
        return 0, ""
    if days <= 7:
        return 15, "recently modified within 7 days"
    if days <= 30:
        return 10, "recently modified within 30 days"
    return 0, ""


def _final_version_points(file_name: str, summary_text: str) -> tuple[int, str]:
    haystack = f"{file_name} {summary_text[:300]}".lower()
    points = 0
    reasons: list[str] = []

    if any(word.lower() in haystack for word in _FINAL_WORDS):
        points = max(points, 8)
        reasons.append("final/confirmed document signal")

    versions = []
    for m in re.finditer(
        r"(?:^|[^a-z0-9])v(?:er(?:sion)?)?\.?\s*(\d+(?:\.\d+)*)\b",
        haystack,
    ):
        parts = tuple(int(p) for p in m.group(1).split(".") if p.isdigit())
        if parts:
            versions.append(parts)
    if versions:
        version = max(versions)
        major = version[0]
        version_points = min(10, 5 + major)
        if len(version) > 1:
            version_points = min(10, version_points + 1)
        points = max(points, version_points)
        reasons.append(f"version signal v{'.'.join(str(x) for x in version)}")

    return points, "; ".join(reasons)


def _location_points(display_name: str) -> tuple[int, list[str]]:
    parts = [p.lower() for p in display_name.replace("\\", "/").split("/")[:-1]]
    joined = "/".join(parts)
    points = 0
    reasons: list[str] = []

    for folder in _POSITIVE_FOLDERS:
        if folder in joined:
            points += 5
            reasons.append(f"positive folder: {folder}")
    for folder in _NEGATIVE_FOLDERS:
        if folder in joined:
            points -= 5
            reasons.append(f"low-value folder: {folder}")

    return _clamp(points, -10, 10), reasons[:4]


def _noise_penalty(display_name: str, file_name: str) -> tuple[int, list[str]]:
    lower_display = display_name.lower()
    stem = Path(file_name).stem.lower()
    penalty = 0
    reasons: list[str] = []

    for pattern in _NOISE_PATTERNS:
        if pattern in lower_display:
            penalty = max(penalty, 30)
            reasons.append(f"strong noise pattern: {pattern}")
            break

    for word in _NOISE_WORDS:
        if stem == word or stem.startswith(word + "_") or stem.endswith("_" + word):
            penalty = max(penalty, 15)
            reasons.append(f"weak noise word: {word}")

    return penalty, reasons


def _clamp(value: int | float, low: int, high: int) -> int:
    return int(max(low, min(high, value)))
