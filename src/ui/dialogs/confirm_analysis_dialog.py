from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class ConfirmAnalysisDialog(QDialog):
    """
    GPT 업무복원 분석 시작 전 선택된 프로젝트·비용·시간을 표시하고
    사용자 승인을 받는 다이얼로그.
    """

    def __init__(self, cost_info: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 분석 시작 확인")
        self.setMinimumWidth(440)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self._build_ui(cost_info)

    def _build_ui(self, info: dict) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        # ── 제목 ──────────────────────────────────────────────────────
        title = QLabel("AI 업무복원 분석을 시작합니다")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #1F497D;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # ── 선택 프로젝트 요약 박스 ────────────────────────────────────
        sel_box = QFrame()
        sel_box.setFrameShape(QFrame.Shape.StyledPanel)
        sel_box.setStyleSheet(
            "QFrame { background-color: #EEF4FB; border: 1px solid #B8D0E8; border-radius: 6px; }"
        )
        sel_layout = QVBoxLayout(sel_box)
        sel_layout.setContentsMargins(14, 8, 14, 8)
        sel_layout.setSpacing(4)

        sel_header = QLabel("선택된 분석 범위")
        sel_header.setStyleSheet("font-size: 11px; font-weight: bold; color: #1F497D; border: none;")
        sel_layout.addWidget(sel_header)

        sel_rows = [
            ("선택된 프로젝트 수",   f"{info.get('selected_project_count', info.get('project_count', 0))}개"),
            ("고객사 수",            f"{info.get('customer_count', 0)}개"),
            ("선택된 중요 정보 수",  f"{info.get('selected_critical_info_count', 0)}개"),
            ("분석 문서 수",         f"{info.get('doc_count', 0)}개"),
        ]
        for label_text, value_text in sel_rows:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #555; font-size: 11px; border: none;")
            val = QLabel(value_text)
            val.setStyleSheet("color: #1F497D; font-size: 11px; font-weight: bold; border: none;")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(lbl)
            row.addStretch()
            row.addWidget(val)
            sel_layout.addLayout(row)

        layout.addWidget(sel_box)

        # ── 예상 비용 박스 ────────────────────────────────────────────
        cost_box = QFrame()
        cost_box.setFrameShape(QFrame.Shape.StyledPanel)
        cost_box.setStyleSheet(
            "QFrame { background-color: #F5F5F5; border: 1px solid #DDD; border-radius: 6px; }"
        )
        cost_layout = QVBoxLayout(cost_box)
        cost_layout.setContentsMargins(14, 8, 14, 8)
        cost_layout.setSpacing(4)

        cost_header = QLabel("예상 비용 및 시간")
        cost_header.setStyleSheet("font-size: 11px; font-weight: bold; color: #555; border: none;")
        cost_layout.addWidget(cost_header)

        # 선택된 프로젝트 비용이 있으면 우선 표시
        sel_input = info.get("selected_input_tokens", info.get("input_tokens", 0))
        sel_cost = info.get("selected_cost_str", info.get("cost_str", "$0.000"))
        run_mode = info.get("run_mode", "정밀 모드")

        cost_rows = [
            ("실행 모드",       run_mode),
            ("예상 입력 토큰",  f"{sel_input:,}"),
            ("예상 출력 토큰",  f"{info.get('output_tokens', 0):,}"),
            ("총 예상 토큰",    f"{sel_input + info.get('output_tokens', 0):,}"),
            ("예상 비용",       sel_cost),
            ("예상 소요시간",   info.get("est_time_str", "알 수 없음")),
        ]

        for label_text, value_text in cost_rows:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #555; font-size: 12px; border: none;")
            val = QLabel(value_text)
            # 실행 모드 행은 색상으로 구분
            if label_text == "실행 모드":
                mode_color = (
                    "#1A7D26" if "라이트" in value_text
                    else "#1F497D"
                )
                val.setStyleSheet(
                    f"color: {mode_color}; font-size: 12px; font-weight: bold; border: none;"
                )
            else:
                val.setStyleSheet("color: #333; font-size: 12px; font-weight: bold; border: none;")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(lbl)
            row.addStretch()
            row.addWidget(val)
            cost_layout.addLayout(row)

        layout.addWidget(cost_box)

        # ── 규칙기반 초안 품질 박스 (draft_cat_count 있을 때만 표시) ──
        draft_cat_count = info.get("draft_cat_count", 0)
        draft_class_acc = info.get("draft_class_acc", 0)
        draft_chars     = info.get("draft_chars", 0)

        if draft_cat_count > 0:
            draft_box = QFrame()
            draft_box.setFrameShape(QFrame.Shape.StyledPanel)
            draft_box.setStyleSheet(
                "QFrame { background-color: #F0F7EC; border: 1px solid #A8D08D; border-radius: 6px; }"
            )
            draft_layout = QVBoxLayout(draft_box)
            draft_layout.setContentsMargins(14, 8, 14, 8)
            draft_layout.setSpacing(4)

            draft_header = QLabel("규칙기반 초안 생성 완료")
            draft_header.setStyleSheet(
                "font-size: 11px; font-weight: bold; color: #375623; border: none;"
            )
            draft_layout.addWidget(draft_header)

            draft_rows = [
                ("업무 카테고리",   f"{draft_cat_count}개"),
                ("프로젝트",        f"{info.get('selected_project_count', 0)}개"),
                ("문서",            f"{info.get('doc_count', 0)}개"),
                ("분류 정확도",     f"{draft_class_acc}점"),
                ("초안 크기",       f"{draft_chars:,}자"),
            ]
            for label_text, value_text in draft_rows:
                row = QHBoxLayout()
                lbl = QLabel(label_text)
                lbl.setStyleSheet("color: #375623; font-size: 11px; border: none;")
                val = QLabel(value_text)
                val.setStyleSheet(
                    "color: #1A5E20; font-size: 11px; font-weight: bold; border: none;"
                )
                val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                row.addWidget(lbl)
                row.addStretch()
                row.addWidget(val)
                draft_layout.addLayout(row)

            layout.addWidget(draft_box)

        # ── 안내 메시지 ───────────────────────────────────────────────
        note = QLabel(
            "※ 토큰 수와 비용은 예상치입니다. 실제 값과 차이가 있을 수 있습니다.\n"
            "   모델: gpt-5.5 기준"
        )
        note.setStyleSheet("color: #888; font-size: 10px;")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(note)

        # ── 버튼 ──────────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        cancel_btn = QPushButton("취소")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setStyleSheet(
            "QPushButton { background-color: #AAA; color: white; font-size: 13px; border-radius: 5px; }"
            "QPushButton:hover { background-color: #888; }"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        # 초안 보기 버튼 (draft_report.md 가 있을 때만)
        _draft_path = Path(__file__).resolve().parents[3] / "output" / "draft_report.md"
        if draft_cat_count > 0 and _draft_path.exists():
            preview_btn = QPushButton("초안 보기")
            preview_btn.setFixedHeight(36)
            preview_btn.setStyleSheet(
                "QPushButton { background-color: #375623; color: white; font-size: 12px; border-radius: 5px; }"
                "QPushButton:hover { background-color: #4E7A30; }"
            )
            preview_btn.clicked.connect(lambda: self._open_draft(_draft_path))
            btn_layout.addWidget(preview_btn)

        confirm_btn = QPushButton("GPT 보강 시작")
        confirm_btn.setFixedHeight(36)
        confirm_btn.setDefault(True)
        confirm_btn.setStyleSheet(
            "QPushButton { background-color: #1F497D; color: white; font-size: 13px; "
            "font-weight: bold; border-radius: 5px; }"
            "QPushButton:hover { background-color: #2E6DA4; }"
        )
        confirm_btn.clicked.connect(self.accept)
        btn_layout.addWidget(confirm_btn)

        layout.addLayout(btn_layout)

    def _open_draft(self, path: Path) -> None:
        """규칙기반 초안 파일을 OS 기본 앱(메모장 등)으로 열기."""
        try:
            if sys.platform == "win32":
                subprocess.Popen(["notepad.exe", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            print(f"[초안 보기] 파일 열기 실패: {exc}")
