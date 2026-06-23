from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path


NOT_STARTED = "NOT_STARTED"
IN_PROGRESS = "IN_PROGRESS"
WAITING_REVIEW = "WAITING_REVIEW"
WAITING_APPROVAL = "WAITING_APPROVAL"
COMPLETED = "COMPLETED"
MAINTENANCE = "MAINTENANCE"
UNKNOWN = "UNKNOWN"


@dataclass
class WorkStatus:
    work_unit_name: str
    status: str
    completed_items: list[str] = field(default_factory=list)
    in_progress_items: list[str] = field(default_factory=list)
    pending_items: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    confidence: int = 0


_FINAL_SIGNALS = ("final", "최종", "확정")
_IN_PROGRESS_SIGNALS = ("draft", "초안", "v1", "v2", "v3")
_REVIEW_SIGNALS = ("검토", "review", "feedback", "수정", "피드백")
_APPROVAL_SIGNALS = ("승인", "결재", "approval")
_MAINTENANCE_SIGNALS = ("운영", "유지보수", "maintenance", "운영안")
_RISK_SIGNALS = ("old", "backup", "copy", "복사본", "draft", "초안")
_DELIVERABLE_ITEMS = {
    "평가기획안": "평가기획안 작성",
    "기획안": "기획안 작성",
    "설계서": "설계서 작성",
    "가이드": "가이드 작성",
    "양식": "양식 작성",
    "계약서": "계약서 작성",
    "제안서": "제안서 작성",
}


class WorkStatusEngine:
    """Infer work status from rule-based document and action-plan signals."""

    def infer_work_status(
        self,
        work_unit_name: str,
        representative_docs=None,
        supporting_docs=None,
        document_families=None,
        action_plan=None,
        work_cluster=None,
    ) -> WorkStatus:
        docs = _unique_docs(
            list(representative_docs or [])
            + list(supporting_docs or [])
            + list(getattr(work_cluster, "documents", []) or [])
            + list(getattr(work_cluster, "representative_docs", []) or [])
        )
        family_docs = _docs_from_families(document_families or [])
        docs = _unique_docs(docs + family_docs)

        names = [_doc_name(doc) for doc in docs]
        text = " ".join(
            names
            + [_doc_summary(doc) for doc in docs]
            + _action_plan_text(action_plan)
        )
        lower = text.lower()

        final_count = _count_signals(lower, _FINAL_SIGNALS)
        progress_count = _version_signal_count(lower) + _count_signals(lower, ("draft", "초안"))
        review_count = _count_signals(lower, _REVIEW_SIGNALS)
        approval_count = _count_signals(lower, _APPROVAL_SIGNALS)
        maintenance_count = _count_signals(lower, _MAINTENANCE_SIGNALS)
        risk_count = _count_signals(lower, _RISK_SIGNALS)

        completed_items = _completed_items(names)
        in_progress_items = _in_progress_items(names, lower)
        pending_items = _pending_items(
            final_count=final_count,
            review_count=review_count,
            approval_count=approval_count,
            progress_count=progress_count,
        )
        risks = _risks(names, lower, action_plan)

        status = _decide_status(
            approval_count=approval_count,
            review_count=review_count,
            final_count=final_count,
            progress_count=progress_count,
            maintenance_count=maintenance_count,
            docs_count=len(docs),
        )
        next_actions = _next_actions(status, action_plan, pending_items)
        confidence = _confidence(
            docs_count=len(docs),
            final_count=final_count,
            progress_count=progress_count,
            review_count=review_count,
            approval_count=approval_count,
            completed_count=len(completed_items),
            risk_count=risk_count,
        )

        return WorkStatus(
            work_unit_name=work_unit_name or UNKNOWN,
            status=status,
            completed_items=completed_items,
            in_progress_items=in_progress_items,
            pending_items=pending_items,
            next_actions=next_actions,
            risks=risks,
            confidence=confidence,
        )

    def infer_from_work_clusters(
        self,
        work_clusters,
        representative_results=None,
        document_families=None,
        action_plans=None,
    ) -> list[WorkStatus]:
        representative_results = representative_results or {}
        document_families = document_families or {}
        action_plans = action_plans or {}

        statuses: list[WorkStatus] = []
        for cluster in work_clusters or []:
            project_keys = _project_keys_from_docs(getattr(cluster, "documents", []) or [])
            representative_docs = list(getattr(cluster, "representative_docs", []) or [])
            supporting_docs = []
            families = []
            plans = []

            for key in project_keys:
                rep_result = representative_results.get(key)
                if rep_result is not None:
                    representative_docs.extend(getattr(rep_result, "representative_docs", []) or [])
                    supporting_docs.extend(getattr(rep_result, "supporting_docs", []) or [])
                families.extend(document_families.get(key, []) or [])
                plan = action_plans.get(key)
                if plan is not None:
                    plans.append(plan)

            statuses.append(
                self.infer_work_status(
                    work_unit_name=getattr(cluster, "cluster_key", ""),
                    representative_docs=representative_docs,
                    supporting_docs=supporting_docs,
                    document_families=_families_for_cluster(cluster, families),
                    action_plan=_merge_action_plans(plans),
                    work_cluster=cluster,
                )
            )

        statuses.sort(key=lambda status: (status.status, -status.confidence, status.work_unit_name))
        return statuses


def write_work_status_report(statuses: list[WorkStatus], output_dir: str | None = None) -> str:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "output"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "work_status.txt"

    lines = [
        "# Work Status",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for status in statuses:
        lines.extend([
            "[Work Unit]",
            "",
            status.work_unit_name,
            "",
            "Status:",
            status.status,
            "",
            "Confidence:",
            str(status.confidence),
            "",
            "Completed:",
            "",
        ])
        _append_bullets(lines, status.completed_items)
        lines.extend(["", "In Progress:", ""])
        _append_bullets(lines, status.in_progress_items)
        lines.extend(["", "Pending:", ""])
        _append_bullets(lines, status.pending_items)
        lines.extend(["", "Next Actions:", ""])
        _append_bullets(lines, status.next_actions)
        lines.extend(["", "Risks:", ""])
        _append_bullets(lines, status.risks)
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return str(path)


def _decide_status(
    approval_count: int,
    review_count: int,
    final_count: int,
    progress_count: int,
    maintenance_count: int,
    docs_count: int,
) -> str:
    if docs_count == 0:
        return UNKNOWN
    if approval_count:
        return WAITING_APPROVAL
    if review_count:
        return WAITING_REVIEW
    if final_count:
        return COMPLETED
    if progress_count:
        return IN_PROGRESS
    if maintenance_count:
        return MAINTENANCE
    return UNKNOWN


def _completed_items(names: list[str]) -> list[str]:
    items: list[str] = []
    for name in names:
        stem = _stem(name)
        for signal, item in _DELIVERABLE_ITEMS.items():
            if signal in stem:
                items.append(item)
    return _unique_limited(items, 10)


def _in_progress_items(names: list[str], lower_text: str) -> list[str]:
    items: list[str] = []
    for name in names:
        stem = _stem(name)
        lower = stem.lower()
        if re.search(r"(?i)(^|[^a-z0-9])v\d+(?:\.\d+)?\b", lower) or "draft" in lower or "초안" in stem:
            items.append(f"{_clean_status_tokens(stem)} 수정 중")
        if any(signal in lower for signal in ("feedback", "수정", "review")) or "검토" in stem:
            items.append("고객 검토")
    if "feedback" in lower_text or "피드백" in lower_text:
        items.append("고객 피드백 확인")
    return _unique_limited(items, 10)


def _pending_items(
    final_count: int,
    review_count: int,
    approval_count: int,
    progress_count: int,
) -> list[str]:
    items: list[str] = []
    if review_count:
        items.append("고객 검토 결과 확인")
    if approval_count:
        items.append("승인 및 결재 결과 확인")
    if not final_count:
        items.append("최종본 확정")
    if progress_count:
        items.append("수정본 작성")
    return _unique_limited(items, 10)


def _risks(names: list[str], lower_text: str, action_plan) -> list[str]:
    risks: list[str] = []
    if "old" in lower_text:
        risks.append("old 문서 존재")
    if "backup" in lower_text:
        risks.append("backup 문서 존재")
    if "copy" in lower_text or "복사본" in lower_text:
        risks.append("복사본 문서 존재")
    if "draft" in lower_text or "초안" in lower_text:
        risks.append("draft 문서 존재")
    if not any(signal in lower_text for signal in _FINAL_SIGNALS):
        risks.append("최종본 부재")
    risks.extend(getattr(action_plan, "risks", []) or [])
    return _unique_limited(risks, 10)


def _next_actions(status: str, action_plan, pending_items: list[str]) -> list[str]:
    actions = list(getattr(action_plan, "first_week_actions", []) or [])
    if status == WAITING_REVIEW:
        actions = ["고객 피드백 수집", "수정본 작성"] + actions
    elif status == WAITING_APPROVAL:
        actions = ["승인권자 확인", "결재 상태 확인"] + actions
    elif status == IN_PROGRESS:
        actions = ["진행 중 문서 최신본 확인", "미완료 항목 정리"] + actions
    elif status == COMPLETED:
        actions = ["최종 산출물 전달 여부 확인"] + actions
    elif status == MAINTENANCE:
        actions = ["운영 이슈 확인", "정기 점검 항목 확인"] + actions
    else:
        actions = ["대표문서 확인", "업무 상태 확인"]
    actions.extend(pending_items)
    return _unique_limited(actions, 10)


def _confidence(
    docs_count: int,
    final_count: int,
    progress_count: int,
    review_count: int,
    approval_count: int,
    completed_count: int,
    risk_count: int,
) -> int:
    if docs_count == 0:
        return 20
    score = 45
    score += min(15, docs_count * 3)
    score += min(25, (final_count + progress_count + review_count + approval_count) * 8)
    score += min(10, completed_count * 2)
    score -= min(10, risk_count * 2)
    return max(0, min(100, score))


def _docs_from_families(families) -> list:
    docs: list = []
    for family in families or []:
        latest = getattr(family, "latest_doc", None)
        if latest is not None:
            docs.append(latest)
        docs.extend(getattr(family, "family_docs", []) or [])
    return docs


def _families_for_cluster(cluster, families) -> list:
    cluster_names = {_doc_name(doc) for doc in getattr(cluster, "documents", []) or []}
    matched = []
    for family in families or []:
        family_docs = getattr(family, "family_docs", []) or []
        names = {_doc_name(doc) for doc in family_docs}
        if cluster_names & names:
            matched.append(family)
    return matched


def _merge_action_plans(plans: list):
    if not plans:
        return None
    merged = type("_MergedActionPlan", (), {})()
    merged.first_week_actions = _unique_limited(
        [item for plan in plans for item in (getattr(plan, "first_week_actions", []) or [])],
        10,
    )
    merged.risks = _unique_limited(
        [item for plan in plans for item in (getattr(plan, "risks", []) or [])],
        10,
    )
    return merged


def _action_plan_text(action_plan) -> list[str]:
    if action_plan is None:
        return []
    values: list[str] = []
    for attr in ("priority_tasks", "required_documents", "risks", "first_week_actions"):
        values.extend(str(item) for item in (getattr(action_plan, attr, []) or []))
    return values


def _project_keys_from_docs(docs) -> set[str]:
    keys: set[str] = set()
    for doc in docs or []:
        parts = _doc_name(doc).replace("\\", "/").split("/")
        if len(parts) >= 2:
            keys.add(parts[0])
    return keys


def _unique_docs(docs: list) -> list:
    seen: set[str] = set()
    result: list = []
    for doc in docs:
        name = _doc_name(doc)
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(doc)
    return result


def _doc_name(doc) -> str:
    if isinstance(doc, str):
        return doc
    return str(getattr(doc, "display_name", "") or getattr(doc, "file_name", "") or "")


def _doc_summary(doc) -> str:
    return str(getattr(doc, "summary_text", "") or "")


def _stem(name: str) -> str:
    return Path(name.replace("\\", "/")).stem


def _clean_status_tokens(stem: str) -> str:
    cleaned = re.sub(r"(?i)([_\-\s]?v\d+(?:\.\d+)?)", "", stem)
    cleaned = re.sub(r"(?i)([_\-\s]?(draft|초안))", "", cleaned)
    return cleaned.strip(" _-") or stem


def _count_signals(lower_text: str, signals: tuple[str, ...]) -> int:
    return sum(1 for signal in signals if signal.lower() in lower_text)


def _version_signal_count(lower_text: str) -> int:
    return len(re.findall(r"(?i)(^|[^a-z0-9])v\d+(?:\.\d+)?\b", lower_text))


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
