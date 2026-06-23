from pathlib import Path
from unittest.mock import patch

from src.core.business_context_engine_v3 import BusinessContextV3
from src.core.description_generator import UNKNOWN_DESCRIPTION
from src.core.report_assembly_preview import (
    WorkUnitEvidence,
    assemble_engine_summary,
    assemble_from_evidence,
    assemble_work_summary,
    load_work_units_from_work_clusters,
    merge_similar_work_units,
    write_engine_cleanup_report,
    write_engine_only_preview,
    write_final_handover_report_preview,
    write_report_assembly_preview,
)


def test_assembles_summary_from_generator_outputs():
    summary = assemble_work_summary(
        business_context=BusinessContextV3(
            purpose_candidates=["평가 및 보상 운영"],
            confidence=90,
            evidence=["performance_evaluation_guide.xlsx"],
        ),
        representative_documents=[
            "performance_evaluation_guide.xlsx",
            "performance_review_scorecard.xlsx",
            "criteria_v2.docx",
        ],
        supporting_documents=["performance_review_criteria_v1.docx"],
    )

    assert summary.purpose == "평가 및 보상 운영"
    assert summary.description != UNKNOWN_DESCRIPTION
    assert "Excel" in summary.tools
    assert "Word" in summary.tools
    assert "Version history should be managed." in summary.knowhow
    assert summary.major_deliverables == [
        "performance_evaluation_guide.xlsx",
        "performance_review_scorecard.xlsx",
        "criteria_v2.docx",
    ]
    assert "performance_evaluation_guide.xlsx" in summary.evidence
    assert summary.confidence >= 50
    assert summary.overall_confidence == summary.confidence
    assert summary.purpose_confidence == 90
    assert summary.description_confidence >= 50
    assert summary.tool_confidence >= 50
    assert summary.knowhow_confidence >= 50
    assert summary.abstention_reasons == []


def test_unknown_evidence_stays_low_confidence_without_guessing():
    summary = assemble_from_evidence(
        WorkUnitEvidence(
            work_unit="Displayed Label Only",
            representative_documents=["random_notes.bin"],
            supporting_documents=["misc_archive.tmp"],
            document_families=["reference_material"],
        )
    )

    assert summary.purpose == "Unknown"
    assert summary.description == UNKNOWN_DESCRIPTION
    assert summary.tools == []
    assert summary.knowhow == []
    assert summary.confidence <= 30
    assert "Purpose unknown: insufficient document evidence" in summary.abstention_reasons
    assert "Description unknown: no supported pattern match" in summary.abstention_reasons
    assert "No tools inferred: no supported extension or tool keyword" in summary.abstention_reasons
    assert "No knowhow inferred: insufficient supported evidence" in summary.abstention_reasons


def test_work_unit_name_is_not_used_for_primary_inference():
    summary = assemble_from_evidence(
        WorkUnitEvidence(
            work_unit="GitHub Jira Evaluation Training",
            representative_documents=["random_notes.bin"],
            supporting_documents=[],
            document_families=[],
        )
    )

    assert summary.purpose == "Unknown"
    assert summary.tools == []
    assert summary.knowhow == []
    assert summary.confidence <= 30


def test_load_work_units_from_work_clusters():
    units = load_work_units_from_work_clusters("output/work_clusters.txt", limit=2)

    assert len(units) == 2
    assert units[0].work_unit
    assert units[0].representative_documents


def test_engine_preview_writer_uses_reconstruction_fields_only():
    path = "output/_test_engine_only_preview.txt"
    captured = {}
    work_units = [
        WorkUnitEvidence(
            work_unit="A",
            representative_documents=["asset_plan.xlsx", "facility_usage.docx"],
            supporting_documents=[],
        ),
        WorkUnitEvidence(
            work_unit="B",
            representative_documents=["asset_status.xlsx", "admin_facility.docx"],
            supporting_documents=[],
        ),
    ]

    def capture_write(path, text, **kwargs):
        captured["path"] = path
        captured["text"] = text
        captured["kwargs"] = kwargs

    with patch.object(Path, "write_text", capture_write):
        write_engine_only_preview(work_units, path)

    text = captured["text"]

    assert captured["path"].as_posix() == "output/_test_engine_only_preview.txt"
    assert captured["kwargs"] == {"encoding": "utf-8", "newline": "\n"}
    assert "Work Units:" in text
    assert "Start Here" in text
    assert "Recent Documents Count" in text
    assert "Related Documents:" in text
    assert "Deliverables:" in text
    assert "Recent Documents (30 days only):" in text
    assert "Final Deliverables:" in text
    assert "Latest Modified Date:" in text
    assert "Related Document Count:" in text
    assert "Deliverable Count:" in text
    assert "Importance:" in text
    assert "Importance Reasons:" in text
    assert "Evidence:" in text
    assert "Relationships:" in text
    assert "Timeline:" in text
    assert "Importance Reasons:" in text
    assert "Status:" not in text
    assert "Explicit Owners:" not in text
    assert "Purpose:" not in text
    assert "Description:" not in text
    assert "Knowhow:" not in text


def test_final_handover_preview_includes_required_fields():
    path = "output/_test_final_handover_report_preview.txt"
    captured = {}
    work_units = [
        WorkUnitEvidence(
            work_unit="A",
            representative_documents=["monthly_report.xlsx"],
            supporting_documents=["asset_register.docx"],
        )
    ]

    def capture_write(path, text, **kwargs):
        captured["path"] = path
        captured["text"] = text
        captured["kwargs"] = kwargs

    with patch.object(Path, "write_text", capture_write):
        write_final_handover_report_preview(work_units, path)

    text = captured["text"]

    assert captured["path"].as_posix() == "output/_test_final_handover_report_preview.txt"
    assert captured["kwargs"] == {"encoding": "utf-8", "newline": "\n"}
    assert "Work Units:" in text
    assert "Start Here" in text
    assert "Related Documents:" in text
    assert "Deliverables:" in text
    assert "Recent Documents (30 days only):" in text
    assert "Final Deliverables:" in text
    assert "Latest Modified Date:" in text
    assert "Related Document Count:" in text
    assert "Deliverable Count:" in text
    assert "Importance:" in text
    assert "Evidence:" in text
    assert "Relationships:" in text
    assert "Timeline:" in text
    assert "Importance Reasons:" in text
    assert "Status:" not in text
    assert "Explicit Owners:" not in text
    assert "Purpose:" not in text
    assert "Description:" not in text
    assert "Knowhow:" not in text


def test_engine_summary_collects_documents_and_relationships():
    summary = assemble_engine_summary(
        WorkUnitEvidence(
            work_unit="Unit A",
            representative_documents=["main_final_20260604.xlsx"],
            supporting_documents=["support_20260601.docx"],
            document_families=["family-v1"],
        )
    )

    assert summary.work_unit == "Unit A"
    assert summary.deliverables == ["main_final_20260604.xlsx"]
    assert summary.final_deliverables == ["main_final_20260604.xlsx"]
    assert summary.related_documents == ["main_final_20260604.xlsx", "support_20260601.docx", "family-v1"]
    assert summary.evidence == ["main_final_20260604.xlsx", "support_20260601.docx", "family-v1"]
    assert summary.timeline == ["2026-06-04", "2026-06-01"]
    assert summary.latest_modified_date == "2026-06-04"
    assert summary.recent_documents == ["main_final_20260604.xlsx", "support_20260601.docx"]
    assert summary.related_document_count == 3
    assert summary.deliverable_count == 1
    assert summary.importance == 65
    assert summary.importance_reasons == [
        "3 related documents",
        "Deliverable detected",
        "Explicit date detected",
    ]
    assert "Representative documents -> Work unit" in summary.relationships
    assert "Supporting documents -> Work unit" in summary.relationships
    assert "Document families -> Work unit" in summary.relationships


def test_engine_cleanup_report_marks_gpt_components():
    path = "output/_test_engine_cleanup_report.txt"
    captured = {}

    def capture_write(path, text, **kwargs):
        captured["path"] = path
        captured["text"] = text
        captured["kwargs"] = kwargs

    with patch.object(Path, "write_text", capture_write):
        write_engine_cleanup_report(path)

    text = captured["text"]

    assert "Components removed from Engine outputs:" in text
    assert "BusinessContextV3 / Purpose Generation" in text
    assert "Description Generator" in text
    assert "Knowhow Generator" in text
    assert "Components retained:" in text
    assert "Future GPT-only components:" in text
    assert "Before vs After report structure:" in text
    assert "Final Engine MVP Definition:" in text
    assert "Metadata additions:" in text
    assert "Duplicate reduction results:" in text
    assert "Start Here output:" in text
    assert "Expected usability improvement:" in text
    assert "Remaining Engine Limitations:" in text


def test_merge_similar_work_units_deterministically():
    merged, notes = merge_similar_work_units(
        [
            WorkUnitEvidence(work_unit="채용", representative_documents=["a.docx"]),
            WorkUnitEvidence(work_unit="채용관리", representative_documents=["b.xlsx"]),
            WorkUnitEvidence(work_unit="평가", representative_documents=["c.docx"]),
            WorkUnitEvidence(work_unit="인사평가", representative_documents=["d.xlsx"]),
        ]
    )

    assert len(merged) == 3
    assert merged[0].work_unit == "채용"
    assert merged[0].representative_documents == ["a.docx", "b.xlsx"]
    assert "['채용', '채용관리'] -> 채용" in notes
