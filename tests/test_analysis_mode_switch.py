import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QMessageBox

from app.services.analysis_result import AnalysisResult
from app.ui.main_window import AnalysisWorker, MainWindow


class AnalysisModeSwitchTests(unittest.TestCase):
    def _mode_window(self, selected_mode: str):
        return SimpleNamespace(
            license_activated=True,
            selected_analysis_mode=selected_mode,
            current_analysis_result=None,
            _credit_insufficient=False,
            _show_license_lock_warning=MagicMock(),
            _get_selected_folder_paths=MagicMock(return_value=[]),
            _get_selected_email_file_paths=MagicMock(return_value=[]),
            _get_selected_kakao_file_paths=MagicMock(return_value=[]),
            _has_analysis_targets_or_memo_content=MagicMock(return_value=True),
            _reset_for_analysis_mode_change=MagicMock(),
            _update_mode_card_styles=MagicMock(),
            _update_analysis_mode_notice=MagicMock(),
            _update_analysis_target_tabs_enabled=MagicMock(),
        )

    def test_basic_to_ai_yes_resets_before_switching(self):
        window = self._mode_window("basic")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            MainWindow._select_analysis_mode(window, "ai")

        window._reset_for_analysis_mode_change.assert_called_once_with()
        self.assertEqual(window.selected_analysis_mode, "ai")
        window._update_analysis_target_tabs_enabled.assert_called_once_with()

    def test_ai_to_basic_yes_uses_the_same_full_reset(self):
        window = self._mode_window("ai")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            MainWindow._select_analysis_mode(window, "basic")

        window._reset_for_analysis_mode_change.assert_called_once_with()
        self.assertEqual(window.selected_analysis_mode, "basic")

    def test_no_preserves_mode_and_all_existing_state(self):
        window = self._mode_window("basic")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            MainWindow._select_analysis_mode(window, "ai")

        self.assertEqual(window.selected_analysis_mode, "basic")
        window._reset_for_analysis_mode_change.assert_not_called()
        window._update_mode_card_styles.assert_not_called()

    def test_empty_state_switches_without_warning(self):
        window = self._mode_window("basic")
        window._has_analysis_targets_or_memo_content.return_value = False
        with patch.object(QMessageBox, "question") as question:
            MainWindow._select_analysis_mode(window, "ai")

        question.assert_not_called()

    def test_credit_insufficient_blocks_ai_selection_and_stays_basic(self):
        window = self._mode_window("basic")
        window._credit_insufficient = True
        with patch.object(QMessageBox, "warning") as warning:
            MainWindow._select_analysis_mode(window, "ai")

        warning.assert_called_once()
        self.assertEqual(window.selected_analysis_mode, "basic")
        window._reset_for_analysis_mode_change.assert_not_called()
        window._update_mode_card_styles.assert_called_once_with()

    def test_credit_insufficient_still_allows_basic_selection(self):
        window = self._mode_window("ai")
        window._credit_insufficient = True
        with patch.object(QMessageBox, "warning") as warning, patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            MainWindow._select_analysis_mode(window, "basic")

        warning.assert_not_called()
        self.assertEqual(window.selected_analysis_mode, "basic")
        window._reset_for_analysis_mode_change.assert_called_once_with()

    def test_mode_warning_uses_new_exact_message(self):
        window = self._mode_window("basic")
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            MainWindow._select_analysis_mode(window, "ai")

        self.assertEqual(
            question.call_args.args[2],
            "모드를 변경하면 선택한 내용과 작성한 메모가 모두 초기화 됩니다.",
        )

    def test_target_or_memo_content_and_links_trigger_mode_warning(self):
        def build_window(*, folders=None, memos=None):
            return SimpleNamespace(
                _get_selected_folder_paths=lambda: folders or [],
                _get_selected_email_file_paths=lambda: [],
                _get_selected_kakao_file_paths=lambda: [],
                current_analysis_result=(
                    SimpleNamespace(memos=memos or []) if memos is not None else None
                ),
                _has_memo_content_or_links=lambda: MainWindow._has_memo_content_or_links(window),
            )

        window = build_window(folders=["folder"])
        self.assertTrue(MainWindow._has_analysis_targets_or_memo_content(window))

        for memo in (
            SimpleNamespace(title="메모", content="", linked_folders=[], linked_files=[], linked_emails=[], linked_kakao_files=[]),
            SimpleNamespace(title="", content="내용", linked_folders=[], linked_files=[], linked_emails=[], linked_kakao_files=[]),
            SimpleNamespace(title="", content="", linked_folders=["folder"], linked_files=[], linked_emails=[], linked_kakao_files=[]),
            SimpleNamespace(title="", content="", linked_folders=[], linked_files=[], linked_emails=["mail"], linked_kakao_files=[]),
            SimpleNamespace(title="", content="", linked_folders=[], linked_files=[], linked_emails=[], linked_kakao_files=["chat"]),
        ):
            window = build_window(memos=[memo])
            self.assertTrue(MainWindow._has_analysis_targets_or_memo_content(window))

        window = build_window(memos=[])
        self.assertFalse(MainWindow._has_analysis_targets_or_memo_content(window))

    def test_analysis_restart_confirmation_empty_cancel_and_confirm(self):
        window = SimpleNamespace(_has_memo_content_or_links=MagicMock(return_value=False))
        with patch.object(QMessageBox, "question") as question:
            self.assertTrue(MainWindow._confirm_clear_memos_for_analysis_restart(window))
        question.assert_not_called()

        window._has_memo_content_or_links.return_value = True
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Cancel,
        ) as question:
            self.assertFalse(MainWindow._confirm_clear_memos_for_analysis_restart(window))
        self.assertEqual(
            question.call_args.args[2],
            "분석시작을 다시 하면 메모작성팝업의 내용이 모두 삭제됩니다.",
        )
        self.assertEqual(
            question.call_args.args[3],
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Ok,
        ):
            self.assertTrue(MainWindow._confirm_clear_memos_for_analysis_restart(window))

    def test_confirmed_analysis_restart_clears_memos_links_ai_cache_and_answers(self):
        memo = SimpleNamespace(
            title="메모",
            content="내용",
            linked_folders=["folder"],
            linked_files=["file"],
            linked_emails=["mail"],
            linked_kakao_files=["chat"],
            ai_result={"cached": "value"},
        )
        result = SimpleNamespace(
            memos=[memo],
            handover_qa=SimpleNamespace(answers=["답1", "답2", "", "", ""]),
        )
        dialog = MagicMock()
        window = SimpleNamespace(_memo_dialog=dialog, current_analysis_result=result)

        MainWindow._clear_memos_for_analysis_restart(window)

        dialog.discard_and_close.assert_called_once_with()
        self.assertIsNone(window._memo_dialog)
        self.assertEqual(result.memos, [])
        self.assertEqual(result.handover_qa.answers, ["", "", "", "", ""])

    def test_full_reset_clears_targets_analysis_memos_links_and_ai_cache(self):
        memo = SimpleNamespace(
            title="작성 중 메모",
            content="내용",
            linked_folders=["folder"],
            linked_emails=["mail"],
            linked_kakao_files=["chat"],
            ai_result={"status_summary": "cached"},
            ai_result_content_hash="hash",
        )
        result = SimpleNamespace(memos=[memo])
        memo_dialog = MagicMock()
        window = SimpleNamespace(
            _memo_dialog=memo_dialog,
            folder_path_input=MagicMock(),
            folder_list_widget=MagicMock(),
            email_file_list_widget=MagicMock(),
            kakao_file_list_widget=MagicMock(),
            analysis_target_tabs=MagicMock(),
            _folder_tab_index=0,
            current_analysis_result=result,
            current_analysis_analyzed_at=object(),
            _analyzed_selection_signature=(("folder",), (), ()),
            result_preview=MagicMock(),
            _last_saved_report_fingerprint="saved",
            _last_saved_word_path="report.docx",
            edit_memo_button=MagicMock(),
            create_rag_package_button=MagicMock(),
            _update_empty_state_labels=MagicMock(),
            _update_start_button_enabled=MagicMock(),
        )

        MainWindow._reset_for_analysis_mode_change(window)

        memo_dialog.discard_and_close.assert_called_once_with()
        self.assertIsNone(window._memo_dialog)
        window.folder_path_input.clear.assert_called_once_with()
        window.folder_list_widget.clear.assert_called_once_with()
        window.email_file_list_widget.clear.assert_called_once_with()
        window.kakao_file_list_widget.clear.assert_called_once_with()
        self.assertIsNone(window.current_analysis_result)
        self.assertIsNone(window.current_analysis_analyzed_at)
        self.assertIsNone(window._analyzed_selection_signature)
        self.assertIsNone(window._last_saved_report_fingerprint)
        self.assertIsNone(window._last_saved_word_path)
        # [분석시작] 통합 이후 이 버튼은 선택 상태와 무관하게 항상 눌러야 하므로
        # (필요하면 알아서 분석부터 실행) 더 이상 여기서 직접 비활성화하지 않고,
        # 항상 활성화 상태로 되돌리는 _update_start_button_enabled에 위임한다.
        window._update_start_button_enabled.assert_called_once_with()
        window.edit_memo_button.setEnabled.assert_not_called()
        # The package button is always enabled now, so a mode-change reset
        # must not touch it.
        window.create_rag_package_button.setEnabled.assert_not_called()

    def test_new_analysis_and_word_save_use_analysis_result_mode(self):
        base_result = AnalysisResult(
            root_folder_path="root",
            total_folder_count=0,
            total_file_count=0,
            total_size_bytes=0,
            modified_within_7_days_count=0,
            modified_within_30_days_count=0,
            modified_within_90_days_count=0,
            error_count=0,
            child_folder_summaries=[],
            analysismode="basic",
        )
        worker = AnalysisWorker(["root"], "ai")
        worker._build_merged_analysis_result = MagicMock(return_value=base_result)
        emitted = []
        worker.succeeded.connect(emitted.append)
        worker.run()
        self.assertEqual(emitted[0].analysismode, "ai")

        from pathlib import Path

        source = (Path(__file__).parents[1] / "app/ui/main_window.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("self._get_selected_analysis_mode()", source)
        self.assertIn("analysismode=self.analysis_mode", source)
        self.assertIn('result.analysismode != "ai"', source)


if __name__ == "__main__":
    unittest.main()
