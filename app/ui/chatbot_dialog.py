import base64
import binascii
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.api_config import load_api_key
from app.license import load_saved_license_code
from app.license_credits import consume_credits, precheck_action
from app.services.package_loader import (
    load_packages_from_folder,
    load_packages_from_gdrive_link,
    merge_and_deduplicate_chunks,
)
from app.services.rag_search import embed_query, generate_answer, search_relevant_chunks


_CONFIDENCE_BADGE_STYLES = {
    "확실함": "background-color: #dcfce7; color: #166534; border: 1px solid #86efac;",
    "확인 필요": "background-color: #ffedd5; color: #9a3412; border: 1px solid #fdba74;",
    "추정": "background-color: #fef9c3; color: #854d0e; border: 1px solid #fde047;",
    "확인불가": "background-color: #f3f4f6; color: #4b5563; border: 1px solid #d1d5db;",
}


class ChatAnswerWorker(QThread):
    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        query: str,
        chunks: list[dict],
        api_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.query = query
        self.chunks = chunks
        self.api_key = api_key

    def run(self) -> None:
        try:
            query_embedding = embed_query(self.query, self.api_key)
            relevant_chunks = search_relevant_chunks(
                query_embedding,
                self.chunks,
                query=self.query,
            )
            answer = generate_answer(self.query, relevant_chunks, self.api_key)
            usage = answer.setdefault("_usage", {})
            usage["embedding_tokens"] = int(getattr(query_embedding, "usage_tokens", 0))
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(answer)


class PackageLoadWorker(QThread):
    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        source: Path | str,
        parent: QWidget | None = None,
        *,
        source_kind: str = "folder",
    ) -> None:
        super().__init__(parent)
        self.source = source
        self.source_kind = source_kind

    def run(self) -> None:
        try:
            if self.source_kind == "gdrive":
                packages = load_packages_from_gdrive_link(str(self.source))
                api_key = load_api_key()
            else:
                folder = Path(self.source)
                packages = load_packages_from_folder(str(folder))
                if not packages and _is_package_root(folder):
                    packages = load_packages_from_folder(str(folder.parent))
                api_key = _load_api_key_from_package_folder(folder) or load_api_key()
            merged = merge_and_deduplicate_chunks(packages)
            chunks = merged["chunks"]
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        self.succeeded.emit(
            {
                "packages": packages,
                "chunks": chunks,
                "api_key": api_key,
            }
        )


class ChatbotDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("물어보기")
        self.resize(720, 640)

        self.selected_folder: Path | None = None
        self.selected_package_source: str = ""
        self.packages: list[dict] = []
        self.chunks: list[dict] = []
        self.api_key: str | None = None
        self.package_load_worker: PackageLoadWorker | None = None
        self.worker: ChatAnswerWorker | None = None
        self.pending_load_row: QWidget | None = None
        self.pending_answer_row: QWidget | None = None

        self.select_folder_button = QPushButton("패키지 폴더 선택")
        self.load_gdrive_button = QPushButton("구글드라이브 링크로 불러오기")
        self.folder_label = QLabel("선택된 폴더 없음")
        self.folder_label.setWordWrap(True)

        self.chat_area = QScrollArea()
        self.chat_area.setWidgetResizable(True)
        self.chat_area.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_area.setStyleSheet(
            "QScrollArea { background-color: #f8fafc; border: 1px solid #e5e7eb; "
            "border-radius: 8px; }"
        )
        self.chat_container = QWidget()
        self.chat_container.setStyleSheet("background-color: #f8fafc;")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(12, 12, 12, 12)
        self.chat_layout.setSpacing(10)
        self.chat_layout.addStretch()
        self.chat_area.setWidget(self.chat_container)
        self._add_system_message("패키지 폴더를 선택한 뒤 질문을 입력하세요.")

        self.status_label = QLabel("패키지 폴더를 먼저 선택하세요.")
        self.question_input = QLineEdit()
        self.question_input.setPlaceholderText("질문을 입력하세요...")
        self.question_input.setMinimumHeight(44)
        self.question_input.setStyleSheet(
            "QLineEdit {"
            "background-color: #ffffff;"
            "border: 1px solid #cbd5e1;"
            "border-radius: 22px;"
            "padding: 0 14px;"
            "font-size: 14px;"
            "color: #111827;"
            "}"
            "QLineEdit:focus { border: 2px solid #2563eb; }"
            "QLineEdit:disabled {"
            "background-color: #f1f5f9;"
            "color: #94a3b8;"
            "border-color: #e2e8f0;"
            "}"
        )
        self.question_input.setEnabled(False)
        self.send_button = QPushButton("전송")
        self.send_button.setMinimumSize(64, 44)
        self.send_button.setStyleSheet(
            "QPushButton {"
            "background-color: #2563eb;"
            "color: #ffffff;"
            "border: none;"
            "border-radius: 22px;"
            "font-weight: bold;"
            "padding: 0 16px;"
            "}"
            "QPushButton:hover { background-color: #1d4ed8; }"
            "QPushButton:disabled {"
            "background-color: #cbd5e1;"
            "color: #f8fafc;"
            "}"
        )
        self.send_button.setEnabled(False)

        top_row = QHBoxLayout()
        top_row.addWidget(self.select_folder_button)
        top_row.addWidget(self.load_gdrive_button)
        top_row.addWidget(self.folder_label, stretch=1)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.question_input, stretch=1)
        bottom_row.addWidget(self.send_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self.chat_area, stretch=1)
        layout.addWidget(self.status_label)
        layout.addLayout(bottom_row)

        self.select_folder_button.clicked.connect(self._select_package_folder)
        self.load_gdrive_button.clicked.connect(self._load_package_from_gdrive_link)
        self.send_button.clicked.connect(self._send_question)
        self.question_input.returnPressed.connect(self._send_question)

    def _select_package_folder(self) -> None:
        if self.package_load_worker is not None:
            return

        folder = QFileDialog.getExistingDirectory(self, "패키지 폴더 선택")
        if not folder:
            return

        self._start_package_load(Path(folder), "folder", "패키지를 불러오는 중입니다...")

    def _load_package_from_gdrive_link(self) -> None:
        if self.package_load_worker is not None:
            return

        share_url, accepted = QInputDialog.getText(
            self,
            "구글드라이브 링크로 불러오기",
            "구글드라이브 공유 폴더 링크를 입력하세요",
        )
        if not accepted:
            return
        share_url = share_url.strip()
        if not share_url:
            return
        if not _looks_like_google_drive_link(share_url):
            QMessageBox.warning(
                self,
                "구글드라이브 링크",
                "구글드라이브 공유 폴더 링크를 입력해주세요.",
            )
            return

        self._start_package_load(
            share_url,
            "gdrive",
            "구글드라이브에서 패키지를 불러오는 중입니다...",
        )

    def _start_package_load(self, source: Path | str, source_kind: str, loading_message: str) -> None:
        self.selected_folder = Path(source) if source_kind == "folder" else None
        self.selected_package_source = str(source)
        self.folder_label.setText(str(source))
        self.status_label.setText("패키지를 로드하는 중...")
        self.packages = []
        self.chunks = []
        self.api_key = None
        self.select_folder_button.setEnabled(False)
        self.load_gdrive_button.setEnabled(False)
        self.question_input.setEnabled(False)
        self.send_button.setEnabled(False)
        self._remove_pending_load()
        self.pending_load_row = self._add_system_message(loading_message)

        worker = PackageLoadWorker(source, self, source_kind=source_kind)
        worker.succeeded.connect(self._handle_package_load_succeeded)
        worker.failed.connect(self._handle_package_load_failed)
        worker.finished.connect(self._clear_package_load_worker)
        worker.finished.connect(worker.deleteLater)
        self.package_load_worker = worker
        worker.start()

    def _handle_package_load_succeeded(self, result: dict) -> None:
        self._remove_pending_load()
        self.packages = result["packages"]
        self.chunks = result["chunks"]
        self.api_key = result.get("api_key")

        self.status_label.setText(
            f"총 {len(self.packages)}개 패키지 로드, {len(self.chunks)}개 청크 사용 가능"
        )
        self.select_folder_button.setEnabled(True)
        self.load_gdrive_button.setEnabled(True)

        if not self.packages:
            self.status_label.setText("읽을 수 있는 인수인계패키지가 없습니다.")
            self._add_missing_package_notice()
            self.question_input.setEnabled(False)
            self.send_button.setEnabled(False)
            return

        if not self.chunks:
            self.status_label.setText("패키지에서 읽을 수 있는 내용이 없습니다.")
            self._add_warning_message(
                "패키지는 찾았지만 읽을 수 있는 내용이 없습니다.\n\n"
                "패키지가 손상되었거나 생성 중 오류가 있었을 수 있습니다. "
                "메인 화면에서 [인수인계패키지 생성]을 다시 실행해 주세요."
            )
            self.question_input.setEnabled(False)
            self.send_button.setEnabled(False)
            return

        self._add_system_message(f"패키지 {len(self.packages)}개를 로드했습니다.")

        if not self.api_key:
            self.status_label.setText("API 키를 먼저 설정해주세요.")
            self._add_system_message("API 키를 먼저 설정해주세요.")
            self.question_input.setEnabled(False)
            self.send_button.setEnabled(False)
            return

        self.question_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.question_input.setFocus()

    def _handle_package_load_failed(self, error_message: str) -> None:
        self._remove_pending_load()
        self.packages = []
        self.chunks = []
        self.api_key = None
        self.status_label.setText("패키지 로드 실패")
        self._add_system_message(f"패키지 로드에 실패했습니다. {error_message}")
        self.select_folder_button.setEnabled(True)
        self.load_gdrive_button.setEnabled(True)
        self.question_input.setEnabled(False)
        self.send_button.setEnabled(False)

    def _clear_package_load_worker(self) -> None:
        self.package_load_worker = None

    def _send_question(self) -> None:
        query = self.question_input.text().strip()
        if not query or self.worker is not None:
            return
        if not self.chunks:
            QMessageBox.warning(self, "물어보기", "패키지 폴더를 먼저 선택해주세요.")
            return
        if not self.api_key:
            QMessageBox.warning(self, "물어보기", "API 키를 먼저 설정해주세요.")
            return

        license_code = load_saved_license_code() or ""
        precheck = precheck_action(license_code, "chat")
        if precheck is not None and precheck.get("allowed") is False:
            self._add_warning_message(
                "크레딧이 부족합니다. 설명서 페이지에서 사용량을 구매해 주세요."
            )
            return

        self._add_user_message(query)
        self.question_input.clear()
        self._set_busy(True)
        self.pending_answer_row = self._add_bot_message("답변 생성 중...", confidence="처리 중")

        self.worker = ChatAnswerWorker(query, self.chunks, self.api_key, self)
        self.worker.succeeded.connect(self._handle_answer)
        self.worker.failed.connect(self._handle_answer_error)
        self.worker.finished.connect(self._clear_worker)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _handle_answer(self, result: dict) -> None:
        self._remove_pending_answer()
        confidence = result.get("confidence", "추정")
        answer = result.get("answer", "")
        self._add_bot_message(
            str(answer),
            confidence=str(confidence),
            sources=[str(source) for source in result.get("sources", [])],
        )

        related = result.get("related", [])
        if related:
            for item in related:
                self._add_related_message(
                    str(item.get("answer", "")),
                    confidence=str(item.get("confidence", "추정")),
                    sources=[str(source) for source in item.get("sources", [])],
                )

        self.status_label.setText("답변 완료")
        self._set_busy(False)
        usage = result.get("_usage", {})
        consume_credits(
            load_saved_license_code() or "",
            "chat",
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            embedding_tokens=usage.get("embedding_tokens", 0),
        )
        parent = self.parent()
        if parent is not None and hasattr(parent, "_refresh_credit_balance"):
            parent._refresh_credit_balance()

    def _handle_answer_error(self, error_message: str) -> None:
        self._remove_pending_answer()
        self._add_system_message(f"답변 생성에 실패했습니다. {error_message}")
        self.status_label.setText("답변 생성 실패")
        self._set_busy(False)

    def _clear_worker(self) -> None:
        self.worker = None
        if self.chunks and self.api_key:
            self.question_input.setEnabled(True)
            self.send_button.setEnabled(True)

    def _set_busy(self, busy: bool) -> None:
        self.status_label.setText("답변 생성 중..." if busy else self.status_label.text())
        self.question_input.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.select_folder_button.setEnabled(not busy)
        self.load_gdrive_button.setEnabled(not busy)

    def _add_user_message(self, text: str) -> QWidget:
        return self._add_message_row(
            self._create_text_bubble(
                text,
                "#2563eb",
                "#ffffff",
                "border-radius: 14px;",
            ),
            align_right=True,
        )

    def _add_bot_message(
        self,
        text: str,
        *,
        confidence: str,
        sources: list[str] | None = None,
    ) -> QWidget:
        return self._add_message_row(
            self._create_answer_bubble(
                text,
                confidence=confidence,
                sources=sources or [],
                background="#ffffff",
                border="#e5e7eb",
                label="물어보기",
            ),
            align_right=False,
        )

    def _add_related_message(
        self,
        text: str,
        *,
        confidence: str,
        sources: list[str] | None = None,
    ) -> QWidget:
        return self._add_message_row(
            self._create_answer_bubble(
                text,
                confidence=confidence,
                sources=sources or [],
                background="#f0f9ff",
                border="#bae6fd",
                label="관련 자료",
            ),
            align_right=False,
        )

    def _add_system_message(self, text: str) -> QWidget:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            "QLabel { color: #64748b; background-color: #eef2f7; border-radius: 10px; "
            "padding: 6px 10px; font-size: 12px; }"
        )
        label.setMaximumWidth(520)
        return self._add_message_row(label, align_right=False, centered=True)

    def _add_warning_message(self, text: str) -> QWidget:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(
            "QLabel { color: #7c2d12; background-color: #ffedd5; "
            "border: 1px solid #fdba74; border-radius: 12px; "
            "padding: 10px 12px; font-size: 13px; }"
        )
        label.setMaximumWidth(560)
        return self._add_message_row(label, align_right=False, centered=True)

    def _add_missing_package_notice(self) -> QWidget:
        target_text = (
            "이 구글드라이브 링크"
            if _looks_like_google_drive_link(self.selected_package_source)
            else "이 폴더"
        )
        return self._add_warning_message(
            f"{target_text}에서 읽을 수 있는 인수인계패키지를 찾지 못했습니다.\n\n"
            "물어보기는 인수인계 프로그램에서 [인수인계패키지 생성]으로 만든 "
            "패키지 파일(.zip)만 읽을 수 있습니다. 일반 문서 폴더는 인식하지 못합니다.\n\n"
            "먼저 메인 화면에서 분석 후 [인수인계패키지 생성]을 실행해서 "
            "패키지를 만들어주세요."
        )

    def _add_message_row(
        self,
        bubble: QWidget,
        *,
        align_right: bool,
        centered: bool = False,
    ) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        if centered:
            row_layout.addStretch()
            row_layout.addWidget(bubble)
            row_layout.addStretch()
        elif align_right:
            row_layout.addStretch()
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch()

        insert_index = max(self.chat_layout.count() - 1, 0)
        self.chat_layout.insertWidget(insert_index, row)
        self._scroll_to_bottom()
        return row

    def _create_text_bubble(
        self,
        text: str,
        background: str,
        color: str,
        extra_style: str,
    ) -> QFrame:
        bubble = QFrame()
        bubble.setMaximumWidth(460)
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        bubble.setStyleSheet(
            f"QFrame {{ background-color: {background}; color: {color}; "
            f"{extra_style} }}"
        )
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(12, 10, 12, 10)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(f"color: {color}; font-size: 14px;")
        layout.addWidget(label)
        return bubble

    def _create_answer_bubble(
        self,
        text: str,
        *,
        confidence: str,
        sources: list[str],
        background: str,
        border: str,
        label: str,
    ) -> QFrame:
        bubble = QFrame()
        bubble.setMaximumWidth(520)
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        bubble.setStyleSheet(
            f"QFrame {{ background-color: {background}; border: 1px solid {border}; "
            "border-radius: 14px; }"
        )

        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        title = QLabel(label)
        title.setStyleSheet("color: #334155; font-size: 12px; font-weight: bold;")
        badge = QLabel(confidence)
        badge.setStyleSheet(
            "QLabel { border-radius: 8px; padding: 2px 7px; font-size: 11px; "
            f"{_CONFIDENCE_BADGE_STYLES.get(confidence, _CONFIDENCE_BADGE_STYLES['확인불가'])} }}"
        )
        header_row.addWidget(title)
        header_row.addWidget(badge)
        header_row.addStretch()
        layout.addLayout(header_row)

        body = QLabel(text or "응답 내용이 비어 있습니다.")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet("color: #111827; font-size: 14px;")
        layout.addWidget(body)

        if sources:
            source_label = QLabel("출처\n" + "\n".join(f"- {source}" for source in sources))
            source_label.setWordWrap(True)
            source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            source_label.setStyleSheet("color: #64748b; font-size: 11px;")
            layout.addWidget(source_label)

        return bubble

    def _remove_pending_answer(self) -> None:
        if self.pending_answer_row is None:
            return
        self.pending_answer_row.setParent(None)
        self.pending_answer_row.deleteLater()
        self.pending_answer_row = None

    def _remove_pending_load(self) -> None:
        if self.pending_load_row is None:
            return
        self.pending_load_row.setParent(None)
        self.pending_load_row.deleteLater()
        self.pending_load_row = None

    def _scroll_to_bottom(self) -> None:
        def scroll() -> None:
            bar = self.chat_area.verticalScrollBar()
            bar.setValue(bar.maximum())

        QTimer.singleShot(0, scroll)


def _is_package_root(path: Path) -> bool:
    required = {"manifest.json", "chunks.jsonl", "source_map.json"}
    try:
        names = {child.name for child in path.iterdir() if child.is_file()}
    except OSError:
        return False
    return required.issubset(names)


def _looks_like_google_drive_link(value: str) -> bool:
    normalized = value.casefold()
    return normalized.startswith(("http://", "https://")) and "drive.google.com" in normalized


def _load_api_key_from_package_folder(folder: Path) -> str | None:
    candidates = [folder / "api_key.dat"]
    try:
        candidates.extend(child / "api_key.dat" for child in folder.iterdir() if child.is_dir())
    except OSError:
        pass

    for candidate in candidates:
        api_key = _read_api_key_file(candidate)
        if api_key:
            return api_key

    try:
        zip_paths = sorted(folder.glob("*.zip"))
    except OSError:
        zip_paths = []
    for zip_path in zip_paths:
        api_key = _read_api_key_from_zip(zip_path)
        if api_key:
            return api_key

    return None


def _read_api_key_from_zip(zip_path: Path) -> str | None:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for name in archive.namelist():
                if Path(name).name != "api_key.dat":
                    continue
                try:
                    data = archive.read(name)
                except KeyError:
                    continue
                return _decode_api_key_bytes(data)
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def _read_api_key_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return _decode_api_key_bytes(path.read_bytes())
    except OSError:
        return None


def _decode_api_key_bytes(data: bytes) -> str | None:
    text = data.decode("utf-8", errors="ignore").strip()
    if text.startswith("sk-"):
        return text
    try:
        decoded = base64.b64decode(data, validate=True).decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError):
        return text or None
    return decoded or text or None
