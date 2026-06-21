from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _fmt_duration(seconds: int) -> str:
    """초 단위를 '2분 35초' 형식으로 변환한다."""
    if seconds <= 0:
        return "잠시 후"
    if seconds < 60:
        return f"약 {seconds}초"
    m, s = divmod(seconds, 60)
    return f"{m}분 {s:02d}초" if s else f"{m}분"


class ActionPanel(QWidget):
    """버튼 및 진행 상태를 표시하는 패널."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ── 시작 / 중단 버튼 행 ──────────────────────────────────────
        btn_top = QHBoxLayout()
        btn_top.setSpacing(8)

        self.analyze_btn = QPushButton("업무 복원 시작")
        self.analyze_btn.setFixedHeight(44)
        self.analyze_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #1F497D; color: white;"
            "  font-size: 14px; font-weight: bold; border-radius: 6px;"
            "}"
            "QPushButton:hover { background-color: #2E6DA4; }"
            "QPushButton:disabled { background-color: #aaa; }"
        )
        btn_top.addWidget(self.analyze_btn, stretch=3)

        self.cancel_btn = QPushButton("분석 중단")
        self.cancel_btn.setFixedHeight(44)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #C0392B; color: white;"
            "  font-size: 13px; font-weight: bold; border-radius: 6px;"
            "}"
            "QPushButton:hover { background-color: #E74C3C; }"
            "QPushButton:disabled { background-color: #aaa; color: #ddd; }"
        )
        btn_top.addWidget(self.cancel_btn, stretch=1)

        layout.addLayout(btn_top)

        # ── 예상 시간 정보 영역 ───────────────────────────────────────
        self._time_frame = QFrame()
        self._time_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._time_frame.setStyleSheet(
            "QFrame { background-color: #EBF3FB; border: 1px solid #BDD7EE; border-radius: 4px; }"
        )
        time_layout = QVBoxLayout(self._time_frame)
        time_layout.setContentsMargins(10, 6, 10, 6)
        time_layout.setSpacing(2)

        self._time_header = QLabel("")
        self._time_header.setStyleSheet("color: #1F497D; font-size: 11px; font-weight: bold; border: none;")
        self._time_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        time_layout.addWidget(self._time_header)

        self._time_detail = QLabel("")
        self._time_detail.setStyleSheet("color: #555; font-size: 10px; border: none;")
        self._time_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        time_layout.addWidget(self._time_detail)

        self._time_frame.hide()
        layout.addWidget(self._time_frame)

        # ── 진행 상태 ─────────────────────────────────────────────
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.hide()
        layout.addWidget(self._progress_bar)

        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: #555; font-size: 11px;")
        self._status_label.setWordWrap(True)
        self._status_label.hide()
        layout.addWidget(self._status_label)

        # ── Word 저장 / 열기 버튼 ─────────────────────────────────
        btn_row = QHBoxLayout()

        self.save_btn = QPushButton("Word 저장")
        self.save_btn.setFixedHeight(36)
        self.save_btn.setEnabled(False)
        self.save_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #217346; color: white;"
            "  font-size: 13px; border-radius: 5px;"
            "}"
            "QPushButton:hover { background-color: #2E8C58; }"
            "QPushButton:disabled { background-color: #aaa; }"
        )
        btn_row.addWidget(self.save_btn)

        self.open_btn = QPushButton("Word 열기")
        self.open_btn.setFixedHeight(36)
        self.open_btn.setEnabled(False)
        self.open_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #555; color: white;"
            "  font-size: 13px; border-radius: 5px;"
            "}"
            "QPushButton:hover { background-color: #333; }"
            "QPushButton:disabled { background-color: #aaa; }"
        )
        btn_row.addWidget(self.open_btn)

        layout.addLayout(btn_row)

        # ── 실행 모드 체크박스 (상호 배타) ──────────────────────────────
        _mode_style_base = (
            "QCheckBox {{ color: {fg}; font-size: 11px; font-weight: bold; padding: 4px 0 0 2px; }}"
            "QCheckBox::indicator {{ width: 14px; height: 14px; }}"
            "QCheckBox:checked {{ color: {fg_on}; }}"
        )

        self.light_mode_cb = QCheckBox(
            "라이트 모드  (빠른 분석 / 저비용 — 핵심 5개 섹션)"
        )
        self.light_mode_cb.setStyleSheet(
            _mode_style_base.format(fg="#155A1A", fg_on="#1A7D26")
        )
        layout.addWidget(self.light_mode_cb)

        self.test_mode_cb = QCheckBox("테스트 모드  (GPT 없이 실행 — 비용 $0, 규칙 기반 보고서)")
        self.test_mode_cb.setStyleSheet(
            _mode_style_base.format(fg="#7D4B00", fg_on="#B45309")
        )
        layout.addWidget(self.test_mode_cb)

        # 상호 배타 연결
        self.light_mode_cb.stateChanged.connect(self._on_light_mode_changed)
        self.test_mode_cb.stateChanged.connect(self._on_test_mode_changed)

    # ── 모드 체크박스 상호 배타 ──────────────────────────────────────
    def _on_light_mode_changed(self, state: int) -> None:
        if state and self.test_mode_cb.isChecked():
            self.test_mode_cb.setChecked(False)

    def _on_test_mode_changed(self, state: int) -> None:
        if state and self.light_mode_cb.isChecked():
            self.light_mode_cb.setChecked(False)

    def is_light_mode(self) -> bool:
        """라이트 모드 체크박스 상태를 반환한다."""
        return self.light_mode_cb.isChecked()

    def is_test_mode(self) -> bool:
        """테스트 모드 체크박스 상태를 반환한다."""
        return self.test_mode_cb.isChecked()

    # ── 상태 제어 ──────────────────────────────────────────────────────
    def set_loading(self, loading: bool, status: str = "") -> None:
        """분석 시작/종료 시 버튼 상태와 진행 표시를 전환한다."""
        self.analyze_btn.setEnabled(not loading)
        self.cancel_btn.setEnabled(loading)

        if loading:
            self._progress_bar.show()
            self._status_label.setText(status)
            self._status_label.show()
        else:
            self._progress_bar.hide()
            self._status_label.hide()
            self._time_frame.hide()

    def set_result_ready(self, ready: bool) -> None:
        self.save_btn.setEnabled(ready)
        self.open_btn.setEnabled(ready)

    def update_status(self, message: str) -> None:
        self._status_label.setText(message)

    def show_time_estimate(self, info: dict) -> None:
        """
        필터링 완료 후 예상 시간 정보를 표시한다.

        info 키: total_files, date_filtered, image_filtered, dedup_filtered,
                 analysis_targets, est_extract_sec, est_summary_sec,
                 est_ai_sec, est_total_sec
        """
        total = info.get("total_files", 0)
        targets = info.get("analysis_targets", 0)
        est_total = info.get("est_total_sec", 0)
        est_extract = info.get("est_extract_sec", 0)
        est_summary = info.get("est_summary_sec", 0)
        est_ai = info.get("est_ai_sec", 0)

        date_exc = info.get("date_filtered", 0)
        img_exc = info.get("image_filtered", 0)
        dedup_exc = info.get("dedup_filtered", 0)

        header = (
            f"총 파일: {total:,}개  |  분석 대상: {targets:,}개  |  "
            f"예상 소요: {_fmt_duration(est_total)}"
        )
        detail_parts = []
        if date_exc:
            detail_parts.append(f"30일 초과 {date_exc:,}개")
        if img_exc:
            detail_parts.append(f"이미지 {img_exc:,}개")
        if dedup_exc:
            detail_parts.append(f"구버전 {dedup_exc:,}개")

        detail = ""
        if detail_parts:
            detail = "제외: " + " · ".join(detail_parts) + "  |  "
        detail += (
            f"추출 ~{_fmt_duration(est_extract)}  "
            f"요약 ~{_fmt_duration(est_summary)}  "
            f"AI ~{_fmt_duration(est_ai)}"
        )

        self._time_header.setText(header)
        self._time_detail.setText(detail)
        self._time_frame.show()

    def update_progress_with_eta(
        self,
        stage: str,
        done: int,
        total: int,
        remaining_sec: int,
        expected_finish: str = "",
    ) -> None:
        """
        진행 중 단계명·진행률·남은 시간·예상 종료 시각을 표시한다.
        """
        pct = int(done / total * 100) if total > 0 else 0
        duration_str = _fmt_duration(remaining_sec)
        finish_str = f"  |  예상 종료: {expected_finish}" if expected_finish else ""
        display_str = f"예상 남은 시간: {duration_str}{finish_str}"
        print(
            f"[ETA DISPLAY]\n"
            f"  remaining_sec           = {remaining_sec}\n"
            f"  _fmt_duration 결과      = {duration_str!r}\n"
            f"  expected_finish         = {expected_finish!r}\n"
            f"  실제 화면 표시 문자열   = {display_str!r}"
        )
        self._time_header.setText(
            f"현재 단계: {stage}  |  진행률: {pct}%  ({done:,}/{total:,}개)"
        )
        self._time_detail.setText(display_str)

    def show_cancelled(self) -> None:
        """중단 완료 시 UI를 초기 상태로 복원하고 중단 메시지를 표시한다."""
        self.analyze_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._progress_bar.hide()
        self._time_frame.hide()
        self._status_label.setText("사용자 요청으로 분석이 중단되었습니다.")
        self._status_label.setStyleSheet("color: #C0392B; font-size: 11px; font-weight: bold;")
        self._status_label.show()

    def reset_status_style(self) -> None:
        self._status_label.setStyleSheet("color: #555; font-size: 11px;")
