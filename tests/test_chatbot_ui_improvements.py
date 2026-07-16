import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QFrame, QProgressBar, QPushButton

from app.ui.chatbot_dialog import ChatbotDialog


class ChatbotUiImprovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QCoreApplication.instance()
        cls.ui_available = existing is None or isinstance(existing, QApplication)
        cls.app = existing or QApplication([])

    def setUp(self):
        if not self.ui_available:
            self.skipTest("A QCoreApplication was created earlier in the full test suite")
        self.dialog = ChatbotDialog()

    def tearDown(self):
        if hasattr(self, "dialog"):
            self.dialog.close()

    def test_user_bubble_uses_soft_blue(self):
        row = self.dialog._add_user_message("질문")
        self.assertIn("#4A78B8", row.findChild(QLabel).parentWidget().styleSheet())

    def test_answer_body_uses_120_percent_line_height(self):
        row = self.dialog._add_bot_message("첫 줄\n둘째 줄", confidence="확실함")
        body = row.findChild(QLabel, "answerBody")
        self.assertEqual(body.textFormat().name, "RichText")
        self.assertIn("line-height:120%", body.text())
        self.assertIn("첫 줄<br>둘째 줄", body.text())

    def test_loading_animation_stops_on_first_stream_delta(self):
        row = self.dialog._add_bot_message("답변 생성 중...", confidence="처리 중")
        self.dialog.pending_answer_row = row
        self.dialog.streaming_answer_label = row.findChild(QLabel, "answerBody")
        self.dialog.streaming_answer_text = ""
        self.dialog._start_answer_loading_animation()
        frames = []
        for _ in range(3):
            frames.append(self.dialog.streaming_answer_label.text())
            self.dialog._advance_answer_loading_animation()
        self.assertTrue(any("..." in frame for frame in frames))
        self.assertTrue(any(".." in frame for frame in frames))
        self.dialog._handle_answer_delta("첫 토큰")
        self.assertFalse(self.dialog.answer_loading_timer.isActive())
        self.assertIn("첫 토큰", self.dialog.streaming_answer_label.text())
        self.assertNotIn("답변 생성 중", self.dialog.streaming_answer_label.text())

    def test_package_measurement_and_loading_bubble(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "one.zip").write_bytes(b"a" * 1024)
            (folder / "two.zip").write_bytes(b"b" * 2048)
            count, size_bytes = self.dialog._measure_local_packages(folder)
        self.assertEqual(count, 2)
        self.assertEqual(size_bytes, 3072)

        row = self.dialog._add_package_loading_message(
            "총 0.00기가의 인수인계패키지 2개를 준비 중입니다"
        )
        bubble = row.findChild(QFrame, "packageLoadingBubble")
        indicator = row.findChild(QProgressBar, "packageLoadingIndicator")
        self.assertIn("#F3E8FF", bubble.styleSheet())
        self.assertEqual((indicator.minimum(), indicator.maximum()), (0, 0))

    def test_loading_removes_duplicate_status_and_initial_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "package.zip").write_bytes(b"package")
            with patch("app.ui.chatbot_dialog.PackageLoadWorker.start"):
                self.dialog._start_package_load(
                    folder,
                    "folder",
                    "ignored",
                )
        self.assertEqual(self.dialog.status_label.text(), "")
        loading_label = self.dialog.pending_load_row.findChild(
            QLabel, "packageLoadingLabel"
        )
        self.assertEqual(
            loading_label.text(),
            "총 0.00기가의 인수인계패키지 1개를 준비 중입니다",
        )
        self.dialog.package_loading_timer.stop()
        self.dialog.package_load_worker = None

    def test_success_replaces_initial_notice_with_real_load_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            zip_path = Path(directory) / "package.zip"
            zip_path.write_bytes(b"x" * 4096)
            self.dialog._handle_package_load_succeeded(
                {
                    "packages": [{"zip_path": str(zip_path)}],
                    "chunks": [{"text": "내용"}],
                    "search_index": object(),
                    "api_key": "key",
                }
            )
        texts = [label.text() for label in self.dialog.chat_container.findChildren(QLabel)]
        self.assertFalse(any("패키지 폴더를 선택한 뒤" in text for text in texts))
        self.assertFalse(any("패키지 0개를 로드" in text for text in texts))
        self.assertTrue(any("패키지 1개, 총 0.00기가를 불러왔습니다" in text for text in texts))

    def test_bot_title_removed_and_send_button_uses_soft_purple(self):
        row = self.dialog._add_bot_message("답변", confidence="확실함")
        texts = [label.text() for label in row.findChildren(QLabel)]
        self.assertNotIn("물어보기", texts)
        self.assertIn("#A78BFA", self.dialog.send_button.styleSheet())

    def test_chat_search_cycles_and_highlights_matches(self):
        self.dialog._add_user_message("반복 질문 반복")
        self.dialog._add_bot_message("반복 답변", confidence="확실함")
        self.dialog.chat_search_input.setText("반복")

        with patch.object(self.dialog, "_select_package_folder") as select_folder:
            self.dialog.chat_search_input.setFocus()
            QTest.keyClick(self.dialog.chat_search_input, Qt.Key.Key_Return)
        select_folder.assert_not_called()
        self.assertEqual(self.dialog.chat_search_result_label.text(), "1/3건")
        first_highlight = [
            label for label in self.dialog.chat_container.findChildren(QLabel)
            if "#FDE68A" in label.text()
        ]
        self.assertEqual(len(first_highlight), 1)

        self.dialog._find_next_chat_text()
        self.assertEqual(self.dialog.chat_search_result_label.text(), "2/3건")

        scroll_bar = self.dialog.chat_area.verticalScrollBar()
        scroll_before_failed_search = scroll_bar.value()
        self.dialog.chat_search_input.setText("없는 문구")
        self.dialog._find_next_chat_text()
        self.assertEqual(
            self.dialog.chat_search_result_label.text(), "검색 결과가 없습니다"
        )
        self.assertEqual(scroll_bar.value(), scroll_before_failed_search)

        # repeated Enter presses on the same no-match query must not move
        # the scroll position either
        for _ in range(3):
            self.dialog._find_next_chat_text()
            self.assertEqual(scroll_bar.value(), scroll_before_failed_search)

    def test_search_offsets_distinguish_matches_inside_one_wrapped_label(self):
        text = "\n".join(f"평가 항목 {index}: 상세 설명" for index in range(20))
        row = self.dialog._add_bot_message(text, confidence="확실함")
        label = row.findChild(QLabel, "answerBody")
        self.dialog.show()
        self.app.processEvents()
        starts = [index for index in range(len(text)) if text.startswith("평가", index)]
        offsets = [
            self.dialog._search_match_vertical_offset(label, start)
            for start in starts[:5]
        ]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(len(set(offsets)), 5)

    def test_question_requirement_visibility_and_loading_label_background(self):
        self.assertTrue(self.dialog.question_requirement_label.isVisibleTo(self.dialog))
        self.assertIn("#DC2626", self.dialog.question_requirement_label.styleSheet())
        bubble_row = self.dialog._add_package_loading_message("준비 중")
        loading_label = bubble_row.findChild(QLabel, "packageLoadingLabel")
        self.assertIn("background-color: transparent", loading_label.styleSheet())

        self.dialog._handle_package_load_succeeded(
            {
                "packages": [{}],
                "chunks": [{"text": "내용"}],
                "search_index": object(),
                "api_key": "key",
            }
        )
        self.assertTrue(self.dialog.question_requirement_label.isHidden())


if __name__ == "__main__":
    unittest.main()
