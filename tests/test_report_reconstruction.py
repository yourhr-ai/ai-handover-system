import unittest
from types import SimpleNamespace

from src.core.report_reconstruction import reconstruct_report_units
from src.core.work_unit_resolver import WorkUnit, WorkUnitResolverResult
from src.core.work_unit_normalizer import WorkUnitNormalizerResult


def ps(key, files=None):
    return SimpleNamespace(
        project_key=key,
        project_name=key,
        key_outputs="",
        related_files=files or [],
        representative_docs=[],
    )


class ReportReconstructionTest(unittest.TestCase):
    def test_containers_are_suppressed(self):
        resolver = WorkUnitResolverResult(
            work_units=[
                WorkUnit("FUNCTION", "고객사", 70, "folder"),
                WorkUnit("FUNCTION", "결과물", 70, "folder"),
                WorkUnit("FUNCTION", "회의록", 70, "folder"),
            ]
        )
        normalizer = WorkUnitNormalizerResult(
            work_units=[WorkUnit("FUNCTION", "인사", 85, "promoted")],
            removed_containers=["고객사", "결과물", "회의록"],
        )

        result = reconstruct_report_units(resolver, normalizer, [ps("결과물")], [])

        self.assertEqual(["인사"], [unit.unit_name for unit in result.normalized_units])
        self.assertIn("고객사", result.suppressed_containers)
        self.assertIn("결과물", result.suppressed_containers)
        self.assertIn("회의록", result.suppressed_containers)

    def test_work_count_from_normalized_units(self):
        normalizer = WorkUnitNormalizerResult(
            work_units=[
                WorkUnit("FUNCTION", "인사", 90, "keywords"),
                WorkUnit("FUNCTION", "교육", 88, "keywords"),
                WorkUnit("FUNCTION", "회계", 87, "keywords"),
            ]
        )

        result = reconstruct_report_units(WorkUnitResolverResult(), normalizer, [], [])

        self.assertEqual(3, len(result.normalized_units))

    def test_auto_discovered_work_is_output(self):
        normalizer = WorkUnitNormalizerResult(
            work_units=[WorkUnit("FUNCTION", "마케팅", 82, "auto")]
        )

        result = reconstruct_report_units(WorkUnitResolverResult(), normalizer, [], [])

        self.assertEqual("마케팅", result.normalized_units[0].unit_name)

    def test_fallback_allowed_when_no_work_units(self):
        result = reconstruct_report_units(
            WorkUnitResolverResult(),
            WorkUnitNormalizerResult(),
            [ps("자료")],
            [],
        )

        self.assertTrue(result.fallback_used)
        self.assertEqual(1, len(result.normalized_units))

    def test_mixed_mode_keeps_project_and_work(self):
        normalizer = WorkUnitNormalizerResult(
            work_units=[
                WorkUnit("PROJECT", "중소기업 인사컨설팅", 95, "project"),
                WorkUnit("FUNCTION", "인사", 90, "keywords"),
                WorkUnit("FUNCTION", "회계", 88, "keywords"),
            ]
        )

        result = reconstruct_report_units(WorkUnitResolverResult(), normalizer, [], [])
        names = [unit.unit_name for unit in result.normalized_units]

        self.assertIn("중소기업 인사컨설팅", names)
        self.assertIn("인사", names)
        self.assertIn("회계", names)


if __name__ == "__main__":
    unittest.main()
