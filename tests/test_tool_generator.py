from types import SimpleNamespace

from src.core.tool_generator import ToolGenerator, generate_tools


def test_office_tools_from_file_extensions():
    result = generate_tools(
        representative_documents=["production_plan.xlsx", "site_report.docx"],
        supporting_documents=["customer_pitch.pptx"],
    )

    assert result.tools[:3] == ["Excel", "Word", "PowerPoint"]
    assert result.confidence >= 50
    assert result.evidence == ["production_plan.xlsx", "site_report.docx", "customer_pitch.pptx"]


def test_design_development_and_project_tools_from_document_names():
    result = ToolGenerator().generate(
        representative_documents=["mobile_ui_design.fig", "github_pull_request_notes.md"],
        supporting_documents=["jira_sprint_backlog.csv", "team_notion_workspace.txt"],
    )

    assert "Figma" in result.tools
    assert "GitHub" in result.tools
    assert "Jira" in result.tools
    assert "Notion" in result.tools
    assert "Excel" in result.tools
    assert result.confidence >= 50


def test_family_names_and_family_documents_are_evidence():
    family = SimpleNamespace(
        family_key="repository_backend_api",
        latest_doc=SimpleNamespace(display_name="wireframe_review.docx"),
        family_docs=[SimpleNamespace(display_name="release_backlog.xlsx")],
    )

    result = generate_tools(document_families=[family])

    assert "GitHub" in result.tools
    assert "Figma" in result.tools
    assert "Word" in result.tools
    assert "Jira" in result.tools
    assert "Excel" in result.tools
    assert result.evidence[:3] == [
        "repository_backend_api",
        "wireframe_review.docx",
        "release_backlog.xlsx",
    ]


def test_unknown_evidence_returns_no_tools_with_low_confidence():
    result = generate_tools(
        representative_documents=["random_notes.bin"],
        supporting_documents=["misc_archive.tmp"],
        document_families=["reference_material"],
    )

    assert result.tools == []
    assert result.confidence <= 30
    assert result.evidence == []


def test_company_department_and_job_title_words_do_not_create_tools():
    result = generate_tools(
        representative_documents=["accounting_firm_partner_memo.bin"],
        supporting_documents=["hospital_operations_manager_note.tmp"],
        document_families=["manufacturing_department_reference"],
    )

    assert result.tools == []
    assert result.confidence <= 30
