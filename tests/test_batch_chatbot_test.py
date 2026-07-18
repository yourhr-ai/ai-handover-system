import tempfile
import unittest
from pathlib import Path

from scripts.batch_chatbot_test import (
    TestResult,
    _credits_used,
    _flatten_questions,
    _has_source_reference,
    _write_report,
)


class BatchChatbotTestScriptTests(unittest.TestCase):
    def test_question_suite_contains_exactly_one_hundred_questions(self):
        self.assertEqual(len(_flatten_questions(None)), 100)

    def test_credits_use_balance_difference(self):
        self.assertEqual(
            _credits_used({"balance": 1000}, {"balance": 926}),
            74,
        )

    def test_source_reference_accepts_structured_or_in_answer_citation(self):
        self.assertTrue(_has_source_reference("답변", ["자료.xlsx"]))
        self.assertTrue(_has_source_reference("근거 자료는 조직진단.docx입니다.", []))
        self.assertFalse(_has_source_reference("제공된 자료에서 확인되지 않습니다.", []))

    def test_report_escapes_table_text_and_keeps_full_answer(self):
        result = TestResult(
            category="카테고리 1: 업무 개요 파악",
            number=1,
            question="A | B?",
            answer="첫 줄\n둘째 줄",
            elapsed_seconds=1.25,
            credits_used=74,
            has_sources=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "report.md"
            _write_report(path, Path("package"), [result], 1)
            text = path.read_text(encoding="utf-8")

        self.assertIn("A &#124; B?", text)
        self.assertIn("첫 줄<br>둘째 줄", text)
        self.assertIn("| 1.25 | 74 | 예 |", text)


if __name__ == "__main__":
    unittest.main()
