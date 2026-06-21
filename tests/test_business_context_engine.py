import unittest
from types import SimpleNamespace

from src.core.business_context_engine import BusinessContextEngine
from src.core.work_status_engine import COMPLETED, WAITING_APPROVAL, WAITING_REVIEW


class BusinessContextEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = BusinessContextEngine()

    def test_objective_from_project_name_and_review_stage(self):
        project = SimpleNamespace(
            project_key="동우국제",
            project_name="동우국제 평가제도",
            client_name="동우국제",
            key_outputs="평가기획안",
            stakeholders="[정보 부족]",
            project_purpose="[정보 부족]",
            current_status="[정보 부족]",
            risks="[정보 부족]",
            successor_notes="[정보 부족]",
        )
        status = SimpleNamespace(
            work_unit_name="동우국제 평가제도",
            status=WAITING_REVIEW,
            completed_items=["평가기획안 작성"],
            pending_items=["고객 검토 결과 확인"],
            next_actions=["고객 피드백 수집"],
            risks=["최종본 부재"],
            confidence=82,
        )
        action = SimpleNamespace(
            priority_tasks=["평가기획안 검토"],
            required_documents=["평가기획안_v2"],
            risks=["old 문서 존재"],
            first_week_actions=["Day1 대표문서 검토"],
            stakeholders=["팀장"],
        )

        context = self.engine.build_context(
            project,
            representative_documents=["평가기획안_v2.docx"],
            action_plan=action,
            work_status=status,
        )

        self.assertEqual("동우국제 평가제도", context.objective)
        self.assertEqual("Review", context.current_stage)
        self.assertIn("동우국제", context.stakeholders)
        self.assertIn("old 문서 존재", context.risks)

    def test_approval_stage_from_work_status(self):
        project = SimpleNamespace(project_key="계약", project_name="[정보 부족]", client_name="[정보 부족]", key_outputs="계약서")
        status = SimpleNamespace(
            work_unit_name="계약검토",
            status=WAITING_APPROVAL,
            completed_items=["계약서 작성"],
            pending_items=["승인 및 결재 결과 확인"],
            next_actions=[],
            risks=[],
            confidence=75,
        )

        context = self.engine.build_context(project, ["계약서_승인대기.docx"], None, status)

        self.assertEqual("Approval", context.current_stage)
        self.assertIn("승인 및 결재 결과 확인", context.pending_items)

    def test_completed_stage_and_final_document_completed_item(self):
        project = SimpleNamespace(project_key="ERP", project_name="[정보 부족]", client_name="동우국제", key_outputs="")
        status = SimpleNamespace(
            work_unit_name="ERP 구축",
            status=COMPLETED,
            completed_items=[],
            pending_items=[],
            next_actions=[],
            risks=[],
            confidence=90,
        )

        context = self.engine.build_context(project, ["ERP_제안서_final.docx"], None, status)

        self.assertEqual("Completed", context.current_stage)
        self.assertIn("ERP 제안서 완료", context.completed_items)


if __name__ == "__main__":
    unittest.main()
