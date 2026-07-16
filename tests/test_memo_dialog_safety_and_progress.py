import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QTextBlockFormat
from PySide6.QtWidgets import QApplication, QMessageBox

from app.services.analysis_result import HandoverQA, WorkMemo
from app.ui.handover_qa_dialog import HandoverQADialog
from app.ui.memodialog import MemoDialog


class MemoDialogSafetyAndProgressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        existing_app = QCoreApplication.instance()
        if existing_app is not None and not isinstance(existing_app, QApplication):
            raise unittest.SkipTest(
                "A non-GUI QCoreApplication was created by an earlier test module"
            )
        cls.app = QApplication.instance() or QApplication([])

    def make_dialog(self) -> MemoDialog:
        return MemoDialog([], [WorkMemo(title="기존 메모", content="기존 내용")])

    def test_debounced_autosave_persists_current_memo_and_shows_status(self) -> None:
        dialog = self.make_dialog()
        dialog.content_input.setPlainText("자동 저장할 내용")

        self.assertTrue(dialog.has_unsaved_changes)
        self.assertTrue(dialog.autosave_timer.isActive())

        dialog._autosave_current_memo()

        self.assertEqual(dialog.memos[0].content, "자동 저장할 내용")
        self.assertFalse(dialog.has_unsaved_changes)
        self.assertEqual(dialog.status_label.text(), "임시 저장됨")
        dialog.discard_and_close()

    def test_explicit_memo_save_uses_dialog_without_duplicate_inline_text(self) -> None:
        dialog = self.make_dialog()
        dialog.title_input.setText("저장할 메모")
        dialog.content_input.setPlainText("저장할 내용")

        with patch.object(QMessageBox, "information") as information:
            self.assertTrue(dialog._save_current_memo())

        information.assert_called_once_with(
            dialog,
            "업무 메모 저장",
            "메모가 저장되었습니다.",
        )
        self.assertEqual(dialog.status_label.text(), "")
        dialog.discard_and_close()

    def test_action_buttons_share_height_and_progress_bar_is_static(self) -> None:
        dialog = MemoDialog([], [WorkMemo(title="", content="")])

        # The step bar is a fixed, always-neutral 5-step guide now — it must
        # never track live completion state (writing a memo or answering a
        # handover question must not change its appearance), so there is no
        # per-step completion/current-step API left to call.
        self.assertEqual(
            dialog.workflow_progress.STEP_LABELS,
            ("메모작성", "자료연결", "메모저장", "알려주세요", "인수인계서저장"),
        )
        self.assertFalse(hasattr(dialog.workflow_progress, "set_states"))
        self.assertFalse(hasattr(dialog.workflow_progress, "_completed"))

        dialog.title_input.setText("메모 제목")
        dialog.handover_qa.answers[0] = "답변"
        dialog._update_completion_progress()
        self.assertFalse(hasattr(dialog.workflow_progress, "_completed"))

        heights = {
            button.height()
            for button in (
                dialog.add_button,
                dialog.save_button,
                dialog.delete_button,
                dialog.handover_qa_button,
                dialog.complete_button,
            )
        }
        self.assertEqual(heights, {40})
        layout = dialog.layout()
        self.assertEqual(layout.itemAt(0).spacerItem().sizeHint().height(), 20)
        self.assertIs(layout.itemAt(1).widget(), dialog.workflow_progress)
        self.assertEqual(layout.itemAt(2).spacerItem().sizeHint().height(), 20)
        dialog.discard_and_close()

        # A fully-linked, saved memo must render the exact same static bar as
        # an empty one — no data state should ever change its appearance.
        linked_dialog = MemoDialog(
            [],
            [
                WorkMemo(
                    title="연결된 메모",
                    content="내용",
                    linked_folders=["folder"],
                )
            ],
        )
        self.assertEqual(
            linked_dialog.workflow_progress.STEP_LABELS,
            dialog.workflow_progress.STEP_LABELS,
        )
        self.assertFalse(hasattr(linked_dialog.workflow_progress, "_completed"))
        linked_dialog.discard_and_close()

    def test_close_warning_keeps_or_closes_dialog_according_to_answer(self) -> None:
        dialog = self.make_dialog()
        dialog.content_input.setPlainText("저장하지 않은 변경")
        event = MagicMock()

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            dialog.closeEvent(event)

        question.assert_called_once()
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()

        event.reset_mock()
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            dialog.closeEvent(event)
        event.accept.assert_called_once_with()

    def test_clean_close_does_not_show_warning(self) -> None:
        dialog = self.make_dialog()
        event = MagicMock()
        with patch.object(QMessageBox, "question") as question:
            dialog.closeEvent(event)
        question.assert_not_called()
        event.accept.assert_called_once_with()

    def test_handover_categories_show_live_completion_checks(self) -> None:
        qa = HandoverQA(answers=["완료", "", "반복 답변", "", "기타 답변"])
        dialog = HandoverQADialog(qa)

        self.assertTrue(dialog.category_buttons[0].text().startswith("✓ "))
        self.assertFalse(dialog.category_buttons[1].text().startswith("✓ "))
        self.assertTrue(dialog.category_buttons[2].text().startswith("✓ "))
        self.assertFalse(dialog.category_buttons[3].text().startswith("✓ "))
        self.assertTrue(dialog.category_buttons[4].text().startswith("✓ "))

        dialog._select_category(1)
        dialog.answer_input.setPlainText("주의사항 답변")
        self.assertTrue(dialog.category_buttons[1].text().startswith("✓ "))
        dialog.answer_input.clear()
        self.assertFalse(dialog.category_buttons[1].text().startswith("✓ "))
        dialog.reject()

    def test_handover_answer_line_spacing_is_130_percent(self) -> None:
        dialog = HandoverQADialog(HandoverQA())
        dialog.answer_input.setPlainText("첫째 줄\n둘째 줄")
        block_format = dialog.answer_input.document().firstBlock().blockFormat()

        self.assertEqual(block_format.lineHeight(), 130)
        self.assertEqual(
            block_format.lineHeightType(),
            QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
        )
        dialog.reject()

    def test_handover_explicit_save_uses_dialog_without_inline_status(self) -> None:
        qa = HandoverQA()
        dialog = HandoverQADialog(qa)
        dialog.answer_input.setPlainText("저장할 답변")

        with patch.object(QMessageBox, "information") as information:
            dialog._save_with_confirmation()

        self.assertEqual(qa.answers[0], "저장할 답변")
        information.assert_called_once_with(dialog, "알려주세요", "저장되었습니다")
        self.assertFalse(hasattr(dialog, "save_status_label"))
        dialog.reject()

    def test_handover_save_reports_all_missing_requirements_at_once(self) -> None:
        dialog = self.make_dialog()
        with patch.object(dialog, "_save_current_memo", return_value=True), patch.object(
            QMessageBox, "warning"
        ) as warning:
            dialog._save_word_and_close()
        message = warning.call_args.args[2]
        self.assertIn("관련 폴더/이메일/메신저를 1개 이상 연결해주세요", message)
        self.assertIn("알려주세요에서 1개 이상 답변해 주세요", message)
        dialog.discard_and_close()

    def test_handover_save_with_link_only_reports_missing_answer(self) -> None:
        dialog = MemoDialog(
            [],
            [WorkMemo(title="메모", content="내용", linked_folders=["folder"])],
        )
        with patch.object(dialog, "_save_current_memo", return_value=True), patch.object(
            QMessageBox, "warning"
        ) as warning:
            dialog._save_word_and_close()
        message = warning.call_args.args[2]
        self.assertNotIn("관련 폴더/이메일/메신저", message)
        self.assertIn("알려주세요에서 1개 이상 답변해 주세요", message)
        dialog.discard_and_close()

    def test_handover_save_proceeds_when_all_requirements_are_met(self) -> None:
        callback = MagicMock(return_value=True)
        dialog = MemoDialog(
            [],
            [WorkMemo(title="메모", content="내용", linked_emails=["mail.eml"])],
            handover_qa=HandoverQA(answers=["답변", "", "", "", ""]),
            on_save_word=callback,
        )
        with patch.object(dialog, "_save_current_memo", return_value=True), patch.object(
            QMessageBox, "warning"
        ) as warning:
            dialog._save_word_and_close()
        warning.assert_not_called()
        callback.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
