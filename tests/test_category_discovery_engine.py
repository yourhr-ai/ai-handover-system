import unittest
from types import SimpleNamespace

from src.core.category_discovery_engine import (
    AUTO_DISCOVERED,
    HYBRID,
    USER_DEFINED,
    CategoryDiscoveryEngine,
)


def doc(name, summary_text=""):
    return SimpleNamespace(display_name=name, summary_text=summary_text)


class CategoryDiscoveryEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = CategoryDiscoveryEngine()

    def test_user_category_only(self):
        result = self.engine.discover(user_job_categories="인사")

        self.assertEqual(1, len(result.categories))
        self.assertEqual("인사", result.categories[0].category_name)
        self.assertEqual(USER_DEFINED, result.categories[0].source)

    def test_file_category_only(self):
        result = self.engine.discover(
            document_summaries=[
                doc("자료/부가세신고.xlsx"),
                doc("output/결산자료.xlsx"),
            ]
        )

        category = self._category(result, "회계")
        self.assertEqual(AUTO_DISCOVERED, category.source)
        self.assertGreaterEqual(category.confidence, 70)

    def test_mixed_category(self):
        result = self.engine.discover(
            user_job_categories="인사",
            document_summaries=[
                doc("인사/채용공고.docx"),
                doc("인사/면접평가표.xlsx"),
            ],
        )

        category = self._category(result, "인사")
        self.assertEqual(HYBRID, category.source)
        self.assertIn("채용공고", category.evidence_keywords)

    def test_no_category_input_auto_discovers(self):
        result = self.engine.discover(
            document_summaries=[
                doc("법무/NDA_계약서.docx"),
                doc("교육/신입사원 교육자료.pptx"),
            ]
        )

        names = {category.category_name for category in result.categories}
        self.assertIn("법무", names)
        self.assertIn("교육", names)

    def test_container_folder_exclusion(self):
        result = self.engine.discover(
            user_job_categories="자료\nmisc",
            document_summaries=[
                doc("자료/문서/output.txt"),
                doc("output/result.xlsx"),
            ],
        )

        names = {category.category_name for category in result.categories}
        self.assertNotIn("자료", names)
        self.assertNotIn("misc", names)
        self.assertFalse(result.categories)

    def _category(self, result, name):
        for category in result.categories:
            if category.category_name == name:
                return category
        self.fail(f"category not found: {name}")


if __name__ == "__main__":
    unittest.main()
