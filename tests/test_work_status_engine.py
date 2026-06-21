import unittest
from types import SimpleNamespace

from src.core.work_status_engine import (
    COMPLETED,
    IN_PROGRESS,
    WAITING_APPROVAL,
    WAITING_REVIEW,
    WorkStatusEngine,
)


def doc(display_name, summary_text="", score=80, modified_dt="2026-06-20"):
    return SimpleNamespace(
        display_name=display_name,
        score=score,
        modified_dt=modified_dt,
        summary_text=summary_text,
    )


class WorkStatusEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = WorkStatusEngine()

    def test_draft_only_is_in_progress(self):
        status = self.engine.infer_work_status(
            "평가제도",
            representative_docs=[doc("평가기획안_draft.docx", "초안 작성 중")],
        )

        self.assertEqual(IN_PROGRESS, status.status)
        self.assertIn("최종본 부재", status.risks)

    def test_final_document_is_completed(self):
        status = self.engine.infer_work_status(
            "평가제도",
            representative_docs=[doc("평가기획안_final.docx", "최종 확정")],
        )

        self.assertEqual(COMPLETED, status.status)
        self.assertIn("평가기획안 작성", status.completed_items)

    def test_review_keywords_waiting_review(self):
        status = self.engine.infer_work_status(
            "평가제도",
            representative_docs=[doc("평가기획안_v2.docx", "고객 검토 feedback 반영 필요")],
        )

        self.assertEqual(WAITING_REVIEW, status.status)
        self.assertIn("고객 피드백 수집", status.next_actions)

    def test_approval_keywords_waiting_approval(self):
        status = self.engine.infer_work_status(
            "계약서 검토",
            representative_docs=[doc("계약서_승인대기.docx", "결재 approval 필요")],
        )

        self.assertEqual(WAITING_APPROVAL, status.status)
        self.assertIn("승인권자 확인", status.next_actions)


if __name__ == "__main__":
    unittest.main()
