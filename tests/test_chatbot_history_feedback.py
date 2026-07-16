import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QFrame

from app.ui.chatbot_dialog import ChatbotDialog, ChatFeedbackWorker
from app.license_credits import submit_chat_feedback


class ChatbotHistoryFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QCoreApplication.instance()
        cls.ui_available = existing is None or isinstance(existing, QApplication)
        cls.app = existing or QApplication([])

    def setUp(self):
        if not self.ui_available:
            self.skipTest("A QCoreApplication was created earlier in the full test suite")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.history_path = Path(self.temp_dir.name) / "history.json"
        self.history_patch = patch.object(
            ChatbotDialog, "_history_path", return_value=self.history_path
        )
        self.history_patch.start()
        self.dialog = ChatbotDialog()

    def tearDown(self):
        if hasattr(self, "dialog"):
            self.dialog.close()
        if hasattr(self, "history_patch"):
            self.history_patch.stop()
        if hasattr(self, "temp_dir"):
            self.temp_dir.cleanup()

    @staticmethod
    def history_item(index: int) -> dict:
        return {
            "question": f"질문 {index}",
            "answer": f"답변 {index}",
            "confidence": "확실함",
            "sources": [],
            "related": [],
            "feedback": "",
        }

    def test_history_persists_caps_at_500_and_loads_without_package(self):
        self.dialog.chat_history = [self.history_item(index) for index in range(501)]
        self.dialog._save_chat_history()
        saved = json.loads(self.history_path.read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 500)
        self.assertEqual(saved[0]["question"], "질문 1")

        restored = ChatbotDialog()
        try:
            self.assertEqual(len(restored.chat_history), 500)
            self.assertEqual(restored.chat_history[-1]["answer"], "답변 500")
            self.assertFalse(restored.question_input.isEnabled())
            self.assertIsNone(restored.selected_folder)
        finally:
            restored.close()

    def test_saved_feedback_history_renders_after_question_input_exists(self):
        self.history_path.write_text(
            json.dumps(
                [
                    {
                        "question": "과거 질문",
                        "answer": "과거 답변",
                        "confidence": "확실함",
                        "sources": [],
                        "related": [],
                        "feedback": "up",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        restored = ChatbotDialog()
        try:
            up = restored.findChild(QPushButton, "feedbackUpButton")
            down = restored.findChild(QPushButton, "feedbackDownButton")
            submitted = restored.findChild(QLabel, "feedbackSubmittedLabel")
            self.assertIsNotNone(restored.question_input)
            self.assertFalse(up.isEnabled())
            self.assertFalse(down.isEnabled())
            self.assertEqual(submitted.text(), "제출됨")
        finally:
            restored.close()

    def test_source_label_shows_plain_text_without_clickable_links(self):
        source_file = Path(self.temp_dir.name) / "원본.docx"
        source_file.write_text("test", encoding="utf-8")
        source = f"원본.docx (경로: {source_file}, 수정일: 2026-07-15)"
        source_label = self.dialog._create_sources_label([source])
        self.assertNotIn("<a href", source_label.text())
        self.assertIn("원본.docx", source_label.text())
        self.assertFalse(hasattr(self.dialog, "_open_source_in_explorer"))
        self.assertIn("font-size:10pt", source_label.text())
        self.assertIn("line-height:110%", source_label.text())

    def test_low_confidence_never_creates_related_material(self):
        for confidence in ("확인 필요", "확인불가", "확인 불가"):
            row = self.dialog._add_bot_message(
                "문구와 무관한 답변",
                confidence=confidence,
                sources=["자료.docx (경로: C:/자료.docx)"],
                related=[{"answer": "관련 설명", "sources": []}],
            )
            self.assertIsNone(row.findChild(QPushButton, "relatedToggleButton"))

    def test_feedback_buttons_submit_once_and_show_selected_state(self):
        item = self.history_item(1)
        self.dialog.chat_history = [item]
        row = self.dialog._add_bot_message(
            item["answer"], confidence="확실함", question=item["question"]
        )
        up = row.findChild(QPushButton, "feedbackUpButton")
        down = row.findChild(QPushButton, "feedbackDownButton")
        submitted = row.findChild(QLabel, "feedbackSubmittedLabel")
        with patch.object(ChatFeedbackWorker, "start") as start:
            up.click()
        start.assert_called_once()
        self.assertFalse(up.isEnabled())
        self.assertFalse(down.isEnabled())
        self.assertEqual(submitted.text(), "제출됨")
        self.assertEqual(self.dialog.chat_history[0]["feedback"], "up")

    def test_feedback_click_restores_question_input_focus(self):
        item = self.history_item(1)
        self.dialog.chat_history = [item]
        row = self.dialog._add_bot_message(
            item["answer"], confidence="확실함", question=item["question"]
        )
        down = row.findChild(QPushButton, "feedbackDownButton")
        self.dialog.question_input.setEnabled(True)
        self.dialog.show()
        self.dialog.question_input.setFocus()
        self.app.processEvents()
        self.assertIs(QApplication.focusWidget(), self.dialog.question_input)
        with patch.object(ChatFeedbackWorker, "start"):
            QTest.mouseClick(down, Qt.MouseButton.LeftButton)
            QTest.qWait(800)
        self.assertIs(QApplication.focusWidget(), self.dialog.question_input)

    def test_related_material_is_collapsed_then_expands(self):
        row = self.dialog._add_bot_message(
            "본문은 항상 표시",
            confidence="확실함",
            question="질문",
            related=[
                {
                    "answer": "관련.xlsx (경로: C:/remote/관련.xlsx, 수정일: 2026-07-15)",
                    "confidence": "추정",
                    "sources": [],
                }
            ],
        )
        body = row.findChild(QLabel, "answerBody")
        toggle = row.findChild(QPushButton, "relatedToggleButton")
        container = row.findChild(QFrame, "relatedContainer")
        self.assertIn("본문은 항상 표시", body.text())
        self.assertTrue(container.isHidden())
        self.assertEqual(toggle.text(), "관련 자료 보기 ▾")
        for index in range(20):
            self.dialog._add_user_message(f"스크롤 채우기 {index}")
        self.dialog.show()
        self.app.processEvents()
        scroll_bar = self.dialog.chat_area.verticalScrollBar()
        scroll_bar.setValue(max(1, scroll_bar.maximum() // 2))
        before = scroll_bar.value()
        toggle.click()
        self.app.processEvents()
        self.assertFalse(container.isHidden())
        self.assertEqual(toggle.text(), "관련 자료 숨기기 ▴")
        self.assertEqual(scroll_bar.value(), before)

    def test_sources_and_related_are_unified_and_deduplicated(self):
        source = "같은파일.xlsx (경로: C:/remote/같은파일.xlsx, 수정일: 2026-07-15)"
        row = self.dialog._add_bot_message(
            "본문",
            confidence="확실함",
            sources=[source],
            related=[{"answer": source, "confidence": "추정", "sources": [source]}],
        )
        toggle = row.findChild(QPushButton, "relatedToggleButton")
        container = row.findChild(QFrame, "relatedContainer")
        source_labels = [
            label for label in container.findChildren(QLabel)
            if "C:/remote/같은파일.xlsx" in label.text()
        ]
        self.assertEqual(toggle.text(), "관련 자료 보기 ▾")
        self.assertTrue(container.isHidden())
        self.assertEqual(len(source_labels), 1)
        self.assertEqual(source_labels[0].text().count("•"), 1)

    def test_feedback_worker_swallows_transport_failure(self):
        worker = ChatFeedbackWorker("license", "question", "answer", "down")
        with patch(
            "app.ui.chatbot_dialog.submit_chat_feedback",
            side_effect=OSError("offline"),
        ):
            worker.run()

    def test_feedback_transport_uses_api_shape_and_truncates_preview(self):
        with patch("app.license_credits._request_json", return_value={"status": "received"}) as request:
            result = submit_chat_feedback(
                " LICENSE-1 ", "질문 전문", "가" * 250, "up"
            )
        self.assertEqual(result, {"status": "received"})
        payload = request.call_args.kwargs["payload"]
        self.assertEqual(payload["licenseCode"], "LICENSE-1")
        self.assertEqual(payload["question"], "질문 전문")
        self.assertEqual(len(payload["answerPreview"]), 200)
        self.assertEqual(payload["rating"], "up")


if __name__ == "__main__":
    unittest.main()
