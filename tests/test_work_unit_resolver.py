import unittest
from types import SimpleNamespace

from src.core.work_unit_resolver import (
    CUSTOMER,
    DOCUMENT_SET,
    FUNCTION,
    PROJECT,
    WorkUnitResolver,
)


def doc(name):
    return SimpleNamespace(display_name=name)


class WorkUnitResolverTest(unittest.TestCase):
    def setUp(self):
        self.resolver = WorkUnitResolver()

    def test_container_names_are_rejected(self):
        clusters = [
            SimpleNamespace(cluster_key="output", documents=[doc("output/채용 계획서.docx")]),
            SimpleNamespace(cluster_key="자료", documents=[doc("자료/계약서 검토.docx")]),
        ]

        result = self.resolver.resolve(work_clusters=clusters)
        names = {unit.unit_name for unit in result.work_units}

        self.assertNotIn("output", names)
        self.assertNotIn("자료", names)
        self.assertTrue(result.warnings)

    def test_project_has_highest_priority(self):
        clusters = [
            SimpleNamespace(cluster_key="채용", documents=[doc("채용 계획서.docx")]),
            SimpleNamespace(cluster_key="ERP 구축", documents=[doc("ERP 구축 제안서.docx")]),
        ]

        result = self.resolver.resolve(work_clusters=clusters)

        self.assertEqual(PROJECT, result.work_units[0].unit_type)
        self.assertEqual("ERP 구축", result.work_units[0].unit_name)

    def test_function_detected_from_shared_keywords(self):
        clusters = [
            SimpleNamespace(cluster_key="결과물", documents=[doc("결과물/채용 계획서.docx")]),
        ]

        result = self.resolver.resolve(work_clusters=clusters)

        self.assertTrue(any(unit.unit_type == FUNCTION and unit.unit_name == "채용" for unit in result.work_units))

    def test_customer_detected_when_no_project_or_function(self):
        clusters = [
            SimpleNamespace(cluster_key="동우국제", documents=[doc("동우국제/회의록.docx")]),
        ]

        result = self.resolver.resolve(work_clusters=clusters)

        self.assertEqual(CUSTOMER, result.work_units[0].unit_type)
        self.assertEqual("동우국제", result.work_units[0].unit_name)

    def test_document_set_fallback_from_family(self):
        family = SimpleNamespace(
            family_key="평가기획안",
            family_docs=[doc("자료/평가기획안_v1.docx"), doc("자료/평가기획안_v2.docx")],
        )

        result = self.resolver.resolve(document_families={"자료": [family]})

        self.assertEqual(DOCUMENT_SET, result.work_units[0].unit_type)
        self.assertEqual("평가기획안 묶음", result.work_units[0].unit_name)


if __name__ == "__main__":
    unittest.main()
