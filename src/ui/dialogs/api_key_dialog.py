from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class ApiKeyDialog(QDialog):
    """Prompt the user to enter an OpenAI API key when none is configured."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("API Key 설정")
        self.setMinimumWidth(420)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        notice = QLabel(
            "OPENAI_API_KEY가 설정되지 않았습니다.\n"
            "아래에 API Key를 입력하면 이번 실행에만 사용됩니다."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)

        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("sk-proj-...")
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self._key_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_key(self) -> str:
        return self._key_input.text().strip()
