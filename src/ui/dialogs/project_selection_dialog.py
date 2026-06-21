from __future__ import annotations

"""
프로젝트 요약 미리보기 + 선택 다이얼로그.

GPT 호출 전에 분석할 프로젝트를 사용자가 선택한다.
표시 항목: 프로젝트명, 고객사명, 문서 수, 중요 정보 수, 현재 진행상태
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.token_estimator import EST_OUTPUT_TOKENS, chars_to_tokens, estimate_cost


class _ProjectItem(QWidget):
    """프로젝트 목록의 개별 항목 (체크박스 + 정보 표시)."""

    toggled = Signal()

    _STATUS_COLORS: dict[str, str] = {
        "현재 진행 중": "#1F7D1F",
        "최근 완료":    "#1F497D",
        "과거 완료":    "#888888",
        "[정보 부족]":  "#999999",
    }

    def __init__(self, data: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_key: str = data["project_key"]
        self._summary_chars: int = data.get("summary_chars", 0)
        self._critical_info_count: int = data.get("critical_info_count", 0)

        self._build_ui(data)

    def _build_ui(self, data: dict) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(10)

        # 체크박스
        self._chk = QCheckBox()
        self._chk.setChecked(True)
        self._chk.stateChanged.connect(lambda _: self.toggled.emit())
        self._chk.setFixedWidth(20)
        root.addWidget(self._chk, alignment=Qt.AlignmentFlag.AlignTop)

        # 정보 영역
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        info_col.setContentsMargins(0, 0, 0, 0)

        # 첫 행: 프로젝트명 (고객사명)
        project_name = data.get("project_name", "")
        client_name = data.get("client_name", "")
        if project_name and project_name != "[정보 부족]":
            header_text = project_name
            if client_name and client_name != "[정보 부족]":
                header_text += f"  ({client_name})"
        else:
            header_text = data.get("project_key", "")

        name_lbl = QLabel(header_text)
        name_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #1A1A1A;")
        name_lbl.setWordWrap(True)
        info_col.addWidget(name_lbl)

        # 둘째 행: 문서 수 · 중요정보 수
        doc_count = data.get("doc_count", 0)
        critical_count = data.get("critical_info_count", 0)
        detail_parts = [f"문서 {doc_count}개"]
        if critical_count > 0:
            detail_parts.append(f"중요정보 {critical_count}개")
        else:
            detail_parts.append("중요정보 없음")
        detail_lbl = QLabel("  ·  ".join(detail_parts))
        detail_lbl.setStyleSheet("font-size: 11px; color: #666;")
        info_col.addWidget(detail_lbl)

        root.addLayout(info_col, stretch=1)

        # 진행상태 뱃지
        status = data.get("current_status", "[정보 부족]")
        badge_color = self._STATUS_COLORS.get(status, "#999999")
        status_lbl = QLabel(status)
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_lbl.setFixedWidth(90)
        status_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: bold; color: white; "
            f"background-color: {badge_color}; border-radius: 4px; padding: 2px 4px;"
        )
        root.addWidget(status_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

    # ── 프로퍼티 ──────────────────────────────────────────────────────
    @property
    def is_checked(self) -> bool:
        return self._chk.isChecked()

    @property
    def project_key(self) -> str:
        return self._project_key

    @property
    def summary_chars(self) -> int:
        return self._summary_chars

    @property
    def critical_info_count(self) -> int:
        return self._critical_info_count

    def set_checked(self, checked: bool) -> None:
        self._chk.setChecked(checked)


class ProjectSelectionDialog(QDialog):
    """
    GPT 호출 전 분석할 프로젝트를 사용자가 선택하는 다이얼로그.

    사용자가 선택한 프로젝트만 GPT에 전달된다.
    """

    def __init__(self, projects_data: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("프로젝트 선택")
        self.setMinimumSize(540, 500)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._items: list[_ProjectItem] = []
        self._build_ui(projects_data)

    def _build_ui(self, projects_data: list[dict]) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 14, 16, 14)

        # ── 헤더 ──────────────────────────────────────────────────────
        total_projects = len(projects_data)
        total_docs = sum(d.get("doc_count", 0) for d in projects_data)
        title_lbl = QLabel("GPT에 전달할 프로젝트를 선택하세요")
        title_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #1F497D;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_lbl)

        sub_lbl = QLabel(f"총 {total_projects}개 프로젝트  ·  {total_docs}개 문서")
        sub_lbl.setStyleSheet("font-size: 11px; color: #888;")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sub_lbl)

        # ── 전체 선택/해제 버튼 ────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        select_all_btn = QPushButton("전체 선택")
        select_all_btn.setFixedHeight(28)
        select_all_btn.setStyleSheet(
            "QPushButton { background-color: #E8EEF5; color: #1F497D; "
            "font-size: 11px; border: 1px solid #B0BED0; border-radius: 4px; }"
            "QPushButton:hover { background-color: #D0DCF0; }"
        )
        select_all_btn.clicked.connect(lambda: self._set_all(True))
        btn_row.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("전체 해제")
        deselect_all_btn.setFixedHeight(28)
        deselect_all_btn.setStyleSheet(
            "QPushButton { background-color: #F0F0F0; color: #555; "
            "font-size: 11px; border: 1px solid #CCC; border-radius: 4px; }"
            "QPushButton:hover { background-color: #E0E0E0; }"
        )
        deselect_all_btn.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(deselect_all_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── 스크롤 가능 프로젝트 목록 ──────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.StyledPanel)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #DDD; border-radius: 4px; }")

        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setSpacing(0)
        list_layout.setContentsMargins(0, 0, 0, 0)

        for i, data in enumerate(projects_data):
            item = _ProjectItem(data)
            item.toggled.connect(self._update_footer)
            self._items.append(item)

            # 구분선 (마지막 항목 제외)
            if i > 0:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setStyleSheet("color: #EEE;")
                list_layout.addWidget(sep)

            list_layout.addWidget(item)

        list_layout.addStretch()
        scroll.setWidget(list_widget)
        layout.addWidget(scroll, stretch=1)

        # ── 하단 통계 표시줄 ──────────────────────────────────────────
        footer_frame = QFrame()
        footer_frame.setFrameShape(QFrame.Shape.StyledPanel)
        footer_frame.setStyleSheet(
            "QFrame { background-color: #F5F5F5; border: 1px solid #DDD; border-radius: 4px; }"
        )
        footer_layout = QHBoxLayout(footer_frame)
        footer_layout.setContentsMargins(12, 6, 12, 6)

        self._footer_lbl = QLabel()
        self._footer_lbl.setStyleSheet("font-size: 11px; color: #555; border: none;")
        footer_layout.addWidget(self._footer_lbl, stretch=1)

        self._cost_lbl = QLabel()
        self._cost_lbl.setStyleSheet("font-size: 11px; color: #1F497D; font-weight: bold; border: none;")
        self._cost_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        footer_layout.addWidget(self._cost_lbl)

        layout.addWidget(footer_frame)

        # ── 액션 버튼 ─────────────────────────────────────────────────
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        action_row.addStretch()

        cancel_btn = QPushButton("취소")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setMinimumWidth(80)
        cancel_btn.setStyleSheet(
            "QPushButton { background-color: #AAA; color: white; font-size: 13px; border-radius: 5px; }"
            "QPushButton:hover { background-color: #888; }"
        )
        cancel_btn.clicked.connect(self.reject)
        action_row.addWidget(cancel_btn)

        self._start_btn = QPushButton("분석 시작")
        self._start_btn.setFixedHeight(36)
        self._start_btn.setMinimumWidth(100)
        self._start_btn.setDefault(True)
        self._start_btn.setStyleSheet(
            "QPushButton { background-color: #1F497D; color: white; "
            "font-size: 13px; font-weight: bold; border-radius: 5px; }"
            "QPushButton:hover { background-color: #2E6DA4; }"
            "QPushButton:disabled { background-color: #AAA; }"
        )
        self._start_btn.clicked.connect(self.accept)
        action_row.addWidget(self._start_btn)

        layout.addLayout(action_row)

        # 초기 통계 표시
        self._update_footer()

    # ── 내부 메서드 ───────────────────────────────────────────────────
    def _set_all(self, checked: bool) -> None:
        for item in self._items:
            item.set_checked(checked)
        self._update_footer()

    def _update_footer(self) -> None:
        selected = [item for item in self._items if item.is_checked]
        n_sel = len(selected)
        n_critical = sum(item.critical_info_count for item in selected)
        total_chars = sum(item.summary_chars for item in selected)

        # 예상 토큰 및 비용 계산
        input_tokens = chars_to_tokens(total_chars)
        cost_usd = estimate_cost(input_tokens, EST_OUTPUT_TOKENS)

        self._footer_lbl.setText(
            f"선택: {n_sel}개 프로젝트  ·  중요정보: {n_critical}개"
        )
        self._cost_lbl.setText(
            f"예상 입력 토큰: {input_tokens:,}  ·  예상 비용: ${cost_usd:.3f}"
        )

        # 프로젝트 미선택 시 분석 시작 비활성화
        self._start_btn.setEnabled(n_sel > 0)

    # ── 외부 인터페이스 ───────────────────────────────────────────────
    def get_selected_keys(self) -> list[str]:
        """선택된 프로젝트 키 목록을 반환한다."""
        return [item.project_key for item in self._items if item.is_checked]

    def get_selection_stats(self) -> dict:
        """선택 결과 통계를 반환한다."""
        selected = [item for item in self._items if item.is_checked]
        n_critical = sum(item.critical_info_count for item in selected)
        total_chars = sum(item.summary_chars for item in selected)
        input_tokens = chars_to_tokens(total_chars)
        cost_usd = estimate_cost(input_tokens, EST_OUTPUT_TOKENS)
        return {
            "selected_project_count": len(selected),
            "selected_critical_info_count": n_critical,
            "selected_input_tokens": input_tokens,
            "selected_cost_usd": cost_usd,
            "selected_cost_str": f"${cost_usd:.3f}",
        }
