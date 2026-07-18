import html
import json
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QTextCursor, QTextDocument
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
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.size_units import bytes_to_gb
from app.license import is_license_active, load_saved_license_code
from app.license_credits import (
    check_balance,
    flush_pending_consumptions,
    submit_chat_feedback,
)
from app.services.ai_proxy_client import InsufficientCreditsError
from app.services.package_loader import (
    PackageLoadCancelled,
    load_packages_from_folder,
    load_packages_from_gdrive_link,
    merge_and_deduplicate_chunks,
)
from app.services.rag_search import (
    ChunkSearchIndex,
    build_chunk_search_index,
    embed_query,
    generate_answer,
    search_relevant_chunks,
)


_CONFIDENCE_BADGE_STYLES = {
    "확실함": "background-color: #dcfce7; color: #166534; border: 1px solid #86efac;",
    "확인 필요": "background-color: #ffedd5; color: #9a3412; border: 1px solid #fdba74;",
    "추정": "background-color: #fef9c3; color: #854d0e; border: 1px solid #fde047;",
    "확인불가": "background-color: #f3f4f6; color: #4b5563; border: 1px solid #d1d5db;",
}


class QuestionLineEdit(QLineEdit):
    """Consume an empty Enter press without letting the dialog move focus."""

    def keyPressEvent(self, event) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not self.text().strip()
        ):
            event.accept()
            return
        super().keyPressEvent(event)


class ChatAnswerWorker(QThread):
    succeeded = Signal(dict)
    failed = Signal(str)
    rejected = Signal(str)
    answer_delta = Signal(str)

    def __init__(
        self,
        query: str,
        search_index: ChunkSearchIndex,
        license_code: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.query = query
        self.search_index = search_index
        self.license_code = license_code

    def run(self) -> None:
        try:
            started_at = time.perf_counter()
            query_embedding = embed_query(self.query, self.license_code)
            embedding_finished_at = time.perf_counter()

            relevant_chunks = search_relevant_chunks(
                query_embedding,
                query=self.query,
                search_index=self.search_index,
            )
            search_finished_at = time.perf_counter()
            first_delta_at: float | None = None

            def emit_delta(delta: str) -> None:
                nonlocal first_delta_at
                if first_delta_at is None:
                    first_delta_at = time.perf_counter()
                self.answer_delta.emit(delta)

            try:
                answer = generate_answer(
                    self.query,
                    relevant_chunks,
                    self.license_code,
                    on_answer_delta=emit_delta,
                )
            except InsufficientCreditsError as exc:
                self.rejected.emit(exc.message)
                return
            answer_finished_at = time.perf_counter()
            usage = answer.setdefault("_usage", {})
            usage["embedding_tokens"] = int(getattr(query_embedding, "usage_tokens", 0))
            answer["_timings"] = {
                "embedding_seconds": embedding_finished_at - started_at,
                "search_seconds": search_finished_at - embedding_finished_at,
                "first_answer_text_seconds": (
                    first_delta_at - started_at if first_delta_at is not None else None
                ),
                "answer_seconds": answer_finished_at - search_finished_at,
                "total_seconds": answer_finished_at - started_at,
            }
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(answer)


class CreditUsageWorker(QThread):
    balance_ready = Signal(str, object)

    def __init__(
        self,
        license_code: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.license_code = license_code

    def run(self) -> None:
        # The server already reserved/finalized credits inside the chat proxy
        # call itself - this worker only refreshes the balance shown in the UI.
        flush_pending_consumptions()
        self.balance_ready.emit(self.license_code, check_balance(self.license_code))


class ChatFeedbackWorker(QThread):
    def __init__(
        self,
        license_code: str,
        question: str,
        answer_preview: str,
        rating: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.license_code = license_code
        self.question = question
        self.answer_preview = answer_preview
        self.rating = rating

    def run(self) -> None:
        try:
            submit_chat_feedback(
                self.license_code,
                self.question,
                self.answer_preview,
                self.rating,
            )
        except Exception:
            # Feedback is supplemental and must never interrupt the chat UI.
            return


class PackageLoadWorker(QThread):
    # object avoids QVariantMap conversion of tens of thousands of chunk dicts.
    succeeded = Signal(object)
    failed = Signal(str)
    canceled = Signal()
    progress = Signal(str, int)

    def __init__(
        self,
        source: Path | str,
        parent: QWidget | None = None,
        *,
        source_kind: str = "folder",
        existing_packages: list[dict] | None = None,
    ) -> None:
        super().__init__(parent)
        self.source = source
        self.source_kind = source_kind
        self.existing_packages = list(existing_packages or [])

    def run(self) -> None:
        try:
            progress_callback = lambda stage, current: self.progress.emit(
                stage, current
            )
            cancel_check = self.isInterruptionRequested
            if self.source_kind == "gdrive":
                packages = load_packages_from_gdrive_link(str(self.source))
                if cancel_check():
                    raise PackageLoadCancelled()
            else:
                folder = Path(self.source)
                selected_package_count, selected_package_bytes = (
                    ChatbotDialog._measure_local_packages(folder)
                )
                packages = load_packages_from_folder(
                    str(folder),
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                )
                if not packages and _is_package_root(folder):
                    packages = load_packages_from_folder(
                        str(folder.parent),
                        progress_callback=progress_callback,
                        cancel_check=cancel_check,
                    )
            packages = [*self.existing_packages, *packages]
            merged = merge_and_deduplicate_chunks(
                packages,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
            chunks = merged["chunks"]
            search_index = build_chunk_search_index(
                chunks,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                discard_embeddings=True,
            )
            # merge_and_deduplicate_chunks creates runtime chunk copies. Release
            # embeddings retained by the source package objects as well.
            for package in packages:
                for chunk in package.get("chunks", []):
                    chunk.pop("embedding", None)
                    chunk.pop("embedding_vector", None)
        except (PackageLoadCancelled, InterruptedError):
            self.canceled.emit()
            return
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        self.succeeded.emit(
            {
                "packages": packages,
                "chunks": chunks,
                "search_index": search_index,
                "selected_package_count": (
                    selected_package_count if self.source_kind == "folder" else 0
                ),
                "selected_package_bytes": (
                    selected_package_bytes if self.source_kind == "folder" else 0
                ),
            }
        )


class ChatbotDialog(QDialog):
    MAX_HISTORY_ITEMS = 500

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("물어보기")
        self.resize(720, 640)

        self.selected_folder: Path | None = None
        self.selected_package_source: str = ""
        self.packages: list[dict] = []
        self.chunks: list[dict] = []
        self.search_index: ChunkSearchIndex | None = None
        self.package_load_worker: PackageLoadWorker | None = None
        self.package_loading_timer = QTimer(self)
        self.package_loading_timer.setInterval(350)
        self.package_loading_timer.timeout.connect(
            self._advance_package_loading_animation
        )
        self._package_loading_frame = 0
        self._package_loading_text = "패키지 불러오는 중"
        self._package_loading_summary = ""
        self._selected_package_bytes = 0
        self._selected_package_count = 0
        self.worker: ChatAnswerWorker | None = None
        self.credit_workers: set[CreditUsageWorker] = set()
        self.feedback_workers: set[ChatFeedbackWorker] = set()
        self.chat_history: list[dict] = []
        self.pending_question = ""
        self.pending_load_row: QWidget | None = None
        self.pending_answer_row: QWidget | None = None
        self.streaming_answer_label: QLabel | None = None
        self.streaming_answer_text = ""
        self.answer_loading_timer = QTimer(self)
        self.answer_loading_timer.setInterval(350)
        self.answer_loading_timer.timeout.connect(
            self._advance_answer_loading_animation
        )
        self._answer_loading_frame = 0
        self._chat_search_query = ""
        self._chat_search_matches: list[tuple[QLabel, int, int]] = []
        self._chat_search_index = -1

        self.select_folder_button = QPushButton("패키지 폴더 선택")
        self.load_gdrive_button = QPushButton("구글드라이브 링크로 불러오기")
        self.cancel_load_button = QPushButton("불러오기 취소")
        for button in (
            self.select_folder_button,
            self.load_gdrive_button,
            self.cancel_load_button,
        ):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.cancel_load_button.setObjectName("cancelPackageLoadButton")
        self.cancel_load_button.setStyleSheet(
            "QPushButton { color: #B91C1C; background: #FEF2F2; "
            "border: 1px solid #FECACA; border-radius: 6px; padding: 6px 10px; } "
            "QPushButton:hover { background: #FEE2E2; } "
            "QPushButton:disabled { color: #94A3B8; background: #F8FAFC; "
            "border-color: #E2E8F0; }"
        )
        self.cancel_load_button.hide()
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

        # No initial text: question_requirement_label already conveys this
        # guidance, and duplicating it here confused users into thinking
        # there were two separate unmet conditions.
        self.status_label = QLabel("")
        self.chat_search_input = QLineEdit()
        self.chat_search_input.setObjectName("chatSearchInput")
        self.chat_search_input.setPlaceholderText("채팅 내용 검색")
        self.chat_search_input.setClearButtonEnabled(True)
        self.chat_search_input.setMaximumWidth(260)
        self.chat_search_input.setStyleSheet(
            "QLineEdit { background: #FFFFFF; border: 1px solid #CBD5E1; "
            "border-radius: 14px; padding: 5px 10px; color: #334155; } "
            "QLineEdit:focus { border-color: #A78BFA; }"
        )
        self.chat_search_button = QPushButton("🔍")
        self.chat_search_button.setObjectName("chatSearchButton")
        self.chat_search_button.setFixedSize(30, 30)
        self.chat_search_button.setToolTip("채팅에서 다음 항목 찾기")
        self.chat_search_button.setAutoDefault(False)
        self.chat_search_button.setDefault(False)
        self.chat_search_result_label = QLabel("")
        self.chat_search_result_label.setObjectName("chatSearchResultLabel")
        # Keep the search field/caret at a fixed screen position when the
        # no-result text appears. A minimum width allowed this label to expand
        # and push the input left even though the chat scroll was restored.
        self.chat_search_result_label.setFixedWidth(110)
        self.chat_search_result_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.chat_search_result_label.setStyleSheet(
            "color: #64748B; font-size: 11px; padding-right: 6px;"
        )
        self.question_requirement_label = QLabel(
            "패키지 폴더 또는 구글드라이브 링크를 입력해야 질문 가능합니다."
        )
        self.question_requirement_label.setObjectName("questionRequirementLabel")
        self.question_requirement_label.setStyleSheet(
            "color: #DC2626; font-size: 11px; padding: 0 8px 2px 8px;"
        )
        self.question_input = QuestionLineEdit()
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
        self.send_button.setAutoDefault(False)
        self.send_button.setDefault(False)
        self.send_button.setMinimumSize(64, 44)
        self.send_button.setStyleSheet(
            "QPushButton {"
            "background-color: #A78BFA;"
            "color: #ffffff;"
            "border: none;"
            "border-radius: 22px;"
            "font-weight: bold;"
            "padding: 0 16px;"
            "}"
            "QPushButton:hover { background-color: #8B5CF6; }"
            "QPushButton:disabled {"
            "background-color: #cbd5e1;"
            "color: #f8fafc;"
            "}"
        )
        self.send_button.setEnabled(False)

        top_row = QHBoxLayout()
        top_row.addWidget(self.select_folder_button)
        top_row.addWidget(self.load_gdrive_button)
        top_row.addWidget(self.cancel_load_button)
        top_row.addWidget(self.folder_label, stretch=1)

        search_row = QHBoxLayout()
        search_row.addStretch()
        search_row.addWidget(self.chat_search_result_label)
        search_row.addWidget(self.chat_search_input)
        search_row.addWidget(self.chat_search_button)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.question_input, stretch=1)
        bottom_row.addWidget(self.send_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addLayout(search_row)
        layout.addWidget(self.chat_area, stretch=1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.question_requirement_label)
        layout.addLayout(bottom_row)

        self.select_folder_button.clicked.connect(self._select_package_folder)
        self.load_gdrive_button.clicked.connect(self._load_package_from_gdrive_link)
        self.cancel_load_button.clicked.connect(self._cancel_package_load)
        self.send_button.clicked.connect(self._send_question)
        self.question_input.returnPressed.connect(self._send_question)
        self.chat_search_input.returnPressed.connect(self._find_next_chat_text)
        self.chat_search_button.clicked.connect(self._find_next_chat_text)
        self.chat_search_input.textChanged.connect(self._reset_chat_search)

        # History bubbles can replay a saved feedback state, whose rendering
        # touches the question input's focus behavior. Build and connect every
        # widget first so history restoration never observes a partial dialog.
        self._load_chat_history()
        self._render_chat_history()

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
        self.status_label.clear()
        self._selected_package_count = 0
        self._selected_package_bytes = 0
        self.select_folder_button.setEnabled(False)
        self.load_gdrive_button.setEnabled(False)
        self.question_input.setEnabled(False)
        self.send_button.setEnabled(False)
        self._remove_pending_load()
        self.pending_load_row = self._add_package_loading_message(loading_message)

        worker = PackageLoadWorker(
            source,
            self,
            source_kind=source_kind,
            existing_packages=self.packages,
        )
        worker.succeeded.connect(self._handle_package_load_succeeded)
        worker.failed.connect(self._handle_package_load_failed)
        worker.canceled.connect(self._handle_package_load_canceled)
        worker.progress.connect(self._handle_package_load_progress)
        worker.finished.connect(self._clear_package_load_worker)
        worker.finished.connect(worker.deleteLater)
        self.package_load_worker = worker
        self.cancel_load_button.setEnabled(True)
        self.cancel_load_button.show()
        self._package_loading_summary = loading_message.rstrip(".")
        self._package_loading_text = self._package_loading_summary
        self._package_loading_frame = 0
        self._advance_package_loading_animation()
        self.package_loading_timer.start()
        worker.start(QThread.Priority.LowPriority)

    def _handle_package_load_succeeded(self, result: dict) -> None:
        self._finish_package_loading_ui()
        self._remove_pending_load()
        self.packages = result["packages"]
        self.chunks = result["chunks"]
        self.search_index = result["search_index"]
        self._selected_package_count = int(result.get("selected_package_count", 0))
        self._selected_package_bytes = int(result.get("selected_package_bytes", 0))

        self.status_label.setText(
            f"총 {len(self.packages)}개 패키지 로드, {len(self.chunks)}개 청크 사용 가능"
        )
        self.select_folder_button.setEnabled(True)
        self.load_gdrive_button.setEnabled(True)

        if not self.packages:
            self.question_requirement_label.show()
            self.status_label.setText("읽을 수 있는 인수인계패키지가 없습니다.")
            self._add_missing_package_notice()
            self.question_input.setEnabled(False)
            self.send_button.setEnabled(False)
            return

        if not self.chunks:
            self.question_requirement_label.show()
            self.status_label.setText("패키지에서 읽을 수 있는 내용이 없습니다.")
            self._add_warning_message(
                "패키지는 찾았지만 읽을 수 있는 내용이 없습니다.\n\n"
                "패키지가 손상되었거나 생성 중 오류가 있었을 수 있습니다. "
                "메인 화면에서 [인수인계패키지 생성]을 다시 실행해 주세요."
            )
            self.question_input.setEnabled(False)
            self.send_button.setEnabled(False)
            return

        loaded_bytes = sum(
            self._safe_file_size(package.get("zip_path"))
            for package in self.packages
            if isinstance(package, dict)
        )
        if loaded_bytes == 0:
            loaded_bytes = self._selected_package_bytes
        self._add_system_message(
            f"패키지 {len(self.packages)}개, 총 {self._format_gigabytes(loaded_bytes)}기가를 "
            "불러왔습니다."
        )

        if not is_license_active():
            self.question_requirement_label.show()
            self.status_label.setText("라이선스를 먼저 등록해주세요.")
            self._add_system_message("라이선스를 먼저 등록해주세요.")
            self.question_input.setEnabled(False)
            self.send_button.setEnabled(False)
            return

        self.question_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.question_requirement_label.hide()
        self.question_input.setFocus()

    def _handle_package_load_failed(self, error_message: str) -> None:
        self._finish_package_loading_ui()
        self._remove_pending_load()
        self.status_label.setText("패키지 로드 실패")
        self._add_system_message(f"패키지 로드에 실패했습니다. {error_message}")
        self.select_folder_button.setEnabled(True)
        self.load_gdrive_button.setEnabled(True)
        can_ask = bool(self.packages and self.chunks and self.search_index and is_license_active())
        self.question_requirement_label.setVisible(not can_ask)
        self.question_input.setEnabled(can_ask)
        self.send_button.setEnabled(can_ask)

    def _handle_package_load_canceled(self) -> None:
        self._finish_package_loading_ui()
        self._remove_pending_load()
        self.status_label.setText("패키지 불러오기가 취소되었습니다.")
        self._add_system_message("패키지 불러오기가 취소되었습니다.")
        self.select_folder_button.setEnabled(True)
        self.load_gdrive_button.setEnabled(True)
        can_ask = bool(self.packages and self.chunks and self.search_index and is_license_active())
        self.question_requirement_label.setVisible(not can_ask)
        self.question_input.setEnabled(can_ask)
        self.send_button.setEnabled(can_ask)

    def _cancel_package_load(self) -> None:
        worker = self.package_load_worker
        if worker is None or not worker.isRunning():
            return
        worker.requestInterruption()
        self.cancel_load_button.setEnabled(False)
        self._package_loading_text = "불러오기를 취소하는 중"
        self.status_label.setText("불러오기를 취소하는 중...")

    def _handle_package_load_progress(self, stage: str, current: int) -> None:
        if stage == "extract":
            self._package_loading_text = f"ZIP 압축 해제 중 ({current / (1024 * 1024):,.0f}MB)"
        elif stage == "parse":
            self._package_loading_text = f"청크 데이터 읽는 중 ({current:,}개)"
        elif stage == "deduplicate":
            self._package_loading_text = f"중복 청크 정리 중 ({current:,}개)"
        elif stage == "index":
            self._package_loading_text = f"검색 데이터 확인 중 ({current:,}개)"
        elif stage == "matrix":
            self._package_loading_text = f"검색 행렬 생성 중 ({current:,}개)"
        elif stage == "metadata":
            self._package_loading_text = f"출처 정보 정리 중 ({current:,}개)"
        self._advance_package_loading_animation()

    def _advance_package_loading_animation(self) -> None:
        text = self._package_loading_summary
        if self._package_loading_text != self._package_loading_summary:
            text = f"{text}\n{self._package_loading_text}"
        if self.pending_load_row is not None:
            label = self.pending_load_row.findChild(QLabel, "packageLoadingLabel")
            if label is not None:
                label.setText(text)
        self._package_loading_frame = (self._package_loading_frame + 1) % 3

    def _finish_package_loading_ui(self) -> None:
        self.package_loading_timer.stop()
        self.cancel_load_button.hide()
        self.cancel_load_button.setEnabled(True)

    def _clear_package_load_worker(self) -> None:
        self.package_load_worker = None

    def closeEvent(self, event) -> None:
        worker = self.package_load_worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
        self.package_loading_timer.stop()
        self.answer_loading_timer.stop()
        super().closeEvent(event)

    def _send_question(self) -> None:
        query = self.question_input.text().strip()
        if not query or self.worker is not None:
            return
        if not self.chunks or self.search_index is None:
            QMessageBox.warning(self, "물어보기", "패키지 폴더를 먼저 선택해주세요.")
            return
        if not is_license_active():
            QMessageBox.warning(self, "물어보기", "라이선스를 먼저 등록해주세요.")
            return

        self._add_user_message(query)
        self.pending_question = query
        self.question_input.clear()
        self._set_busy(True)
        self.pending_answer_row = self._add_bot_message("답변 생성 중...", confidence="처리 중")
        self.streaming_answer_label = self.pending_answer_row.findChild(QLabel, "answerBody")
        self.streaming_answer_text = ""
        self._start_answer_loading_animation()

        self.worker = ChatAnswerWorker(
            query,
            self.search_index,
            load_saved_license_code() or "",
            self,
        )
        self.worker.succeeded.connect(self._handle_answer)
        self.worker.failed.connect(self._handle_answer_error)
        self.worker.rejected.connect(self._handle_answer_rejected)
        self.worker.answer_delta.connect(self._handle_answer_delta)
        self.worker.finished.connect(self._clear_worker)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _handle_answer(self, result: dict) -> None:
        self._remove_pending_answer()
        confidence = result.get("confidence", "추정")
        answer = str(result.get("answer", ""))
        sources = [str(source) for source in result.get("sources", [])]
        related = [
            {
                "answer": str(item.get("answer", "")),
                "confidence": str(item.get("confidence", "추정")),
                "sources": [str(source) for source in item.get("sources", [])],
            }
            for item in result.get("related", [])
            if isinstance(item, dict)
        ]
        self._add_bot_message(
            answer,
            confidence=str(confidence),
            sources=sources,
            question=self.pending_question,
            related=related,
        )
        self.chat_history.append(
            {
                "question": self.pending_question,
                "answer": answer,
                "confidence": str(confidence),
                "sources": sources,
                "related": related,
                "feedback": "",
            }
        )
        self.chat_history = self.chat_history[-self.MAX_HISTORY_ITEMS :]
        self._save_chat_history()
        self.pending_question = ""

        self.status_label.setText("답변 완료")
        self._set_busy(False)
        self._start_credit_usage_update()

    def _handle_answer_delta(self, delta: str) -> None:
        if not delta or self.streaming_answer_label is None:
            return
        self._stop_answer_loading_animation(streaming_started=True)
        self.streaming_answer_text += delta
        self._set_answer_body_text(
            self.streaming_answer_label, self.streaming_answer_text
        )
        self._scroll_to_bottom()

    def _start_answer_loading_animation(self) -> None:
        self._answer_loading_frame = 0
        self._advance_answer_loading_animation()
        self.answer_loading_timer.start()

    def _advance_answer_loading_animation(self) -> None:
        if self.streaming_answer_label is None or self.streaming_answer_text:
            self._stop_answer_loading_animation()
            return
        dots = ("...", "..", ".")[self._answer_loading_frame]
        loading_text = f"답변 생성 중{dots}"
        self._set_answer_body_text(self.streaming_answer_label, loading_text)
        self.status_label.setText(loading_text)
        self._answer_loading_frame = (self._answer_loading_frame + 1) % 3

    def _stop_answer_loading_animation(
        self, *, streaming_started: bool = False
    ) -> None:
        if self.answer_loading_timer.isActive():
            self.answer_loading_timer.stop()
        if streaming_started:
            self.status_label.setText("답변을 작성하는 중...")

    def _handle_answer_rejected(self, message: str) -> None:
        self._remove_pending_answer()
        self._add_warning_message(message)
        self.status_label.setText("크레딧 부족")
        self.pending_question = ""
        self._set_busy(False)

    def _start_credit_usage_update(self) -> None:
        license_code = load_saved_license_code() or ""
        if not license_code:
            return
        worker = CreditUsageWorker(license_code, self)
        worker.balance_ready.connect(self._apply_credit_balance)
        worker.finished.connect(lambda worker=worker: self._clear_credit_worker(worker))
        worker.finished.connect(worker.deleteLater)
        self.credit_workers.add(worker)
        worker.start()

    def _apply_credit_balance(self, license_code: str, balance: object) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "_apply_credit_balance"):
            parent._apply_credit_balance(license_code, balance)

    def _clear_credit_worker(self, worker: CreditUsageWorker) -> None:
        self.credit_workers.discard(worker)

    def _handle_answer_error(self, error_message: str) -> None:
        self._remove_pending_answer()
        self._add_system_message(f"답변 생성에 실패했습니다. {error_message}")
        self.status_label.setText("답변 생성 실패")
        self.pending_question = ""
        self._set_busy(False)

    def _clear_worker(self) -> None:
        self.worker = None
        if self.chunks and is_license_active():
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
                "#4A78B8",
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
        question: str = "",
        related: list[dict] | None = None,
        feedback: str = "",
    ) -> QWidget:
        return self._add_message_row(
            self._create_answer_bubble(
                text,
                confidence=confidence,
                sources=sources or [],
                question=question,
                related=related or [],
                feedback=feedback,
                background="#ffffff",
                border="#e5e7eb",
                label="",
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
                question="",
                related=[],
                feedback="",
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

    def _add_package_loading_message(self, text: str) -> QWidget:
        bubble = QFrame()
        bubble.setObjectName("packageLoadingBubble")
        bubble.setMaximumWidth(560)
        bubble.setStyleSheet(
            "QFrame#packageLoadingBubble { background: #F3E8FF; "
            "border: 1px solid #D8B4FE; border-radius: 12px; }"
        )
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(9)
        label = QLabel(text)
        label.setObjectName("packageLoadingLabel")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            "QLabel#packageLoadingLabel { color: #6B21A8; font-size: 13px; "
            "font-weight: 600; background-color: transparent; border: none; }"
        )
        indicator = QProgressBar()
        indicator.setObjectName("packageLoadingIndicator")
        indicator.setRange(0, 0)
        indicator.setTextVisible(False)
        indicator.setFixedHeight(8)
        indicator.setStyleSheet(
            "QProgressBar { background: #E9D5FF; border: none; border-radius: 4px; } "
            "QProgressBar::chunk { background: #A78BFA; border-radius: 4px; }"
        )
        layout.addWidget(label)
        layout.addWidget(indicator)
        return self._add_message_row(bubble, align_right=False, centered=True)

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
        label.setProperty("chatSearchText", text)
        label.setProperty("chatSearchColor", color)
        layout.addWidget(label)
        return bubble

    def _create_answer_bubble(
        self,
        text: str,
        *,
        confidence: str,
        sources: list[str],
        question: str,
        related: list[dict],
        feedback: str,
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
        badge = QLabel(confidence)
        badge.setStyleSheet(
            "QLabel { border-radius: 8px; padding: 2px 7px; font-size: 11px; "
            f"{_CONFIDENCE_BADGE_STYLES.get(confidence, _CONFIDENCE_BADGE_STYLES['확인불가'])} }}"
        )
        if label:
            title = QLabel(label)
            title.setStyleSheet("color: #334155; font-size: 12px; font-weight: bold;")
            header_row.addWidget(title)
        header_row.addWidget(badge)
        header_row.addStretch()
        layout.addLayout(header_row)

        body = QLabel(text or "응답 내용이 비어 있습니다.")
        body.setObjectName("answerBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet("color: #111827; font-size: 14px;")
        self._set_answer_body_text(body, text or "응답 내용이 비어 있습니다.")
        layout.addWidget(body)

        # Related material is a confidence decision, never an answer-text decision.
        if confidence in {"확인 필요", "확인불가", "확인 불가"}:
            sources = []
            related = []

        material_sources: list[str] = []
        material_notes: list[str] = []
        seen_materials: set[str] = set()

        def add_source(source: str) -> None:
            display, path = self._split_source_display_and_path(source)
            key = f"path:{os.path.normcase(path)}" if path else f"text:{display}"
            if key not in seen_materials:
                seen_materials.add(key)
                material_sources.append(source)

        for source in sources:
            add_source(source)
        for item in related:
            related_text = str(item.get("answer", "")).strip()
            if related_text:
                if self._extract_source_path(related_text):
                    add_source(related_text)
                elif f"note:{related_text}" not in seen_materials:
                    seen_materials.add(f"note:{related_text}")
                    material_notes.append(related_text)
            for source in item.get("sources", []):
                add_source(str(source))

        if material_sources or material_notes:
            toggle_button = QPushButton("관련 자료 보기 ▾")
            toggle_button.setObjectName("relatedToggleButton")
            toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
            toggle_button.setStyleSheet(
                "QPushButton { color: #475569; background: transparent; border: none; "
                "font-size: 12px; font-weight: 600; text-align: left; padding: 3px 0; } "
                "QPushButton:hover { color: #2563EB; }"
            )
            related_container = QFrame()
            related_container.setObjectName("relatedContainer")
            related_container.setStyleSheet(
                "QFrame#relatedContainer { background: #F8FAFC; border: 1px solid #E2E8F0; "
                "border-radius: 8px; }"
            )
            related_layout = QVBoxLayout(related_container)
            related_layout.setContentsMargins(9, 8, 9, 8)
            related_layout.setSpacing(6)
            if material_sources:
                related_layout.addWidget(self._create_sources_label(material_sources))
            for related_text in material_notes:
                related_answer = QLabel(related_text)
                related_answer.setWordWrap(True)
                related_answer.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                related_answer.setStyleSheet("color: #475569; font-size: 12px;")
                related_answer.setProperty("chatSearchText", related_text)
                related_answer.setProperty("chatSearchColor", "#475569")
                related_layout.addWidget(related_answer)
            related_container.hide()

            def toggle_related() -> None:
                scroll_bar = self.chat_area.verticalScrollBar()
                saved_scroll_value = scroll_bar.value()
                expanded = not related_container.isVisible()
                related_container.setVisible(expanded)
                toggle_button.setText(
                    "관련 자료 숨기기 ▴" if expanded else "관련 자료 보기 ▾"
                )
                scroll_bar.setValue(saved_scroll_value)
                QTimer.singleShot(
                    0,
                    lambda value=saved_scroll_value, bar=scroll_bar: bar.setValue(value),
                )

            toggle_button.clicked.connect(toggle_related)
            layout.addWidget(toggle_button)
            layout.addWidget(related_container)

        if question:
            feedback_row = QHBoxLayout()
            feedback_row.setSpacing(6)
            feedback_label = QLabel("답변이 도움됐나요?")
            feedback_label.setStyleSheet("color: #94A3B8; font-size: 11px;")
            up_button = QPushButton("👍")
            down_button = QPushButton("👎")
            up_button.setObjectName("feedbackUpButton")
            down_button.setObjectName("feedbackDownButton")
            for button in (up_button, down_button):
                button.setFixedSize(34, 26)
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setStyleSheet(
                    "QPushButton { background: #F8FAFC; border: 1px solid #E2E8F0; "
                    "border-radius: 6px; font-size: 13px; } "
                    "QPushButton:hover { background: #F1F5F9; border-color: #CBD5E1; }"
                )
            submitted_label = QLabel("")
            submitted_label.setObjectName("feedbackSubmittedLabel")
            submitted_label.setStyleSheet(
                "color: #64748B; font-size: 11px; font-weight: 600;"
            )
            feedback_row.addWidget(feedback_label)
            feedback_row.addStretch()
            feedback_row.addWidget(up_button)
            feedback_row.addWidget(down_button)
            feedback_row.addWidget(submitted_label)
            layout.addLayout(feedback_row)

            def mark_submitted(rating: str, *, send: bool) -> None:
                focused_widget = self.question_input
                up_button.setEnabled(False)
                down_button.setEnabled(False)
                selected = up_button if rating == "up" else down_button
                selected.setStyleSheet(
                    "QPushButton { background: #EDE9FE; border: 1px solid #8B5CF6; "
                    "border-radius: 6px; font-size: 13px; }"
                )
                submitted_label.setText("제출됨")
                if send:
                    self._submit_answer_feedback(
                        question, text, rating, up_button, down_button
                    )
                self._restore_feedback_focus(focused_widget)
                for delay in (0, 250, 750):
                    QTimer.singleShot(
                        delay,
                        lambda target=focused_widget: self._restore_feedback_focus(
                            target
                        ),
                    )

            up_button.clicked.connect(lambda: mark_submitted("up", send=True))
            down_button.clicked.connect(lambda: mark_submitted("down", send=True))
            if feedback in {"up", "down"}:
                mark_submitted(feedback, send=False)

        return bubble

    def _restore_feedback_focus(self, target: QWidget) -> None:
        if not target.isEnabled() or not target.isVisible():
            target = self.question_input
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _create_sources_label(self, sources: list[str]) -> QLabel:
        source_records = [self._split_source_display_and_path(source) for source in sources]
        lines = [html.escape(display) for display, _source_path in source_records]
        plain_text = "\n".join(f"• {display}" for display, _source_path in source_records)
        source_label = QLabel(
            '<div style="font-size:10pt; line-height:110%;">'
            + "<br>".join(f"• {line}" for line in lines)
            + "</div>"
        )
        source_label.setWordWrap(True)
        source_label.setTextFormat(Qt.TextFormat.RichText)
        source_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        source_label.setStyleSheet("color: #64748b; font-size: 10pt;")
        source_label.setProperty("chatSearchText", plain_text)
        source_label.setProperty("chatSearchColor", "#64748b")
        source_label.setProperty("chatSearchLineHeight", 110)
        return source_label

    @staticmethod
    def _extract_source_path(source: str) -> str:
        match = re.search(r"\(경로:\s*(.*)\)$", source.strip())
        if match is None:
            return ""
        metadata = match.group(1)
        for separator in (", 수정일시:", ", 수정일:"):
            if separator in metadata:
                metadata = metadata.split(separator, 1)[0]
                break
        path = metadata.strip()
        return "" if path in {"", "확인되지 않음"} else path

    @classmethod
    def _split_source_display_and_path(cls, source: str) -> tuple[str, str]:
        source_path = cls._extract_source_path(source)
        if not source_path:
            return source, ""
        path = Path(source_path).expanduser()
        if path.is_absolute():
            return source, str(path)
        candidates = (
            Path.cwd() / path,
            Path.home() / "Desktop" / path,
            Path.home() / "Documents" / path,
            Path.home() / path,
        )
        for candidate in candidates:
            if candidate.exists():
                return source, str(candidate.resolve())
        return source, source_path

    def _submit_answer_feedback(
        self,
        question: str,
        answer: str,
        rating: str,
        _up_button: QPushButton,
        _down_button: QPushButton,
    ) -> None:
        for item in reversed(self.chat_history):
            if item.get("question") == question and item.get("answer") == answer:
                item["feedback"] = rating
                break
        self._save_chat_history()
        worker = ChatFeedbackWorker(
            load_saved_license_code() or "",
            question,
            answer[:200],
            rating,
            self,
        )
        worker.finished.connect(
            lambda worker=worker: self.feedback_workers.discard(worker)
        )
        worker.finished.connect(worker.deleteLater)
        self.feedback_workers.add(worker)
        worker.start()

    def _history_path(self) -> Path:
        license_code = load_saved_license_code() or "unlicensed"
        safe_code = re.sub(r"[^A-Za-z0-9._-]", "_", license_code)
        return Path("config") / f"chat_history_{safe_code}.json"

    def _load_chat_history(self) -> None:
        try:
            raw = json.loads(self._history_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            self.chat_history = []
            return
        if not isinstance(raw, list):
            self.chat_history = []
            return
        self.chat_history = [
            item
            for item in raw[-self.MAX_HISTORY_ITEMS :]
            if isinstance(item, dict)
            and isinstance(item.get("question"), str)
            and isinstance(item.get("answer"), str)
        ]

    def _save_chat_history(self) -> None:
        path = self._history_path()
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(
                    self.chat_history[-self.MAX_HISTORY_ITEMS :],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary_path.replace(path)
        except OSError:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _render_chat_history(self) -> None:
        if not self.chat_history:
            return
        self._add_system_message(
            f"이전 대화 {len(self.chat_history)}개를 불러왔습니다."
        )
        for item in self.chat_history:
            self._add_user_message(str(item.get("question", "")))
            self._add_bot_message(
                str(item.get("answer", "")),
                confidence=str(item.get("confidence", "추정")),
                sources=[str(source) for source in item.get("sources", [])],
                question=str(item.get("question", "")),
                related=[
                    related
                    for related in item.get("related", [])
                    if isinstance(related, dict)
                ],
                feedback=str(item.get("feedback", "")),
            )

    def _remove_pending_answer(self) -> None:
        self._stop_answer_loading_animation()
        if self.pending_answer_row is None:
            return
        self.pending_answer_row.setParent(None)
        self.pending_answer_row.deleteLater()
        self.pending_answer_row = None
        self.streaming_answer_label = None
        self.streaming_answer_text = ""

    @staticmethod
    def _set_answer_body_text(label: QLabel, text: str) -> None:
        safe_text = html.escape(text).replace("\n", "<br>")
        label.setProperty("chatSearchText", text)
        label.setProperty("chatSearchColor", "#111827")
        label.setProperty("chatSearchLineHeight", 120)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(f'<div style="line-height:120%;">{safe_text}</div>')

    def _remove_pending_load(self) -> None:
        if self.pending_load_row is None:
            return
        self.pending_load_row.setParent(None)
        self.pending_load_row.deleteLater()
        self.pending_load_row = None

    @staticmethod
    def _safe_file_size(value: object) -> int:
        if not value:
            return 0
        try:
            return os.path.getsize(str(value))
        except OSError:
            return 0

    @classmethod
    def _measure_local_packages(cls, folder: Path) -> tuple[int, int]:
        try:
            zip_paths = [path for path in folder.glob("*.zip") if path.is_file()]
            package_folders = [
                path for path in folder.iterdir() if path.is_dir() and _is_package_root(path)
            ]
        except OSError:
            return 0, 0
        return (
            len(zip_paths) + len(package_folders),
            sum(cls._safe_file_size(path) for path in zip_paths),
        )

    @staticmethod
    def _format_gigabytes(size_bytes: int) -> str:
        return f"{bytes_to_gb(size_bytes):.2f}"

    def _reset_chat_search(self, *_args) -> None:
        if self._chat_search_index >= 0:
            self._restore_search_labels_preserving_scroll()
        self._chat_search_query = ""
        self._chat_search_matches = []
        self._chat_search_index = -1
        self.chat_search_result_label.clear()

    def _restore_search_labels_preserving_scroll(self) -> None:
        # Re-rendering every searchable label (to clear a prior highlight)
        # can nudge the scroll area's content height, which otherwise drags
        # the visible scroll position along with it even though nothing the
        # user is looking at should move.
        scroll_bar = self.chat_area.verticalScrollBar()
        saved_scroll_value = scroll_bar.value()
        self._restore_search_labels()
        scroll_bar.setValue(saved_scroll_value)
        QTimer.singleShot(
            0, lambda value=saved_scroll_value, bar=scroll_bar: bar.setValue(value)
        )

    def _restore_search_labels(self) -> None:
        for label in self.chat_container.findChildren(QLabel):
            original = label.property("chatSearchText")
            if isinstance(original, str):
                self._render_searchable_label(label, original)

    def _render_searchable_label(
        self,
        label: QLabel,
        original: str,
        match: tuple[int, int] | None = None,
    ) -> None:
        if match is None:
            content = html.escape(original).replace("\n", "<br>")
        else:
            start, end = match
            content = (
                html.escape(original[:start])
                + '<span style="background-color:#FDE68A;color:#111827;">'
                + html.escape(original[start:end])
                + "</span>"
                + html.escape(original[end:])
            ).replace("\n", "<br>")
        line_height = label.property("chatSearchLineHeight")
        if line_height:
            content = f'<div style="line-height:{int(line_height)}%;">{content}</div>'
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(content)

    def _find_next_chat_text(self) -> None:
        scroll_bar = self.chat_area.verticalScrollBar()
        saved_scroll_value = scroll_bar.value()
        saved_cursor_position = self.chat_search_input.cursorPosition()
        had_focus = self.chat_search_input.hasFocus()
        query = self.chat_search_input.text().strip()
        if not query:
            return
        if query.casefold() != self._chat_search_query.casefold():
            self._restore_search_labels_preserving_scroll()
            self._chat_search_query = query
            self._chat_search_matches = []
            folded_query = query.casefold()
            for label in self.chat_container.findChildren(QLabel):
                original = label.property("chatSearchText")
                if not isinstance(original, str):
                    continue
                folded_text = original.casefold()
                start = 0
                while True:
                    index = folded_text.find(folded_query, start)
                    if index < 0:
                        break
                    self._chat_search_matches.append(
                        (label, index, index + len(query))
                    )
                    start = index + max(len(query), 1)
            self._chat_search_index = -1

        if not self._chat_search_matches:
            self.chat_search_result_label.setText("검색 결과가 없습니다")
            self._restore_failed_search_view(
                scroll_bar,
                saved_scroll_value,
                saved_cursor_position,
                had_focus,
            )
            QTimer.singleShot(
                0,
                lambda value=saved_scroll_value, position=saved_cursor_position,
                focus=had_focus, bar=scroll_bar: self._restore_failed_search_view(
                    bar, value, position, focus
                ),
            )
            return

        self._restore_search_labels()
        self._chat_search_index = (
            self._chat_search_index + 1
        ) % len(self._chat_search_matches)
        label, start, end = self._chat_search_matches[self._chat_search_index]
        original = str(label.property("chatSearchText"))
        self._render_searchable_label(label, original, (start, end))
        self.chat_search_result_label.setText(
            f"{self._chat_search_index + 1}/{len(self._chat_search_matches)}건"
        )
        self._reveal_and_scroll_to_search_match(label, start)

    def _reveal_and_scroll_to_search_match(
        self, label: QLabel, match_start: int
    ) -> None:
        parent = label.parentWidget()
        while parent is not None and parent is not self.chat_container:
            if parent.objectName() == "relatedContainer" and not parent.isVisible():
                parent.show()
                toggle = parent.parentWidget().findChild(
                    QPushButton, "relatedToggleButton"
                )
                if toggle is not None:
                    toggle.setText("관련 자료 숨기기 ▴")
            parent = parent.parentWidget()

        def scroll_to_target() -> None:
            self.chat_container.layout().activate()
            self.chat_area.ensureWidgetVisible(label, 20, 40)
            bar = self.chat_area.verticalScrollBar()
            label_y = label.mapTo(self.chat_container, QPoint(0, 0)).y()
            target_y = label_y + self._search_match_vertical_offset(
                label, match_start
            )
            # Keep the matched text near the top of the viewport. Centering
            # made several early lines clamp to scroll value 0 (and several
            # final lines clamp to maximum), making distinct matches appear
            # not to move even with correct per-line coordinates.
            positioned = target_y - 40
            bar.setValue(max(bar.minimum(), min(positioned, bar.maximum())))

        QTimer.singleShot(0, scroll_to_target)
        QTimer.singleShot(30, scroll_to_target)

    @staticmethod
    def _search_match_vertical_offset(label: QLabel, match_start: int) -> int:
        original = label.property("chatSearchText")
        if not isinstance(original, str) or match_start <= 0:
            return 0
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(label.font())
        document.setPlainText(original)
        document.setTextWidth(max(1, label.contentsRect().width()))
        cursor = QTextCursor(document)
        cursor.setPosition(min(match_start, len(original)))
        block = cursor.block()
        block_rect = document.documentLayout().blockBoundingRect(block)
        line = block.layout().lineForTextPosition(
            max(0, cursor.position() - block.position())
        )
        offset = block_rect.top() + (line.y() if line.isValid() else 0)
        if line.isValid() and line.textLength() > 0:
            # A vertical scrollbar cannot express two matches on the same
            # wrapped line. Encode the horizontal position as a small offset
            # within that line so consecutive same-line matches still produce
            # a distinct viewport position while keeping the correct line in
            # view and highlighted.
            cursor_position = line.cursorToX(
                max(0, cursor.position() - block.position())
            )
            cursor_x = float(
                cursor_position[0]
                if isinstance(cursor_position, tuple)
                else cursor_position
            )
            line_span = max(1.0, float(line.naturalTextWidth()))
            offset += min(
                max(0.0, float(line.height()) - 1.0),
                max(0.0, cursor_x / line_span) * max(1.0, float(line.height()) - 1.0),
            )
        line_height = label.property("chatSearchLineHeight")
        if line_height:
            offset *= max(1, int(line_height)) / 100
        return max(0, int(round(offset)))

    def _restore_failed_search_view(
        self,
        scroll_bar,
        scroll_value: int,
        cursor_position: int,
        restore_focus: bool,
    ) -> None:
        scroll_bar.setValue(scroll_value)
        self.chat_search_input.setCursorPosition(cursor_position)
        if restore_focus:
            self.chat_search_input.setFocus(Qt.FocusReason.OtherFocusReason)

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


