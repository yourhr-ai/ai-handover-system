import unittest
from types import SimpleNamespace

from src.core.business_context_engine_v2 import BusinessContextEngineV2
from src.core.work_unit_resolver import FUNCTION, WorkUnit


def rep(name, dvs=85):
    return SimpleNamespace(display_name=name, file_name=name, dvs=dvs)


def family(name, docs=None):
    docs = docs or [rep(f"{name}.docx", 70)]
    return SimpleNamespace(family_key=name, latest_doc=docs[0], family_docs=docs)


class BusinessContextEngineV2Test(unittest.TestCase):
    def setUp(self):
        self.engine = BusinessContextEngineV2()

    def test_hr_context_from_evidence(self):
        context = self.engine.build_context(
            WorkUnit(FUNCTION, "채용", 88, "test"),
            representative_documents=[
                rep("채용공고.docx", 91),
                rep("면접평가표.xlsx", 88),
                rep("입사서류체크리스트.xlsx", 84),
            ],
            document_families=[
                family("채용공고"),
                family("면접평가표"),
                family("입사서류"),
            ],
        )

        self.assertEqual("채용 운영", context.purpose_candidates[0].name)
        self.assertGreaterEqual(context.purpose_candidates[0].confidence, 80)
        self.assertIn("채용공고 작성", context.workflow_candidates[0].steps)
        self.assertIn("면접 진행", context.workflow_candidates[0].steps)
        self.assertIn("입사 처리", context.workflow_candidates[0].steps)
        self.assertIn("Excel", {candidate.name for candidate in context.tool_candidates})
        self.assertIn("Word", {candidate.name for candidate in context.tool_candidates})

    def test_sales_context_from_evidence(self):
        context = self.engine.build_context(
            WorkUnit(FUNCTION, "영업", 82, "test"),
            representative_documents=[rep("견적서.xlsx", 86), rep("제안서.pptx", 90)],
            document_families=[family("견적서"), family("제안서")],
        )

        self.assertEqual("영업 제안", context.purpose_candidates[0].name)
        self.assertIn("견적 작성", context.workflow_candidates[0].steps)
        self.assertIn("제안서 작성", context.workflow_candidates[0].steps)
        self.assertIn("PowerPoint", {candidate.name for candidate in context.tool_candidates})

    def test_finance_context_from_evidence(self):
        context = self.engine.build_context(
            WorkUnit(FUNCTION, "재무", 80, "test"),
            representative_documents=[rep("월별_정산표.xlsx", 87), rep("세금계산서_청구내역.xlsx", 84)],
            document_families=[family("정산"), family("세금계산서")],
        )

        self.assertEqual("재무 관리", context.purpose_candidates[0].name)
        self.assertIn("정산 처리", context.workflow_candidates[0].steps)
        self.assertIn("청구 처리", context.workflow_candidates[0].steps)
        self.assertEqual("Excel", context.tool_candidates[0].name)

    def test_development_context_from_evidence(self):
        context = self.engine.build_context(
            WorkUnit(FUNCTION, "개발", 86, "test"),
            representative_documents=[rep("요구사항정의서.docx", 88), rep("api_server.py", 78), rep("frontend.tsx", 76)],
            document_families=[family("요구사항"), family("API 개발")],
        )

        self.assertEqual("개발 운영", context.purpose_candidates[0].name)
        self.assertIn("요구사항 정리", context.workflow_candidates[0].steps)
        self.assertIn("개발", context.workflow_candidates[0].steps)
        self.assertIn("Python", {candidate.name for candidate in context.tool_candidates})
        self.assertIn("TypeScript", {candidate.name for candidate in context.tool_candidates})

    def test_marketing_context_from_evidence(self):
        context = self.engine.build_context(
            WorkUnit(FUNCTION, "마케팅", 83, "test"),
            representative_documents=[rep("캠페인기획안.docx", 89), rep("광고성과리포트.pptx", 82)],
            document_families=[family("캠페인"), family("광고 성과")],
        )

        self.assertEqual("마케팅 운영", context.purpose_candidates[0].name)
        self.assertIn("캠페인 기획", context.workflow_candidates[0].steps)
        self.assertIn("성과 분석", context.workflow_candidates[0].steps)

    def test_unknown_documents_stay_low_confidence_without_fake_purpose(self):
        context = self.engine.build_context(
            WorkUnit(FUNCTION, "기타", 25, "test"),
            representative_documents=[rep("random_notes.bin", 20), rep("misc_archive.tmp", 15)],
            document_families=[family("misc_archive", [rep("misc_archive.tmp", 15)])],
        )

        self.assertEqual([], context.purpose_candidates)
        self.assertEqual([], context.workflow_candidates)
        self.assertLessEqual(context.confidence, 35)
        self.assertNotIn("운영", [candidate.name for candidate in context.purpose_candidates])


if __name__ == "__main__":
    unittest.main()
