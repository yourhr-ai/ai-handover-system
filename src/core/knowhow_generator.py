from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class KnowhowResult:
    knowhow_items: list[str]
    confidence: int
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _KnowhowRule:
    item: str
    keywords: tuple[str, ...]
    min_evidence: int = 2
    version_signal: bool = False


_RULES: tuple[_KnowhowRule, ...] = (
    _KnowhowRule(
        "Version history should be managed.",
        ("version", "revision", "draft", "final", "updated"),
        min_evidence=2,
        version_signal=True,
    ),
    _KnowhowRule(
        "Review and reporting schedules should be tracked.",
        ("report", "reporting", "monthly", "weekly", "deadline", "closing", "statement"),
        min_evidence=1,
    ),
    _KnowhowRule(
        "Records should be maintained consistently.",
        ("checklist", "register", "ledger", "log", "record", "tracker", "inventory"),
        min_evidence=1,
    ),
    _KnowhowRule(
        "Change history should be documented.",
        ("proposal", "quotation", "quote", "estimate", "scope", "contract"),
        min_evidence=1,
    ),
    _KnowhowRule(
        "Training results should be reviewed periodically.",
        ("training", "education", "curriculum", "attendance", "survey", "lesson"),
        min_evidence=1,
    ),
    _KnowhowRule(
        "Criteria changes should be documented.",
        ("evaluation", "criteria", "scorecard", "assessment", "review guide", "performance"),
        min_evidence=1,
    ),
    _KnowhowRule(
        "Approval history should be preserved.",
        ("approval", "approved", "signoff", "sign-off", "review request"),
        min_evidence=1,
    ),
    _KnowhowRule(
        "Communication history should be retained.",
        ("customer", "client", "meeting", "minutes", "communication", "contact"),
        min_evidence=1,
    ),
)

_VERSION_RE = re.compile(r"(^|[_\-\s])v\d+(\.\d+)?($|[_\-\s])|20\d{6}")


class KnowhowGenerator:
    """Infer practical handover knowhow from document evidence only."""

    def generate(
        self,
        representative_documents=None,
        supporting_documents=None,
        document_families=None,
    ) -> KnowhowResult:
        weighted_docs = _collect_weighted_documents(
            representative_documents,
            supporting_documents,
            document_families,
        )
        if not weighted_docs:
            return KnowhowResult([], 25, [])

        scored = [_score_rule(rule, weighted_docs) for rule in _RULES]
        scored = [score for score in scored if score[0] > 0]
        if not scored:
            return KnowhowResult([], 25, [])

        scored.sort(key=lambda item: -item[0])
        items = [rule.item for _, rule, _ in scored]
        matched = {item.lower() for _, _, items in scored for item in items}
        evidence = [name for name, _ in weighted_docs if name.lower() in matched]
        confidence = _clamp(max(score for score, _, _ in scored), 0, 100)
        return KnowhowResult(items, confidence, evidence[:12])


def generate_knowhow(
    representative_documents=None,
    supporting_documents=None,
    document_families=None,
) -> KnowhowResult:
    return KnowhowGenerator().generate(
        representative_documents=representative_documents,
        supporting_documents=supporting_documents,
        document_families=document_families,
    )


def _score_rule(
    rule: _KnowhowRule,
    weighted_docs: list[tuple[str, int]],
) -> tuple[int, _KnowhowRule, list[str]]:
    evidence: list[str] = []
    score = 0

    for name, weight in weighted_docs:
        normalized = _normalize(name)
        keyword_match = any(_normalize(keyword) in normalized for keyword in rule.keywords)
        version_match = rule.version_signal and bool(_VERSION_RE.search(Path(name).stem.lower()))

        if not keyword_match and not version_match:
            continue

        evidence.append(name)
        score += 14 * weight
        if keyword_match and version_match:
            score += 6

    evidence = _unique(evidence)
    if len(evidence) < rule.min_evidence:
        return 0, rule, []

    score += min(24, len(evidence) * 8)
    return _clamp(score, 0, 100), rule, evidence


def _collect_weighted_documents(
    representative_documents,
    supporting_documents,
    document_families,
) -> list[tuple[str, int]]:
    weighted: list[tuple[str, int]] = []
    weighted.extend((name, 3) for name in _document_names(representative_documents))
    weighted.extend((name, 2) for name in _document_names(supporting_documents))
    weighted.extend((name, 1) for name in _family_document_names(document_families))

    unique: list[tuple[str, int]] = []
    seen: set[str] = set()
    for name, weight in weighted:
        clean = _clean_name(name)
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        unique.append((clean, weight))
    return unique


def _document_names(documents) -> list[str]:
    names: list[str] = []
    for item in _flatten(documents):
        name = _extract_name(item)
        if name:
            names.append(name)
    return names


def _family_document_names(document_families) -> list[str]:
    names: list[str] = []
    for family in _flatten(document_families):
        for attr in ("family_key", "name", "display_name", "file_name"):
            value = getattr(family, attr, None)
            if value:
                names.append(str(value))
        for attr in ("latest_doc", "family_docs", "previous_docs"):
            names.extend(_document_names(getattr(family, attr, None)))
        if not any(hasattr(family, attr) for attr in ("latest_doc", "family_docs", "previous_docs")):
            name = _extract_name(family)
            if name:
                names.append(name)
    return names


def _flatten(values) -> list:
    if values is None:
        return []
    if isinstance(values, dict):
        flattened: list = []
        for value in values.values():
            flattened.extend(_flatten(value))
        return flattened
    if isinstance(values, (list, tuple, set)):
        flattened = []
        for value in values:
            flattened.extend(_flatten(value))
        return flattened
    return [values]


def _extract_name(item) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    for attr in ("display_name", "file_name", "name", "family_key"):
        value = getattr(item, attr, None)
        if value:
            return str(value)
    return ""


def _clean_name(value: str) -> str:
    return Path(str(value).replace("\\", "/")).name.strip()


def _normalize(value: str) -> str:
    stem = Path(str(value).replace("\\", "/")).stem
    return "".join(ch for ch in stem.lower() if ch.isalnum())


def _unique(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _clamp(value: int | float, low: int, high: int) -> int:
    return int(max(low, min(high, value)))
