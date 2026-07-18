import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QTextBlockFormat
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

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
            "인계 내용 저장",
            "인계 내용이 저장되었습니다.",
        )
        self.assertEqual(dialog.status_label.text(), "")
        dialog.discard_and_close()

    def test_action_buttons_share_height_and_progress_bar_is_static(self) -> None:
        dialog = MemoDialog([], [WorkMemo(title="", content="")])

        # The step bar is a fixed, always-neutral 4-step guide now — it must
        # never track live completion state (writing a memo or answering a
        # handover question must not change its appearance), so there is no
        # per-step completion/current-step API left to call. The standalone
        # "알려주세요" step was folded into "인수인계서저장" since that button
        # now opens the handover-QA popup automatically.
        self.assertEqual(
            dialog.workflow_progress.STEP_LABELS,
            ("내용작성", "자료연결", "내용저장", "인수인계서저장"),
        )
        self.assertFalse(hasattr(dialog.workflow_progress, "set_states"))
        self.assertFalse(hasattr(dialog.workflow_progress, "_completed"))

        dialog.title_input.setText("메모 제목")
        dialog.handover_qa.answers[0] = "답변"
        self.assertFalse(hasattr(dialog.workflow_progress, "_completed"))

        # The "완성도 %" label and the standalone "알려주세요" button were
        # removed entirely as part of the button-consolidation UX change.
        self.assertFalse(hasattr(dialog, "completion_label"))
        self.assertFalse(hasattr(dialog, "handover_qa_button"))

        heights = {
            button.height()
            for button in (
                dialog.add_button,
                dialog.save_button,
                dialog.delete_button,
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

    def test_handover_save_button_saves_marks_proceed_and_closes(self) -> None:
        # [저장하고 인수인계서 만들기] 버튼은 정보 팝업 없이 곧바로 답변을 저장하고
        # (1) should_proceed_to_save를 True로 표시한 뒤 (2) 팝업을 닫는다 — 실제
        # 워드 생성은 호출한 쪽(MemoDialog)이 이 플래그를 보고 이어서 수행한다.
        qa = HandoverQA()
        dialog = HandoverQADialog(qa)
        dialog.answer_input.setPlainText("저장할 답변")

        self.assertFalse(dialog.should_proceed_to_save)
        dialog._save_and_proceed()

        self.assertEqual(qa.answers[0], "저장할 답변")
        self.assertTrue(dialog.should_proceed_to_save)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_handover_autosave_debounce_persists_answer_after_timeout(self) -> None:
        qa = HandoverQA()
        dialog = HandoverQADialog(qa)
        dialog.answer_input.setPlainText("자동 저장될 답변")

        self.assertTrue(dialog.autosave_timer.isActive())
        dialog.autosave_timer.timeout.emit()

        self.assertEqual(qa.answers[0], "자동 저장될 답변")
        self.assertFalse(dialog.should_proceed_to_save)
        dialog.reject()

    def test_handover_close_and_reject_preserve_typed_answer(self) -> None:
        qa = HandoverQA()
        dialog = HandoverQADialog(qa)
        dialog.answer_input.setPlainText("닫아도 남는 답변")
        dialog.reject()
        self.assertEqual(qa.answers[0], "닫아도 남는 답변")

    def test_handover_save_blocks_on_missing_link_without_opening_popup(self) -> None:
        # [알려주세요]가 [인수인계서 저장]에 통합된 뒤로는 메모/연결자료 조건은
        # 알려주세요 팝업을 띄우기 *전에* 걸러야 한다 — 연결자료가 없으면 팝업 자체를
        # 열지 않고 곧바로 경고만 보여준다 (메모 1개는 make_dialog()가 이미 채움).
        dialog = self.make_dialog()
        with patch.object(
            dialog, "_save_current_memo", return_value=True
        ), patch.object(QMessageBox, "warning") as warning, patch(
            "app.ui.memodialog.HandoverQADialog"
        ) as mock_qa_dialog:
            dialog._save_word_and_close()
        message = warning.call_args.args[2]
        self.assertIn("관련 폴더/이메일/메신저를 1개 이상 연결해주세요", message)
        mock_qa_dialog.assert_not_called()
        dialog.discard_and_close()

    def test_handover_save_opens_popup_once_memo_and_link_conditions_pass(self) -> None:
        # 알려주세요 답변이 아직 0개라도(최초 실행) 메모+연결자료 조건만 통과하면
        # 팝업은 자동으로 열려야 한다.
        dialog = MemoDialog(
            [],
            [WorkMemo(title="메모", content="내용", linked_folders=["folder"])],
        )
        with patch.object(
            dialog, "_save_current_memo", return_value=True
        ), patch("app.ui.memodialog.HandoverQADialog") as mock_qa_dialog:
            mock_instance = mock_qa_dialog.return_value
            mock_instance.should_proceed_to_save = False
            dialog._save_word_and_close()
        mock_qa_dialog.assert_called_once_with(
            handover_qa=dialog.handover_qa, parent=dialog
        )
        mock_instance.exec.assert_called_once_with()
        dialog.discard_and_close()

    def test_handover_popup_cancel_keeps_answers_without_saving_word(self) -> None:
        # 팝업을 취소(닫기/X)하면 그 시점까지의 답변은 보존되지만(자동저장),
        # 워드 생성으로는 이어지지 않고 조용히 메모 화면으로 돌아간다.
        callback = MagicMock(return_value=True)
        dialog = MemoDialog(
            [],
            [WorkMemo(title="메모", content="내용", linked_folders=["folder"])],
            on_save_word=callback,
        )
        with patch.object(
            dialog, "_save_current_memo", return_value=True
        ), patch.object(QMessageBox, "warning") as warning, patch(
            "app.ui.memodialog.HandoverQADialog"
        ) as mock_qa_dialog:
            mock_qa_dialog.return_value.should_proceed_to_save = False
            dialog._save_word_and_close()
        warning.assert_not_called()
        callback.assert_not_called()
        dialog.discard_and_close()

    def test_handover_popup_save_with_no_answers_still_blocks_word_save(self) -> None:
        # 팝업에서 "저장하고 인수인계서 만들기"를 눌렀더라도(should_proceed_to_save
        # =True) 실제 답변이 여전히 0개면 기존 필요조건 검사가 그대로 막아야 한다.
        callback = MagicMock(return_value=True)
        dialog = MemoDialog(
            [],
            [WorkMemo(title="메모", content="내용", linked_folders=["folder"])],
            on_save_word=callback,
        )
        with patch.object(
            dialog, "_save_current_memo", return_value=True
        ), patch.object(QMessageBox, "warning") as warning, patch(
            "app.ui.memodialog.HandoverQADialog"
        ) as mock_qa_dialog:
            mock_qa_dialog.return_value.should_proceed_to_save = True
            dialog._save_word_and_close()
        message = warning.call_args.args[2]
        self.assertIn("알려주세요에서 1개 이상 답변해 주세요", message)
        callback.assert_not_called()
        dialog.discard_and_close()

    def test_handover_save_proceeds_when_all_requirements_are_met(self) -> None:
        callback = MagicMock(return_value=True)
        dialog = MemoDialog(
            [],
            [WorkMemo(title="메모", content="내용", linked_emails=["mail.eml"])],
            handover_qa=HandoverQA(answers=["답변", "", "", "", ""]),
            on_save_word=callback,
        )
        with patch.object(
            dialog, "_save_current_memo", return_value=True
        ), patch.object(QMessageBox, "warning") as warning, patch(
            "app.ui.memodialog.HandoverQADialog"
        ) as mock_qa_dialog:
            mock_qa_dialog.return_value.should_proceed_to_save = True
            dialog._save_word_and_close()
        warning.assert_not_called()
        callback.assert_called_once_with()
        dialog.discard_and_close()


if __name__ == "__main__":
    unittest.main()
