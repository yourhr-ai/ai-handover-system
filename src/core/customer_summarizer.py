from __future__ import annotations

"""
고객사 단위 요약 엔진.

처리 흐름:
  프로젝트 요약 목록 (선택된 것만)
  ↓ 고객사명 추출 + 그룹화
  ↓ 동일 고객사 프로젝트 통합
  ↓ AI 고객사 요약 생성 (고객사당 1회 호출)
  ↓ 고객사 요약 목록

목표: 12개 프로젝트 요약 → 8개 고객사 요약 → GPT
"""

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import openai

from src.config.settings import Settings

if TYPE_CHECKING:
    from src.core.document_summarizer import DocumentSummary
    from src.core.project_summarizer import ProjectSummary

_CUSTOMER_SUMMARY_MAX = 3_000       # 고객사 요약 최대 글자 수
_LIGHT_CUSTOMER_SUMMARY_MAX = 1_200  # 라이트 모드: 고객사 요약 최대 글자 수

# 고객사 분류에 쓰이는 접미어 패턴 (회사 유형 키워드)
_COMPANY_SUFFIXES = (
    "법인", "협회", "조합", "재단", "학회", "센터", "학원",
    "그룹", "기업", "기관", "공단", "공사", "주식회사",
    "주식", "유한", "대학교", "대학", "병원",
)

_SYSTEM_PROMPT = """\
당신은 HR 컨설팅 업무복원 전문가입니다.
아래는 동일 고객사와 관련된 프로젝트 요약들입니다.
이를 통합하여 고객사 단위 업무 요약을 작성하십시오.

반드시 아래 형식 그대로 항목 순서를 지켜 작성하십시오.
정보가 없는 항목은 [정보 부족] 으로 표시하십시오.
동일 고객사의 여러 프로젝트를 통합하여 서술하고, 공통 내용은 반복하지 마십시오.

고객사명: [고객사명]
현재 상태: [현재 진행 중 / 최근 완료 / 과거 완료]
진행 중 프로젝트: [현재 진행 중인 프로젝트 목록, 없으면 없음]
완료 프로젝트: [완료된 프로젝트 목록, 없으면 없음]
주요 산출물: [생성된 주요 문서·결과물 목록]
미완료 업무: [완료되지 않은 업무 목록, 없으면 [정보 부족]]
향후 액션: [후임자가 수행해야 할 다음 단계]
주의사항: [이 고객사 업무에서 반드시 주의할 사항]
후임자 인수 포인트: [가장 중요한 인수 포인트 3-5가지]
중요 의사결정 이력: [주요 결정 사항과 배경]

전체 응답은 3,000자를 초과하지 마십시오.
불필요한 서론 없이 바로 항목부터 시작하십시오.
"""

# 구조화 필드 정의
_CUSTOMER_FIELDS: list[tuple[str, str]] = [
    ("고객사명",          "customer_name_parsed"),
    ("현재 상태",         "current_status"),
    ("진행 중 프로젝트",  "current_projects_text"),
    ("완료 프로젝트",     "completed_projects_text"),
    ("주요 산출물",       "key_outputs"),
    ("미완료 업무",       "incomplete_work"),
    ("향후 액션",         "next_actions"),
    ("주의사항",          "cautions"),
    ("후임자 인수 포인트","handover_points"),
    ("중요 의사결정 이력","decision_history"),
]


@dataclass
class CustomerGroup:
    """동일 고객사로 분류된 프로젝트 그룹"""
    customer_name: str
    projects: list["ProjectSummary"]


@dataclass
class CustomerSummary:
    """고객사 단위 요약 결과"""
    customer_name: str
    project_count: int
    summary_text: str           # AI 전체 출력 (원문 보존)
    summary_chars: int

    # 구조화 필드
    current_status: str = "[정보 부족]"
    current_projects_text: str = "[정보 부족]"
    completed_projects_text: str = "[정보 부족]"
    key_outputs: str = "[정보 부족]"
    incomplete_work: str = "[정보 부족]"
    next_actions: str = "[정보 부족]"
    cautions: str = "[정보 부족]"
    handover_points: str = "[정보 부족]"
    decision_history: str = "[정보 부족]"

    # 수집된 프로젝트 중요 정보
    critical_info: list[str] = field(default_factory=list)
    project_keys: list[str] = field(default_factory=list)


class CustomerSummarizer:
    """
    프로젝트 요약 목록을 고객사 단위로 묶어 통합 요약한다.

    1. 고객사명 추출 + 그룹화
    2. 고객사당 1회 AI 요약 생성
    3. 고객사 요약 목록 반환
    """

    def __init__(self, settings: Settings) -> None:
        self._client = openai.OpenAI(api_key=settings.api_key)
        self._model = settings.model

    def group_by_customer(
        self, project_summaries: list["ProjectSummary"]
    ) -> list[CustomerGroup]:
        """프로젝트 요약 목록을 고객사 기준으로 그룹화한다."""
        groups: dict[str, list["ProjectSummary"]] = {}
        for ps in project_summaries:
            name = _extract_customer_name(ps)
            groups.setdefault(name, []).append(ps)

        result = [
            CustomerGroup(customer_name=k, projects=v)
            for k, v in groups.items()
        ]
        print(
            f"[고객사 그룹화] {len(project_summaries)}개 프로젝트 "
            f"→ {len(result)}개 고객사: "
            + ", ".join(f"{g.customer_name}({len(g.projects)})" for g in result)
        )
        return result

    def summarize_customer(
        self, group: CustomerGroup, summary_max: int = _CUSTOMER_SUMMARY_MAX
    ) -> CustomerSummary:
        """고객사 그룹을 AI로 통합 요약한다."""
        if not group.projects:
            return CustomerSummary(
                customer_name=group.customer_name,
                project_count=0,
                summary_text="(분석 프로젝트 없음)",
                summary_chars=10,
            )

        # 프로젝트 요약 합산
        proj_parts: list[str] = []
        all_critical: list[str] = []
        for i, ps in enumerate(group.projects, 1):
            proj_parts.append(
                f"[프로젝트 {i}] {ps.project_key}\n"
                f"진행상태: {ps.current_status}\n"
                f"목적: {ps.project_purpose}\n"
                f"미완료: {ps.incomplete_work}\n"
                f"리스크: {ps.risks}\n"
                f"주요산출물: {ps.key_outputs}\n"
                f"후임자확인: {ps.successor_notes}"
            )
            all_critical.extend(ps.critical_info)

        user_msg = (
            f"고객사: {group.customer_name}\n"
            f"프로젝트 수: {len(group.projects)}개\n\n"
            + "\n\n---\n\n".join(proj_parts)
        )

        if all_critical:
            user_msg += (
                "\n\n[수집된 중요 정보 — 반드시 요약에 반영]\n"
                + "\n".join(f"• {c}" for c in all_critical)
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

        if len(summary_text) > summary_max:
            summary_text = summary_text[:summary_max] + "\n[...길이 초과로 잘림...]"

        parsed = _parse_customer_summary(summary_text)

        return CustomerSummary(
            customer_name=group.customer_name,
            project_count=len(group.projects),
            summary_text=summary_text,
            summary_chars=len(summary_text),
            current_status=parsed.get("current_status", "[정보 부족]"),
            current_projects_text=parsed.get("current_projects_text", "[정보 부족]"),
            completed_projects_text=parsed.get("completed_projects_text", "[정보 부족]"),
            key_outputs=parsed.get("key_outputs", "[정보 부족]"),
            incomplete_work=parsed.get("incomplete_work", "[정보 부족]"),
            next_actions=parsed.get("next_actions", "[정보 부족]"),
            cautions=parsed.get("cautions", "[정보 부족]"),
            handover_points=parsed.get("handover_points", "[정보 부족]"),
            decision_history=parsed.get("decision_history", "[정보 부족]"),
            critical_info=list(dict.fromkeys(all_critical)),   # 중복 제거
            project_keys=[ps.project_key for ps in group.projects],
        )

    def summarize_all(
        self,
        project_summaries: list["ProjectSummary"],
        progress_cb=None,
        cancel_fn=None,
        light_mode: bool = False,
    ) -> list[CustomerSummary]:
        """전체 프로젝트 요약을 고객사 단위로 처리하여 고객사 요약 목록을 반환한다.

        light_mode: True 시 고객사 요약 1,200자 제한 적용
        """
        summary_max = _LIGHT_CUSTOMER_SUMMARY_MAX if light_mode else _CUSTOMER_SUMMARY_MAX
        if light_mode:
            print(f"[CustSummarizer][라이트] summary_max={summary_max}자")

        groups = self.group_by_customer(project_summaries)
        results: list[CustomerSummary] = []

        total = len(groups)
        for i, group in enumerate(groups, 1):
            if cancel_fn and cancel_fn():
                print("[고객사 요약] 취소 요청 감지")
                return results

            if progress_cb:
                progress_cb(f"고객사 요약 중... ({i}/{total})  [{group.customer_name}]")

            cs = self.summarize_customer(group, summary_max=summary_max)
            results.append(cs)
            print(
                f"  [{i}/{total}] {cs.customer_name}  "
                f"프로젝트 {cs.project_count}개  "
                f"요약 {cs.summary_chars}자  "
                f"중요정보 {len(cs.critical_info)}개"
            )

        return results


# ── eval_text 빌더 ─────────────────────────────────────────────────────
_SEP = "─" * 60
_OUTPUT_DIR_CS = None   # 지연 초기화 (순환 임포트 방지)


def _get_output_dir() -> "Path":
    from pathlib import Path
    return Path(__file__).resolve().parents[2] / "output"


def build_eval_from_customer_summaries(
    all_display_map: dict[str, str],
    customer_summaries: list[CustomerSummary],
    doc_summaries: list["DocumentSummary"],
) -> str:
    """
    고객사 요약만으로 최종 GPT에 전달할 평가 텍스트를 생성한다.

    ▸ 폴더 트리 전달 금지 — GPT가 트리에서 고객사를 추론하지 못하게 한다.
    ▸ 고객사 요약(customer_summaries)에 포함된 데이터만 전달한다.
    ▸ 프로젝트 요약 원문, 전체 파일 목록, 30일 이전 데이터는 전달하지 않는다.
    """
    # ── [GPT INPUT SOURCES] 콘솔 출력 ─────────────────────────
    cust_names = [cs.customer_name for cs in customer_summaries]
    total_chars = sum(cs.summary_chars for cs in customer_summaries)
    total_critical = sum(len(cs.critical_info) for cs in customer_summaries)

    print("\n" + "=" * 60)
    print("[GPT INPUT SOURCES]")
    print(f"  customer_summaries : {len(customer_summaries)}개")
    print(f"  고객사 목록        : {', '.join(cust_names)}")
    print(f"  총 중요 정보       : {total_critical}개")
    print(f"  총 글자 수 (입력)  : {total_chars:,}자")
    print(f"  project_summaries  : 전달 안 함 (고객사요약으로 대체)")
    print(f"  folder_tree        : 전달 안 함 (고객사 추론 방지)")
    print(f"  all_files          : 전달 안 함 (30일 필터 이전 데이터 차단)")
    print(f"  all_display_map    : {len(all_display_map)}개 (로깅용만, GPT 비전달)")
    print(f"  doc_summaries      : {len(doc_summaries)}개 (로깅용만, GPT 비전달)")
    print("=" * 60 + "\n")

    # ── eval_text 구성 (고객사 요약만) ────────────────────────
    customer_list_str = "\n".join(
        f"  {i}. {cs.customer_name}  (프로젝트 {cs.project_count}개, 상태: {cs.current_status})"
        for i, cs in enumerate(customer_summaries, 1)
    )

    parts = [
        "=" * 60,
        f"[분석 대상 고객사 목록 — 총 {len(customer_summaries)}개]",
        "아래 목록에 있는 고객사만 보고서에 포함하십시오.",
        "이 목록에 없는 고객사는 절대 보고서에 추가하지 마십시오.",
        "",
        customer_list_str,
        "=" * 60,
        f"\n[고객사별 상세 요약 — {len(customer_summaries)}개]\n",
    ]

    for i, cs in enumerate(customer_summaries, 1):
        block_lines = [
            f"\n{'='*60}",
            f"[고객사 {i}/{len(customer_summaries)}] {cs.customer_name}",
            f"{'='*60}",
            f"현재 상태: {cs.current_status}",
            f"진행 중 프로젝트: {cs.current_projects_text}",
            f"완료 프로젝트: {cs.completed_projects_text}",
            f"주요 산출물: {cs.key_outputs}",
            f"미완료 업무: {cs.incomplete_work}",
            f"향후 액션: {cs.next_actions}",
            f"주의사항: {cs.cautions}",
            f"후임자 인수 포인트: {cs.handover_points}",
            f"중요 의사결정 이력: {cs.decision_history}",
        ]

        if cs.critical_info:
            block_lines += [
                "",
                "▶ 중요 정보 [압축 금지 — 보고서에 그대로 반영]",
            ]
            for j, info in enumerate(cs.critical_info, 1):
                block_lines.append(f"  {j}. {info}")

        block_lines.append("")
        parts.append("\n".join(block_lines))

    footer = (
        "=" * 60 + "\n"
        "[분석 요청]\n"
        f"위 {len(customer_summaries)}개 고객사 요약을 기반으로 담당자의 업무를 복원하십시오.\n"
        f"분석 대상 고객사는 반드시 위 목록({', '.join(cust_names)})에 한정하십시오.\n"
        "목록에 없는 고객사는 추가하지 마십시오.\n"
        "[중요 정보] 항목은 압축 없이 보고서에 그대로 반영하십시오.\n"
        "후임자가 첫날부터 업무를 이어받을 수 있도록 구체적으로 작성하십시오."
    )
    parts.append(footer)

    eval_text = "\n".join(parts)

    # ── final_eval_input.txt 저장 ─────────────────────────────
    _save_final_eval_input(eval_text, customer_summaries)

    return eval_text


def _save_final_eval_input(
    eval_text: str,
    customer_summaries: list[CustomerSummary],
) -> None:
    """최종 GPT 입력 전문을 output/final_eval_input.txt 에 저장한다."""
    from pathlib import Path
    import datetime

    output_dir = Path(__file__).resolve().parents[2] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "final_eval_input.txt"

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_chars = sum(cs.summary_chars for cs in customer_summaries)
    est_tokens = total_chars // 2

    header_lines = [
        f"[최종 GPT 입력 기록]  {ts}",
        "=" * 60,
        f"customer_summaries 개수 : {len(customer_summaries)}개",
        f"고객사 목록             : {', '.join(cs.customer_name for cs in customer_summaries)}",
        f"총 글자 수              : {total_chars:,}자",
        f"총 토큰 추정            : {est_tokens:,} tokens",
        "=" * 60,
        "",
        "[실제 GPT에 전달된 텍스트 전문]",
        "",
        eval_text,
    ]

    path.write_text("\n".join(header_lines), encoding="utf-8", newline="\n")
    print(f"[로그] output/final_eval_input.txt 저장 완료  ({total_chars:,}자)")


# ── 유틸리티 ──────────────────────────────────────────────────────────
def extract_customer_name_from_key(project_key: str) -> str:
    """project_key 문자열에서 고객사명을 직접 파싱·반환한다 (공개 함수).

    우선순위:
    1. "_" 구분자 앞 토큰 ("채움세무법인_생활가이드" → "채움세무법인")
    2. 회사 접미어(_COMPANY_SUFFIXES) 포함 단어
    3. 공백 기준 첫 토큰 (최소 2자)
    4. project_key 전체
    """
    key = (project_key or "").strip()
    if "_" in key:
        candidate = key.split("_")[0].strip()
        if len(candidate) >= 2:
            return candidate
    words = key.split()
    for word in words:
        if any(suf in word for suf in _COMPANY_SUFFIXES):
            return word
    if len(words) >= 2 and len(words[0]) >= 2:
        return words[0]
    return key


def _extract_customer_name(ps: "ProjectSummary") -> str:
    """프로젝트 요약에서 고객사명을 추출한다.

    우선순위:
    1. AI 추출 client_name (유효한 경우)
    2. project_key에서 패턴 추출 (공개 함수 위임)
    """
    # 1. AI 추출 고객사명
    cn = (ps.client_name or "").strip()
    if cn and cn not in ("[정보 부족]", "없음", "미확인", ""):
        return cn

    # 2. project_key 파싱
    return extract_customer_name_from_key(ps.project_key)


def _parse_customer_summary(raw: str) -> dict[str, str]:
    """AI 출력에서 구조화된 고객사 요약 필드를 추출한다."""
    all_labels = [f[0] for f in _CUSTOMER_FIELDS]
    result: dict[str, str] = {}

    for i, (label, key) in enumerate(_CUSTOMER_FIELDS):
        next_labels = all_labels[i + 1:]
        lookahead_parts = [re.escape(lbl) + r"\s*:" for lbl in next_labels]
        lookahead = "(?:" + "|".join(lookahead_parts) + ")" if lookahead_parts else r"\Z"

        pattern = rf"^{re.escape(label)}\s*:\s*(.*?)(?=\n{lookahead}|\Z)"
        m = re.search(pattern, raw, re.DOTALL | re.MULTILINE)
        if m:
            value = m.group(1).strip()
            result[key] = value if value and value != "[정보 부족]" else "[정보 부족]"
        else:
            result[key] = "[정보 부족]"

    return result
