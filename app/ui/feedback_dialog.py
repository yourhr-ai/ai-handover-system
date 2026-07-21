import json
import urllib.error
import urllib.request

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.license import (
    LICENSE_SERVER_BASE_URL,
    LICENSE_SERVER_TIMEOUT_SECONDS,
    load_saved_license_code,
)

FEEDBACK_URL = f"{LICENSE_SERVER_BASE_URL}/api/handover/feedback"


def send_feedback_to_server(license_code: str, sender_name: str, message: str) -> str:
    """hr-ai-review 서버에 의견을 전송한다.

    반환값은 다음 중 하나:
    - "received": 정상 접수됨
    - "not_found": 서버에 존재하지 않는 라이선스 키
    - "network_error": 서버 요청 자체가 실패함 (타임아웃/네트워크 오류/이상 응답)
    """
    payload = json.dumps(
        {"licenseCode": license_code, "senderName": sender_name, "message": message}
    ).encode("utf-8")
    request = urllib.request.Request(
        FEEDBACK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=LICENSE_SERVER_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "not_found"
        return "network_error"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return "network_error"

    if isinstance(body, dict) and body.get("status") == "received":
        return "received"
    return "network_error"


class FeedbackDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("의견 보내기")
        self.setModal(True)
        self.resize(480, 420)

        notice_label = QLabel(
            "프로그램 사용 중 불편한 점이나 개선 의견을 자유롭게 남겨주세요."
        )
        notice_label.setWordWrap(True)

        sender_name_label = QLabel("보내는 분 이름")
        self.sender_name_input = QLineEdit()
        self.sender_name_input.setPlaceholderText("이름 (선택)")

        message_label = QLabel("의견 내용")
        self.message_input = QPlainTextEdit()
        self.message_input.setPlaceholderText(
            "예: 카카오톡 파일을 여러 개 한번에 올리고 싶어요"
        )
        self.message_input.setMinimumHeight(220)

        self.send_button = QPushButton("보내기")
        self.cancel_button = QPushButton("취소")

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.send_button)

        layout = QVBoxLayout(self)
        layout.addWidget(notice_label)
        layout.addWidget(sender_name_label)
        layout.addWidget(self.sender_name_input)
        layout.addWidget(message_label)
        layout.addWidget(self.message_input, 1)
        layout.addLayout(button_row)

        self.send_button.clicked.connect(self._send_feedback)
        self.cancel_button.clicked.connect(self.reject)

    def _send_feedback(self) -> None:
        message = self.message_input.toPlainText().strip()
        if not message:
            QMessageBox.warning(self, "의견 보내기", "의견 내용을 입력해 주세요")
            return

        license_code = load_saved_license_code()
        if not license_code:
            QMessageBox.warning(
                self,
                "의견 보내기",
                "라이선스 등록 후 이용할 수 있는 기능입니다.",
            )
            return

        sender_name = self.sender_name_input.text().strip()
        status = send_feedback_to_server(license_code, sender_name, message)

        if status == "received":
            QMessageBox.information(self, "의견 보내기", "소중한 의견 감사합니다.")
            self.accept()
            return
        if status == "not_found":
            QMessageBox.warning(
                self,
                "의견 보내기",
                "라이선스 확인에 실패했습니다. 담당 컨설턴트에게 문의해주세요.",
            )
            return
        QMessageBox.warning(
            self,
            "의견 보내기",
            "서버 연결에 실패했습니다. 인터넷 연결을 확인해주세요.",
        )
