import unittest
from types import SimpleNamespace

from src.core.work_cluster_engine import (
    MIXED_MODE,
    PROJECT_MODE,
    WORK_CLUSTER_MODE,
    WorkClusterEngine,
)


def doc(display_name, summary_text="", score=80, modified_dt="2026-06-20"):
    return SimpleNamespace(
        display_name=display_name,
        score=score,
        modified_dt=modified_dt,
        summary_text=summary_text,
    )


class WorkClusterEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = WorkClusterEngine()
        self.banned_names = {
            "result", "results", "output", "outputs", "document", "documents",
            "자료", "문서", "기타", "misc",
        }

    def test_domain_clusters_do_not_use_container_names(self):
        docs = [
            doc("output/HR/채용 계획서.docx", "HR 채용 운영"),
            doc("documents/Development/쇼핑몰 구축 설계서.docx", "웹사이트 개발 구축"),
            doc("자료/Sales/ERP 제안서.docx", "영업 제안"),
            doc("misc/Marketing/마케팅 캠페인 기획안.docx", "마케팅 캠페인"),
            doc("result/Legal/계약서 검토.docx", "법무 계약 검토"),
            doc("outputs/Finance/재무 결산표.xlsx", "회계 재무 결산"),
            doc("문서/Education/신입사원 교육안.docx", "교육 온보딩"),
        ]

        clusters = self.engine.group_work_clusters(docs)
        names = {cluster.cluster_key.lower() for cluster in clusters}

        self.assertFalse(names & self.banned_names)
        self.assertTrue(any("채용" in cluster.cluster_key for cluster in clusters))
        self.assertTrue(any("계약서 검토" in cluster.cluster_key for cluster in clusters))
        self.assertTrue(any("재무 결산" in cluster.cluster_key for cluster in clusters))
        self.assertTrue(any("신입사원 교육" in cluster.cluster_key for cluster in clusters))

    def test_project_mode_detection(self):
        docs = [
            doc("동우국제 ERP 제안/ERP 제안서.docx", "동우국제 ERP 구축 제안"),
            doc("동우국제 ERP 제안/구축 일정표.xlsx", "ERP 구현 일정"),
            doc("동우국제 ERP 제안/완료보고서.docx", "구축 완료보고"),
        ]

        result = self.engine.detect_work_unit_mode(docs)

        self.assertEqual(PROJECT_MODE, result.mode)

    def test_work_cluster_mode_detection(self):
        docs = [
            doc("output/채용 계획서.docx", "채용 운영"),
            doc("documents/계약서 검토.docx", "법무 계약 검토"),
            doc("자료/재무 결산표.xlsx", "회계 결산"),
            doc("misc/신입사원 교육안.docx", "교육 운영"),
        ]

        result = self.engine.detect_work_unit_mode(docs)

        self.assertEqual(WORK_CLUSTER_MODE, result.mode)

    def test_mixed_mode_detection(self):
        docs = [
            doc("동우국제 ERP 제안/ERP 제안서.docx", "동우국제 ERP 구축 제안"),
            doc("동우국제 ERP 제안/구축 일정표.xlsx", "ERP 구현 일정"),
            doc("동우국제 ERP 제안/완료보고서.docx", "구축 완료보고"),
            doc("output/채용 계획서.docx", "채용 운영"),
            doc("documents/계약서 검토.docx", "법무 계약 검토"),
            doc("자료/재무 결산표.xlsx", "회계 결산"),
            doc("misc/신입사원 교육안.docx", "교육 운영"),
        ]

        result = self.engine.detect_work_unit_mode(docs)

        self.assertEqual(MIXED_MODE, result.mode)


if __name__ == "__main__":
    unittest.main()
