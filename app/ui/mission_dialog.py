import math
import re

from PySide6.QtCore import QPointF, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.license_credits import (
    claim_link_mission,
    get_missions,
    submit_quiz_mission,
    submit_review_mission,
)

_MISSION_TYPE_LABELS = {"link": "링크 열기", "quiz": "퀴즈 풀기", "review": "후기 작성"}

# hr-ai-review의 src/lib/handover-reviews/spam-check.ts(checkReviewSpam)와
# 동일한 규칙 - 서버가 최종 재검증하므로 완전히 동일할 필요는 없지만,
# 서버에서 막힐 내용을 제출 전에 먼저 걸러 사용자 경험을 개선한다.
_JAMO_ONLY_RUN = re.compile(r"[ㄱ-ㅎㅏ-ㅣ]{3,}")
_SAME_SYMBOL_RUN = re.compile(r"([^\w\s]|_)\1{2,}")
_LONG_WHITESPACE_RUN = re.compile(r"\s{5,}")
_MIN_CONTENT_LENGTH = 20
_DOMINANT_WORD_RATIO = 0.5


def _validate_review_spam(content: str) -> str | None:
    """검증을 통과하면 None, 아니면 사유 문자열을 반환한다."""
    trimmed = content.strip()
    if len(trimmed) < _MIN_CONTENT_LENGTH:
        return f"후기 내용은 최소 {_MIN_CONTENT_LENGTH}자 이상 작성해주세요."
    if _JAMO_ONLY_RUN.search(trimmed):
        return "자음/모음만 3자 이상 연속으로 사용할 수 없습니다."
    if _SAME_SYMBOL_RUN.search(trimmed):
        return "같은 기호를 3회 이상 연속으로 사용할 수 없습니다."
    if _LONG_WHITESPACE_RUN.search(trimmed):
        return "공백을 5칸 이상 연속으로 사용할 수 없습니다."

    tokens = trimmed.split()
    if tokens:
        counts: dict[str, int] = {}
        for token in tokens:
            key = token.lower()
            counts[key] = counts.get(key, 0) + 1
        if max(counts.values()) / len(tokens) >= _DOMINANT_WORD_RATIO:
            return "같은 단어를 과도하게 반복할 수 없습니다."
    return None


def _apply_content_based_height(dialog: QDialog, width: int, min_height: int, max_height: int) -> None:
    """다이얼로그의 폭은 고정하고, 실제 내용(레이아웃 sizeHint)에 맞춰 높이만
    동적으로 계산해 적용한다. 내용이 적으면 작아지고 많으면 커지되,
    min_height/max_height 범위를 벗어나지 않게 clamp한다."""
    layout = dialog.layout()
    if layout is None:
        return
    # 일반 sizeHint()는 폭 제약과 무관한 "선호 크기"라 wordWrap 라벨처럼
    # 폭에 따라 줄 수가 바뀌는 위젯의 실제 필요 높이를 반영하지 못한다.
    # heightForWidth(width)를 써야 그 폭에서 줄바꿈된 실제 높이를 얻는다.
    if layout.hasHeightForWidth():
        content_height = layout.heightForWidth(width)
    else:
        content_height = layout.sizeHint().height()
    target_height = max(min_height, min(content_height, max_height))
    dialog.resize(width, target_height)


def _build_star_path(center: QPointF, outer_radius: float, inner_radius: float) -> QPainterPath:
    """5각 별 모양의 QPainterPath를 만든다(각진 유니코드 글리프 대신 직접
    그려서 채움/테두리를 독립적으로 제어할 수 있게 한다)."""
    path = QPainterPath()
    points: list[QPointF] = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        radius = outer_radius if i % 2 == 0 else inner_radius
        points.append(QPointF(center.x() + radius * math.cos(angle), center.y() - radius * math.sin(angle)))
    path.moveTo(points[0])
    for point in points[1:]:
        path.lineTo(point)
    path.closeSubpath()
    return path


class StarRatingWidget(QWidget):
    """별 5개, 마우스 호버 시 왼쪽부터 미리보기로 채워지고 클릭 시 확정된다.

    채워진 별은 노란색(#F59E0B)으로 꽉 채우고, 빈 별은 옅은 회색(#D1D5DB)
    테두리만 그려서 채움/빈 상태의 대비를 뚜렷하게 한다."""

    ratingChanged = Signal(int)

    _STAR_COUNT = 5
    # 별 사이의 "실제 화면(edge-to-edge)" 간격 px. 이전 구현은 이 값을 슬롯
    # 계산에만 썼고, 위젯이 가로로 늘어나면 슬롯(=늘어난폭/5)이 커지는데 별은
    # 높이로 크기가 고정돼 슬롯 안 빈 공간이 그대로 간격이 됐다(실측 51px).
    # 그래서 아래 기하 계산은 늘어난 위젯 폭과 무관하게 별 폭+간격으로 직접
    # 배치하고 그룹을 가운데 정렬한다.
    _STAR_GAP = 10
    # 별 바깥 반지름. 이전(높이 42로 캡되어 실측 outer_radius≈18.9, 별 폭≈35px)
    # 대비 80%로 축소: 18.9 * 0.8 = 15.12 (원래 대비로는 70%*80%=56%).
    _STAR_OUTER_RADIUS = 15.12
    _STAR_INNER_RATIO = 0.5
    _FILLED_COLOR = QColor("#F59E0B")
    _EMPTY_OUTLINE_COLOR = QColor("#D1D5DB")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rating = 0
        self._hover_index = -1
        self.setMouseTracking(True)
        self.setFixedHeight(38)
        # 그룹(별5 + 간격4)의 실제 가로폭 이상으로만 최소폭을 잡는다. 위젯이 더
        # 넓어져도 _group_left()가 그룹을 가운데로 배치하므로 좌우 여백은 대칭.
        self.setMinimumWidth(int(self._content_width()) + 4)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def rating(self) -> int:
        return self._rating

    def _inner_radius(self) -> float:
        return self._STAR_OUTER_RADIUS * self._STAR_INNER_RATIO

    def _star_width(self) -> float:
        """실제로 그려지는 별 1개의 가로폭(경로 boundingRect 기준 정확값)."""
        path = _build_star_path(QPointF(0.0, 0.0), self._STAR_OUTER_RADIUS, self._inner_radius())
        return path.boundingRect().width()

    def _content_width(self) -> float:
        """별 5개 + 간격 4개의 전체 그룹 가로폭."""
        return self._STAR_COUNT * self._star_width() + (self._STAR_COUNT - 1) * self._STAR_GAP

    def _group_left(self) -> float:
        """그룹을 위젯 안에서 가운데 정렬했을 때 그룹 왼쪽 x."""
        return (self.width() - self._content_width()) / 2.0

    def _star_center_x(self, index: int) -> float:
        star_w = self._star_width()
        return self._group_left() + star_w / 2.0 + index * (star_w + self._STAR_GAP)

    def _index_at(self, x: float) -> int:
        star_w = self._star_width()
        step = star_w + self._STAR_GAP
        if step <= 0:
            return 0
        first_center = self._group_left() + star_w / 2.0
        index = round((x - first_center) / step)
        return max(0, min(self._STAR_COUNT - 1, int(index)))

    def mouseMoveEvent(self, event) -> None:
        self._hover_index = self._index_at(event.position().x())
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover_index = -1
        self.update()

    def mousePressEvent(self, event) -> None:
        self._rating = self._index_at(event.position().x()) + 1
        self.ratingChanged.emit(self._rating)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        filled_count = (self._hover_index + 1) if self._hover_index >= 0 else self._rating
        outer_radius = self._STAR_OUTER_RADIUS
        inner_radius = self._inner_radius()
        center_y = self.height() / 2
        for i in range(self._STAR_COUNT):
            center = QPointF(self._star_center_x(i), center_y)
            path = _build_star_path(center, outer_radius, inner_radius)
            if i < filled_count:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._FILLED_COLOR)
            else:
                painter.setPen(QPen(self._EMPTY_OUTLINE_COLOR, 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.end()


class MissionQuizDialog(QDialog):
    _MIN_HEIGHT = 180
    _MAX_HEIGHT = 560
    _WIDTH = 420
    _SUBMIT_LABEL = "제출"
    _CHECKING_LABEL = "확인 중입니다"

    def __init__(self, license_code: str, mission: dict, parent=None) -> None:
        super().__init__(parent)
        self.license_code = license_code
        self.mission = mission
        self.setWindowTitle(mission.get("missionName") or "퀴즈")
        self.setModal(True)
        self._submitting = False

        question_label = QLabel(mission.get("missionContent") or "")
        question_label.setWordWrap(True)

        self.answer_input = QLineEdit()
        self.answer_input.setPlaceholderText("정답을 입력하세요")

        self.submit_button = QPushButton(self._SUBMIT_LABEL)
        self.cancel_button = QPushButton("취소")

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.submit_button)

        layout = QVBoxLayout(self)
        layout.addWidget(question_label)
        layout.addWidget(self.answer_input)
        layout.addStretch()
        layout.addLayout(button_row)

        self.submit_button.clicked.connect(self._submit)
        self.cancel_button.clicked.connect(self.reject)
        self.answer_input.returnPressed.connect(self._submit)

        _apply_content_based_height(self, self._WIDTH, self._MIN_HEIGHT, self._MAX_HEIGHT)

    def _submit(self) -> None:
        if self._submitting:
            return

        answer = self.answer_input.text().strip()
        if not answer:
            QMessageBox.warning(self, "퀴즈", "답을 입력해주세요.")
            return

        self._submitting = True
        self.submit_button.setText(self._CHECKING_LABEL)
        self.submit_button.setEnabled(False)
        QApplication.processEvents()
        try:
            result = submit_quiz_mission(self.license_code, self.mission["id"], answer)
        finally:
            self.submit_button.setText(self._SUBMIT_LABEL)
            self.submit_button.setEnabled(True)
            self._submitting = False

        if result is None:
            QMessageBox.warning(self, "퀴즈", "서버 연결에 실패했습니다. 인터넷 연결을 확인해주세요.")
            return

        status_code, body = result
        if status_code == 200:
            credits = body.get("creditsGranted", 0)
            if body.get("granted"):
                QMessageBox.information(self, "퀴즈", f"정답입니다! 크레딧이 지급되었습니다. (+{credits})")
            else:
                QMessageBox.information(self, "퀴즈", "정답입니다! 잠시 후 크레딧이 지급됩니다.")
            self.accept()
            return
        if status_code == 400 and body.get("status") == "incorrect_answer":
            QMessageBox.warning(self, "퀴즈", "정답이 아닙니다.")
            return
        if status_code == 409:
            QMessageBox.information(self, "퀴즈", "이미 완료한 미션입니다.")
            self.accept()
            return
        QMessageBox.warning(self, "퀴즈", body.get("message") or "요청을 처리하지 못했습니다.")


_REVIEW_EXAMPLE_TEXT = (
    "퇴사자 인수인계 자료가 폴더 여기저기 흩어져 있어서 늘 걱정이었는데, "
    "폴더 하나만 선택하니 알아서 다 정리해줬어요. 인계 문서 만드는 데 하루 종일 걸리던 게 "
    "10분으로 줄었습니다. 다음 퇴사자 나올 때도 이거 하나면 될 것 같아요."
)


class MissionReviewDialog(QDialog):
    _MIN_HEIGHT = 320
    _MAX_HEIGHT = 620
    _WIDTH = 440
    _STAR_TO_INPUT_GAP = 20
    _SUBMIT_LABEL = "제출"
    _SUBMITTING_LABEL = "후기 등록 및 크레딧 지급 중입니다"

    def __init__(self, license_code: str, product_id: str, mission: dict, parent=None) -> None:
        super().__init__(parent)
        self.license_code = license_code
        self.product_id = product_id
        self.mission = mission
        self.setWindowTitle(mission.get("missionName") or "후기 작성")
        self.setModal(True)
        self._submitting = False

        notice = QLabel(
            (mission.get("missionContent") or "실제 사용 경험을 20자 이상 자유롭게 남겨주세요.")
            + "\n(20자 이상 작성해주세요)"
        )
        notice.setWordWrap(True)

        self.content_input = QPlainTextEdit()
        self.content_input.setPlaceholderText(f"예: {_REVIEW_EXAMPLE_TEXT}")
        self.content_input.setMinimumHeight(160)

        # "별점" 라벨 없이 별 아이콘만으로 충분히 인지 가능하도록 라벨을 두지 않는다.
        self.star_widget = StarRatingWidget()
        self.star_widget.setAccessibleName("별점 선택")

        self.submit_button = QPushButton(self._SUBMIT_LABEL)
        self.cancel_button = QPushButton("취소")

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.submit_button)

        layout = QVBoxLayout(self)
        layout.addWidget(notice)
        layout.addWidget(self.content_input, 1)
        # 레이아웃이 위젯 사이마다 자체 spacing()을 이미 넣어주므로, 그만큼을
        # 뺀 나머지만 addSpacing으로 더해서 별점-입력란 사이 총 여백이
        # 정확히 _STAR_TO_INPUT_GAP(20)px가 되게 한다.
        layout.addSpacing(max(0, self._STAR_TO_INPUT_GAP - layout.spacing()))
        layout.addWidget(self.star_widget)
        layout.addLayout(button_row)

        self.submit_button.clicked.connect(self._submit)
        self.cancel_button.clicked.connect(self.reject)

        _apply_content_based_height(self, self._WIDTH, self._MIN_HEIGHT, self._MAX_HEIGHT)

    def _submit(self) -> None:
        if self._submitting:
            return

        content = self.content_input.toPlainText()
        rating = self.star_widget.rating()
        if rating < 1:
            QMessageBox.warning(self, "후기 작성", "별점을 선택해주세요.")
            return

        spam_reason = _validate_review_spam(content)
        if spam_reason:
            QMessageBox.warning(self, "후기 작성", f"다시 작성해주세요.\n\n{spam_reason}")
            return

        if not self.product_id:
            QMessageBox.warning(self, "후기 작성", "상품 정보를 확인하지 못했습니다. 잠시 후 다시 시도해주세요.")
            return

        self._submitting = True
        self.submit_button.setText(self._SUBMITTING_LABEL)
        self.submit_button.setEnabled(False)
        QApplication.processEvents()
        try:
            result = submit_review_mission(self.license_code, self.product_id, content, rating)
        finally:
            self.submit_button.setText(self._SUBMIT_LABEL)
            self.submit_button.setEnabled(True)
            self._submitting = False

        if result is None:
            QMessageBox.warning(self, "후기 작성", "서버 연결에 실패했습니다. 인터넷 연결을 확인해주세요.")
            return

        status_code, body = result
        if status_code == 200:
            completion = body.get("missionCompletion")
            if isinstance(completion, dict) and completion.get("granted"):
                credits = completion.get("creditsGranted", 0)
                QMessageBox.information(self, "후기 작성", f"후기가 등록되었습니다. 크레딧이 지급되었습니다. (+{credits})")
            elif isinstance(completion, dict):
                QMessageBox.information(self, "후기 작성", "후기가 등록되었습니다. 잠시 후 크레딧이 지급됩니다.")
            else:
                QMessageBox.information(self, "후기 작성", "후기가 등록되었습니다. 감사합니다.")
            self.accept()
            return
        if status_code == 400:
            QMessageBox.warning(self, "후기 작성", body.get("message") or "다시 작성해주세요.")
            return
        QMessageBox.warning(self, "후기 작성", body.get("message") or "요청을 처리하지 못했습니다.")


class MissionRowWidget(QFrame):
    def __init__(self, license_code: str, product_id: str, mission: dict, on_credit_granted, parent=None) -> None:
        super().__init__(parent)
        self.license_code = license_code
        self.product_id = product_id
        self.mission = mission
        self.on_credit_granted = on_credit_granted

        self.setObjectName("missionRow")
        self.setStyleSheet(
            "QFrame#missionRow { background: #FAFAFA; border: 1px solid #E5E7EB; border-radius: 8px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label, 1)

        self.action_button = QPushButton()
        self.action_button.setFixedWidth(90)
        self.complete_button = QPushButton("완료")
        self.complete_button.setFixedWidth(70)
        self.complete_button.setVisible(False)
        layout.addWidget(self.action_button)
        layout.addWidget(self.complete_button)

        self.action_button.clicked.connect(self._handle_action_click)
        self.complete_button.clicked.connect(self._handle_complete_click)

        self._refresh_view()

    def _refresh_view(self) -> None:
        name = self.mission.get("missionName", "")
        credits = self.mission.get("rewardCredits", 0)
        self.info_label.setText(f"{name}\n+{credits} 크레딧")

        mission_type = self.mission.get("missionType")
        completed = bool(self.mission.get("completed"))

        if completed:
            self.action_button.setText("완료됨")
            self.action_button.setEnabled(False)
            self.complete_button.setVisible(False)
            return

        self.action_button.setText(_MISSION_TYPE_LABELS.get(mission_type, "참여하기"))
        self.action_button.setEnabled(True)
        self.complete_button.setVisible(False)

    def _handle_action_click(self) -> None:
        mission_type = self.mission.get("missionType")
        if mission_type == "link":
            self._handle_link_click()
        elif mission_type == "quiz":
            self._handle_quiz_click()
        elif mission_type == "review":
            self._handle_review_click()

    def _handle_link_click(self) -> None:
        url = self.mission.get("missionLink") or ""
        if url:
            QDesktopServices.openUrl(QUrl(url))
        self.action_button.setEnabled(False)
        self.complete_button.setVisible(True)
        self.complete_button.setEnabled(False)
        QTimer.singleShot(3000, lambda: self.complete_button.setEnabled(True))

    def _handle_complete_click(self) -> None:
        result = claim_link_mission(self.license_code, self.mission["id"])
        if result is None:
            QMessageBox.warning(self, "크레딧 받기", "서버 연결에 실패했습니다. 인터넷 연결을 확인해주세요.")
            return

        status_code, body = result
        if status_code == 200:
            credits = body.get("creditsGranted", 0)
            if body.get("granted"):
                QMessageBox.information(self, "크레딧 받기", f"크레딧이 지급되었습니다. (+{credits})")
            else:
                QMessageBox.information(self, "크레딧 받기", "완료 처리되었습니다. 잠시 후 크레딧이 지급됩니다.")
            self.mission["completed"] = True
            self._refresh_view()
            self.on_credit_granted()
            return
        if status_code == 409:
            QMessageBox.information(self, "크레딧 받기", "이미 완료한 미션입니다.")
            self.mission["completed"] = True
            self._refresh_view()
            return
        QMessageBox.warning(self, "크레딧 받기", body.get("message") or "요청을 처리하지 못했습니다.")
        self.action_button.setEnabled(True)

    def _handle_quiz_click(self) -> None:
        dialog = MissionQuizDialog(self.license_code, self.mission, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.mission["completed"] = True
            self._refresh_view()
            self.on_credit_granted()

    def _handle_review_click(self) -> None:
        dialog = MissionReviewDialog(self.license_code, self.product_id, self.mission, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.mission["completed"] = True
            self._refresh_view()
            self.on_credit_granted()


class MissionListDialog(QDialog):
    _MIN_HEIGHT = 220
    _MAX_HEIGHT = 640
    _WIDTH = 460

    def __init__(self, license_code: str, on_credit_granted, parent=None) -> None:
        super().__init__(parent)
        self.license_code = license_code
        self.on_credit_granted = on_credit_granted
        self.product_id: str | None = None

        self.setWindowTitle("크레딧 받기")
        self.setModal(True)
        self.resize(self._WIDTH, self._MIN_HEIGHT)

        self.info_label = QLabel("미션을 완료하면 크레딧을 받을 수 있어요.")
        self.info_label.setWordWrap(True)

        # 각 미션 행은 라벨+버튼을 함께 담은 커스텀 위젯이라, QListWidget의
        # setItemWidget()으로 넣으면 접근성 트리(스크린리더/UI 자동화)에서
        # 그 안의 버튼들이 보이지 않는다. 일반 QVBoxLayout+QScrollArea로
        # 바꿔 각 행의 버튼이 정상적으로 접근 가능하도록 한다.
        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)
        self.rows_layout.addStretch()

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidget(self.rows_container)

        self.close_button = QPushButton("닫기")

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.info_label)
        layout.addWidget(self.scroll_area, 1)
        layout.addLayout(button_row)

        self.close_button.clicked.connect(self.accept)

        self._load_missions()
        self._apply_dynamic_height()

    def _apply_dynamic_height(self) -> None:
        """QScrollArea는 그 자체 sizeHint가 내용물(미션 행 개수)에 따라
        커지지 않으므로(스크롤 영역이라 일부러 그렇게 설계됨), rows_container의
        실제 sizeHint를 직접 더해서 팝업 높이를 계산한다."""
        layout = self.layout()
        layout.activate()
        chrome_height = self.info_label.sizeHint().height() + self.close_button.sizeHint().height()
        spacing_total = layout.spacing() * 2
        margins = layout.contentsMargins()
        content_height = (
            self.rows_container.sizeHint().height()
            + chrome_height
            + spacing_total
            + margins.top()
            + margins.bottom()
        )
        target_height = max(self._MIN_HEIGHT, min(content_height, self._MAX_HEIGHT))
        self.resize(self._WIDTH, target_height)

    def _clear_rows(self) -> None:
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _insert_row(self, widget: QWidget) -> None:
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, widget)

    def _show_placeholder(self, text: str) -> None:
        self._clear_rows()
        placeholder = QLabel(text)
        placeholder.setWordWrap(True)
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._insert_row(placeholder)

    def _load_missions(self) -> None:
        self._clear_rows()
        result = get_missions(self.license_code)
        if result is None:
            self._show_placeholder("서버 연결에 실패했습니다. 인터넷 연결을 확인해주세요.")
            return

        status_code, body = result
        if status_code != 200 or "missions" not in body:
            self._show_placeholder(body.get("message") or "미션 목록을 불러오지 못했습니다.")
            return

        self.product_id = body.get("productId")
        missions = body.get("missions") or []
        if not missions:
            self._show_placeholder("현재 진행 가능한 미션이 없습니다.")
            return

        for mission in missions:
            self._add_mission_item(mission)

    def _add_mission_item(self, mission: dict) -> None:
        row_widget = MissionRowWidget(
            self.license_code, self.product_id, mission, self._handle_credit_granted, self
        )
        self._insert_row(row_widget)

    def _handle_credit_granted(self) -> None:
        self.on_credit_granted()
