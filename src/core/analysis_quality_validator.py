from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.core.work_cluster_engine import MIXED_MODE, PROJECT_MODE, WORK_CLUSTER_MODE
from src.core.work_status_engine import UNKNOWN


@dataclass
class QualitySection:
    name: str
    score: int
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, int | float | str] = field(default_factory=dict)


@dataclass
class AnalysisQualityReport:
    document_families: QualitySection
    work_clusters: QualitySection
    representative_docs: QualitySection
    work_status: QualitySection
    work_unit_detection: QualitySection
    overall_score: int
    warnings: list[str] = field(default_factory=list)


_BAD_CLUSTER_NAMES = {
    "result", "results", "output", "outputs", "document", "documents",
    "자료", "문서", "기타", "misc", "결과물",
}
_FINAL_SIGNALS = ("final", "최종", "확정")
_OLD_SIGNALS = ("old", "backup", "copy", "복사본", "draft", "초안")
_VALID_MODES = {PROJECT_MODE, WORK_CLUSTER_MODE, MIXED_MODE}


class AnalysisQualityValidator:
    """Validate quality of rule-based inference outputs."""

    def validate(
        self,
        document_families,
        work_clusters,
        representative_results,
        work_statuses,
        work_unit_detection,
    ) -> AnalysisQualityReport:
        family_section = self._validate_document_families(document_families)
        cluster_section = self._validate_work_clusters(work_clusters)
        representative_section = self._validate_representative_docs(representative_results)
        status_section = self._validate_work_status(work_statuses)
        detection_section = self._validate_work_unit_detection(work_unit_detection)

        sections = [
            family_section,
            cluster_section,
            representative_section,
            status_section,
            detection_section,
        ]
        overall = round(sum(section.score for section in sections) / len(sections))
        warnings = [
            warning
            for section in sections
            for warning in section.warnings
        ]
        return AnalysisQualityReport(
            document_families=family_section,
            work_clusters=cluster_section,
            representative_docs=representative_section,
            work_status=status_section,
            work_unit_detection=detection_section,
            overall_score=overall,
            warnings=warnings,
        )

    def _validate_document_families(self, results) -> QualitySection:
        families = _flatten_families(results)
        score = 100
        warnings: list[str] = []
        if not families:
            return QualitySection("Document Families", 0, ["no document families detected"])

        duplicate_families = [family for family in families if len(getattr(family, "family_docs", []) or []) >= 2]
        impure_count = 0
        for family in families:
            key_tokens = _tokens(getattr(family, "family_key", ""))
            docs = getattr(family, "family_docs", []) or []
            for doc in docs:
                doc_tokens = _tokens(_stem(_doc_name(doc)))
                if key_tokens and doc_tokens and not (key_tokens & doc_tokens):
                    impure_count += 1
                    break

        if len(families) > 80:
            score -= 10
            warnings.append("high family count may indicate under-grouping")
        if impure_count:
            penalty = min(30, impure_count * 8)
            score -= penalty
            warnings.append(f"family purity issue detected: {impure_count}")
        if not duplicate_families:
            score -= 12
            warnings.append("no duplicate/version family detected")

        return QualitySection(
            "Document Families",
            _clamp(score),
            warnings,
            {
                "family_count": len(families),
                "duplicate_family_count": len(duplicate_families),
                "impure_family_count": impure_count,
            },
        )

    def _validate_work_clusters(self, clusters) -> QualitySection:
        clusters = list(clusters or [])
        score = 100
        warnings: list[str] = []
        if not clusters:
            return QualitySection("Work Clusters", 0, ["no work clusters detected"])

        leakage = [cluster.cluster_key for cluster in clusters if _is_bad_cluster_name(cluster.cluster_key)]
        weak_names = [
            cluster.cluster_key for cluster in clusters
            if len(_tokens(cluster.cluster_key)) == 0 or len(cluster.cluster_key.strip()) <= 1
        ]
        singleton_ratio = sum(1 for cluster in clusters if len(getattr(cluster, "documents", []) or []) == 1) / len(clusters)

        if leakage:
            score -= min(45, len(leakage) * 20)
            warnings.append("container folder selected as cluster")
        if weak_names:
            score -= min(25, len(weak_names) * 8)
            warnings.append("weak cluster naming quality detected")
        if singleton_ratio > 0.8 and len(clusters) >= 5:
            score -= 10
            warnings.append("high singleton cluster ratio")

        return QualitySection(
            "Work Clusters",
            _clamp(score),
            warnings,
            {
                "cluster_count": len(clusters),
                "container_leakage_count": len(leakage),
                "weak_name_count": len(weak_names),
                "singleton_ratio": round(singleton_ratio, 2),
            },
        )

    def _validate_representative_docs(self, results) -> QualitySection:
        result_list = _representative_result_list(results)
        score = 100
        warnings: list[str] = []
        if not result_list:
            return QualitySection("Representative Docs", 0, ["no representative result detected"])

        missing = 0
        final_not_preferred = 0
        duplicate_versions = 0
        for result in result_list:
            reps = list(getattr(result, "representative_docs", []) or [])
            if not reps:
                missing += 1
                continue
            rep_names = [_doc_name(doc) for doc in reps]
            refs = list(getattr(result, "reference_docs", []) or [])
            support = list(getattr(result, "supporting_docs", []) or [])
            all_names = rep_names + [_doc_name(doc) for doc in refs + support]
            if any(_has_final(name) for name in all_names) and not any(_has_final(name) for name in rep_names):
                final_not_preferred += 1
            families = [_version_family(name) for name in rep_names]
            duplicate_versions += len(families) - len({family for family in families if family})

        if missing:
            score -= min(35, missing * 12)
            warnings.append("no representative document")
        if final_not_preferred:
            score -= min(30, final_not_preferred * 12)
            warnings.append("final version not preferred")
        if duplicate_versions > 0:
            score -= min(25, duplicate_versions * 10)
            warnings.append("duplicate versions included in representative docs")

        return QualitySection(
            "Representative Docs",
            _clamp(score),
            warnings,
            {
                "result_count": len(result_list),
                "missing_representative_count": missing,
                "final_not_preferred_count": final_not_preferred,
                "duplicate_version_count": duplicate_versions,
            },
        )

    def _validate_work_status(self, statuses) -> QualitySection:
        statuses = list(statuses or [])
        score = 100
        warnings: list[str] = []
        if not statuses:
            return QualitySection("Work Status", 0, ["no work status inferred"])

        unknown_count = sum(1 for status in statuses if getattr(status, "status", "") == UNKNOWN)
        low_confidence = sum(1 for status in statuses if getattr(status, "confidence", 0) < 50)
        no_risk_count = sum(1 for status in statuses if not (getattr(status, "risks", []) or []))
        avg_confidence = round(sum(getattr(status, "confidence", 0) for status in statuses) / len(statuses))

        if unknown_count:
            score -= min(45, unknown_count * 15)
            warnings.append("excessive unknown status" if unknown_count / len(statuses) > 0.3 else "unknown status detected")
        if low_confidence:
            score -= min(25, low_confidence * 8)
            warnings.append("low confidence work status detected")
        if no_risk_count == len(statuses):
            score -= 10
            warnings.append("no risk detection in work status")
        if avg_confidence < 65:
            score -= 10
            warnings.append("average work status confidence is low")

        return QualitySection(
            "Work Status",
            _clamp(score),
            warnings,
            {
                "status_count": len(statuses),
                "unknown_count": unknown_count,
                "low_confidence_count": low_confidence,
                "average_confidence": avg_confidence,
                "no_risk_count": no_risk_count,
            },
        )

    def _validate_work_unit_detection(self, detection) -> QualitySection:
        score = 100
        warnings: list[str] = []
        if detection is None:
            return QualitySection("Work Unit Detection", 0, ["no work unit detection result"])

        mode = getattr(detection, "mode", "")
        project_score = getattr(detection, "project_score", 0)
        cluster_score = getattr(detection, "work_cluster_score", 0)
        if mode not in _VALID_MODES:
            score -= 60
            warnings.append("invalid work unit detection mode")
        if mode == PROJECT_MODE and project_score < cluster_score:
            score -= 20
            warnings.append("PROJECT_MODE score conflict")
        if mode == WORK_CLUSTER_MODE and cluster_score < project_score:
            score -= 20
            warnings.append("WORK_CLUSTER_MODE score conflict")
        if mode == MIXED_MODE and min(project_score, cluster_score) < 35:
            score -= 20
            warnings.append("MIXED_MODE lacks balanced signals")
        if max(project_score, cluster_score) < 30:
            score -= 15
            warnings.append("weak work unit detection signals")

        return QualitySection(
            "Work Unit Detection",
            _clamp(score),
            warnings,
            {
                "mode": mode,
                "project_score": project_score,
                "work_cluster_score": cluster_score,
            },
        )


def write_analysis_quality_report(report: AnalysisQualityReport, output_dir: str | None = None) -> str:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "output"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "analysis_quality_report.txt"

    lines = [
        "# Analysis Quality Report",
        f"# Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for section in [
        report.document_families,
        report.work_clusters,
        report.representative_docs,
        report.work_status,
        report.work_unit_detection,
    ]:
        lines.extend([section.name, f"Score: {section.score}", ""])
        if section.metrics:
            lines.append("Metrics:")
            for key, value in section.metrics.items():
                lines.append(f"* {key}: {value}")
            lines.append("")
        if section.warnings:
            lines.append("Warnings:")
            for warning in section.warnings:
                lines.append(f"* {warning}")
            lines.append("")

    lines.extend(["Overall Quality", f"Score: {report.overall_score}", "", "Warnings"])
    if report.warnings:
        for warning in report.warnings:
            lines.append(f"* {warning}")
    else:
        lines.append("* (none)")

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _flatten_families(results) -> list:
    if isinstance(results, dict):
        return [family for families in results.values() for family in (families or [])]
    return list(results or [])


def _representative_result_list(results) -> list:
    if isinstance(results, dict):
        return list(results.values())
    return list(results or [])


def _doc_name(doc) -> str:
    if isinstance(doc, str):
        return doc
    return str(getattr(doc, "display_name", "") or getattr(doc, "file_name", "") or "")


def _stem(name: str) -> str:
    return Path(name.replace("\\", "/")).stem


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.split(r"[^0-9A-Za-z가-힣]+", value.lower())
        if len(token) >= 2 and not re.fullmatch(r"v\d+(?:\.\d+)?", token)
    }


def _is_bad_cluster_name(value: str) -> bool:
    normalized = re.sub(r"[\s_\-]+", "", str(value).lower())
    return normalized in {re.sub(r"[\s_\-]+", "", item.lower()) for item in _BAD_CLUSTER_NAMES}


def _has_final(name: str) -> bool:
    lower = name.lower()
    return any(signal.lower() in lower for signal in _FINAL_SIGNALS)


def _version_family(name: str) -> str:
    stem = _stem(name).lower()
    if not (re.search(r"(?i)(^|[^a-z0-9])v\d+(?:\.\d+)?\b", stem) or _has_final(stem)):
        return ""
    family = re.sub(r"(?i)(^|[_\-\s])v\d+(?:\.\d+)?\b", "", stem)
    for signal in _FINAL_SIGNALS + _OLD_SIGNALS:
        family = family.replace(signal.lower(), "")
    return re.sub(r"[\s_\-]+", "", family)


def _clamp(value: int | float) -> int:
    return int(max(0, min(100, value)))
