from src.core.business_context_engine_v3 import BusinessContextEngineV3, infer_business_context_v3


def test_recruiting_documents_infer_recruiting_purposes():
    result = BusinessContextEngineV3().infer(["채용공고", "면접평가표", "입사서류"])

    assert result.purpose_candidates[0] == "채용 운영"
    assert result.confidence >= 80
    assert result.evidence == ["채용공고", "면접평가표", "입사서류"]


def test_education_documents_outrank_recruiting_entry_keyword():
    result = BusinessContextEngineV3().infer(
        ["신입사원교육자료", "교육결과보고서", "교육교안", "만족도조사"]
    )

    assert result.purpose_candidates[0] == "교육 운영"
    assert "채용 운영" not in result.purpose_candidates
    assert result.confidence >= 80
    assert result.evidence == ["신입사원교육자료", "교육결과보고서", "교육교안", "만족도조사"]


def test_sales_documents_infer_customer_acquisition_and_proposal_sales():
    result = BusinessContextEngineV3().infer(["제안서", "견적서", "고객미팅메모", "제안발표자료"])

    assert result.purpose_candidates[:2] == ["신규 고객 확보", "제안 영업 수행"]
    assert result.confidence >= 80
    assert result.evidence == ["제안서", "견적서", "고객미팅메모", "제안발표자료"]


def test_admin_documents_infer_asset_and_operations_support():
    result = BusinessContextEngineV3().infer(["비품관리대장", "법인차량관리대장", "자산관리대장", "회의실예약"])

    assert result.purpose_candidates == ["사내 자산 및 운영 지원 관리"]
    assert result.confidence >= 80
    assert result.evidence == ["비품관리대장", "법인차량관리대장", "자산관리대장", "회의실예약"]


def test_performance_and_salary_documents_infer_combined_evaluation_and_compensation():
    result = BusinessContextEngineV3().infer(["성과평가가이드", "평가양식", "연봉인상안"])

    assert result.purpose_candidates[0] == "평가 및 보상 운영"
    assert result.confidence >= 80
    assert result.evidence == ["성과평가가이드", "평가양식", "연봉인상안"]


def test_hr_rules_outrank_tax_terms_in_company_names():
    result = BusinessContextEngineV3().infer(
        ["채움세무법인 인사 자문 견적서_v1.21.xlsx", "면접평가표.xlsx", "성과평가가이드.docx", "연봉인상안_2026.xlsx"]
    )

    assert result.purpose_candidates[0] == "평가 및 보상 운영"
    assert "세무 신고" not in result.purpose_candidates
    assert result.confidence >= 80


def test_finance_and_tax_documents_infer_closing_and_tax():
    result = infer_business_context_v3(["결산보고서", "재무제표", "부가세신고"])

    assert result.purpose_candidates[:2] == ["결산 관리", "세무 신고"]
    assert result.confidence >= 80
    assert result.evidence == ["결산보고서", "재무제표", "부가세신고"]


def test_unknown_documents_return_unknown_with_low_confidence():
    result = BusinessContextEngineV3().build_context(["메모", "참고자료", "random.txt"])

    assert result.purpose_candidates == ["Unknown"]
    assert result.confidence == 25
    assert result.evidence == []
