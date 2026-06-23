from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


UNKNOWN_DESCRIPTION = "업무 설명을 추론할 수 없음"


@dataclass(frozen=True)
class DescriptionResult:
    description: str
    confidence: int
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _DescriptionPattern:
    description: str
    keywords: tuple[str, ...]


_PATTERNS: tuple[_DescriptionPattern, ...] = (
    _DescriptionPattern(
        "필요 인력 채용을 위해 채용공고를 운영하고 면접 및 입사 절차를 관리한다.",
        ("채용공고", "채용", "면접평가", "면접", "입사서류", "입사", "지원자", "recruit", "hiring", "interview"),
    ),
    _DescriptionPattern(
        "직원 성과를 평가하고 보상 수준을 검토하기 위한 평가 및 보상 체계를 운영한다.",
        ("성과평가", "평가가이드", "평가양식", "평가", "연봉인상", "연봉", "보상", "급여", "salary", "compensation", "review"),
    ),
    _DescriptionPattern(
        "잠재 고객에게 서비스를 제안하고 계약 체결을 위한 영업 활동을 수행한다.",
        ("제안서", "견적서", "고객미팅", "고객", "계약", "수주", "proposal", "quotation", "quote", "sales"),
    ),
    _DescriptionPattern(
        "재무 현황을 정리하고 세무 신고를 수행하여 회사의 재무 상태를 관리한다.",
        ("결산보고서", "결산", "재무제표", "재무", "부가세신고", "부가세", "세무신고", "세금계산서", "vat", "tax", "financial"),
    ),
    _DescriptionPattern(
        "신규 입사자의 조직 적응과 업무 이해도 향상을 위한 교육을 운영한다.",
        ("교육결과보고서", "신입사원교육", "교육자료", "만족도조사", "교육", "training", "education", "survey"),
    ),
    _DescriptionPattern(
        "제품 생산과 품질 확보를 위해 생산 계획, 작업 지시, 검사 활동을 관리한다.",
        ("생산계획", "작업지시", "품질검사", "불량", "출하", "제조", "manufacturing", "production", "quality"),
    ),
    _DescriptionPattern(
        "공사 수행과 준공 관리를 위해 공정, 시공, 안전 점검 자료를 관리한다.",
        ("공정표", "시공계획", "안전점검", "준공", "검측", "현장", "construction", "inspection"),
    ),
    _DescriptionPattern(
        "환자 진료와 치료 품질 관리를 위해 진료, 검사, 처방 기록을 관리한다.",
        ("진료기록", "간호기록", "검사결과", "처방", "환자", "병동", "medical", "patient", "clinic"),
    ),
    _DescriptionPattern(
        "고객 주문 이행과 물류 효율화를 위해 배송, 배차, 입출고 현황을 관리한다.",
        ("배송", "배차", "운송", "입고", "출고", "재고", "물류", "logistics", "delivery", "shipment"),
    ),
    _DescriptionPattern(
        "요구사항을 구현하고 서비스 품질을 확보하기 위해 설계, 개발, 테스트 자료를 관리한다.",
        ("요구사항", "설계서", "개발계획", "테스트결과", "릴리즈", "api", "frontend", "software", "test"),
    ),
    _DescriptionPattern(
        "고객의 문제 진단과 개선안 도출을 위해 인터뷰, 분석 보고서, 실행 계획을 관리한다.",
        ("진단보고서", "개선안", "인터뷰", "워크숍", "컨설팅", "분석보고서", "consulting", "workshop"),
    ),
    _DescriptionPattern(
        "사내 운영 지원을 위해 자산, 비품, 차량, 회의실 사용 현황을 관리한다.",
        ("비품", "자산", "차량", "회의실", "구매요청", "총무", "asset", "admin", "facility"),
    ),
)


class DescriptionGenerator:
    """Generate a work description from document evidence only."""

    def generate(
        self,
        purpose_candidates=None,
        representative_documents=None,
        supporting_documents=None,
        document_families=None,
    ) -> DescriptionResult:
        del purpose_candidates  # Purpose labels are not primary evidence.

        weighted_docs = _collect_weighted_documents(
            representative_documents,
            supporting_documents,
            document_families,
        )
        if not weighted_docs:
            return DescriptionResult(UNKNOWN_DESCRIPTION, 25, [])

        scored = [
            _score_pattern(pattern, weighted_docs)
            for pattern in _PATTERNS
        ]
        scored = [score for score in scored if score[0] > 0]
        if not scored:
            return DescriptionResult(UNKNOWN_DESCRIPTION, 25, [])

        scored.sort(key=lambda item: (-item[0], -len(item[2]), item[1].description))
        score, pattern, evidence = scored[0]

        if score < 45 or len(evidence) < 2:
            return DescriptionResult(UNKNOWN_DESCRIPTION, min(30, score), evidence)

        return DescriptionResult(
            description=pattern.description,
            confidence=_clamp(score, 0, 100),
            evidence=evidence[:8],
        )


def generate_description(
    purpose_candidates=None,
    representative_documents=None,
    supporting_documents=None,
    document_families=None,
) -> DescriptionResult:
    return DescriptionGenerator().generate(
        purpose_candidates=purpose_candidates,
        representative_documents=representative_documents,
        supporting_documents=supporting_documents,
        document_families=document_families,
    )


def _score_pattern(
    pattern: _DescriptionPattern,
    weighted_docs: list[tuple[str, int]],
) -> tuple[int, _DescriptionPattern, list[str]]:
    evidence: list[str] = []
    keyword_hits = 0
    score = 0

    for doc_name, weight in weighted_docs:
        normalized = _normalize(doc_name)
        matches = [keyword for keyword in pattern.keywords if _normalize(keyword) in normalized]
        if not matches:
            continue
        evidence.append(doc_name)
        keyword_hits += len(matches)
        score += 12 * weight + min(12, len(matches) * 4)

    if not evidence:
        return 0, pattern, []

    score += min(18, len(evidence) * 5)
    score += min(15, keyword_hits * 3)
    return _clamp(score, 0, 100), pattern, _unique(evidence)


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
        for attr in ("latest_doc", "family_docs", "previous_docs"):
            value = getattr(family, attr, None)
            names.extend(_document_names(value))
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
