from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import Settings
from src.core.document_writer import DocumentWriter
from src.ui.dialogs.api_key_dialog import ApiKeyDialog
from src.ui.dialogs.confirm_analysis_dialog import ConfirmAnalysisDialog
from src.ui.dialogs.project_selection_dialog import ProjectSelectionDialog
from src.ui.widgets.action_panel import ActionPanel
from src.ui.widgets.input_panel import InputPanel
from src.ui.widgets.preview_panel import PreviewPanel
from src.ui.workers.analysis_worker import AnalysisWorker


class MainWindow(QMainWindow):
    """메인 윈도우 — 업무 인수인계 자동화 시스템."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._last_markdown: str = ""
        self._last_saved_path: str = ""
        self._worker: AnalysisWorker | None = None
        self._doc_writer = DocumentWriter()

        self.setWindowTitle("업무 인수인계 자동화 시스템")
        self.setMinimumSize(820, 900)
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(12)

        self.input_panel = InputPanel()
        content_layout.addWidget(self.input_panel)

        self.action_panel = ActionPanel()
        content_layout.addWidget(self.action_panel)

        self.preview_panel = PreviewPanel()
        content_layout.addWidget(self.preview_panel, stretch=1)

        content_layout.addStretch(0)
        scroll.setWidget(content)
        root_layout.addWidget(scroll)

    def _connect_signals(self) -> None:
        self.action_panel.analyze_btn.clicked.connect(self._on_analyze_clicked)
        self.action_panel.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.action_panel.save_btn.clicked.connect(self._on_save_clicked)
        self.action_panel.open_btn.clicked.connect(self._on_open_clicked)

    # ------------------------------------------------------------------
    @Slot()
    def _on_analyze_clicked(self) -> None:
        job_desc = self.input_panel.get_job_description()
        if not job_desc:
            QMessageBox.warning(self, "입력 오류", "업무분장을 입력해 주세요.")
            return

        test_mode  = self.action_panel.is_test_mode()
        light_mode = self.action_panel.is_light_mode()

        if not test_mode and not self._settings.is_valid():
            if not self._request_api_key():
                return

        file_display_map = self.input_panel.get_file_display_map()

        self.preview_panel.clear()
        self.action_panel.set_result_ready(False)
        self.action_panel.reset_status_style()
        if test_mode:
            _loading_msg = "[테스트 모드] 날짜 필터 적용 중..."
        elif light_mode:
            _loading_msg = "[라이트 모드] 날짜 필터 적용 중..."
        else:
            _loading_msg = "날짜 필터 적용 중..."
        self.action_panel.set_loading(True, _loading_msg)

        self._worker = AnalysisWorker(
            self._settings, job_desc, file_display_map,
            test_mode=test_mode, light_mode=light_mode,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_analysis_done)
        self._worker.error.connect(self._on_analysis_error)
        self._worker.extract_warnings.connect(self._on_extract_warnings)
        self._worker.stats_ready.connect(self._on_stats_ready)
        self._worker.time_estimated.connect(self._on_time_estimated)
        self._worker.eta_updated.connect(self._on_eta_updated)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.projects_ready.connect(self._on_projects_ready)
        self._worker.approval_needed.connect(self._on_approval_needed)
        self._worker.start()

    @Slot()
    def _on_cancel_clicked(self) -> None:
        if self._worker and self._worker.isRunning():
            self.action_panel.cancel_btn.setEnabled(False)
            self.action_panel.update_status("중단 요청 중... 현재 작업 완료 후 종료됩니다.")
            self._worker.request_cancel()

    # ── 분석 결과 슬롯 ────────────────────────────────────────────────
    @Slot(str)
    def _on_progress(self, message: str) -> None:
        self.action_panel.update_status(message)

    @Slot(str)
    def _on_analysis_done(self, markdown: str) -> None:
        self._last_markdown = markdown
        self.preview_panel.set_markdown(markdown)
        self.action_panel.set_loading(False)
        self.action_panel.set_result_ready(True)

    @Slot(str)
    def _on_analysis_error(self, message: str) -> None:
        self.action_panel.set_loading(False)
        QMessageBox.critical(self, "분석 오류", f"분석 중 오류가 발생했습니다:\n{message}")

    @Slot(list)
    def _on_extract_warnings(self, failed: list) -> None:
        names = "\n".join(f"  • {f}" for f in failed[:20])
        suffix = f"\n  ... 외 {len(failed) - 20}개" if len(failed) > 20 else ""
        QMessageBox.warning(
            self,
            "파일 추출 경고",
            f"아래 파일은 텍스트를 추출하지 못했습니다.\n나머지 파일로 분석을 계속합니다.\n\n{names}{suffix}",
        )

    @Slot(dict)
    def _on_time_estimated(self, info: dict) -> None:
        """필터 완료 후 예상 시간 정보를 액션 패널에 표시한다."""
        self.action_panel.show_time_estimate(info)
        targets = info.get("analysis_targets", 0)
        total = info.get("total_files", 0)
        self.action_panel.update_status(
            f"분석 준비 완료  |  전체 {total:,}개 → 대상 {targets:,}개"
        )

    @Slot(dict)
    def _on_eta_updated(self, info: dict) -> None:
        """단계 진행 중 남은 시간·종료 시각을 시간 표시 영역에 업데이트한다."""
        _rs = info.get("remaining_sec", 0)
        _ef = info.get("expected_finish", "")
        print(
            f"[ETA RAW]\n"
            f"  remaining_sec    = {_rs}\n"
            f"  expected_finish  = {_ef!r}\n"
            f"  stage            = {info.get('stage', '')!r}"
        )
        self.action_panel.update_progress_with_eta(
            stage=info.get("stage", "처리 중"),
            done=info.get("done", 0),
            total=max(1, info.get("total", 1)),
            remaining_sec=_rs,
            expected_finish=_ef,
        )

    @Slot(list)
    def _on_projects_ready(self, projects_data: list) -> None:
        """
        워커가 프로젝트 요약 완료 후 프로젝트 선택을 요청한다.
        이 슬롯은 반드시 메인(GUI) 스레드에서 실행된다.
        """
        if self._worker is None:
            return

        print(f"[DEBUG] ProjectSelectionDialog 생성 ({len(projects_data)}개 프로젝트)")
        dialog = ProjectSelectionDialog(projects_data, parent=self)
        accepted = dialog.exec() == ProjectSelectionDialog.DialogCode.Accepted
        print(f"[DEBUG] ProjectSelectionDialog 결과: {'수락' if accepted else '취소'}")

        if accepted:
            selected_keys = dialog.get_selected_keys()
            self._worker.set_selected_project_keys(selected_keys)
        else:
            self._worker.set_selected_project_keys(None)
            self.action_panel.show_cancelled()

    @Slot(dict)
    def _on_approval_needed(self, cost_info: dict) -> None:
        """
        워커가 GPT 호출 전 사용자 승인을 요청한다.
        이 슬롯은 반드시 메인(GUI) 스레드에서 실행된다.
        """
        if self._worker is None:
            return

        dialog = ConfirmAnalysisDialog(cost_info, parent=self)
        approved = dialog.exec() == ConfirmAnalysisDialog.DialogCode.Accepted

        self._worker.grant_approval(approved)

        if not approved:
            self.action_panel.show_cancelled()

    @Slot(dict)
    def _on_stats_ready(self, stats: dict) -> None:
        parts = [
            f"전체 {stats['total_files']:,}개",
            f"대상 {stats['success_files']:,}개",
            f"프로젝트 {stats['project_count']:,}개",
        ]
        if stats.get("similar_filtered"):
            parts.append(f"유사제거 {stats['similar_filtered']:,}개")
        reduction = stats.get("project_summary_reduction_pct", 0)
        if reduction > 0:
            parts.append(f"토큰절감 {reduction:.0f}%")
        self.action_panel.update_status("  |  ".join(parts))

    @Slot(dict)
    def _on_cancelled(self, info: dict) -> None:
        self.action_panel.show_cancelled()
        print(
            f"[UI] 분석 중단 완료  위치: {info.get('aborted_at')}  "
            f"처리: {info.get('processed')}개"
        )

    # ── Word 저장/열기 ─────────────────────────────────────────────────
    @Slot()
    def _on_save_clicked(self) -> None:
        if not self._last_markdown:
            return
        default_name = "업무 인수인계 보고서.docx"
        path, _ = QFileDialog.getSaveFileName(
            self, "Word 파일 저장", default_name, "Word 문서 (*.docx)"
        )
        if not path:
            return
        try:
            doc = self._doc_writer.create(self._last_markdown)
            self._last_saved_path = self._doc_writer.save(doc, path)
            QMessageBox.information(self, "저장 완료", f"저장되었습니다:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "저장 오류", str(exc))

    @Slot()
    def _on_open_clicked(self) -> None:
        if not self._last_saved_path:
            QMessageBox.information(self, "알림", "먼저 Word 파일을 저장해 주세요.")
            return
        try:
            self._doc_writer.open_file(self._last_saved_path)
        except Exception as exc:
            QMessageBox.critical(self, "열기 오류", str(exc))

    # ------------------------------------------------------------------
    def _request_api_key(self) -> bool:
        dialog = ApiKeyDialog(self)
        if dialog.exec() == ApiKeyDialog.DialogCode.Accepted:
            key = dialog.get_key()
            if key:
                self._settings.apply_key(key)
                return True
        QMessageBox.warning(self, "API Key 없음", "API Key를 입력해야 분석을 시작할 수 있습니다.")
        return False
