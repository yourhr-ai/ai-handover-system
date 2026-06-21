from __future__ import annotations

"""
문서 단위 사전요약 엔진

처리 흐름:
  텍스트 추출 완료 후 → AI 전달 전
  - 높은 점수 문서(>=80): AI 배치 요약 (3개씩 묶어 1 API call)
  - 중간 점수 문서(50-79): 룰 기반 요약 (API 호출 없음)
  - 95점 이상: 요약 + 원문 일부(3,000자) 함께 전달
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

import openai

from src.config.settings import Settings
from src.core.file_classifier import THRESHOLD_MUST

_EXCERPT_CHARS_HIGH = 3_000   # 95점 이상 원문 포함 글자 수
_SUMMARY_MAX_CHARS = 1_000    # AI 요약 최대 목표 글자 수
_MEDIUM_EXCERPT_CHARS = 800   # 중간 점수 룰기반 발췌 글자 수
_BATCH_SIZE = 3               # 한 번의 AI 호출에 묶을 문서 수
_SCORE_FULL_TEXT = 95         # 원문 첨부 기준 점수

# 라이트 모드 제한값
_LIGHT_EXCERPT_CHARS = 2_000  # 라이트 모드: 원문 샘플 글자 수
_LIGHT_SUMMARY_MAX_CHARS = 300  # 라이트 모드: 요약 최대 글자 수
_LIGHT_MEDIUM_EXCERPT_CHARS = 500  # 라이트 모드: 중간 점수 발췌 글자 수


_SYSTEM_PROMPT = """\
당신은 업무복원 전문가입니다.
아래 문서들을 읽고 후임자를 위한 핵심 업무 정보를 추출하십시오.

각 문서에 대해 반드시 아래 형식을 정확히 지켜 응답하십시오.
태그를 바꾸거나 생략하지 마십시오.

===시작: {파일명}===
문서종류: [보고서/회의록/제안서/계획서/계약서/교육자료/안내문/기타]
작성시기: [YYYY-MM 또는 YYYY년 N분기, 추정]
프로젝트명: [없음 또는 추정 프로젝트명]
고객사명: [없음 또는 추정 고객사명]
주요주제: [2-3줄 이내 핵심 주제]
주요의사결정: [결정 사항. 없으면 '없음']
주요산출물: [산출물 목록. 없으면 '없음']
주요이슈: [미결/진행 중인 이슈. 없으면 '없음']
인수인계: [후임자가 반드시 알아야 할 사항 1-3가지]
진행상태: [현재 진행 중 / 최근 완료 / 과거 완료] — [판단 근거 1줄]
===끝===

각 항목은 2-3줄 이내로 간결하게 작성하십시오.
불필요한 서론 없이 바로 형식에 맞게 작성하십시오.
"""


@dataclass
class DocumentInfo:
    """요약 엔진에 전달되는 문서 정보"""
    abs_path: str
    display_name: str
    score: int             # 최종 가중치 점수
    relevance: int         # 업무 관련성 점수 (날짜 제외)
    modified_dt: str       # 포매팅된 수정일 (YYYY-MM-DD)
    created_dt: str        # 포매팅된 생성일 (YYYY-MM-DD)
    work_status: str       # "현재 진행 업무" | "최근 완료 업무" | ...
    is_current_work: bool
    text: str              # 추출된 원문 텍스트
    extra: dict = field(default_factory=dict)


@dataclass
class DocumentSummary:
    """요약 결과"""
    display_name: str
    score: int
    work_status: str
    is_current_work: bool
    modified_dt: str
    created_dt: str
    ai_summarized: bool    # True = AI 요약, False = 룰 기반
    summary_text: str      # 구조화된 요약 텍스트
    excerpt: str           # 원문 발췌 (95점 이상만, 나머지 "")
    original_chars: int
    summary_chars: int


class DocumentSummarizer:
    """
    문서별 사전요약 엔진.

    - 80점 이상: AI 배치 요약 (3개씩 묶어 1회 API 호출)
    - 50-79점: 룰 기반 발췌 (API 호출 없음)
    - 95점 이상: 요약 + 원문 3,000자 첨부
    """

    def __init__(self, settings: Settings) -> None:
        self._client = openai.OpenAI(api_key=settings.api_key)
        self._model = settings.model

    # ── 공개 메서드 ────────────────────────────────────────────────────
    def summarize_all(
        self,
        docs: list[DocumentInfo],
        progress_cb=None,
        cancel_fn=None,
        light_mode: bool = False,
    ) -> list[DocumentSummary]:
        """
        모든 분석 대상 문서를 요약한다.

        light_mode: True 시 샘플링 2,000자 / 요약 300자 제한 적용
        cancel_fn: 취소 여부를 반환하는 callable (lambda: bool)
        """
        excerpt_chars  = _LIGHT_EXCERPT_CHARS       if light_mode else _EXCERPT_CHARS_HIGH
        summary_max    = _LIGHT_SUMMARY_MAX_CHARS    if light_mode else _SUMMARY_MAX_CHARS
        medium_excerpt = _LIGHT_MEDIUM_EXCERPT_CHARS if light_mode else _MEDIUM_EXCERPT_CHARS

        if light_mode:
            print(f"[DocSummarizer][라이트] excerpt={excerpt_chars}자 / summary_max={summary_max}자")

        high = [d for d in docs if d.score >= THRESHOLD_MUST]
        medium = [d for d in docs if d.score < THRESHOLD_MUST]

        results: list[DocumentSummary] = []

        # ── AI 요약: 높은 점수 문서 ──────────────────────────────────
        total_high = len(high)
        for i in range(0, total_high, _BATCH_SIZE):
            if cancel_fn and cancel_fn():
                print(f"[요약 중단] AI 요약 중 취소 요청 (완료 {len(results)}/{total_high}개)")
                return results

            batch = high[i : i + _BATCH_SIZE]
            end_idx = min(i + _BATCH_SIZE, total_high)
            if progress_cb:
                progress_cb(f"AI 문서 요약 중... ({end_idx}/{total_high}개)")
            try:
                batch_results = self._summarize_batch(
                    batch, excerpt_chars=excerpt_chars, summary_max=summary_max
                )
                results.extend(batch_results)
            except Exception as exc:
                print(f"[요약 경고] AI 요약 실패 ({exc}), 룰 기반으로 전환")
                for doc in batch:
                    results.append(self._rule_based(doc, excerpt_chars=medium_excerpt))

        # ── 룰 기반: 중간 점수 문서 ──────────────────────────────────
        for doc in medium:
            if cancel_fn and cancel_fn():
                print(f"[요약 중단] 룰 기반 발췌 중 취소 요청")
                return results
            results.append(self._rule_based(doc, excerpt_chars=medium_excerpt))

        return results

    # ── 내부 메서드 ────────────────────────────────────────────────────
    def _summarize_batch(
        self,
        docs: list[DocumentInfo],
        excerpt_chars: int = _EXCERPT_CHARS_HIGH,
        summary_max: int = _SUMMARY_MAX_CHARS,
    ) -> list[DocumentSummary]:
        """배치 내 문서들을 AI로 요약한다."""
        user_msg_parts: list[str] = []
        for doc in docs:
            text_input = doc.text[:excerpt_chars] if doc.text else "(텍스트 없음)"
            user_msg_parts.append(
                f"===시작: {doc.display_name}===\n"
                f"[생성일: {doc.created_dt}  수정일: {doc.modified_dt}  "
                f"업무상태: {doc.work_status}  점수: {doc.score}점]\n\n"
                f"{text_input}\n"
                f"===끝==="
            )

        user_msg = "\n\n".join(user_msg_parts)

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        raw = response.choices[0].message.content or ""

        summaries: list[DocumentSummary] = []
        for doc in docs:
            parsed = _parse_summary(raw, doc.display_name)
            # summary_max 적용
            if len(parsed) > summary_max:
                parsed = parsed[:summary_max]
            excerpt = ""
            if doc.score >= _SCORE_FULL_TEXT and doc.text:
                excerpt = doc.text[:excerpt_chars]
            summaries.append(
                DocumentSummary(
                    display_name=doc.display_name,
                    score=doc.score,
                    work_status=doc.work_status,
                    is_current_work=doc.is_current_work,
                    modified_dt=doc.modified_dt,
                    created_dt=doc.created_dt,
                    ai_summarized=True,
                    summary_text=parsed,
                    excerpt=excerpt,
                    original_chars=len(doc.text),
                    summary_chars=len(parsed),
                )
            )
        return summaries

    def _rule_based(
        self, doc: DocumentInfo, excerpt_chars: int = _MEDIUM_EXCERPT_CHARS
    ) -> DocumentSummary:
        """룰 기반 발췌 요약 (API 호출 없음)"""
        excerpt_text = doc.text[:excerpt_chars] if doc.text else "(텍스트 없음)"
        if doc.text and len(doc.text) > excerpt_chars:
            excerpt_text += "..."

        summary = (
            f"문서종류: (자동 분류 미수행)\n"
            f"작성시기: (추정 불가)\n"
            f"프로젝트명: (미확인)\n"
            f"고객사명: (미확인)\n"
            f"주요주제: {excerpt_text}\n"
            f"주요의사결정: 없음\n"
            f"주요산출물: 없음\n"
            f"주요이슈: 없음\n"
            f"인수인계: (중간 우선순위 문서 — 직접 확인 권장)\n"
            f"진행상태: 불명 — 내용 발췌만 제공"
        )
        return DocumentSummary(
            display_name=doc.display_name,
            score=doc.score,
            work_status=doc.work_status,
            is_current_work=doc.is_current_work,
            modified_dt=doc.modified_dt,
            created_dt=doc.created_dt,
            ai_summarized=False,
            summary_text=summary,
            excerpt="",
            original_chars=len(doc.text),
            summary_chars=len(summary),
        )


# ── 파싱 헬퍼 ──────────────────────────────────────────────────────────
def _parse_summary(raw: str, display_name: str) -> str:
    """
    AI 응답에서 해당 파일명의 요약 블록을 추출한다.
    찾지 못하면 전체 응답을 반환한다.
    """
    # ===시작: {name}=== 이후 ===끝=== 이전 블록 추출
    safe = re.escape(display_name)
    pattern = rf"===시작:\s*{safe}\s*===(.*?)===끝==="
    m = re.search(pattern, raw, re.DOTALL)
    if m:
        return m.group(1).strip()

    # 여러 문서가 있는 배치에서 순서 기반 폴백:
    # '===끝===' 기준으로 나눠서 순서대로 매칭
    blocks = re.split(r"===끝===", raw)
    for block in blocks:
        if display_name in block or display_name.split("/")[-1] in block:
            # '===시작:' 헤더 제거
            cleaned = re.sub(r"===시작:.*?===", "", block).strip()
            if cleaned:
                return cleaned

    # 최후 폴백: 전체 응답의 앞 _SUMMARY_MAX_CHARS
    return raw[:_SUMMARY_MAX_CHARS].strip()


# ── 평가 텍스트 빌더 ───────────────────────────────────────────────────
_SEP = "─" * 60


def build_eval_from_summaries(
    all_display_map: dict[str, str],
    summaries: list[DocumentSummary],
) -> str:
    """
    요약 목록으로부터 AI에 전달할 최종 평가 텍스트를 생성한다.
    원문 대신 구조화된 요약 + 95점 이상 원문 발췌가 포함된다.
    """
    display_names = list(all_display_map.values())

    # ── 폴더 구조 트리 ──────────────────────────────────────────────
    tree = _build_tree(display_names)

    # ── 현재 진행 업무 후보 목록 ─────────────────────────────────────
    current = [s for s in summaries if s.is_current_work]
    current_lines = "\n".join(
        f"  • {s.display_name}  [{s.score}점]  수정일: {s.modified_dt}"
        for s in sorted(current, key=lambda x: x.score, reverse=True)
    )
    if not current_lines:
        current_lines = "  (최근 30일 내 수정 파일 없음)"

    parts = [
        f"{_SEP}\n[전체 폴더 구조 — 총 {len(display_names)}개 파일]\n\n{tree}",
        f"{_SEP}\n[현재 진행 업무 후보 — 최근 30일 내 수정]\n\n{current_lines}",
        f"{_SEP}\n[문서별 요약 — AI 전달 대상 {len(summaries)}개]\n",
    ]

    # ── 문서 요약 블록 ────────────────────────────────────────────
    for i, s in enumerate(summaries, 1):
        status_tag = "★현재 진행★" if s.is_current_work else ""
        ai_tag = "AI 요약" if s.ai_summarized else "발췌"
        block_lines = [
            f"\n[문서 {i}]  {s.display_name}  "
            f"[{s.score}점]  [{s.work_status}]  {status_tag}",
            f"생성일: {s.created_dt}  |  수정일: {s.modified_dt}  |  ({ai_tag})",
            "─" * 40,
            s.summary_text,
        ]
        if s.excerpt:
            block_lines += [
                "",
                f"[원문 발췌 — 상위 점수 문서 {_EXCERPT_CHARS_HIGH:,}자]",
                s.excerpt,
            ]
        block_lines.append(_SEP)
        parts.append("\n".join(block_lines))

    return "\n".join(parts)


def _build_tree(display_names: list[str]) -> str:
    root: dict = {}
    for name in display_names:
        parts = name.replace("\\", "/").split("/")
        node = root
        for part in parts:
            node = node.setdefault(part, {})
    lines: list[str] = []

    def _render(node: dict, prefix: str = "") -> None:
        items = list(node.items())
        for i, (name, children) in enumerate(items):
            is_last = i == len(items) - 1
            lines.append(f"{prefix}{'└─ ' if is_last else '├─ '}{name}")
            if children:
                _render(children, prefix + ("   " if is_last else "│  "))

    _render(root)
    return "\n".join(lines)
