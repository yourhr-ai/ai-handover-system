from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.core.work_status_engine import (
    COMPLETED,
    IN_PROGRESS,
    MAINTENANCE,
    WAITING_APPROVAL,
    WAITING_REVIEW,
)


@dataclass
class BusinessContext:
    objective: str
    current_stage: str
    completed_items: list[str] = field(default_factory=list)
    pending_items: list[str] = field(default_factory=list)
    stakeholders: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    confidence: int = 0


_STAGES = {
    "planning": "Planning",
    "design": "Design",
    "review": "Review",
    "approval": "Approval",
    "implementation": "Implementation",
    "operation": "Operation",
    "completed": "Completed",
}
_DESIGN_SIGNALS = ("설계", "design", "요구사항", "명세")
_PLANNING_SIGNALS = ("기획", "계획", "제안", "proposal", "planning")
_IMPLEMENTATION_SIGNALS = ("구축", "개발", "구현", "implementation", "build", "development")
_OPERATION_SIGNALS = ("운영", "유지보수", "maintenance", "가이드")
_FINAL_SIGNALS = ("final", "최종", "확정")
_RISK_SIGNALS = ("old", "backup", "copy", "복사본", "draft", "초안")
_ROLE_KEYWORDS = ("대표", "팀장", "담당자", "고객사 담당자", "개발 담당자", "승인권자")
_ORG_PATTERN = re.compile(
    r"([가-힣A-Za-z0-9]+(?:국제|전자|산업|테크|기술|시스템|솔루션|은행|보험|증권|병원|학교|대학교|그룹|세무법인|법인|회사))",
    re.IGNORECASE,
)


class BusinessContextEngine:
    """Infer business context from already-generated analysis outputs."""

    def build_context(
        self,
        project_summary,
        representative_documents=None,
        action_plan=None,
        work_status=None,
    ) -> BusinessContext:
        rep_names = _doc_names(representative_documents or [])
        action_text = _action_text(action_plan)
        status_text = _status_text(work_status)
        project_text = _project_text(project_summary)
        all_text = " ".join([project_text, " ".join(rep_names), " ".join(action_text), status_text])

        objective = _infer_objective(project_summary, rep_names, action_plan)
        current_stage = _infer_stage(all_text, work_status)
        completed_items = _unique_limited(
            _completed_from_docs(rep_names)
            + list(getattr(work_status, "completed_items", []) or []),
            10,
        )
        pending_items = _unique_limited(
            list(getattr(work_status, "pending_items", []) or [])
            + list(getattr(action_plan, "priority_tasks", []) or [])
            + list(getattr(action_plan, "first_week_actions", []) or []),
            10,
        )
        stakeholders = _infer_stakeholders(project_summary, all_text, action_plan)
        risks = _infer_risks(all_text, action_plan, work_status)
        confidence = _confidence(
            objective=objective,
            stage=current_stage,
            representative_count=len(rep_names),
            completed_count=len(completed_items),
            pending_count=len(pending_items),
            stakeholder_count=len(stakeholders),
            status_confidence=getattr(work_status, "confidence", 0) if work_status else 0,
        )

        return BusinessContext(
            objective=objective,
            current_stage=current_stage,
            completed_items=completed_items,
            pending_items=pending_items,
            stakeholders=stakeholders,
            risks=risks,
            confidence=confidence,
        )

    def build_contexts(
        self,
        project_summaries,
        representative_results=None,
        action_plans=None,
        work_statuses=None,
    ) -> dict[str, BusinessContext]:
        representative_results = representative_results or {}
        action_plans = action_plans or {}
        work_statuses = list(work_statuses or [])
        contexts: dict[str, BusinessContext] = {}

        for project_summary in project_summaries or []:
            project_key = getattr(project_summary, "project_key", "") or "기타"
            rep_result = representative_results.get(project_key)
            representative_docs = []
            if rep_result is not None:
                representative_docs = list(getattr(rep_result, "representative_docs", []) or [])
            elif getattr(project_summary, "representative_docs", None):
                representative_docs = list(getattr(project_summary, "representative_docs", []) or [])

            action_plan = action_plans.get(project_key)
            work_status = _match_work_status(project_summary, representative_docs, work_statuses)
            contexts[project_key] = self.build_context(
                project_summary,
                representative_docs,
                action_plan,
                work_status,
            )

        return contexts


def write_business_context_report(
    contexts: dict[str, BusinessContext],
    output_dir: str | None = None,
) -> str:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "output"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "business_context.txt"

    lines = [
        "# Business Context",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for work_unit, context in contexts.items():
        lines.extend([
            "[Work Unit]",
            work_unit,
            "",
            "Objective:",
            context.objective,
            "",
            "Current Stage:",
            context.current_stage,
            "",
            "Completed:",
        ])
        _append_bullets(lines, context.completed_items)
        lines.extend(["", "Pending:"])
        _append_bullets(lines, context.pending_items)
        lines.extend(["", "Stakeholders:"])
        _append_bullets(lines, context.stakeholders)
        lines.extend(["", "Risks:"])
        _append_bullets(lines, context.risks)
        lines.extend(["", "Confidence:", str(context.confidence), ""])

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _infer_objective(project_summary, rep_names: list[str], action_plan) -> str:
    project_name = _info(getattr(project_summary, "project_name", ""))
    if project_name:
        return project_name
    key_outputs = _info(getattr(project_summary, "key_outputs", ""))
    if key_outputs:
        return _first_sentence(key_outputs)
    required_docs = list(getattr(action_plan, "required_documents", []) or [])
    if required_docs:
        return f"{_clean_doc_name(required_docs[0])} 기반 업무 수행"
    if rep_names:
        return f"{_clean_doc_name(rep_names[0])} 기반 업무 수행"
    project_key = _info(getattr(project_summary, "project_key", ""))
    return project_key or "[정보 부족]"


def _infer_stage(text: str, work_status) -> str:
    status = getattr(work_status, "status", "") if work_status else ""
    lower = text.lower()
    if status == WAITING_APPROVAL or any(signal in lower for signal in ("승인", "결재", "approval")):
        return _STAGES["approval"]
    if status == WAITING_REVIEW or any(signal in lower for signal in ("검토", "review", "feedback", "수정")):
        return _STAGES["review"]
    if status == COMPLETED or any(signal in lower for signal in _FINAL_SIGNALS):
        return _STAGES["completed"]
    if status == MAINTENANCE or any(signal in lower for signal in _OPERATION_SIGNALS):
        return _STAGES["operation"]
    if status == IN_PROGRESS or any(signal in lower for signal in _IMPLEMENTATION_SIGNALS):
        return _STAGES["implementation"]
    if any(signal in lower for signal in _DESIGN_SIGNALS):
        return _STAGES["design"]
    if any(signal in lower for signal in _PLANNING_SIGNALS):
        return _STAGES["planning"]
    return _STAGES["planning"]


def _completed_from_docs(rep_names: list[str]) -> list[str]:
    items: list[str] = []
    for name in rep_names:
        stem = _clean_doc_name(name)
        lower = name.lower()
        if any(signal in lower for signal in _FINAL_SIGNALS):
            items.append(f"{stem} 완료")
        elif any(keyword in stem for keyword in ("기획안", "설계서", "가이드", "양식", "계약서", "제안서")):
            items.append(f"{stem} 작성")
    return items


def _infer_stakeholders(project_summary, text: str, action_plan) -> list[str]:
    stakeholders: list[str] = []
    client_name = _info(getattr(project_summary, "client_name", ""))
    if client_name:
        stakeholders.append(client_name)
        stakeholders.append("고객사 담당자")

    summary_stakeholders = _info(getattr(project_summary, "stakeholders", ""))
    if summary_stakeholders:
        stakeholders.extend(_split_items(summary_stakeholders))

    stakeholders.extend(getattr(action_plan, "stakeholders", []) or [])
    role_text = text.replace("대표문서", " ")
    for role in _ROLE_KEYWORDS:
        if role in role_text:
            stakeholders.append(role)
    stakeholders.extend(match.group(1) for match in _ORG_PATTERN.finditer(role_text))
    return _unique_limited(stakeholders, 10)


def _infer_risks(text: str, action_plan, work_status) -> list[str]:
    lower = text.lower()
    risks: list[str] = []
    risks.extend(getattr(work_status, "risks", []) or [])
    risks.extend(getattr(action_plan, "risks", []) or [])
    if "old" in lower:
        risks.append("old 문서 존재")
    if "backup" in lower:
        risks.append("backup 문서 존재")
    if "copy" in lower or "복사본" in lower:
        risks.append("복사본 문서 존재")
    if "draft" in lower or "초안" in lower:
        risks.append("draft 문서 존재")
    if any(signal in lower for signal in ("draft", "초안", "old")) and any(signal in lower for signal in _FINAL_SIGNALS):
        risks.append("구버전과 최종본 혼재")
    if not any(signal in lower for signal in _FINAL_SIGNALS):
        risks.append("최종본 부재")
    return _unique_limited(risks, 10)


def _match_work_status(project_summary, representative_docs: list, work_statuses: list) -> object | None:
    if not work_statuses:
        return None
    basis = " ".join([
        getattr(project_summary, "project_key", ""),
        getattr(project_summary, "project_name", ""),
        *_doc_names(representative_docs),
    ])
    basis_tokens = _tokens(basis)
    best_status = None
    best_score = -1
    for status in work_statuses:
        status_tokens = _tokens(getattr(status, "work_unit_name", ""))
        score = len(basis_tokens & status_tokens)
        if score > best_score:
            best_status = status
            best_score = score
    return best_status


def _confidence(
    objective: str,
    stage: str,
    representative_count: int,
    completed_count: int,
    pending_count: int,
    stakeholder_count: int,
    status_confidence: int,
) -> int:
    score = 35
    if objective and objective != "[정보 부족]":
        score += 15
    if stage:
        score += 10
    score += min(15, representative_count * 5)
    score += min(10, completed_count * 2)
    score += min(10, pending_count * 2)
    score += min(10, stakeholder_count * 2)
    if status_confidence:
        score += min(10, round(status_confidence / 10))
    return max(0, min(100, score))


def _project_text(project_summary) -> str:
    fields = (
        "project_key", "project_name", "client_name", "project_purpose",
        "stakeholders", "key_outputs", "current_status", "risks",
        "successor_notes",
    )
    return " ".join(_info(getattr(project_summary, field, "")) for field in fields)


def _action_text(action_plan) -> list[str]:
    if action_plan is None:
        return []
    values: list[str] = []
    for attr in ("priority_tasks", "required_documents", "risks", "first_week_actions", "stakeholders"):
        values.extend(str(item) for item in (getattr(action_plan, attr, []) or []))
    return values


def _status_text(work_status) -> str:
    if work_status is None:
        return ""
    values = [getattr(work_status, "work_unit_name", ""), getattr(work_status, "status", "")]
    for attr in ("completed_items", "pending_items", "next_actions", "risks"):
        values.extend(str(item) for item in (getattr(work_status, attr, []) or []))
    return " ".join(values)


def _doc_names(docs) -> list[str]:
    names: list[str] = []
    for doc in docs or []:
        if isinstance(doc, str):
            name = doc
        else:
            name = getattr(doc, "display_name", "") or getattr(doc, "file_name", "")
        if name:
            names.append(str(name))
    return names


def _info(value: str) -> str:
    text = str(value or "").strip()
    return "" if text in {"[정보 부족]", "(데이터 없음)"} else text


def _first_sentence(text: str) -> str:
    return re.split(r"[\n.。]", text.strip())[0].strip()


def _clean_doc_name(value: str) -> str:
    stem = Path(str(value).replace("\\", "/")).stem
    stem = re.sub(r"(?i)([_\-\s]?v\d+(?:\.\d+)?)", "", stem)
    stem = re.sub(r"(?i)([_\-\s]?(final|최종|확정|draft|초안|old|backup|copy|복사본))", "", stem)
    stem = re.sub(r"[_\-]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def _split_items(text: str) -> list[str]:
    return [item.strip(" -*") for item in re.split(r"[,/\n]", text) if item.strip(" -*")]


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^0-9A-Za-z가-힣]+", text.lower()) if len(token) >= 2}


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
