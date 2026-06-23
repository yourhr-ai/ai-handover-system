from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ToolResult:
    tools: list[str]
    confidence: int
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ToolRule:
    tool: str
    extensions: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


_TOOL_RULES: tuple[_ToolRule, ...] = (
    _ToolRule("Excel", extensions=(".xlsx", ".xls", ".csv")),
    _ToolRule("Word", extensions=(".docx", ".doc")),
    _ToolRule("PowerPoint", extensions=(".pptx", ".ppt")),
    _ToolRule("Figma", keywords=("figma", "wireframe", "ui design", "uidesign")),
    _ToolRule("GitHub", keywords=("github", "pull request", "pullrequest", "repository")),
    _ToolRule("Jira", keywords=("jira", "sprint", "backlog")),
    _ToolRule("Notion", keywords=("notion",)),
)


class ToolGenerator:
    """Infer likely tools from document evidence only."""

    def generate(
        self,
        representative_documents=None,
        supporting_documents=None,
        document_families=None,
    ) -> ToolResult:
        weighted_docs = _collect_weighted_documents(
            representative_documents,
            supporting_documents,
            document_families,
        )
        if not weighted_docs:
            return ToolResult([], 25, [])

        scored = [_score_rule(rule, weighted_docs) for rule in _TOOL_RULES]
        scored = [score for score in scored if score[0] > 0]
        if not scored:
            return ToolResult([], 25, [])

        scored.sort(key=lambda item: (-item[0], item[1].tool))
        tools = [rule.tool for _, rule, _ in scored]
        matched = {item.lower() for _, _, items in scored for item in items}
        evidence = [name for name, _ in weighted_docs if name.lower() in matched]
        confidence = _clamp(max(score for score, _, _ in scored), 0, 100)
        return ToolResult(tools=tools, confidence=confidence, evidence=evidence[:12])


def generate_tools(
    representative_documents=None,
    supporting_documents=None,
    document_families=None,
) -> ToolResult:
    return ToolGenerator().generate(
        representative_documents=representative_documents,
        supporting_documents=supporting_documents,
        document_families=document_families,
    )


def _score_rule(
    rule: _ToolRule,
    weighted_docs: list[tuple[str, int]],
) -> tuple[int, _ToolRule, list[str]]:
    score = 0
    evidence: list[str] = []

    for name, weight in weighted_docs:
        extension_match = Path(name).suffix.lower() in rule.extensions
        normalized = _normalize(name)
        keyword_match = any(_normalize(keyword) in normalized for keyword in rule.keywords)

        if not extension_match and not keyword_match:
            continue

        evidence.append(name)
        score += 18 * weight
        if keyword_match:
            score += 8

    if not evidence:
        return 0, rule, []

    score += min(20, len(evidence) * 4)
    return _clamp(score, 0, 100), rule, _unique(evidence)


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
