from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QTextBlockFormat, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.services.analysis_result import HandoverQA
from app.services.handover_questions import HANDOVER_QUESTIONS


class HandoverQADialog(QDialog):
    def __init__(
        self,
        handover_qa: HandoverQA,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.handover_qa = handover_qa
        self.current_index = 0
        # [인수인계서 저장] 버튼에 통합된 뒤로는 이 팝업을 여는 경로가 하나뿐이라,
        # "저장하고 인수인계서 만들기"를 눌렀을 때만 True가 되어 호출자가 실제
        # 워드 문서 생성으로 이어갈지 판단하는 신호로 쓴다. 닫기/X/취소는 답변만
        # 보존하고(자동저장) False로 남겨 워드 생성으로 이어지지 않는다.
        self.should_proceed_to_save = False

        self.setWindowTitle("알려주세요")
        self.setModal(True)
        self.resize(720, 480)
        self.setMinimumSize(620, 420)

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(2000)
        self.autosave_timer.timeout.connect(self._save_current_answer)

        self.min_answer_notice_label = QLabel("1개 이상 작성하세요")
        self.min_answer_notice_label.setObjectName("qaMinAnswerNotice")
        self.min_answer_notice_label.setWordWrap(True)

        self.category_group = QButtonGroup(self)
        self.category_buttons: list[QRadioButton] = []
        self.category_labels: list[str] = []
        category_row = QHBoxLayout()
        category_row.setSpacing(12)
        for index, question in enumerate(HANDOVER_QUESTIONS):
            category_label = str(question["category_label"])
            button = QRadioButton(category_label)
            self.category_group.addButton(button, index)
            self.category_buttons.append(button)
            self.category_labels.append(category_label)
            category_row.addWidget(button)
        category_row.addStretch()
        self.save_button = QPushButton("저장")
        category_row.addWidget(self.save_button)
        self.close_button = QPushButton("닫기")
        category_row.addWidget(self.close_button)
        # 두 버튼을 [닫기] 버튼 폭에 맞춰 동일하게 고정한다 - 원래 텍스트가
        # "저장하고 인수인계서 만들기"로 길어서 실제로는 잘려 보였다.
        common_width = max(
            self.save_button.sizeHint().width(), self.close_button.sizeHint().width()
        )
        self.save_button.setFixedWidth(common_width)
        self.close_button.setFixedWidth(common_width)

        self.question_label = QLabel()
        question_font = QFont(self.question_label.font())
        question_font.setPointSize(15)
        question_font.setBold(True)
        self.question_label.setFont(question_font)
        self.question_label.setWordWrap(True)

        self.answer_input = QPlainTextEdit()
        self.answer_input.setObjectName("handoverAnswerInput")
        self.answer_input.setMinimumHeight(260)
        self.answer_input.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.answer_input.setWordWrapMode(
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        )
        self._applying_line_spacing = False
        self._apply_answer_line_spacing()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(18)
        layout.addWidget(self.min_answer_notice_label)
        layout.addLayout(category_row)
        layout.addWidget(self.question_label)
        layout.addWidget(self.answer_input, stretch=1)

        self._ensure_answer_slots()
        self.category_group.idClicked.connect(self._select_category)
        self.answer_input.textChanged.connect(
            self._update_category_completion_indicators
        )
        self.answer_input.textChanged.connect(self._apply_answer_line_spacing)
        self.answer_input.textChanged.connect(self._restart_autosave_timer)
        self.save_button.clicked.connect(self._save_and_proceed)
        self.close_button.clicked.connect(self._save_and_close)
        self.category_buttons[0].setChecked(True)
        self._load_question()
        self._update_category_completion_indicators()

    def _ensure_answer_slots(self) -> None:
        answer_count = len(HANDOVER_QUESTIONS)
        if len(self.handover_qa.answers) < answer_count:
            self.handover_qa.answers.extend(
                [""] * (answer_count - len(self.handover_qa.answers))
            )
        elif len(self.handover_qa.answers) > answer_count:
            del self.handover_qa.answers[answer_count:]

    def _save_current_answer(self) -> None:
        self.autosave_timer.stop()
        self._ensure_answer_slots()
        answer = self.answer_input.toPlainText()
        if self.handover_qa.answers[self.current_index] != answer:
            self.handover_qa.answers[self.current_index] = answer
            self.handover_qa.updatedat = datetime.now().isoformat(timespec="seconds")
        self._update_category_completion_indicators()

    def _restart_autosave_timer(self) -> None:
        self.autosave_timer.start()

    def _save_and_proceed(self) -> None:
        self._save_current_answer()
        self.should_proceed_to_save = True
        self.accept()

    def _apply_answer_line_spacing(self) -> None:
        if self._applying_line_spacing:
            return
        self._applying_line_spacing = True
        cursor = self.answer_input.textCursor()
        position = cursor.position()
        selection_start = cursor.selectionStart()
        selection_end = cursor.selectionEnd()
        document_cursor = QTextCursor(self.answer_input.document())
        document_cursor.select(QTextCursor.SelectionType.Document)
        block_format = QTextBlockFormat()
        block_format.setLineHeight(
            130.0,
            QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
        )
        document_cursor.mergeBlockFormat(block_format)
        cursor.setPosition(selection_start)
        cursor.setPosition(selection_end, QTextCursor.MoveMode.KeepAnchor)
        if selection_start == selection_end:
            cursor.setPosition(position)
        self.answer_input.setTextCursor(cursor)
        self._applying_line_spacing = False

    def _save_and_close(self) -> None:
        self._save_current_answer()
        self.accept()

    def _select_category(self, index: int) -> None:
        if index == self.current_index:
            return
        self._save_current_answer()
        self.current_index = index
        self._load_question()

    def _load_question(self) -> None:
        self._ensure_answer_slots()
        question = HANDOVER_QUESTIONS[self.current_index]
        self.question_label.setText(str(question["title"]))
        self.answer_input.setPlaceholderText(str(question["placeholder"]))
        self.answer_input.setPlainText(self.handover_qa.answers[self.current_index])
        self.answer_input.setFocus()
        self._update_category_completion_indicators()

    def _update_category_completion_indicators(self) -> None:
        self._ensure_answer_slots()
        for index, button in enumerate(self.category_buttons):
            answer = (
                self.answer_input.toPlainText()
                if index == self.current_index
                else self.handover_qa.answers[index]
            )
            completed = bool(answer.strip())
            button.setText(
                f"✓ {self.category_labels[index]}"
                if completed
                else self.category_labels[index]
            )
            button.setStyleSheet(
                "QRadioButton { color: #15803D; font-weight: 600; }"
                if completed
                else ""
            )

    def closeEvent(self, event) -> None:
        self._save_current_answer()
        event.accept()

    def reject(self) -> None:
        self._save_current_answer()
        super().reject()
