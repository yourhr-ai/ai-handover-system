from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextOption
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
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

        self.setWindowTitle("알려주세요")
        self.setModal(True)
        self.resize(720, 480)
        self.setMinimumSize(620, 420)

        self.category_group = QButtonGroup(self)
        self.category_buttons: list[QRadioButton] = []
        category_row = QHBoxLayout()
        category_row.setSpacing(12)
        for index, question in enumerate(HANDOVER_QUESTIONS):
            button = QRadioButton(str(question["category_label"]))
            self.category_group.addButton(button, index)
            self.category_buttons.append(button)
            category_row.addWidget(button)
        category_row.addStretch()

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(18)
        layout.addLayout(category_row)
        layout.addWidget(self.question_label)
        layout.addWidget(self.answer_input, stretch=1)

        self._ensure_answer_slots()
        self.category_group.idClicked.connect(self._select_category)
        self.category_buttons[0].setChecked(True)
        self._load_question()

    def _ensure_answer_slots(self) -> None:
        answer_count = len(HANDOVER_QUESTIONS)
        if len(self.handover_qa.answers) < answer_count:
            self.handover_qa.answers.extend(
                [""] * (answer_count - len(self.handover_qa.answers))
            )
        elif len(self.handover_qa.answers) > answer_count:
            del self.handover_qa.answers[answer_count:]

    def _save_current_answer(self) -> None:
        self._ensure_answer_slots()
        answer = self.answer_input.toPlainText()
        if self.handover_qa.answers[self.current_index] != answer:
            self.handover_qa.answers[self.current_index] = answer
            self.handover_qa.updatedat = datetime.now().isoformat(timespec="seconds")

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

    def closeEvent(self, event) -> None:
        self._save_current_answer()
        event.accept()

    def reject(self) -> None:
        self._save_current_answer()
        super().reject()
