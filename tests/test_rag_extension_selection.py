import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.license import HANDOVER_PORTAL_URL
from app.services.analysis_result import AnalysisResult, AnalyzedFile
from app.services.rag_package_builder import (
    RAG_TEXT_EXTRACTION_EXTENSIONS,
    _build_checkpoint_signature,
    _build_file_chunk_records_with_timeout,
    build_and_save_rag_package,
    estimate_rag_package_cost,
    filter_files_by_selected_extensions,
    get_rag_package_candidate_files,
)


def _analysis(root: Path, names: list[str]) -> AnalysisResult:
    files = []
    for index, name in enumerate(names):
        path = root / name
        files.append(AnalyzedFile(
            file_name=name,
            relative_path=f"{root.name}/{name}",
            modified_at="2026-07-14 00:00:00",
            modified_timestamp=float(index + 1),
            size_bytes=path.stat().st_size,
        ))
    return AnalysisResult(
        root_folder_path=str(root), total_folder_count=0,
        total_file_count=len(files), total_size_bytes=sum(f.size_bytes for f in files),
        modified_within_7_days_count=0, modified_within_30_days_count=0,
        modified_within_90_days_count=0, error_count=0,
        child_folder_summaries=[], all_files=files,
    )


class RagExtensionSelectionTests(unittest.TestCase):
    def test_processing_time_levels_use_weighted_file_count_boundaries(self):
        from app.ui.main_window import FileContentExtensionDialog

        level = FileContentExtensionDialog.processing_time_level
        self.assertEqual(level(250), "낮음")
        self.assertEqual(level(251), "중간")
        self.assertEqual(level(750), "중간")
        self.assertEqual(level(751), "다소 높음")
        self.assertEqual(level(1500), "다소 높음")
        self.assertEqual(level(1501), "매우 높음")
        self.assertEqual(level(1000 * 1), "다소 높음")
        self.assertEqual(level(100 * 12), "다소 높음")

    def test_processing_time_weights_match_format_costs(self):
        from app.ui.main_window import FileContentExtensionDialog

        weights = {
            extensions: weight
            for _label, extensions, weight, _guide_label
            in FileContentExtensionDialog.EXTENSION_GROUPS
        }
        self.assertEqual(weights[(".txt", ".md")], 1)
        self.assertEqual(weights[(".docx",)], 3)
        self.assertEqual(weights[(".pptx", ".ppt")], 3)
        self.assertEqual(weights[(".hwp", ".hwpx")], 8)
        self.assertEqual(weights[(".xlsx", ".xls")], 12)

    def test_dialog_shows_badges_and_merged_long_processing_notice(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QLabel
        from app.ui.main_window import FileContentExtensionDialog

        application = QApplication.instance()
        if application is not None and not isinstance(application, QApplication):
            self.skipTest("A QCoreApplication from another test already owns the process")
        app = application or QApplication([])
        files = [
            AnalyzedFile(f"excel-{index}.xlsx", "", "", 0, 0)
            for index in range(126)
        ] + [
            AnalyzedFile(f"hwp-{index}.hwp", "", "", 0, 0)
            for index in range(94)
        ] + [
            AnalyzedFile(f"text-{index}.txt", "", "", 0, 0)
            for index in range(10)
        ]
        with patch("app.ui.main_window.load_saved_license_code", return_value=None):
            dialog = FileContentExtensionDialog(files)

        self.assertEqual(
            dialog.findChild(QLabel, "processingTimeBadge_xlsx").text(),
            "예상 시간: 매우 높음",
        )
        self.assertEqual(
            dialog.findChild(QLabel, "processingTimeBadge_hwp").text(),
            "예상 시간: 다소 높음",
        )
        # The notice is now the second line of the same estimated-time label
        # (single container, same style) instead of a separate widget.
        self.assertIsNone(dialog.findChild(QLabel, "longProcessingNotice"))
        summary_label = dialog.findChild(QLabel, "estimatedProcessingTimeLabel")
        self.assertIn(
            "※ [문서 외 파일(동영상, 이미지, PDF, 캐드, 포토샵 등), 선택 용량 초과 파일]은 "
            "파일명/경로/수정일만 포함.",
            summary_label.text(),
        )
        # Line 2 only: 9.5pt, normal weight, desaturated pale blue — line 1
        # keeps whatever bold/13px/status-color style SUMMARY_STYLE_* set.
        self.assertEqual(summary_label.textFormat(), Qt.TextFormat.RichText)
        self.assertRegex(
            summary_label.text(),
            r'<span style="font-size: 9\.5pt; font-weight: normal; color: #7B9DC7;">'
            r"※ \[문서 외 파일.+파일명/경로/수정일만 포함\.</span>$",
        )
        self.assertIn("font-weight: 700", summary_label.styleSheet())
        self.assertTrue(
            all(combo.currentText() == "1메가" for combo, _ in dialog._extension_limit_combos)
        )

        excel_combo = next(
            combo
            for combo, extensions in dialog._extension_limit_combos
            if ".xlsx" in extensions
        )
        excel_combo.setCurrentText("안함")
        self.assertNotIn(".xlsx", dialog.selected_extensions())
        self.assertIn(".hwp", dialog.selected_extensions())
        dialog.close()
        app.processEvents()

    def test_dialog_shows_long_processing_notice_when_all_levels_are_low(self):
        from PySide6.QtWidgets import QApplication, QLabel
        from app.ui.main_window import FileContentExtensionDialog

        application = QApplication.instance()
        if application is not None and not isinstance(application, QApplication):
            self.skipTest("A QCoreApplication from another test already owns the process")
        app = application or QApplication([])
        files = [
            AnalyzedFile(f"sample{extension}", "", "", 0, 0)
            for extension in RAG_TEXT_EXTRACTION_EXTENSIONS
        ]
        with patch("app.ui.main_window.load_saved_license_code", return_value=None):
            dialog = FileContentExtensionDialog(files)

        self.assertIsNone(dialog.findChild(QLabel, "longProcessingNotice"))
        self.assertIn(
            "※ [문서 외 파일(동영상, 이미지, PDF, 캐드, 포토샵 등), 선택 용량 초과 파일]은 "
            "파일명/경로/수정일만 포함.",
            dialog.findChild(QLabel, "estimatedProcessingTimeLabel").text(),
        )
        dialog.close()
        app.processEvents()

    def test_dialog_size_limit_options_and_live_target_count(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel
        from app.ui.main_window import FileContentExtensionDialog

        application = QApplication.instance()
        if application is not None and not isinstance(application, QApplication):
            self.skipTest("A QCoreApplication from another test already owns the process")
        app = application or QApplication([])
        mebibyte = 1024 * 1024
        files = [
            AnalyzedFile("small.txt", "", "", 0, mebibyte // 2),
            AnalyzedFile("medium.docx", "", "", 0, 2 * mebibyte),
            AnalyzedFile("large.xlsx", "", "", 0, 7 * mebibyte),
            AnalyzedFile("huge.hwp", "", "", 0, 35 * mebibyte),
            AnalyzedFile("ignored.pdf", "", "", 0, 1),
        ]
        with patch("app.ui.main_window.load_saved_license_code", return_value=None):
            dialog = FileContentExtensionDialog(files)
        estimate_label = dialog.findChild(QLabel, "estimatedProcessingTimeLabel")
        benefit_label = dialog.findChild(QLabel, "packageBenefitLabel")
        combos = dialog.findChildren(QComboBox)
        self.assertFalse(dialog.findChildren(QCheckBox))
        self.assertTrue(combos)
        for combo in combos:
            self.assertEqual(
                [combo.itemText(index) for index in range(combo.count())],
                ["안함", "1메가", "5메가", "10메가", "30메가", "제한없음"],
            )
            self.assertEqual(combo.currentText(), "1메가")
        self.assertIn("1개 파일", estimate_label.text())
        self.assertEqual(estimate_label.textFormat(), Qt.TextFormat.RichText)
        self.assertRegex(
            estimate_label.text(),
            r"^1개 파일\(처리 용량 : \d+\.\dGB\) → 패키지 생성\(약 .+ 소요\) \| [^<]+<br>"
            r'<span style="[^"]*">※ \[문서 외 파일.+파일명/경로/수정일만 포함\.</span>$',
        )
        self.assertEqual(
            benefit_label.text(),
            "챗봇을 사용하려면 '인수인계패키지 생성' 필수. 다른 사람에게 물어보는걸 87% 줄이세요.",
        )
        self.assertIn("#7C3AED", benefit_label.styleSheet())
        self.assertRegex(
            estimate_label.text(),
            r"약 (10분|15~20분|30분|1시간|\d+시간) 소요\) \| [^<]+<br>"
            r'<span style="[^"]*">※ \[문서 외 파일.+파일명/경로/수정일만 포함\.</span>$',
        )
        # No saved license -> the quota lookup can't run, so the dialog must
        # fail closed: show the failure state and keep [확인] disabled.
        self.assertIn("자료 처리 확인 실패", estimate_label.text())
        self.assertFalse(dialog.confirm_button.isEnabled())

        combo_by_extension = {
            extensions[0]: combo
            for combo, extensions in dialog._extension_limit_combos
        }
        combo_by_extension[".xlsx"].setCurrentText("5메가")
        combo_by_extension[".hwp"].setCurrentText("안함")
        combo_by_extension[".docx"].setCurrentText("제한없음")
        app.processEvents()
        self.assertIn("2개 파일", estimate_label.text())
        self.assertEqual(combo_by_extension[".hwp"].currentText(), "안함")
        self.assertEqual(
            dialog.findChild(QLabel, "processingTimeBadge_hwp").text(),
            "예상 시간: 해당 없음",
        )
        dialog.close()
        app.processEvents()

    def test_dialog_title_and_row_alignment(self):
        from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox, QLabel
        from app.ui.main_window import FileContentExtensionDialog

        application = QApplication.instance()
        if application is not None and not isinstance(application, QApplication):
            self.skipTest("A QCoreApplication from another test already owns the process")
        app = application or QApplication([])
        files = [
            AnalyzedFile(f"sample{extension}", "", "", 0, 1)
            for extension in (".docx", ".pptx", ".hwp", ".xlsx", ".txt")
        ]
        with patch("app.ui.main_window.load_saved_license_code", return_value=None):
            dialog = FileContentExtensionDialog(files)
        dialog.show()
        app.processEvents()

        self.assertEqual(dialog.windowTitle(), "인수인계패키지로 만들 파일 선택")
        self.assertLess(dialog.minimumHeight(), 500)
        labels_text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertNotIn("각 파일 종류에서 처리할 최대 용량", labels_text)
        self.assertNotIn("용량 제한을 넘는 파일도 패키지에서 빠지지 않으며", labels_text)
        combo_right_edges = []
        for combo, extensions in dialog._extension_limit_combos:
            label = dialog.findChild(QLabel, f"extensionGroupLabel_{extensions[0][1:]}")
            badge = dialog.findChild(QLabel, f"processingTimeBadge_{extensions[0][1:]}")
            self.assertEqual(badge.geometry().left() - label.geometry().right() - 1, 20)
            combo_right_edges.append(combo.geometry().right())
        self.assertEqual(len(set(combo_right_edges)), 1)
        benefit = dialog.findChild(QLabel, "packageBenefitLabel")
        buttons = dialog.findChild(QDialogButtonBox)
        # 13px explicit spacer + the layout's own 12px inter-item spacing, per
        # the "exact 25px visual gap below the purple benefit text" comment in
        # FileContentExtensionDialog.__init__ (pre-existing off-by-10 here,
        # unrelated to the extension-notice merge in this change).
        self.assertEqual(buttons.geometry().top() - benefit.geometry().bottom() - 1, 25)

        dialog.close()
        app.processEvents()

    def test_common_filter_keeps_unselected_and_unsupported_as_filename_only(self):
        files = [
            AnalyzedFile("a.docx", "root/a.docx", "", 1, 1),
            AnalyzedFile("b.hwp", "root/b.hwp", "", 1, 1),
            AnalyzedFile("c.pdf", "root/c.pdf", "", 1, 1),
        ]
        content, filename_only = filter_files_by_selected_extensions(files, {".docx"})
        self.assertEqual([file.file_name for file in content], ["a.docx"])
        self.assertEqual(
            [file.file_name for file in filename_only], ["b.hwp", "c.pdf"]
        )

    def test_common_filter_applies_extension_and_size_independently(self):
        mebibyte = 1024 * 1024
        files = [
            AnalyzedFile("small.xlsx", "root/small.xlsx", "", 1, mebibyte),
            AnalyzedFile("large.xlsx", "root/large.xlsx", "", 1, 6 * mebibyte),
            AnalyzedFile("small.docx", "root/small.docx", "", 1, mebibyte),
        ]
        content, filename_only = filter_files_by_selected_extensions(
            files, {".xlsx"}, 5 * mebibyte
        )

        self.assertEqual([file.file_name for file in content], ["small.xlsx"])
        self.assertEqual(
            [file.file_name for file in filename_only],
            ["large.xlsx", "small.docx"],
        )

    def test_common_filter_applies_per_extension_limits_and_disabled_group(self):
        mebibyte = 1024 * 1024
        files = [
            AnalyzedFile("word.docx", "root/word.docx", "", 1, 40 * mebibyte),
            AnalyzedFile("sheet.xlsx", "root/sheet.xlsx", "", 1, 4 * mebibyte),
            AnalyzedFile("hangul.hwp", "root/hangul.hwp", "", 1, 1),
        ]
        limits = {".docx": None, ".xlsx": 5 * mebibyte, ".hwp": 0}
        content, filename_only = filter_files_by_selected_extensions(
            files, set(limits), extension_size_limits=limits
        )
        self.assertEqual([file.file_name for file in content], ["word.docx", "sheet.xlsx"])
        self.assertEqual([file.file_name for file in filename_only], ["hangul.hwp"])

    def test_checkpoint_signature_changes_with_each_group_selection(self):
        base = _build_checkpoint_signature(
            ["same-file"], {".docx", ".hwp"}, None, {".docx": 1024, ".hwp": 0}
        )
        changed_size = _build_checkpoint_signature(
            ["same-file"], {".docx", ".hwp"}, None, {".docx": 2048, ".hwp": 0}
        )
        enabled_hwp = _build_checkpoint_signature(
            ["same-file"], {".docx", ".hwp"}, None, {".docx": 1024, ".hwp": None}
        )
        self.assertEqual(len({base, changed_size, enabled_hwp}), 3)

    def test_hwp_unchecked_reduces_estimate_and_uses_filename_placeholder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hwp_path = root / "인사규정.hwp"
            hwp_path.write_bytes(b"private-hwp-content" * 10_000)
            analysis = _analysis(root, [hwp_path.name])

            selected = estimate_rag_package_cost(
                analysis, [str(root)], selected_extensions={".hwp"},
                embedding_unit_cost_per_1k=0.05,
            )
            unselected = estimate_rag_package_cost(
                analysis, [str(root)], selected_extensions=set(),
                embedding_unit_cost_per_1k=0.05,
            )
            records, exclusions, timed_out = _build_file_chunk_records_with_timeout(
                analysis.all_files[0], hwp_path, include_content=False
            )

        self.assertGreater(selected["estimated_tokens"], unselected["estimated_tokens"])
        self.assertEqual(exclusions, [])
        self.assertFalse(timed_out)
        self.assertEqual(records[0]["metadata"]["extraction_status"], "not_in_whitelist")
        self.assertIn("인사규정.hwp", records[0]["chunk_text"])
        self.assertNotIn("private-hwp-content", records[0]["chunk_text"])

    def test_default_selection_preserves_full_whitelist(self):
        files = [
            AnalyzedFile(f"sample{extension}", f"root/sample{extension}", "", 1, 1)
            for extension in sorted(RAG_TEXT_EXTRACTION_EXTENSIONS)
        ]
        content, filename_only = filter_files_by_selected_extensions(
            files, set(RAG_TEXT_EXTRACTION_EXTENSIONS)
        )
        self.assertEqual(len(content), len(RAG_TEXT_EXTRACTION_EXTENSIONS))
        self.assertEqual(filename_only, [])

    def test_dialog_candidate_helper_matches_cost_deduplication(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            older = root / "older.txt"
            latest = root / "latest.txt"
            unique = root / "unique.txt"
            older.write_text("same content", encoding="utf-8")
            latest.write_text("same content", encoding="utf-8")
            unique.write_text("unique content", encoding="utf-8")
            analysis = _analysis(root, [older.name, latest.name, unique.name])

            candidates = get_rag_package_candidate_files(analysis, [str(root)])
            estimate = estimate_rag_package_cost(
                analysis,
                [str(root)],
                selected_extensions={".txt"},
                max_file_size_bytes=1024,
                embedding_unit_cost_per_1k=0.05,
            )
            content, filename_only = filter_files_by_selected_extensions(
                candidates,
                {".txt"},
                1024,
            )

        self.assertEqual(
            [file.file_name for file in candidates],
            ["latest.txt", "unique.txt"],
        )
        self.assertEqual(len(content), estimate["content_file_count"])
        self.assertEqual(filename_only, [])

    def test_actual_build_routes_unchecked_hwp_to_filename_only_chunk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hwp_path = root / "채용계획.hwp"
            hwp_path.write_bytes(b"secret-recruiting-plan")
            analysis = _analysis(root, [hwp_path.name])
            captured = {}

            def capture_embedding(records, _api_key, _progress, _cancel):
                captured["records"] = records
                return [], [], 0

            with patch(
                "app.services.rag_package_builder._embed_chunk_records",
                side_effect=capture_embedding,
            ), patch(
                "app.services.rag_package_builder.save_rag_package",
                return_value=str(root / "package.zip"),
            ):
                build_and_save_rag_package(
                    analysis,
                    [str(root)],
                    "unused",
                    str(root / "package"),
                    selected_extensions=set(),
                )

        text = "\n".join(record["chunk_text"] for record in captured["records"])
        self.assertIn("채용계획.hwp", text)
        self.assertNotIn("secret-recruiting-plan", text)

    def test_cost_and_build_use_same_size_limited_content_set(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            small_path = root / "small.txt"
            large_path = root / "large.txt"
            small_path.write_text("small embedded content", encoding="utf-8")
            large_path.write_text("large private content " * 70_000, encoding="utf-8")
            analysis = _analysis(root, [small_path.name, large_path.name])
            captured = {}

            estimate = estimate_rag_package_cost(
                analysis,
                [str(root)],
                selected_extensions={".txt"},
                max_file_size_bytes=1024 * 1024,
                embedding_unit_cost_per_1k=0.05,
            )

            def capture_embedding(records, _api_key, _progress, _cancel):
                captured["records"] = records
                return [], [], 0

            with patch(
                "app.services.rag_package_builder._embed_chunk_records",
                side_effect=capture_embedding,
            ), patch(
                "app.services.rag_package_builder.save_rag_package",
                return_value=str(root / "package.zip"),
            ):
                result = build_and_save_rag_package(
                    analysis,
                    [str(root)],
                    "unused",
                    str(root / "package"),
                    selected_extensions={".txt"},
                    max_file_size_bytes=1024 * 1024,
                )

        self.assertEqual(estimate["content_file_count"], 1)
        self.assertEqual(result["content_file_count"], 1)
        self.assertEqual(result["filename_only_file_count"], 1)
        records_by_name = {
            record["metadata"]["file_name"]: record
            for record in captured["records"]
        }
        self.assertIn("small embedded content", records_by_name["small.txt"]["chunk_text"])
        self.assertEqual(
            records_by_name["large.txt"]["metadata"]["extraction_status"],
            "not_in_whitelist",
        )
        self.assertNotIn("large private content", records_by_name["large.txt"]["chunk_text"])

    def test_cost_and_build_use_same_per_extension_limit_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "word.docx").write_bytes(b"word-content")
            (root / "sheet.xlsx").write_bytes(b"sheet-content")
            (root / "hangul.hwp").write_bytes(b"hangul-content")
            analysis = _analysis(root, ["word.docx", "sheet.xlsx", "hangul.hwp"])
            limits = {".docx": None, ".xlsx": 5 * 1024 * 1024, ".hwp": 0}
            estimate = estimate_rag_package_cost(
                analysis,
                [str(root)],
                selected_extensions=set(limits),
                extension_size_limits=limits,
                embedding_unit_cost_per_1k=0.05,
            )
            captured = {}

            def capture_embedding(records, _api_key, _progress, _cancel):
                captured["records"] = records
                return [], [], 0

            with patch(
                "app.services.rag_package_builder._embed_chunk_records",
                side_effect=capture_embedding,
            ), patch(
                "app.services.rag_package_builder.save_rag_package",
                return_value=str(root / "package.zip"),
            ):
                result = build_and_save_rag_package(
                    analysis,
                    [str(root)],
                    "unused",
                    str(root / "package"),
                    selected_extensions=set(limits),
                    extension_size_limits=limits,
                )

        self.assertEqual(estimate["content_file_count"], 2)
        self.assertEqual(result["content_file_count"], 2)
        self.assertEqual(result["filename_only_file_count"], 1)
        hwp_record = next(
            record for record in captured["records"]
            if record["metadata"]["file_name"] == "hangul.hwp"
        )
        self.assertEqual(hwp_record["metadata"]["extraction_status"], "not_in_whitelist")

    def test_email_body_attachment_and_kakao_ignore_folder_extension_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            kakao_path = Path(temp_dir) / "kakao.txt"
            kakao_path.write_text("카카오 고정 포함 내용 " * 100, encoding="utf-8")
            analysis = _analysis(Path(temp_dir), [])
            parsed_emails = [{
                "source_file": "mail.eml",
                "subject": "메일 제목",
                "sender": "sender@example.com",
                "date": "2026-07-14",
                "body": "이메일 본문 고정 포함 " * 100,
                "attachments": [{
                    "filename": "attachment.txt",
                    "content_bytes": ("첨부파일 기존 정책 ".encode("utf-8") * 100),
                }],
            }]
            estimate = estimate_rag_package_cost(
                analysis,
                [str(temp_dir)],
                parsed_emails=parsed_emails,
                kakao_file_paths=[str(kakao_path)],
                selected_extensions=set(),
                embedding_unit_cost_per_1k=0.05,
            )

        self.assertGreater(estimate["estimated_tokens"], 0)

    @staticmethod
    def _wait_for_quota_state(dialog, states, timeout_seconds=2.0):
        import time

        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        deadline = time.perf_counter() + timeout_seconds
        while dialog._quota_state not in states and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.005)
        return dialog._quota_state

    def test_quota_checking_state_disables_confirm_until_resolved(self):
        from PySide6.QtWidgets import QApplication
        from app.ui.main_window import FileContentExtensionDialog

        application = QApplication.instance()
        if application is not None and not isinstance(application, QApplication):
            self.skipTest("A QCoreApplication from another test already owns the process")
        app = application or QApplication([])
        files = [AnalyzedFile("a.txt", "", "", 0, 1000)]

        def slow_check(_license_code, _size_gb):
            import time

            time.sleep(0.3)
            return {"configuredFreeQuotaGb": 5.0}

        with (
            patch("app.ui.main_window.load_saved_license_code", return_value="license"),
            patch(
                "app.ui.main_window.create_package_generation_order",
                side_effect=slow_check,
            ),
        ):
            dialog = FileContentExtensionDialog(files)
            self.assertEqual(dialog._quota_state, "checking")
            self.assertIn("자료 처리 확인 중...", dialog.estimated_time_label.text())
            self.assertFalse(dialog.confirm_button.isEnabled())
            self._wait_for_quota_state(dialog, {"ready", "failed"})
        dialog.close()
        app.processEvents()

    def test_quota_sufficient_enables_confirm_and_shows_remaining(self):
        from PySide6.QtWidgets import QApplication
        from app.ui.main_window import FileContentExtensionDialog

        application = QApplication.instance()
        if application is not None and not isinstance(application, QApplication):
            self.skipTest("A QCoreApplication from another test already owns the process")
        app = application or QApplication([])
        mebibyte = 1024 * 1024
        files = [AnalyzedFile("small.txt", "", "", 0, mebibyte // 2)]

        with (
            patch("app.ui.main_window.load_saved_license_code", return_value="license"),
            patch(
                "app.ui.main_window.create_package_generation_order",
                return_value={"configuredFreeQuotaGb": 5.0},
            ),
        ):
            dialog = FileContentExtensionDialog(files)
            self.assertEqual(self._wait_for_quota_state(dialog, {"ready", "failed"}), "ready")
        self.assertIn("자료 처리 5.00GB 가능", dialog.estimated_time_label.text())
        self.assertTrue(dialog.confirm_button.isEnabled())
        self.assertIn("#0F172A", dialog.estimated_time_label.styleSheet())
        dialog.close()
        app.processEvents()

    def test_quota_insufficient_disables_confirm_and_shows_quota_banner(self):
        from PySide6.QtWidgets import QApplication
        from app.ui.main_window import FileContentExtensionDialog

        application = QApplication.instance()
        if application is not None and not isinstance(application, QApplication):
            self.skipTest("A QCoreApplication from another test already owns the process")
        app = application or QApplication([])
        mebibyte = 1024 * 1024
        # ~1.5GB of content, well above the mocked 1.0GB remaining quota.
        files = [
            AnalyzedFile(f"big{index}.txt", "", "", 0, 500 * mebibyte)
            for index in range(3)
        ]

        with (
            patch("app.ui.main_window.load_saved_license_code", return_value="license"),
            patch(
                "app.ui.main_window.create_package_generation_order",
                return_value={"configuredFreeQuotaGb": 1.0},
            ),
        ):
            dialog = FileContentExtensionDialog(files)
            self.assertEqual(self._wait_for_quota_state(dialog, {"ready", "failed"}), "ready")
            # The default per-extension size limit ("1메가") caps each file's
            # contribution, so content stays tiny until the cap is lifted.
            self.assertIn("자료 처리 1.00GB 가능", dialog.estimated_time_label.text())
            combo, _extensions = dialog._extension_limit_combos[0]
            combo.setCurrentText("제한없음")
            app.processEvents()
        # Old inline " | 처리 용량 결재 필요" suffix is retired - the summary
        # line stays neutral now, and the dedicated red banner communicates
        # the insufficient state instead.
        self.assertNotIn("처리 용량 결재 필요", dialog.estimated_time_label.text())
        self.assertIn("#0F172A", dialog.estimated_time_label.styleSheet())
        self.assertFalse(dialog.confirm_button.isEnabled())
        self.assertTrue(dialog.quota_insufficient_banner.isVisibleTo(dialog))
        self.assertIn("자료처리용량 부족", dialog.quota_insufficient_banner.text())
        self.assertIn("[라이선스 키]에서 충전 → 바로가기", dialog.quota_insufficient_banner.text())
        self.assertIn(HANDOVER_PORTAL_URL, dialog.quota_insufficient_banner.text())
        self.assertIn("#FCEBEB", dialog.quota_insufficient_banner.styleSheet())
        self.assertIn("#E24B4A", dialog.quota_insufficient_banner.styleSheet())
        self.assertIn("#791F1F", dialog.quota_insufficient_banner.styleSheet())

        # Shrinking the selected content back under the remaining quota must
        # flip the state back to sufficient in real time, using the already
        # cached remaining_gb (no new network call needed).
        combo.setCurrentText("안함")
        app.processEvents()
        self.assertIn("자료 처리 1.00GB 가능", dialog.estimated_time_label.text())
        self.assertTrue(dialog.confirm_button.isEnabled())
        self.assertTrue(dialog.quota_insufficient_banner.isHidden())

        dialog.close()
        app.processEvents()

    def test_quota_check_failure_keeps_confirm_disabled_and_retries_on_change(self):
        from PySide6.QtWidgets import QApplication
        from app.ui.main_window import FileContentExtensionDialog

        application = QApplication.instance()
        if application is not None and not isinstance(application, QApplication):
            self.skipTest("A QCoreApplication from another test already owns the process")
        app = application or QApplication([])
        files = [AnalyzedFile("a.txt", "", "", 0, 1000)]

        call_count = {"n": 0}

        def failing_check(_license_code, _size_gb):
            call_count["n"] += 1
            return None

        with (
            patch("app.ui.main_window.load_saved_license_code", return_value="license"),
            patch(
                "app.ui.main_window.create_package_generation_order",
                side_effect=failing_check,
            ),
        ):
            dialog = FileContentExtensionDialog(files)
            self.assertEqual(self._wait_for_quota_state(dialog, {"ready", "failed"}), "failed")
            self.assertIn("자료 처리 확인 실패", dialog.estimated_time_label.text())
            self.assertFalse(dialog.confirm_button.isEnabled())
            first_call_count = call_count["n"]
            self.assertGreaterEqual(first_call_count, 1)

            # Touching a combo while in the failed state should retry the
            # lookup rather than leaving the user stuck forever.
            combo, _extensions = dialog._extension_limit_combos[0]
            combo.setCurrentText("안함")
            app.processEvents()
            self._wait_for_quota_state(dialog, {"ready"}, timeout_seconds=1.0)
            self.assertGreater(call_count["n"], first_call_count)
            self.assertFalse(dialog.confirm_button.isEnabled())

        dialog.close()
        app.processEvents()

    def test_ui_selection_precedes_progress_and_passes_selection_through_workers(self):
        source = (Path(__file__).parents[1] / "app/ui/main_window.py").read_text(
            encoding="utf-8"
        )
        selection_index = source.index("extension_dialog.exec()")
        progress_index = source.index(
            "progress_box, progress_label = self._create_rag_package_progress_dialog",
            selection_index,
        )
        self.assertLess(selection_index, progress_index)
        self.assertIn("extension_size_limits=self.extension_size_limits", source)
        self.assertIn('context["extension_size_limits"]', source)
        self.assertIn("get_rag_package_candidate_files", source)
        # The "문서 외 파일" notice is now merged into the same estimated-time
        # label as its second line (same style, single container) instead of
        # a separate widget. (Checked as one source-line fragment since the
        # actual Python string literal spans two adjacent lines in source.)
        self.assertIn(
            "※ [문서 외 파일(동영상, 이미지, PDF, 캐드, 포토샵 등), 선택 용량 초과 파일]은 ",
            source,
        )
        self.assertIn("파일명/경로/수정일만 포함.", source)
        self.assertNotIn("longProcessingNotice", source)


if __name__ == "__main__":
    unittest.main()
