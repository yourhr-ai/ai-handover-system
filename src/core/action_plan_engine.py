from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re


@dataclass
class ActionPlan:
    """후임자가 바로 실행할 수 있는 규칙 기반 행동계획."""

    priority_tasks: list[str] = field(default_factory=list)
    stakeholders: list[str] = field(default_factory=list)
    required_documents: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    first_week_actions: list[str] = field(default_factory=list)


class ActionPlanEngine:
    """대표문서 중심으로 후임자 행동계획을 생성한다."""

    def build_action_plan(
        self,
        project_summary,
        representative_docs,
        supporting_docs,
    ) -> ActionPlan:
        rep_names = _normalize_doc_names(representative_docs)
        support_names = _normalize_doc_names(supporting_docs)
        reference_names = _normalize_doc_names(getattr(project_summary, "reference_docs", []))
        all_names = rep_names + support_names + reference_names

        priority_tasks = _unique_limited(
            _tasks_from_docs(rep_names)
            + _tasks_from_docs(support_names[:2], supporting=True)
            + _tasks_from_project_summary(project_summary),
            5,
        )
        required_documents = _unique_limited(
            [_display_stem(name) for name in rep_names + support_names],
            5,
        )
        stakeholders = _infer_stakeholders(project_summary, all_names)
        risks = _unique_limited(_infer_risks(project_summary, all_names), 5)
        first_week_actions = _build_first_week_actions(priority_tasks, required_documents)

        return ActionPlan(
            priority_tasks=priority_tasks,
            stakeholders=stakeholders,
            required_documents=required_documents,
            risks=risks,
            first_week_actions=first_week_actions,
        )


def write_action_plan_report(project_plans: dict[str, ActionPlan], output_dir: str = "output") -> str:
    """프로젝트별 행동계획 로그를 저장한다."""

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "action_plan.txt"

    lines: list[str] = []
    for project_key, plan in project_plans.items():
        lines.append(f"[{project_key}]")
        lines.append("")
        lines.append("우선 업무")
        _append_bullets(lines, plan.priority_tasks)
        lines.append("")
        lines.append("필수 문서")
        _append_bullets(lines, plan.required_documents)
        lines.append("")
        lines.append("관계자")
        _append_bullets(lines, plan.stakeholders)
        lines.append("")
        lines.append("리스크")
        _append_bullets(lines, plan.risks)
        lines.append("")
        lines.append("첫 주 행동계획")
        _append_bullets(lines, plan.first_week_actions)
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    return str(path)


def _normalize_doc_names(docs) -> list[str]:
    names: list[str] = []
    for doc in docs or []:
        if isinstance(doc, str):
            name = doc
        else:
            name = getattr(doc, "display_name", "") or getattr(doc, "file_path", "")
        name = str(name).strip()
        if name:
            names.append(name)
    return names


def _tasks_from_docs(doc_names: list[str], supporting: bool = False) -> list[str]:
    tasks: list[str] = []
    for name in doc_names:
        stem = _display_stem(name)
        lower = stem.lower()
        if "평가기획안" in stem:
            tasks.append("평가기획안 검토")
        elif "기획안" in stem:
            tasks.append("기획안 검토")
        elif "가이드" in stem:
            tasks.append("가이드 검토")
        elif "운영안" in stem:
            tasks.append("운영안 검토")
        elif "설계서" in stem:
            tasks.append("설계서 검토")
        elif "요구사항" in stem or "명세서" in stem:
            tasks.append("요구사항 및 명세 확인")
        elif "계약서" in stem:
            tasks.append("계약 조건 확인")
        elif "회의록" in stem:
            tasks.append("회의록 의사결정 사항 확인")
        elif "결과보고서" in stem or "보고서" in stem:
            tasks.append("보고서 주요 결과 확인")
        elif "설문" in stem or "인터뷰" in stem or "피드백" in stem:
            tasks.append("고객 피드백 반영 여부 확인")
        elif "결과" in stem:
            tasks.append("결과물 전달 여부 확인")
        elif lower:
            tasks.append(f"{_clean_version_tokens(stem)} 검토")

    if supporting and tasks:
        return tasks[:2]
    return tasks


def _tasks_from_project_summary(project_summary) -> list[str]:
    tasks: list[str] = []
    if _has_info(getattr(project_summary, "successor_notes", "")):
        tasks.append("후임자 확인사항 검토")
    if _has_info(getattr(project_summary, "incomplete_work", "")):
        tasks.append("미완료 항목 정리")
    if _has_info(getattr(project_summary, "key_outputs", "")):
        tasks.append("결과물 전달 여부 확인")
    return tasks


def _infer_stakeholders(project_summary, doc_names: list[str]) -> list[str]:
    text = " ".join(
        [
            getattr(project_summary, "project_key", ""),
            getattr(project_summary, "project_name", ""),
            getattr(project_summary, "client_name", ""),
            getattr(project_summary, "stakeholders", ""),
            *doc_names,
        ]
    )

    stakeholders: list[str] = []
    if "대표" in text:
        stakeholders.append("대표")
    if "팀장" in text:
        stakeholders.append("팀장")
    if _has_info(getattr(project_summary, "client_name", "")) or "고객" in text or "고객사" in text:
        stakeholders.append("고객사 담당자")
    if "개발" in text:
        stakeholders.append("개발 담당자")
    return _unique_limited(stakeholders, 5)


def _infer_risks(project_summary, doc_names: list[str]) -> list[str]:
    text = " ".join(doc_names)
    lower = text.lower()
    risks: list[str] = []

    if re.search(r"(^|[/\\_\-\s])old([_/\\\-\s.]|$)", lower) or "구버전" in text:
        risks.append("old 문서 존재 - 버전 혼선 위험")
    if "copy" in lower or "복사본" in text:
        risks.append("복사본 문서 존재 - 최신본 확인 필요")
    if "회의록" not in text:
        risks.append("회의록 없음 - 의사결정 이력 부족")
    if not re.search(r"(final|최종|확정)", lower):
        risks.append("최종본 없음 - 진행 중 가능성")

    summary_risks = getattr(project_summary, "risks", "")
    if _has_info(summary_risks):
        risks.append(str(summary_risks).strip())
    return risks


def _build_first_week_actions(priority_tasks: list[str], required_documents: list[str]) -> list[str]:
    day1 = "Day1 대표문서 검토"
    if required_documents:
        day1 = f"Day1 {required_documents[0]} 검토"

    day2 = "Day2 최근 수정 문서 확인"
    day3 = "Day3 고객사 진행상황 확인"
    day4 = "Day4 미완료 항목 정리"
    day5 = "Day5 다음 액션 계획 수립"

    if priority_tasks:
        day4 = f"Day4 {priority_tasks[0]} 후속 조치 정리"

    return [day1, day2, day3, day4, day5]


def _display_stem(name: str) -> str:
    return Path(name.replace("\\", "/")).stem.strip()


def _clean_version_tokens(stem: str) -> str:
    cleaned = re.sub(r"(?i)([_\-\s]?v\d+(?:\.\d+)?)", "", stem)
    cleaned = re.sub(r"(?i)([_\-\s]?(final|최종|확정))", "", cleaned)
    return cleaned.strip(" _-") or stem


def _has_info(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text and text != "[정보 부족]" and text != "(데이터 없음)")


def _unique_limited(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _append_bullets(lines: list[str], items: list[str]) -> None:
    if not items:
        lines.append("* (없음)")
        return
    for item in items:
        lines.append(f"* {item}")
