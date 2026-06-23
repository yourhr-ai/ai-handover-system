from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PurposeCandidate:
    name: str
    confidence: int
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BusinessContextV3:
    purpose_candidates: list[str]
    confidence: int
    evidence: list[str]


@dataclass(frozen=True)
class _PurposeRule:
    name: str
    keywords: tuple[str, ...]


_PURPOSE_RULES: tuple[_PurposeRule, ...] = (
    _PurposeRule(
        name="교육 운영",
        keywords=("신입사원교육", "교육자료", "교육결과보고서", "교육교안", "만족도조사", "교육", "교안", "training", "education"),
    ),
    _PurposeRule(
        name="채용 운영",
        keywords=("채용공고", "채용", "면접평가", "면접", "입사서류", "조직진단", "인사자문", "인사 자문", "지원자", "recruit", "interview", "onboarding"),
    ),
    _PurposeRule(
        name="인력 확보",
        keywords=("지원자", "인력", "인재", "recruit", "hiring"),
    ),
    _PurposeRule(
        name="평가 및 보상 운영",
        keywords=("성과평가가이드", "성과평가", "평가가이드", "평가양식", "평가기획안", "평가제도", "평가안", "인사평가", "직원연봉리스트", "연봉리스트", "연봉인상안", "연봉인상", "연봉", "급여", "보상", "인상안", "performance", "evaluation", "review", "salary", "compensation", "raise"),
    ),
    _PurposeRule(
        name="신규 고객 확보",
        keywords=("고객미팅메모", "고객미팅", "제안서", "견적서", "제안발표자료", "고객", "수주", "sales", "proposal", "quotation", "quote"),
    ),
    _PurposeRule(
        name="제안 영업 수행",
        keywords=("제안발표자료", "제안서", "견적서", "제안", "견적", "고객미팅메모", "sales", "proposal", "quotation", "quote"),
    ),
    _PurposeRule(
        name="사내 자산 및 운영 지원 관리",
        keywords=("비품관리대장", "법인차량관리대장", "자산관리대장", "회의실예약", "비품", "법인차량", "차량관리", "자산관리", "회의실", "총무", "행정", "admin", "asset"),
    ),
    _PurposeRule(
        name="결산 관리",
        keywords=("결산보고", "결산", "재무제표", "재무", "회계", "closing", "financial statement"),
    ),
    _PurposeRule(
        name="세무 신고",
        keywords=("부가세신고", "부가세", "세금신고", "세무신고", "vat", "tax"),
    ),
)


class BusinessContextEngineV3:
    """Infer business purpose candidates from document names only."""

    def infer(self, document_names: list[str]) -> BusinessContextV3:
        docs = _clean_document_names(document_names)
        scored = _score_purpose_candidates(docs)

        if not scored:
            return BusinessContextV3(
                purpose_candidates=["Unknown"],
                confidence=25,
                evidence=[],
            )

        return BusinessContextV3(
            purpose_candidates=[candidate.name for candidate in scored],
            confidence=max(candidate.confidence for candidate in scored),
            evidence=_unique_limited(
                [evidence for candidate in scored for evidence in candidate.evidence],
                20,
            ),
        )

    def build_context(self, document_names: list[str]) -> BusinessContextV3:
        return self.infer(document_names)


def infer_business_context_v3(document_names: list[str]) -> BusinessContextV3:
    return BusinessContextEngineV3().infer(document_names)


def _score_purpose_candidates(document_names: list[str]) -> list[PurposeCandidate]:
    candidates: list[PurposeCandidate] = []

    for rule in _PURPOSE_RULES:
        evidence = _matching_documents(document_names, rule.keywords)
        if not evidence:
            continue

        keyword_hits = _keyword_hit_count(evidence, rule.keywords)
        confidence = _clamp(45 + len(evidence) * 12 + keyword_hits * 5, 0, 100)
        candidates.append(
            PurposeCandidate(
                name=rule.name,
                confidence=confidence,
                evidence=evidence,
            )
        )

    candidates.sort(key=lambda candidate: -candidate.confidence)
    return candidates


def _matching_documents(document_names: list[str], keywords: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    lower_keywords = tuple(keyword.lower() for keyword in keywords)

    for document_name in document_names:
        normalized = _normalize(document_name)
        if any(keyword in normalized for keyword in lower_keywords):
            matches.append(document_name)

    return _unique_limited(matches, 10)


def _keyword_hit_count(document_names: list[str], keywords: tuple[str, ...]) -> int:
    count = 0
    lower_keywords = tuple(keyword.lower() for keyword in keywords)
    for document_name in document_names:
        normalized = _normalize(document_name)
        count += sum(1 for keyword in lower_keywords if keyword in normalized)
    return count


def _clean_document_names(document_names: list[str]) -> list[str]:
    cleaned: list[str] = []
    for document_name in document_names or []:
        value = Path(str(document_name).replace("\\", "/")).name.strip()
        if value:
            cleaned.append(value)
    return _unique_limited(cleaned, 100)


def _normalize(value: str) -> str:
    return Path(str(value).replace("\\", "/")).stem.replace(" ", "").replace("_", "").replace("-", "").lower()


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


def _clamp(value: int | float, low: int, high: int) -> int:
    return int(max(low, min(high, value)))
