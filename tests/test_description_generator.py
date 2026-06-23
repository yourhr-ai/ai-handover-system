from types import SimpleNamespace

from src.core.description_generator import (
    UNKNOWN_DESCRIPTION,
    DescriptionGenerator,
    generate_description,
)


def test_recruiting_description_from_document_evidence():
    result = generate_description(
        purpose_candidates=["채용 운영"],
        representative_documents=["채용공고.docx", "면접평가표.xlsx"],
        supporting_documents=["입사서류.zip"],
        document_families=[],
    )

    assert result.description == "필요 인력 채용을 위해 채용공고를 운영하고 면접 및 입사 절차를 관리한다."
    assert result.confidence >= 70
    assert result.evidence == ["채용공고.docx", "면접평가표.xlsx", "입사서류.zip"]


def test_evaluation_and_compensation_description_from_mixed_evidence():
    result = DescriptionGenerator().generate(
        representative_documents=["성과평가가이드.pdf", "평가양식.xlsx"],
        supporting_documents=["연봉인상안.xlsx"],
    )

    assert result.description == "직원 성과를 평가하고 보상 수준을 검토하기 위한 평가 및 보상 체계를 운영한다."
    assert result.confidence >= 70


def test_sales_description_from_representative_supporting_and_family_objects():
    family = SimpleNamespace(
        latest_doc=SimpleNamespace(display_name="제안서_v2.pptx"),
        family_docs=[
            SimpleNamespace(display_name="제안서_v2.pptx"),
            SimpleNamespace(display_name="제안서_v1.pptx"),
        ],
    )

    result = generate_description(
        representative_documents=[SimpleNamespace(display_name="견적서.xlsx")],
        supporting_documents=[SimpleNamespace(file_name="고객미팅메모.docx")],
        document_families=[family],
    )

    assert result.description == "잠재 고객에게 서비스를 제안하고 계약 체결을 위한 영업 활동을 수행한다."
    assert result.confidence >= 70
    assert result.evidence[:3] == ["견적서.xlsx", "고객미팅메모.docx", "제안서_v2.pptx"]


def test_finance_and_tax_description_from_document_evidence():
    result = generate_description(
        representative_documents=["결산보고서.docx", "재무제표.xlsx"],
        supporting_documents=["부가세신고.xlsx"],
    )

    assert result.description == "재무 현황을 정리하고 세무 신고를 수행하여 회사의 재무 상태를 관리한다."
    assert result.confidence >= 70


def test_training_description_from_document_evidence():
    result = generate_description(
        representative_documents=["교육결과보고서.docx", "신입사원교육자료.pptx"],
        supporting_documents=["만족도조사.xlsx"],
    )

    assert result.description == "신규 입사자의 조직 적응과 업무 이해도 향상을 위한 교육을 운영한다."
    assert result.confidence >= 70


def test_unknown_evidence_returns_low_confidence_instead_of_guessing():
    result = generate_description(
        purpose_candidates=["영업 운영"],
        representative_documents=["random_notes.bin"],
        supporting_documents=["misc_archive.tmp"],
        document_families=["참고자료"],
    )

    assert result.description == UNKNOWN_DESCRIPTION
    assert result.confidence <= 30


def test_single_weak_document_does_not_overstate_description():
    result = generate_description(
        representative_documents=["고객메모.txt"],
        supporting_documents=[],
        document_families=[],
    )

    assert result.description == UNKNOWN_DESCRIPTION
    assert result.confidence <= 30
    assert result.evidence == ["고객메모.txt"]
