from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# 파일 업로드 지원 확장자
_FILE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".txt", ".zip", ".hwp", ".hwpx"}
# 폴더 스캔 지원 확장자 (zip은 폴더 내 직접 스캔 대상 제외)
_FOLDER_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".txt", ".hwp", ".hwpx"}
_FILE_FILTER = "지원 파일 (*.pdf *.docx *.xlsx *.txt *.zip *.hwp *.hwpx)"


def _fmt_size(size_bytes: int) -> str:
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.1f}MB"
    if size_bytes >= 1_024:
        return f"{size_bytes / 1_024:.1f}KB"
    return f"{size_bytes}B"


def _scan_folder(root: Path) -> tuple[list[Path], int]:
    """루트 폴더를 재귀 탐색해 지원 파일 목록과 탐색 디렉터리 수를 반환."""
    found: list[Path] = []
    dir_count = 0
    for item in sorted(root.rglob("*")):
        if item.is_dir():
            dir_count += 1
        elif item.is_file() and item.suffix.lower() in _FOLDER_EXTENSIONS:
            found.append(item)
    return found, dir_count


class _FileItem(QWidget):
    """한 파일을 나타내는 행 위젯 (파일명 + 크기 + 삭제 버튼)."""

    def __init__(
        self,
        file_path: str,
        on_remove,
        display_name: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.file_path = file_path
        path = Path(file_path)
        try:
            size_str = _fmt_size(path.stat().st_size)
        except OSError:
            size_str = "?"

        label_text = f"{display_name or path.name}  ({size_str})"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        check = QLabel("✓")
        check.setStyleSheet("color: #217346; font-weight: bold;")
        check.setFixedWidth(16)
        layout.addWidget(check)

        name_label = QLabel(label_text)
        name_label.setToolTip(file_path)
        name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(name_label, stretch=1)

        del_btn = QPushButton("삭제")
        del_btn.setFixedSize(44, 22)
        del_btn.setStyleSheet(
            "QPushButton { color: #c00; border: 1px solid #c00;"
            "  border-radius: 3px; font-size: 11px; }"
            "QPushButton:hover { background: #fdd; }"
        )
        del_btn.clicked.connect(lambda: on_remove(file_path))
        layout.addWidget(del_btn)


class InputPanel(QWidget):
    """업무분장 입력 + 평가자료 다중 파일/폴더 업로드 패널."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 파일 절대 경로 → 표시용 이름
        self._file_map: dict[str, str] = {}
        self._build_ui()

    # ──────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # ── 업무분장 입력 ──────────────────────────────────────────
        job_group = QGroupBox("업무분장 입력")
        job_layout = QVBoxLayout(job_group)
        self.job_text_edit = QTextEdit()
        self.job_text_edit.setPlaceholderText(
            "담당하고 있는 업무 목록을 입력하세요.\n"
            "예) 예산 관리, 계약서 검토, 주간 보고서 작성 등"
        )
        self.job_text_edit.setMinimumHeight(140)
        job_layout.addWidget(self.job_text_edit)
        layout.addWidget(job_group)

        # ── 평가자료 업로드 ────────────────────────────────────────
        self._upload_group = QGroupBox("평가자료 (0개)")
        upload_layout = QVBoxLayout(self._upload_group)

        # 버튼 행
        btn_row = QHBoxLayout()

        self._file_btn = QPushButton("파일 선택")
        self._file_btn.setFixedHeight(30)
        self._file_btn.setToolTip("단일 또는 다중 파일 선택 (PDF·DOCX·XLSX·TXT·ZIP)")
        self._file_btn.clicked.connect(self._on_select_files)
        btn_row.addWidget(self._file_btn)

        self._folder_btn = QPushButton("폴더 선택")
        self._folder_btn.setFixedHeight(30)
        self._folder_btn.setToolTip("폴더를 선택하면 내부의 모든 문서를 재귀 탐색합니다")
        self._folder_btn.clicked.connect(self._on_select_folder)
        btn_row.addWidget(self._folder_btn)

        self._clear_btn = QPushButton("전체 삭제")
        self._clear_btn.setFixedHeight(30)
        self._clear_btn.setStyleSheet("color: #c00;")
        self._clear_btn.clicked.connect(self._on_clear_all)
        self._clear_btn.setEnabled(False)
        btn_row.addWidget(self._clear_btn)

        btn_row.addStretch()
        upload_layout.addLayout(btn_row)

        # 폴더 스캔 결과 요약 레이블
        self._scan_info = QLabel("")
        self._scan_info.setStyleSheet("color: #1F497D; font-size: 11px;")
        self._scan_info.hide()
        upload_layout.addWidget(self._scan_info)

        # 파일 목록 스크롤 영역
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.StyledPanel)
        scroll.setMaximumHeight(180)
        scroll.setMinimumHeight(48)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()

        self._empty_label = QLabel("선택된 파일 없음")
        self._empty_label.setStyleSheet("color: #aaa; font-size: 11px;")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._list_layout.insertWidget(0, self._empty_label)

        scroll.setWidget(self._list_container)
        upload_layout.addWidget(scroll)

        hint = QLabel(
            "파일: PDF · DOCX · XLSX · TXT · ZIP · HWP  |  폴더: PDF · DOCX · XLSX · TXT · HWP (재귀 탐색)"
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        upload_layout.addWidget(hint)

        layout.addWidget(self._upload_group)

    # ── 이벤트 핸들러 ──────────────────────────────────────────────
    def _on_select_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "평가자료 파일 선택 (다중 선택 가능)",
            "",
            _FILE_FILTER,
        )
        if not paths:
            return
        self._scan_info.hide()
        added = 0
        for p in paths:
            if p not in self._file_map:
                name = Path(p).name
                self._file_map[p] = name
                self._add_item(p, name)
                added += 1
        if added:
            self._refresh_header()

    def _on_select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "평가자료 폴더 선택",
            "",
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return

        root = Path(folder)
        found, dir_count = _scan_folder(root)

        if not found:
            self._scan_info.setText(
                f"폴더 [{root.name}] — 지원 파일 없음 (하위 폴더 {dir_count}개 탐색)"
            )
            self._scan_info.show()
            return

        added = 0
        for file_path in found:
            key = str(file_path)
            if key not in self._file_map:
                # 폴더 루트로부터의 상대 경로를 표시명으로 사용
                try:
                    display = str(file_path.relative_to(root))
                except ValueError:
                    display = file_path.name
                self._file_map[key] = display
                self._add_item(key, display)
                added += 1

        self._scan_info.setText(
            f"폴더 [{root.name}]  —  총 {len(found)}개 발견 "
            f"(하위 폴더 {dir_count}개 탐색, 신규 추가 {added}개)"
        )
        self._scan_info.show()
        self._refresh_header()

    def _on_clear_all(self) -> None:
        self._file_map.clear()
        self._scan_info.hide()
        self._rebuild_list()
        self._refresh_header()

    def _on_remove(self, file_path: str) -> None:
        self._file_map.pop(file_path, None)
        self._rebuild_list()
        self._refresh_header()
        if not self._file_map:
            self._scan_info.hide()

    # ── 목록 위젯 관리 ──────────────────────────────────────────────
    def _add_item(self, file_path: str, display_name: str) -> None:
        self._empty_label.hide()
        item = _FileItem(file_path, self._on_remove, display_name)
        count = self._list_layout.count()
        self._list_layout.insertWidget(count - 1, item)

    def _rebuild_list(self) -> None:
        for i in reversed(range(self._list_layout.count())):
            item = self._list_layout.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, _FileItem):
                self._list_layout.removeWidget(widget)
                widget.deleteLater()

        if self._file_map:
            self._empty_label.hide()
            for path, display in self._file_map.items():
                count = self._list_layout.count()
                self._list_layout.insertWidget(
                    count - 1, _FileItem(path, self._on_remove, display)
                )
        else:
            self._empty_label.show()

    def _refresh_header(self) -> None:
        n = len(self._file_map)
        self._upload_group.setTitle(f"평가자료 ({n}개)")
        self._clear_btn.setEnabled(n > 0)

    # ── 공개 인터페이스 ──────────────────────────────────────────────
    def get_job_description(self) -> str:
        return self.job_text_edit.toPlainText().strip()

    def get_uploaded_files(self) -> list[str]:
        return list(self._file_map.keys())

    def get_file_display_map(self) -> dict[str, str]:
        """절대경로 → 표시용 이름(상대경로 또는 파일명) 딕셔너리를 반환."""
        return dict(self._file_map)

    def clear_files(self) -> None:
        self._file_map.clear()
        self._scan_info.hide()
        self._rebuild_list()
        self._refresh_header()
