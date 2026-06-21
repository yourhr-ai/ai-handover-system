import unittest
from types import SimpleNamespace

from src.core.work_unit_normalizer import WorkUnitNormalizer
from src.core.work_unit_resolver import FUNCTION, PROJECT, WorkUnit, WorkUnitResolverResult


def doc(name):
    return SimpleNamespace(display_name=name)


def cluster(name, docs=None, keywords=None):
    return SimpleNamespace(
        cluster_key=name,
        documents=[doc(item) for item in (docs or [])],
        keywords=keywords or [],
    )


class WorkUnitNormalizerTest(unittest.TestCase):
    def setUp(self):
        self.normalizer = WorkUnitNormalizer()

    def test_result_container_with_hr_and_education_keeps_real_units(self):
        resolver_result = WorkUnitResolverResult(
            work_units=[
                WorkUnit(FUNCTION, "결과물", 80, "folder"),
                WorkUnit(FUNCTION, "인사", 90, "shared keywords"),
                WorkUnit(FUNCTION, "교육", 88, "shared keywords"),
            ]
        )

        result = self.normalizer.normalize_work_units(resolver_result)
        names = {unit.unit_name for unit in result.work_units}

        self.assertEqual({"인사", "교육"}, names)
        self.assertIn("결과물", result.removed_containers)

    def test_customer_folder_only_promotes_customer_management_low_confidence(self):
        resolver_result = WorkUnitResolverResult(
            work_units=[WorkUnit(FUNCTION, "고객사", 70, "folder")]
        )

        result = self.normalizer.normalize_work_units(resolver_result)

        self.assertEqual("고객관리", result.work_units[0].unit_name)
        self.assertLess(result.work_units[0].confidence, 60)
        self.assertIn("고객관리", result.retained_low_confidence)

    def test_meeting_folder_only_does_not_create_independent_work(self):
        resolver_result = WorkUnitResolverResult(
            work_units=[WorkUnit(FUNCTION, "회의록", 70, "folder")]
        )

        result = self.normalizer.normalize_work_units(resolver_result)

        self.assertFalse(result.work_units)
        self.assertIn("회의록", result.removed_containers)

    def test_real_units_are_kept(self):
        resolver_result = WorkUnitResolverResult(
            work_units=[
                WorkUnit(FUNCTION, "인사", 91, "shared keywords"),
                WorkUnit(FUNCTION, "회계", 90, "shared keywords"),
                WorkUnit(FUNCTION, "총무", 89, "shared keywords"),
            ]
        )

        result = self.normalizer.normalize_work_units(resolver_result)
        names = [unit.unit_name for unit in result.work_units]

        self.assertEqual(["인사", "회계", "총무"], names)

    def test_mixed_mode_allows_project_and_functions(self):
        resolver_result = WorkUnitResolverResult(
            work_units=[
                WorkUnit(PROJECT, "중소기업 인사컨설팅", 95, "project signals"),
                WorkUnit(FUNCTION, "결과물", 70, "folder"),
            ]
        )
        clusters = [
            cluster("결과물", ["결과물/성과평가가이드.docx", "결과물/결산보고서.xlsx"]),
        ]

        result = self.normalizer.normalize_work_units(resolver_result, work_clusters=clusters)
        names = {unit.unit_name for unit in result.work_units}

        self.assertIn("중소기업 인사컨설팅", names)
        self.assertIn("인사", names)
        self.assertIn("회계", names)


if __name__ == "__main__":
    unittest.main()
