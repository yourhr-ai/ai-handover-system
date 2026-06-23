from types import SimpleNamespace

from src.core.knowhow_generator import KnowhowGenerator, generate_knowhow


def test_many_document_versions_infer_version_control():
    result = generate_knowhow(
        representative_documents=["quality_plan_v1.docx", "quality_plan_v2.docx"],
        supporting_documents=["quality_plan_20260604.xlsx"],
    )

    assert result.knowhow_items[0] == "Version history should be managed."
    assert result.confidence >= 50
    assert result.evidence == [
        "quality_plan_v1.docx",
        "quality_plan_v2.docx",
        "quality_plan_20260604.xlsx",
    ]


def test_customer_specific_files_infer_communication_history():
    result = KnowhowGenerator().generate(
        representative_documents=["client_meeting_minutes.docx", "customer_proposal.pptx"],
        supporting_documents=["account_quote.xlsx"],
    )

    assert "Communication history should be retained." in result.knowhow_items
    assert "Change history should be documented." in result.knowhow_items
    assert result.confidence >= 50


def test_company_neutral_patterns_from_document_evidence():
    result = generate_knowhow(
        representative_documents=[
            "approval_request.docx",
            "final_approved_plan.pdf",
            "evaluation_criteria.xlsx",
            "assessment_scorecard.xlsx",
            "maintenance_checklist.xlsx",
            "asset_register.xlsx",
        ],
        supporting_documents=[
            "financial_report_deadline.xlsx",
            "tax_statement_review.docx",
            "training_attendance.xlsx",
            "training_survey_results.xlsx",
        ],
    )

    assert "Approval history should be preserved." in result.knowhow_items
    assert "Criteria changes should be documented." in result.knowhow_items
    assert "Review and reporting schedules should be tracked." in result.knowhow_items
    assert "Training results should be reviewed periodically." in result.knowhow_items
    assert "Records should be maintained consistently." in result.knowhow_items


def test_family_names_and_family_documents_are_evidence():
    family = SimpleNamespace(
        family_key="client_proposal_versions",
        latest_doc=SimpleNamespace(display_name="proposal_v3.pptx"),
        family_docs=[
            SimpleNamespace(display_name="proposal_v2.pptx"),
            SimpleNamespace(display_name="client_meeting_notes.docx"),
        ],
    )

    result = generate_knowhow(document_families=[family])

    assert "Version history should be managed." in result.knowhow_items
    assert "Change history should be documented." in result.knowhow_items
    assert "Communication history should be retained." in result.knowhow_items
    assert result.evidence[:3] == [
        "client_proposal_versions",
        "proposal_v3.pptx",
        "proposal_v2.pptx",
    ]


def test_unknown_evidence_returns_no_knowhow_with_low_confidence():
    result = generate_knowhow(
        representative_documents=["random_notes.bin"],
        supporting_documents=["misc_archive.tmp"],
        document_families=["reference_material"],
    )

    assert result.knowhow_items == []
    assert result.confidence <= 30
    assert result.evidence == []


def test_single_strong_report_or_register_evidence_infers_knowhow():
    result = generate_knowhow(
        representative_documents=["monthly_report.xlsx", "asset_register.docx"],
    )

    assert "Review and reporting schedules should be tracked." in result.knowhow_items
    assert "Records should be maintained consistently." in result.knowhow_items
    assert result.confidence >= 50
    assert result.evidence == ["monthly_report.xlsx", "asset_register.docx"]


def test_company_department_and_job_title_words_do_not_create_knowhow():
    result = generate_knowhow(
        representative_documents=["accounting_firm_partner_memo.bin"],
        supporting_documents=["hospital_operations_manager_note.tmp"],
        document_families=["manufacturing_department_reference"],
    )

    assert result.knowhow_items == []
    assert result.confidence <= 30
