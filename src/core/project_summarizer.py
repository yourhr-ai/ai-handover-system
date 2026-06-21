from __future__ import annotations

"""
프로젝트 단위 요약 엔진 (고도화 버전).

처리 흐름:
  문서 요약 목록
  ↓ 프로젝트 그룹화 (상위 폴더 기준)
  ↓ 동일 프로젝트 내 유사 문서 제거 (Jaccard ≥ 80%)
  ↓ 프로젝트별 상한선 적용 (최대 30개)
  ↓ AI 프로젝트 요약 생성 (구조화 11개 필드 + 중요 정보 슬롯)
  ↓ 프로젝트 요약 목록

목표: 128개 문서 요약 → 12개 프로젝트 요약 → GPT
"""

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import openai

from src.config.settings import Settings
from src.core.representative_document_selector import (
    RepresentativeDocumentSelector,
)

if TYPE_CHECKING:
    from src.core.document_summarizer import DocumentSummary

# ── 상수 ──────────────────────────────────────────────────────────────
_SIMILARITY_THRESHOLD = 0.80   # 유사도 80% 이상 → 중복 제거
_MAX_DOCS_PER_PROJECT = 30     # 프로젝트당 최대 분석 문서 수
_PROJECT_SUMMARY_MAX = 2_500   # 프로젝트 요약 최대 글자 수 (확장)

# 라이트 모드 제한값
_LIGHT_MAX_DOCS_PER_PROJECT = 10  # 라이트 모드: 프로젝트당 최대 문서 수
_LIGHT_PROJECT_SUMMARY_MAX = 1_000  # 라이트 모드: 프로젝트 요약 최대 글자 수

# ── 구조화 필드 정의 ──────────────────────────────────────────────────
_STRUCT_FIELDS: list[tuple[str, str]] = [
    ("프로젝트명",           "project_name"),
    ("고객사명",             "client_name"),
    ("프로젝트 목적",        "project_purpose"),
    ("주요 이해관계자",      "stakeholders"),
    ("주요 문제",            "main_issues"),
    ("주요 의사결정",        "key_decisions"),
    ("주요 산출물",          "key_outputs"),
    ("현재 진행상태",        "current_status"),
    ("미완료 업무",          "incomplete_work"),
    ("예상 리스크",          "risks"),
    ("후임자 필수 확인사항", "successor_notes"),
]

_ALL_FIELD_LABELS = [f[0] for f in _STRUCT_FIELDS]

_SYSTEM_PROMPT = """\
당신은 업무복원 전문가입니다.
아래는 같은 프로젝트 또는 고객사에 관련된 문서들의 요약입니다.
이 정보를 종합하여 프로젝트 단위 업무복원 구조화 요약을 작성하십시오.

반드시 아래 형식으로, 항목 순서 그대로 작성하십시오.
정보가 없는 항목은 [정보 부족] 으로 표시하십시오.
불필요한 서론이나 설명 없이 바로 항목별로 작성하십시오.

프로젝트명: [최종 확인된 프로젝트명]
고객사명: [고객사명 또는 [정보 부족]]
프로젝트 목적: [2-3줄로 작성]
주요 이해관계자: [핵심 담당자/관계자 목록]
주요 문제: [미결 또는 진행 중인 이슈 목록]
주요 의사결정: [중요 결정 사항 목록]
주요 산출물: [결과물/파일 목록]
현재 진행상태: [현재 진행 중 / 최근 완료 / 과거 완료 중 하나]
미완료 업무: [완료되지 않은 업무 목록, 없으면 [정보 부족]]
예상 리스크: [잠재적 리스크 또는 [정보 부족]]
후임자 필수 확인사항: [후임자가 반드시 알아야 할 3-5가지 핵심 사항]

[중요 정보]
압축 과정에서 사라지면 안 되는 핵심 정보를 번호 목록으로 최대 10개 작성하십시오.
각 항목은 1-2줄로 간결하게 작성하십시오.
1.
2.
3.

전체 응답은 2,500자를 초과하지 마십시오.
"""


@dataclass
class ProjectGroup:
    """동일 프로젝트로 분류된 문서 그룹"""
    project_key: str                    # 그룹 키 (상위 폴더명)
    docs: list["DocumentSummary"]       # 소속 문서 요약 목록
    excluded_similar: list[str] = field(default_factory=list)   # 유사도 제거 파일명
    excluded_limit: list[str] = field(default_factory=list)     # 상한선 제거 파일명


@dataclass
class ProjectSummary:
    """프로젝트 단위 요약 결과 (구조화 필드 + 중요 정보 슬롯 포함)"""
    project_key: str
    doc_count: int                      # 포함된 문서 수
    excluded_similar_count: int         # 유사도로 제거된 수
    excluded_limit_count: int           # 상한선으로 제거된 수
    summary_text: str                   # AI 전체 출력 (원문 보존)
    related_files: list[str]            # 관련 주요 파일 목록
    summary_chars: int

    # ── 구조화 필드 (AI 출력 파싱) ──────────────────────────────────
    project_name: str = "[정보 부족]"
    client_name: str = "[정보 부족]"
    project_purpose: str = "[정보 부족]"
    stakeholders: str = "[정보 부족]"
    main_issues: str = "[정보 부족]"
    key_decisions: str = "[정보 부족]"
    key_outputs: str = "[정보 부족]"
    current_status: str = "[정보 부족]"
    incomplete_work: str = "[정보 부족]"
    risks: str = "[정보 부족]"
    successor_notes: str = "[정보 부족]"

    # ── 중요 정보 슬롯 (최대 10개) ───────────────────────────────────
    critical_info: list[str] = field(default_factory=list)
    representative_docs: list[str] = field(default_factory=list)
    supporting_docs: list[str] = field(default_factory=list)
    reference_docs: list[str] = field(default_factory=list)
    action_plan: object | None = None
    priority_tasks: list[str] = field(default_factory=list)
    action_plan_risks: list[str] = field(default_factory=list)


class ProjectSummarizer:
    """
    문서 요약 목록을 프로젝트 단위로 묶어 압축 요약한다.

    1. 상위 폴더 기준 그룹화
    2. 동일 그룹 내 유사 문서 제거 (Jaccard ≥ 0.80)
    3. 프로젝트별 최대 30개 상한선 (최신+고점수 우선)
    4. AI 프로젝트 요약 생성 (구조화 11개 필드 + 중요 정보 최대 10개)
    """

    def __init__(self, settings: Settings) -> None:
        self._client = openai.OpenAI(api_key=settings.api_key)
        self._model = settings.model

    # ── 그룹화 ────────────────────────────────────────────────────────
    def group_documents(
        self, summaries: list["DocumentSummary"]
    ) -> list[ProjectGroup]:
        """문서 요약 목록을 상위 폴더 기준으로 프로젝트 그룹으로 묶는다."""
        groups: dict[str, list["DocumentSummary"]] = {}
        for s in summaries:
            key = _extract_project_key(s.display_name)
            groups.setdefault(key, []).append(s)

        return [ProjectGroup(project_key=k, docs=v) for k, v in groups.items()]

    # ── 유사 문서 제거 ─────────────────────────────────────────────────
    def remove_similar(
        self, group: ProjectGroup, threshold: float = _SIMILARITY_THRESHOLD
    ) -> None:
        """
        그룹 내 유사도 80% 이상 문서를 제거한다.
        수정일이 최신인 문서를 우선 유지한다.
        """
        docs = sorted(group.docs, key=lambda d: d.modified_dt, reverse=True)
        kept: list["DocumentSummary"] = []
        removed: list[str] = []

        for doc in docs:
            is_duplicate = any(
                _jaccard(doc.summary_text, kept_doc.summary_text) >= threshold
                for kept_doc in kept
            )
            if is_duplicate:
                removed.append(doc.display_name)
                print(f"  [유사 문서] 제거: {doc.display_name}")
            else:
                kept.append(doc)

        group.docs = kept
        group.excluded_similar = removed

    # ── 상한선 적용 ───────────────────────────────────────────────────
    def apply_limit(
        self, group: ProjectGroup, max_docs: int = _MAX_DOCS_PER_PROJECT
    ) -> None:
        """
        그룹 내 문서를 최대 max_docs개로 제한한다.
        우선순위: 현재 진행업무 > 최신 수정 > 높은 점수
        """
        if len(group.docs) <= max_docs:
            return

        docs = sorted(
            group.docs,
            key=lambda d: (
                d.is_current_work,
                d.modified_dt,
                d.score,
            ),
            reverse=True,
        )
        group.excluded_limit = [d.display_name for d in docs[max_docs:]]
        group.docs = docs[:max_docs]
        print(
            f"  [상한선] {group.project_key}: "
            f"{len(group.docs) + len(group.excluded_limit)}개 → {len(group.docs)}개 유지"
        )

    # ── AI 프로젝트 요약 ──────────────────────────────────────────────
    def summarize_project(
        self, group: ProjectGroup, summary_max: int = _PROJECT_SUMMARY_MAX
    ) -> ProjectSummary:
        """그룹 내 문서 요약들을 AI로 통합하여 프로젝트 요약을 생성한다."""
        if not group.docs:
            return ProjectSummary(
                project_key=group.project_key,
                doc_count=0,
                excluded_similar_count=len(group.excluded_similar),
                excluded_limit_count=len(group.excluded_limit),
                summary_text="(분석 문서 없음)",
                related_files=[],
                summary_chars=10,
            )

        rep_result = RepresentativeDocumentSelector().select_representative_documents(
            group.project_key, group.docs
        )
        doc_by_name = {doc.display_name: doc for doc in group.docs}
        representative_docs = [
            doc_by_name[d.display_name]
            for d in rep_result.representative_docs
            if d.display_name in doc_by_name
        ]
        supporting_docs = [
            doc_by_name[d.display_name]
            for d in rep_result.supporting_docs
            if d.display_name in doc_by_name
        ]
        reference_docs = [
            doc_by_name[d.display_name]
            for d in rep_result.reference_docs
            if d.display_name in doc_by_name
        ]

        user_msg = _build_weighted_project_prompt(
            group.project_key,
            representative_docs,
            supporting_docs,
            reference_docs,
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
            summary_text = (response.choices[0].message.content or "").strip()
        except Exception as exc:
            summary_text = f"(AI 요약 실패: {exc})"

        # 길이 제한
        if len(summary_text) > summary_max:
            summary_text = summary_text[:summary_max] + "\n[...길이 초과로 잘림...]"

        # 구조화 파싱
        parsed = _parse_project_summary(summary_text)

        weighted_related = [
            d.display_name
            for d in representative_docs + supporting_docs + reference_docs
        ]
        related = list(dict.fromkeys(
            weighted_related + [d.display_name for d in group.docs]
        ))[:10]

        return ProjectSummary(
            project_key=group.project_key,
            doc_count=len(group.docs),
            excluded_similar_count=len(group.excluded_similar),
            excluded_limit_count=len(group.excluded_limit),
            summary_text=summary_text,
            related_files=related,
            summary_chars=len(summary_text),
            project_name=parsed.get("project_name", "[정보 부족]"),
            client_name=parsed.get("client_name", "[정보 부족]"),
            project_purpose=parsed.get("project_purpose", "[정보 부족]"),
            stakeholders=parsed.get("stakeholders", "[정보 부족]"),
            main_issues=parsed.get("main_issues", "[정보 부족]"),
            key_decisions=parsed.get("key_decisions", "[정보 부족]"),
            key_outputs=parsed.get("key_outputs", "[정보 부족]"),
            current_status=parsed.get("current_status", "[정보 부족]"),
            incomplete_work=parsed.get("incomplete_work", "[정보 부족]"),
            risks=parsed.get("risks", "[정보 부족]"),
            successor_notes=parsed.get("successor_notes", "[정보 부족]"),
            critical_info=parsed.get("critical_info", []),
            representative_docs=[d.display_name for d in representative_docs],
            supporting_docs=[d.display_name for d in supporting_docs],
            reference_docs=[d.display_name for d in reference_docs],
        )

    def summarize_all(
        self,
        summaries: list["DocumentSummary"],
        progress_cb=None,
        cancel_fn=None,
        light_mode: bool = False,
    ) -> tuple[list[ProjectSummary], list[str], list[str]]:
        """
        문서 요약 목록 전체를 처리하여 프로젝트 요약 목록을 반환한다.

        light_mode: True 시 프로젝트당 최대 10개 문서, 요약 1,000자 제한
        Returns:
            (project_summaries, similar_excluded, limit_excluded)
        """
        max_docs = _LIGHT_MAX_DOCS_PER_PROJECT if light_mode else _MAX_DOCS_PER_PROJECT
        summary_max = _LIGHT_PROJECT_SUMMARY_MAX if light_mode else _PROJECT_SUMMARY_MAX
        if light_mode:
            print(f"[ProjSummarizer][라이트] max_docs={max_docs} / summary_max={summary_max}자")

        groups = self.group_documents(summaries)
        print(f"\n[프로젝트 그룹화] {len(summaries)}개 문서 → {len(groups)}개 프로젝트\n")

        all_similar_excluded: list[str] = []
        all_limit_excluded: list[str] = []
        project_summaries: list[ProjectSummary] = []

        total = len(groups)
        for i, group in enumerate(groups, 1):
            if cancel_fn and cancel_fn():
                print("[프로젝트 요약] 취소 요청 감지")
                return project_summaries, all_similar_excluded, all_limit_excluded

            if progress_cb:
                progress_cb(
                    f"프로젝트 요약 중... ({i}/{total})  [{group.project_key}]"
                )

            self.remove_similar(group)
            self.apply_limit(group, max_docs=max_docs)

            all_similar_excluded.extend(group.excluded_similar)
            all_limit_excluded.extend(group.excluded_limit)

            ps = self.summarize_project(group, summary_max=summary_max)
            project_summaries.append(ps)

            print(
                f"  [{i}/{total}] {ps.project_key}  "
                f"문서 {ps.doc_count}개  "
                f"유사제거 {ps.excluded_similar_count}개  "
                f"상한제거 {ps.excluded_limit_count}개  "
                f"요약 {ps.summary_chars}자  "
                f"중요정보 {len(ps.critical_info)}개"
            )

        _save_project_summary_quality(project_summaries)
        return project_summaries, all_similar_excluded, all_limit_excluded


# ── eval_text 빌더 ─────────────────────────────────────────────────────
_SEP = "─" * 60


def build_eval_from_project_summaries(
    all_display_map: dict[str, str],
    project_summaries: list[ProjectSummary],
    doc_summaries: list["DocumentSummary"],
) -> str:
    """
    프로젝트 요약 기반으로 AI에 전달할 최종 평가 텍스트를 생성한다.

    변경된 GPT 전달 구조:
      프로젝트 요약 (구조화 11개 필드)
      + 중요 정보 보존 슬롯 (압축 금지)
      + 현재 진행상태
      + 미완료 업무
      → GPT
    """
    display_names = list(all_display_map.values())
    tree_text = _build_tree(display_names)

    current = [d for d in doc_summaries if d.is_current_work]
    current_lines = "\n".join(
        f"  • {d.display_name}  [{d.score}점]  수정:{d.modified_dt}"
        for d in sorted(current, key=lambda x: x.score, reverse=True)
    ) or "  (최근 30일 내 수정 파일 없음)"

    parts = [
        f"{_SEP}\n[전체 폴더 구조 — 총 {len(display_names)}개 파일]\n\n{tree_text}",
        f"{_SEP}\n[현재 진행 업무 후보 — 최근 30일 내 수정]\n\n{current_lines}",
        f"{_SEP}\n[프로젝트별 요약 — {len(project_summaries)}개 프로젝트]\n",
    ]

    for i, ps in enumerate(project_summaries, 1):
        block_lines = [
            f"\n===프로젝트 {i}: {ps.project_key}===  (문서 {ps.doc_count}개)",
            "",
            f"프로젝트명: {ps.project_name}",
            f"고객사명: {ps.client_name}",
            f"프로젝트 목적: {ps.project_purpose}",
            f"주요 이해관계자: {ps.stakeholders}",
            f"주요 문제: {ps.main_issues}",
            f"주요 의사결정: {ps.key_decisions}",
            f"주요 산출물: {ps.key_outputs}",
            f"현재 진행상태: {ps.current_status}",
            f"미완료 업무: {ps.incomplete_work}",
            f"예상 리스크: {ps.risks}",
            f"후임자 필수 확인사항: {ps.successor_notes}",
        ]

        if ps.critical_info:
            block_lines += [
                "",
                "[중요 정보 — 반드시 보고서에 그대로 반영할 것. 추가 압축 금지]",
            ]
            for j, info in enumerate(ps.critical_info, 1):
                block_lines.append(f"{j}. {info}")

        block_lines += ["", f"관련 파일: {', '.join(ps.related_files[:5])}", "===끝===", _SEP]
        parts.append("\n".join(block_lines))

    footer = (
        f"\n{_SEP}\n"
        "[분석 요청]\n"
        "위 프로젝트 요약을 기반으로 퇴사자의 업무를 복원하십시오.\n"
        "각 프로젝트의 현재 진행상태와 미완료 업무를 반드시 포함하십시오.\n"
        "[중요 정보] 항목은 압축 없이 보고서에 그대로 반영하십시오.\n"
        "후임자가 즉시 업무를 이어받을 수 있도록 구체적으로 작성하십시오."
    )
    parts.append(footer)

    return "\n".join(parts)


def _build_weighted_project_prompt(
    project_key: str,
    representative_docs: list["DocumentSummary"],
    supporting_docs: list["DocumentSummary"],
    reference_docs: list["DocumentSummary"],
) -> str:
    """Build a project summary prompt weighted around representative documents."""
    ratio = _basis_ratio(
        bool(representative_docs),
        bool(supporting_docs),
        bool(reference_docs),
    )
    sections = [
        f"프로젝트/폴더: {project_key}",
        (
            "요약 근거 비율: "
            f"대표문서 {ratio[0]}% / 보조문서 {ratio[1]}% / 참고문서 {ratio[2]}%"
        ),
        "",
        "[작성 지침]",
        "- 프로젝트 목적, 주요 산출물, 진행상태, 의사결정 흔적, 인수인계 포인트는 대표문서를 최우선 근거로 작성하십시오.",
        "- 보조문서는 회의록, 인터뷰, 결과보고서, 설문결과 등 보완 정보로만 사용하십시오.",
        "- 참고문서는 old/draft/sample/backup 등 과거 자료일 수 있으므로 필요 시 사실 확인용으로만 사용하십시오.",
        "",
        _render_doc_section("[대표문서 - 70% 근거]", representative_docs, 900),
        _render_doc_section("[보조문서 - 20% 보완]", supporting_docs, 450),
        _render_doc_section("[참고문서 - 10% 참고]", reference_docs, 120),
    ]
    return "\n".join(sections)


def _render_doc_section(
    title: str,
    docs: list["DocumentSummary"],
    max_chars: int,
) -> str:
    lines = [title]
    if not docs:
        lines.append("(없음)")
        return "\n".join(lines)

    for i, doc in enumerate(docs, 1):
        lines.append(
            f"[문서 {i}] {doc.display_name}"
            f"  (수정:{doc.modified_dt}  점수:{doc.score}점)"
        )
        lines.append(doc.summary_text[:max_chars])
        lines.append("---")
    return "\n".join(lines)


def _basis_ratio(
    has_representative: bool,
    has_supporting: bool,
    has_reference: bool,
) -> tuple[int, int, int]:
    weights = [
        70 if has_representative else 0,
        20 if has_supporting else 0,
        10 if has_reference else 0,
    ]
    total = sum(weights)
    if total <= 0:
        return 0, 0, 0
    normalized = [round(w / total * 100) for w in weights]
    diff = 100 - sum(normalized)
    normalized[0] += diff
    return normalized[0], normalized[1], normalized[2]


def _save_project_summary_quality(project_summaries: list[ProjectSummary]) -> None:
    """Write output/project_summary_quality.txt with weighted summary basis."""
    from pathlib import Path
    import datetime

    output_dir = Path(__file__).resolve().parents[2] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "project_summary_quality.txt"

    lines = [
        "# Project Summary Quality",
        f"# Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for ps in project_summaries:
        ratio = _basis_ratio(
            bool(ps.representative_docs),
            bool(ps.supporting_docs),
            bool(ps.reference_docs),
        )
        lines.append(f"[{ps.project_key}]")
        lines.append("")
        lines.append("대표문서")
        _append_doc_names(lines, ps.representative_docs)
        lines.append("")
        lines.append("보조문서")
        _append_doc_names(lines, ps.supporting_docs)
        lines.append("")
        lines.append("참고문서")
        _append_doc_names(lines, ps.reference_docs)
        lines.append("")
        lines.append("요약 근거 비율")
        lines.append(f"대표문서 {ratio[0]}%")
        lines.append(f"보조문서 {ratio[1]}%")
        lines.append(f"참고문서 {ratio[2]}%")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[프로젝트요약] output/project_summary_quality.txt 저장 완료 ({len(project_summaries)}개 프로젝트)")


def _append_doc_names(lines: list[str], docs: list[str]) -> None:
    if not docs:
        lines.append("* (없음)")
        return
    for doc in docs:
        lines.append(f"* {doc}")


# ── 파싱 유틸리티 ─────────────────────────────────────────────────────
def _parse_project_summary(raw: str) -> dict[str, object]:
    """AI 출력에서 구조화된 필드와 중요 정보를 추출한다."""
    result: dict[str, object] = {}

    for i, (label, key) in enumerate(_STRUCT_FIELDS):
        # 다음 필드 레이블 또는 [중요 정보] 섹션까지 내용 추출
        next_labels = _ALL_FIELD_LABELS[i + 1:]
        lookahead_parts = [re.escape(lbl) + r"\s*:" for lbl in next_labels]
        lookahead_parts.append(r"\[중요 정보\]")
        lookahead = "(?:" + "|".join(lookahead_parts) + ")"

        pattern = rf"^{re.escape(label)}\s*:\s*(.*?)(?=\n{lookahead}|\Z)"
        m = re.search(pattern, raw, re.DOTALL | re.MULTILINE)
        if m:
            value = m.group(1).strip()
            result[key] = value if value and value != "[정보 부족]" else "[정보 부족]"
        else:
            result[key] = "[정보 부족]"

    # 중요 정보 목록 추출
    cm = re.search(r"\[중요 정보\](.*?)$", raw, re.DOTALL)
    if cm:
        items = re.findall(r"^\d+\.\s*(.+)", cm.group(1), re.MULTILINE)
        result["critical_info"] = [item.strip() for item in items if item.strip()][:10]
    else:
        result["critical_info"] = []

    return result


# ── 기타 유틸리티 ─────────────────────────────────────────────────────
def _extract_project_key(display_name: str) -> str:
    """파일의 상위 폴더명을 프로젝트 키로 추출한다."""
    parts = display_name.replace("\\", "/").split("/")
    if len(parts) >= 2:
        return parts[0]
    return "기타 (최상위 파일)"


def _jaccard(text1: str, text2: str, min_token_len: int = 2) -> float:
    """
    두 텍스트의 단어 집합 기반 Jaccard 유사도를 계산한다.
    한국어/영어 혼용 텍스트에 적합한 단순 구현.
    """
    def _tokens(t: str) -> set[str]:
        return {w for w in re.findall(r"\w+", t) if len(w) >= min_token_len}

    t1 = _tokens(text1)
    t2 = _tokens(text2)
    if not t1 or not t2:
        return 0.0
    intersection = t1 & t2
    union = t1 | t2
    return len(intersection) / len(union)


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
