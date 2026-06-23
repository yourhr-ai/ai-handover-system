from src.core.business_context_engine_v3 import BusinessContextEngineV3, infer_business_context_v3


def test_recruiting_documents_infer_recruiting_purposes():
    result = BusinessContextEngineV3().infer(["채용공고", "면접평가표", "입사서류"])

    assert result.purpose_candidates[:2] == ["인력 확보", "채용 운영"]
    assert result.confidence >= 80
    assert result.evidence == ["채용공고", "면접평가표", "입사서류"]


def test_performance_and_salary_documents_infer_evaluation_and_compensation():
    result = BusinessContextEngineV3().infer(["성과평가가이드", "평가양식", "연봉인상안"])

    assert result.purpose_candidates[:2] == ["평가 운영", "보상 운영"]
    assert result.confidence >= 80
    assert result.evidence == ["성과평가가이드", "평가양식", "연봉인상안"]


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
