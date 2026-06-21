import unittest
from types import SimpleNamespace

from src.core.analysis_quality_validator import AnalysisQualityValidator
from src.core.work_cluster_engine import MIXED_MODE
from src.core.work_status_engine import COMPLETED, UNKNOWN


def doc(name):
    return SimpleNamespace(display_name=name)


class AnalysisQualityValidatorTest(unittest.TestCase):
    def setUp(self):
        self.validator = AnalysisQualityValidator()

    def test_quality_report_scores_good_results(self):
        family = SimpleNamespace(
            family_key="평가기획안",
            latest_doc=doc("평가기획안_final.docx"),
            family_docs=[doc("평가기획안_v1.docx"), doc("평가기획안_final.docx")],
        )
        cluster = SimpleNamespace(
            cluster_key="동우국제 평가제도",
            documents=[doc("평가기획안_final.docx"), doc("성과평가 가이드.docx")],
        )
        rep_result = SimpleNamespace(
            representative_docs=[doc("평가기획안_final.docx")],
            supporting_docs=[doc("성과평가 가이드.docx")],
            reference_docs=[doc("평가기획안_v1.docx")],
        )
        status = SimpleNamespace(status=COMPLETED, confidence=82, risks=["old 문서 존재"])
        detection = SimpleNamespace(mode=MIXED_MODE, project_score=40, work_cluster_score=45)

        report = self.validator.validate(
            document_families={"동우국제": [family]},
            work_clusters=[cluster],
            representative_results={"동우국제": rep_result},
            work_statuses=[status],
            work_unit_detection=detection,
        )

        self.assertGreaterEqual(report.overall_score, 80)
        self.assertEqual(0, report.work_clusters.metrics["container_leakage_count"])

    def test_quality_report_warns_bad_cluster_and_unknown_status(self):
        cluster = SimpleNamespace(cluster_key="output", documents=[doc("output/a.docx")])
        rep_result = SimpleNamespace(
            representative_docs=[],
            supporting_docs=[],
            reference_docs=[doc("기획안_final.docx")],
        )
        status = SimpleNamespace(status=UNKNOWN, confidence=35, risks=[])
        detection = SimpleNamespace(mode="BAD_MODE", project_score=0, work_cluster_score=0)

        report = self.validator.validate(
            document_families=[],
            work_clusters=[cluster],
            representative_results={"기타": rep_result},
            work_statuses=[status],
            work_unit_detection=detection,
        )

        self.assertLess(report.overall_score, 60)
        self.assertIn("container folder selected as cluster", report.warnings)
        self.assertIn("no representative document", report.warnings)
        self.assertTrue(any("unknown status" in warning for warning in report.warnings))


if __name__ == "__main__":
    unittest.main()
