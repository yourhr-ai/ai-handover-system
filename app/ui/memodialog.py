import os
import re
from collections.abc import Callable
from html import escape as html_escape

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextOption
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.analysis_result import FolderTreeNode, HandoverQA, WorkMemo
from app.ui.handover_qa_dialog import HandoverQADialog


MEMO_DIALOG_WIDTH = 1000
MEMO_DIALOG_HEIGHT = 720
MEMO_DIALOG_MIN_WIDTH = 1000
MEMO_DIALOG_MIN_HEIGHT = 680
MEMO_DIALOG_MARGIN = 16
MEMO_BUTTON_SPACING = 8
MEMO_SECTION_SPACING = 10
MEMO_CONTENT_SPACING = 24
MEMO_LIST_HEIGHT = 150
MEMO_TITLE_HEIGHT = 42
MEMO_CONTENT_MIN_HEIGHT = 290
MEMO_ACTION_BUTTON_HEIGHT = 40
MEMO_TITLE_LABEL_TOP_MARGIN = 20
MEMO_BUTTON_BAR_BOTTOM_MARGIN = 20
MEMO_PROGRESS_BAR_MARGIN = 20


def _set_button_role(button: QPushButton, role: str) -> None:
    button.setProperty("buttonRole", role)


_CHECK_SYMBOLS = {
    Qt.CheckState.Unchecked: "☐",
    Qt.CheckState.Checked: "☑",
    Qt.CheckState.PartiallyChecked: "◫",
}
# Stored on a custom role (not Qt.ItemDataRole.CheckStateRole) so QTreeWidget's
# native checkbox indicator is never drawn; only the ☐/☑ text symbol represents it.
_CHECK_STATE_ROLE = Qt.ItemDataRole.UserRole + 2


def _get_check_state(item: QTreeWidgetItem) -> Qt.CheckState:
    state = item.data(0, _CHECK_STATE_ROLE)
    return state if state is not None else Qt.CheckState.Unchecked


def _set_check_state(item: QTreeWidgetItem, state: Qt.CheckState) -> None:
    item.setData(0, _CHECK_STATE_ROLE, state)


# Distinguishes folder rows from file rows so checked-folder paths go to
# WorkMemo.linked_folders while individually-checked files go to linked_files.
_ITEM_TYPE_ROLE = Qt.ItemDataRole.UserRole + 3
_ITEM_TYPE_FOLDER = "folder"
_ITEM_TYPE_FILE = "file"


def _is_file_item(item: QTreeWidgetItem) -> bool:
    return item.data(0, _ITEM_TYPE_ROLE) == _ITEM_TYPE_FILE


def _get_parent_folder_relative_path(file_relative_path: str) -> str:
    return file_relative_path.rsplit("/", 1)[0]


class MemoWorkflowProgressBar(QWidget):
    """Static 4-step guide for the memo-writing workflow.

    "알려주세요" no longer has its own step: [인수인계서저장] now opens that
    popup automatically, so that stage is folded into "인수인계서저장".

    Intentionally never reflects live completion state: showing a step as
    "done" tempts users to stop after writing just one memo, even though
    writing several is more useful to the person taking over. This is a
    purely informational, always-neutral order-of-operations label.
    """

    STEP_LABELS = ("내용작성", "자료연결", "내용저장", "인수인계서저장")
    STEP_COLOR = QColor("#7C3AED")
    LABEL_COLOR = QColor("#6B7280")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("memoWorkflowProgressBar")
        self.setFixedHeight(62)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = max(self.width(), 1)
        step_count = len(self.STEP_LABELS)
        side_margin = max(38.0, min(70.0, width * 0.06))
        available_width = max(1.0, width - (side_margin * 2))
        centers = [
            QPointF(side_margin + available_width * index / (step_count - 1), 19.0)
            for index in range(step_count)
        ]

        painter.setPen(QPen(self.STEP_COLOR, 2.0, Qt.PenStyle.SolidLine))
        for index in range(step_count - 1):
            painter.drawLine(
                QPointF(centers[index].x() + 14, centers[index].y()),
                QPointF(centers[index + 1].x() - 14, centers[index + 1].y()),
            )

        symbol_font = QFont(self.font())
        symbol_font.setPixelSize(12)
        symbol_font.setBold(True)
        label_font = QFont(self.font())
        label_font.setPixelSize(11)

        for index, center in enumerate(centers):
            circle = QRectF(center.x() - 12, center.y() - 12, 24, 24)
            painter.setPen(QPen(self.STEP_COLOR, 2.0))
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(circle)

            painter.setFont(symbol_font)
            painter.setPen(self.STEP_COLOR)
            painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(index + 1))

            painter.setFont(label_font)
            painter.setPen(self.LABEL_COLOR)
            painter.drawText(
                QRectF(center.x() - 58, 37, 116, 20),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self.STEP_LABELS[index],
            )


class MemoDialog(QDialog):
    def __init__(
        self,
        folder_tree: list[FolderTreeNode],
        memos: list[WorkMemo],
        initial_linked_folders: list[str] | None = None,
        root_folder_path: str = "",
        parsed_emails: list[dict] | None = None,
        kakao_file_paths: list[str] | None = None,
        handover_qa: HandoverQA | None = None,
        on_save_word: Callable[[], bool | None] | None = None,
        analysismode: str = "basic",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("인수인계서 작성")
        self.setObjectName("memoDialog")
        self.resize(MEMO_DIALOG_WIDTH, MEMO_DIALOG_HEIGHT)
        self.setMinimumSize(MEMO_DIALOG_MIN_WIDTH, MEMO_DIALOG_MIN_HEIGHT)

        self.folder_tree = folder_tree
        self.memos = memos
        self.root_folder_path = root_folder_path
        self.parsed_emails = parsed_emails or []
        self.kakao_file_paths = kakao_file_paths or []
        self.handover_qa = handover_qa or HandoverQA()
        self.on_save_word = on_save_word
        self.analysismode = analysismode
        self.current_memo_index = -1
        self.is_loading = False
        self.is_syncing_tree = False
        self.has_unsaved_changes = False
        self.is_reverting_selection = False
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(2000)
        self.autosave_timer.timeout.connect(self._autosave_current_memo)
        self.autosave_status_timer = QTimer(self)
        self.autosave_status_timer.setSingleShot(True)
        self.autosave_status_timer.setInterval(1500)
        self.autosave_status_timer.timeout.connect(self._clear_autosave_status)
        # Relative paths of files the user checked directly (not via a parent
        # folder check) for the memo currently loaded in the dialog.
        self.directly_checked_file_paths: set[str] = set()
        self._folder_items_by_relative_path: dict[str, QTreeWidgetItem] = {}
        # Folders whose direct file listing has already been scanned from disk,
        # so re-expanding/searching doesn't re-hit the filesystem.
        self._files_loaded_folder_paths: set[str] = set()

        self.memo_list = QListWidget()
        self.memo_list.setObjectName("memoList")
        self.memo_list.setFixedHeight(MEMO_LIST_HEIGHT)
        self.add_button = QPushButton("인계 내용 추가")
        _set_button_role(self.add_button, "secondary")
        self.delete_button = QPushButton("삭제")
        _set_button_role(self.delete_button, "secondary")
        self.save_button = QPushButton("저장")
        _set_button_role(self.save_button, "secondary")
        self.complete_button = QPushButton("인수인계서 저장")
        _set_button_role(self.complete_button, "primary")
        for action_button in (
            self.add_button,
            self.save_button,
            self.delete_button,
            self.complete_button,
        ):
            action_button.setFixedHeight(MEMO_ACTION_BUTTON_HEIGHT)
        self.workflow_progress = MemoWorkflowProgressBar(self)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #64748B; font-size: 11px;")
        self.title_input = QLineEdit()
        self.title_input.setObjectName("memoTitleInput")
        self.title_input.setFixedHeight(MEMO_TITLE_HEIGHT)
        self.folder_search_label = self._create_section_label("🔍 검색")
        self.folder_search_input = QLineEdit()
        self.folder_search_input.setObjectName("memoSearchInput")
        self.folder_search_input.setPlaceholderText("폴더명 검색")
        self.content_input = QPlainTextEdit()
        self.content_input.setObjectName("memoContentInput")
        self.content_input.setFont(QFont("맑은 고딕"))
        self.content_input.setPlaceholderText(
            "업무에 대해 알아야 할 내용 작성 ▶ 관련 폴더/메일/메신저 선택"
        )
        self.content_input.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.content_input.setWordWrapMode(
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        )
        self.content_input.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content_input.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.content_input.setMinimumHeight(MEMO_CONTENT_MIN_HEIGHT)
        self.folder_tree_widget = QTreeWidget()
        self.folder_tree_widget.setHeaderLabel("관련 폴더")
        self.folder_tree_widget.setFrameShape(QFrame.Shape.NoFrame)
        tree_font = self.folder_tree_widget.font()
        tree_font.setPixelSize(13)
        self.folder_tree_widget.setFont(tree_font)

        # Border/rounded corners live on this wrapper, not on the QTreeWidget itself,
        # so its internal QScrollBar keeps the native Qt look instead of the broken
        # arrows-only rendering that occurs when a stylesheet is applied directly to
        # a QAbstractScrollArea-based widget.
        self.folder_tree_frame = QFrame()
        self.folder_tree_frame.setStyleSheet(
            """
            QFrame {
                border: 1px solid #E0E0E0;
                border-radius: 6px;
            }
            """
        )
        folder_tree_frame_layout = QVBoxLayout(self.folder_tree_frame)
        folder_tree_frame_layout.setContentsMargins(0, 0, 0, 0)
        folder_tree_frame_layout.addWidget(self.folder_tree_widget)

        self.email_search_label = self._create_section_label("🔍 검색")
        self.email_search_input = QLineEdit()
        self.email_search_input.setObjectName("memoSearchInput")
        self.email_search_input.setPlaceholderText("발신자/제목 검색")
        self.email_tree_widget = QTreeWidget()
        self.email_tree_widget.setHeaderHidden(True)
        self.email_tree_widget.setFrameShape(QFrame.Shape.NoFrame)
        email_tree_font = self.email_tree_widget.font()
        email_tree_font.setPixelSize(13)
        self.email_tree_widget.setFont(email_tree_font)
        self.email_tree_frame = self._create_tree_frame(self.email_tree_widget)
        self.email_empty_label = QLabel("업로드된 메일 파일이 없습니다")
        self.email_empty_label.setObjectName("memoEmptyLabel")
        self.email_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.email_empty_label.setWordWrap(True)

        self.kakao_search_label = self._create_section_label("🔍 검색")
        self.kakao_search_input = QLineEdit()
        self.kakao_search_input.setObjectName("memoSearchInput")
        self.kakao_search_input.setPlaceholderText("파일명 검색")
        self.kakao_tree_widget = QTreeWidget()
        self.kakao_tree_widget.setHeaderHidden(True)
        self.kakao_tree_widget.setFrameShape(QFrame.Shape.NoFrame)
        kakao_tree_font = self.kakao_tree_widget.font()
        kakao_tree_font.setPixelSize(13)
        self.kakao_tree_widget.setFont(kakao_tree_font)
        self.kakao_tree_frame = self._create_tree_frame(self.kakao_tree_widget)
        self.kakao_empty_label = QLabel("업로드된 카톡 파일이 없습니다")
        self.kakao_empty_label.setObjectName("memoEmptyLabel")
        self.kakao_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.kakao_empty_label.setWordWrap(True)

        self._build_layout()
        self._connect_signals()
        self._populate_folder_tree()
        self._populate_email_tree()
        self._populate_kakao_tree()
        if initial_linked_folders:
            self.memos.append(
                WorkMemo(title="", content="", linked_folders=initial_linked_folders)
            )
        self._refresh_memo_list()
        if initial_linked_folders:
            self.memo_list.setCurrentRow(len(self.memos) - 1)

    def _create_section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("memoSectionLabel")
        font = label.font()
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110)
        label.setFont(font)
        return label

    def _create_emphasis_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("memoEmphasisLabel")
        return label

    def _build_layout(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(
            MEMO_DIALOG_MARGIN,
            MEMO_DIALOG_MARGIN,
            MEMO_DIALOG_MARGIN,
            MEMO_DIALOG_MARGIN,
        )
        main_layout.setSpacing(0)

        button_bar = QHBoxLayout()
        button_bar.setSpacing(MEMO_BUTTON_SPACING)
        button_bar.addWidget(self.add_button)
        button_bar.addWidget(self.save_button)
        button_bar.addWidget(self.delete_button)
        button_bar.addWidget(self.complete_button)
        button_bar.addWidget(self.status_label)
        button_bar.addStretch()

        left_layout = QVBoxLayout()
        left_layout.setSpacing(0)
        left_layout.addWidget(self._create_emphasis_label("인계 내용 목록"))
        left_layout.addSpacing(MEMO_SECTION_SPACING)
        left_layout.addWidget(self.memo_list)
        left_layout.addSpacing(MEMO_TITLE_LABEL_TOP_MARGIN)
        left_layout.addWidget(self._create_emphasis_label("인계 내용 제목"))
        left_layout.addSpacing(MEMO_SECTION_SPACING)
        left_layout.addWidget(self.title_input)
        left_layout.addSpacing(MEMO_SECTION_SPACING)
        left_layout.addWidget(self._create_emphasis_label("인계 내용 상세"))
        left_layout.addSpacing(MEMO_SECTION_SPACING)
        left_layout.addWidget(self.content_input)

        self.related_target_tabs = QTabWidget()
        self.related_target_tabs.setObjectName("memoRelatedTabs")

        folder_tab = QWidget()
        folder_tab_layout = QVBoxLayout(folder_tab)
        folder_tab_layout.setSpacing(MEMO_SECTION_SPACING)
        folder_tab_layout.addWidget(self._create_emphasis_label("관련 폴더 선택"))
        folder_tab_layout.addWidget(self.folder_search_label)
        folder_tab_layout.addWidget(self.folder_search_input)
        folder_tab_layout.addWidget(self.folder_tree_frame)

        email_tab = QWidget()
        email_tab_layout = QVBoxLayout(email_tab)
        email_tab_layout.setSpacing(MEMO_SECTION_SPACING)
        email_tab_layout.addWidget(self.email_search_label)
        email_tab_layout.addWidget(self.email_search_input)
        email_tab_layout.addWidget(self.email_tree_frame)
        email_tab_layout.addWidget(self.email_empty_label)

        kakao_tab = QWidget()
        kakao_tab_layout = QVBoxLayout(kakao_tab)
        kakao_tab_layout.setSpacing(MEMO_SECTION_SPACING)
        kakao_tab_layout.addWidget(self.kakao_search_label)
        kakao_tab_layout.addWidget(self.kakao_search_input)
        kakao_tab_layout.addWidget(self.kakao_tree_frame)
        kakao_tab_layout.addWidget(self.kakao_empty_label)

        self._folder_tab_index = self.related_target_tabs.addTab(folder_tab, "폴더")
        self._email_tab_index = self.related_target_tabs.addTab(email_tab, "메일")
        self._kakao_tab_index = self.related_target_tabs.addTab(kakao_tab, "메신저(카톡)")

        ai_mode_active = self.analysismode == "ai"
        restricted_mode_message = (
            "기본모드에서는 이메일·메신저 자료를 선택할 수 없습니다. "
            "AI 모드에서 이용해 주세요."
        )
        for tab_index in (self._email_tab_index, self._kakao_tab_index):
            self.related_target_tabs.setTabEnabled(tab_index, ai_mode_active)
            if not ai_mode_active:
                self.related_target_tabs.tabBar().setTabToolTip(
                    tab_index,
                    restricted_mode_message,
                )

        content_layout = QHBoxLayout()
        content_layout.setSpacing(MEMO_CONTENT_SPACING)
        content_layout.addLayout(left_layout, 40)
        content_layout.addWidget(self.related_target_tabs, 30)

        main_layout.addSpacing(MEMO_PROGRESS_BAR_MARGIN)
        main_layout.addWidget(self.workflow_progress)
        main_layout.addSpacing(MEMO_PROGRESS_BAR_MARGIN)
        main_layout.addLayout(button_bar)
        main_layout.addSpacing(MEMO_BUTTON_BAR_BOTTOM_MARGIN)
        main_layout.addLayout(content_layout)

    def _connect_signals(self) -> None:
        self.add_button.clicked.connect(self._add_memo)
        self.delete_button.clicked.connect(self._delete_memo)
        self.complete_button.clicked.connect(self._save_word_and_close)
        self.memo_list.currentRowChanged.connect(self._select_memo)
        self.save_button.clicked.connect(lambda: self._save_current_memo())
        self.title_input.textChanged.connect(self._mark_dirty)
        self.content_input.textChanged.connect(self._mark_dirty)
        self.folder_search_input.textChanged.connect(self._filter_folder_tree)
        self.folder_tree_widget.itemChanged.connect(self._handle_tree_item_changed)
        self.folder_tree_widget.itemChanged.connect(self._refresh_item_check_symbol)
        self.folder_tree_widget.itemClicked.connect(self._handle_tree_item_clicked)
        self.folder_tree_widget.itemExpanded.connect(self._handle_tree_item_expanded)
        self.email_search_input.textChanged.connect(
            lambda text: self._filter_simple_tree(self.email_tree_widget, text)
        )
        self.email_tree_widget.itemChanged.connect(self._refresh_simple_check_symbol)
        self.email_tree_widget.itemChanged.connect(self._mark_dirty)
        self.email_tree_widget.itemClicked.connect(self._handle_simple_tree_item_clicked)
        self.kakao_search_input.textChanged.connect(
            lambda text: self._filter_simple_tree(self.kakao_tree_widget, text)
        )
        self.kakao_tree_widget.itemChanged.connect(self._refresh_simple_check_symbol)
        self.kakao_tree_widget.itemChanged.connect(self._mark_dirty)
        self.kakao_tree_widget.itemClicked.connect(self._handle_simple_tree_item_clicked)
        self.title_input.installEventFilter(self)
        self.content_input.installEventFilter(self)

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and watched in (self.title_input, self.content_input)
            and self.memo_list.currentRow() < 0
        ):
            self._add_memo()
            watched.setFocus()
            return True
        return super().eventFilter(watched, event)

    def _populate_folder_tree(self) -> None:
        self._folder_items_by_relative_path.clear()
        self._files_loaded_folder_paths.clear()
        self.folder_tree_widget.clear()
        for node in self.folder_tree:
            self.folder_tree_widget.addTopLevelItem(
                self._create_folder_tree_item(node)
            )
        self.folder_tree_widget.collapseAll()

    def _filter_folder_tree(self, search_text: str) -> None:
        keyword = search_text.strip().casefold()
        if keyword:
            self._ensure_all_folders_files_loaded()
        for index in range(self.folder_tree_widget.topLevelItemCount()):
            self._filter_tree_item(
                self.folder_tree_widget.topLevelItem(index),
                keyword,
            )

        if keyword:
            self.folder_tree_widget.expandAll()
        else:
            self.folder_tree_widget.collapseAll()
            self._expand_checked_paths()

    def _filter_tree_item(self, item: QTreeWidgetItem, keyword: str) -> bool:
        if not keyword:
            item.setHidden(False)
            for index in range(item.childCount()):
                self._filter_tree_item(item.child(index), keyword)
            return True

        name_matches = keyword in item.text(0).casefold()
        has_visible_child = False
        for index in range(item.childCount()):
            if self._filter_tree_item(item.child(index), keyword):
                has_visible_child = True

        is_visible = name_matches or has_visible_child
        item.setHidden(not is_visible)
        return is_visible

    def _create_tree_frame(self, tree_widget: QTreeWidget) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            """
            QFrame {
                border: 1px solid #E0E0E0;
                border-radius: 6px;
            }
            """
        )
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.addWidget(tree_widget)
        return frame

    def _populate_email_tree(self) -> None:
        self.email_tree_widget.clear()
        for email in self.parsed_emails:
            item = self._create_simple_checkable_item(
                email.get("source_file", ""),
                self._format_email_display_text(email),
            )
            item.setToolTip(0, str(email.get("subject") or "(제목 없음)"))
            self.email_tree_widget.addTopLevelItem(item)

        has_emails = bool(self.parsed_emails)
        self.email_tree_frame.setVisible(has_emails)
        self.email_empty_label.setVisible(not has_emails)

    def _format_email_display_text(self, email: dict) -> str:
        date = str(email.get("date") or "")[:10]
        subject = str(email.get("subject") or "(제목 없음)")
        abbreviated_subject = f"{subject[:20]}..." if len(subject) > 20 else subject
        return f"{abbreviated_subject} · {date}" if date else abbreviated_subject

    def _populate_kakao_tree(self) -> None:
        self.kakao_tree_widget.clear()
        for file_path in self.kakao_file_paths:
            file_name = os.path.basename(file_path)
            item = self._create_simple_checkable_item(
                file_path,
                self._format_kakao_display_text(file_name),
            )
            item.setToolTip(0, file_name)
            self.kakao_tree_widget.addTopLevelItem(item)

        has_kakao_files = bool(self.kakao_file_paths)
        self.kakao_tree_frame.setVisible(has_kakao_files)
        self.kakao_empty_label.setVisible(not has_kakao_files)

    def _format_kakao_display_text(self, file_name: str) -> str:
        match = re.fullmatch(
            r"KakaoTalk_(\d{4})(\d{2})(\d{2})_.*_([^_]+)\.txt",
            file_name,
            flags=re.IGNORECASE,
        )
        if not match:
            return file_name
        year, month, day, room_name = match.groups()
        return f"{room_name} · {year}-{month}-{day}"

    def _create_simple_checkable_item(
        self,
        identifier: str,
        display_text: str,
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(0, Qt.ItemDataRole.UserRole, identifier)
        item.setData(0, Qt.ItemDataRole.UserRole + 1, display_text)
        _set_check_state(item, Qt.CheckState.Unchecked)
        # Item isn't attached to a tree yet, so set the initial text directly
        # instead of going through _refresh_simple_check_symbol (which needs
        # item.treeWidget() to block/unblock signals on the right widget).
        item.setText(0, f"{_CHECK_SYMBOLS[Qt.CheckState.Unchecked]} {display_text}")
        return item

    def _refresh_simple_check_symbol(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return

        display_text = item.data(0, Qt.ItemDataRole.UserRole + 1)
        symbol = _CHECK_SYMBOLS.get(_get_check_state(item), "☐")
        new_text = f"{symbol} {display_text}"
        if item.text(0) == new_text:
            return

        tree_widget = item.treeWidget()
        tree_widget.blockSignals(True)
        item.setText(0, new_text)
        tree_widget.blockSignals(False)

    def _handle_simple_tree_item_clicked(
        self,
        item: QTreeWidgetItem,
        column: int,
    ) -> None:
        if not self._can_select_related_target():
            return

        new_state = (
            Qt.CheckState.Unchecked
            if _get_check_state(item) == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        _set_check_state(item, new_state)

    def _can_select_related_target(self) -> bool:
        has_current_memo = (
            bool(self.memos)
            and 0 <= self.current_memo_index < len(self.memos)
        )
        title = self.title_input.text().strip()
        content = self.content_input.toPlainText().strip()
        if has_current_memo and (title or content):
            return True

        QMessageBox.warning(
            self,
            "인계 내용 작성 필요",
            (
                "먼저 인계 내용을 작성해주세요.\n"
                "[인계 내용 추가] 버튼으로 인계 내용을 만들고 제목/내용을 입력한 후,\n"
                "폴더/메일/메신저(카톡)를 선택할 수 있습니다."
            ),
        )
        return False

    def _filter_simple_tree(self, tree_widget: QTreeWidget, search_text: str) -> None:
        keyword = search_text.strip().casefold()
        for index in range(tree_widget.topLevelItemCount()):
            item = tree_widget.topLevelItem(index)
            item.setHidden(bool(keyword) and keyword not in item.text(0).casefold())

    def _get_checked_simple_tree_identifiers(
        self,
        tree_widget: QTreeWidget,
    ) -> list[str]:
        return [
            tree_widget.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
            for index in range(tree_widget.topLevelItemCount())
            if _get_check_state(tree_widget.topLevelItem(index))
            == Qt.CheckState.Checked
        ]

    def _set_checked_simple_tree_identifiers(
        self,
        tree_widget: QTreeWidget,
        identifiers: list[str],
    ) -> None:
        checked = set(identifiers)
        for index in range(tree_widget.topLevelItemCount()):
            item = tree_widget.topLevelItem(index)
            state = (
                Qt.CheckState.Checked
                if item.data(0, Qt.ItemDataRole.UserRole) in checked
                else Qt.CheckState.Unchecked
            )
            _set_check_state(item, state)

    def _create_folder_tree_item(self, node: FolderTreeNode) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(0, Qt.ItemDataRole.UserRole, node.relative_path)
        item.setData(0, Qt.ItemDataRole.UserRole + 1, node.name)
        item.setData(0, _ITEM_TYPE_ROLE, _ITEM_TYPE_FOLDER)
        # Force the expand arrow even though direct-child files aren't loaded
        # yet, so folders with files-only (no subfolders) remain expandable.
        item.setChildIndicatorPolicy(
            QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
        )
        _set_check_state(item, Qt.CheckState.Unchecked)
        self._refresh_item_check_symbol(item, 0)
        self._folder_items_by_relative_path[node.relative_path] = item

        for child in node.children:
            item.addChild(self._create_folder_tree_item(child))

        return item

    def _create_file_tree_item(
        self,
        file_name: str,
        relative_path: str,
        initial_state: Qt.CheckState,
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(0, Qt.ItemDataRole.UserRole, relative_path)
        item.setData(0, Qt.ItemDataRole.UserRole + 1, file_name)
        item.setData(0, _ITEM_TYPE_ROLE, _ITEM_TYPE_FILE)
        _set_check_state(item, initial_state)
        self._refresh_item_check_symbol(item, 0)
        return item

    def _get_absolute_path(self, relative_path: str) -> str:
        if not self.root_folder_path:
            return relative_path
        return os.path.join(self.root_folder_path, *relative_path.split("/"))

    def _handle_tree_item_expanded(self, item: QTreeWidgetItem) -> None:
        if not _is_file_item(item):
            self._ensure_folder_files_loaded(item)

    def _ensure_all_folders_files_loaded(self) -> None:
        for index in range(self.folder_tree_widget.topLevelItemCount()):
            self._ensure_all_folders_files_loaded_in_item(
                self.folder_tree_widget.topLevelItem(index)
            )

    def _ensure_all_folders_files_loaded_in_item(self, item: QTreeWidgetItem) -> None:
        if not _is_file_item(item):
            self._ensure_folder_files_loaded(item)
        for index in range(item.childCount()):
            self._ensure_all_folders_files_loaded_in_item(item.child(index))

    def _ensure_folder_files_loaded(self, folder_item: QTreeWidgetItem) -> None:
        relative_path = folder_item.data(0, Qt.ItemDataRole.UserRole)
        if relative_path in self._files_loaded_folder_paths:
            return
        self._files_loaded_folder_paths.add(relative_path)

        absolute_path = self._get_absolute_path(relative_path)
        try:
            with os.scandir(absolute_path) as entries:
                file_names = sorted(
                    entry.name
                    for entry in entries
                    if entry.is_file(follow_symlinks=False)
                )
        except OSError:
            file_names = []

        if file_names:
            parent_state = _get_check_state(folder_item)
            initial_state = (
                Qt.CheckState.Checked
                if parent_state == Qt.CheckState.Checked
                else Qt.CheckState.Unchecked
            )
            for file_name in file_names:
                file_relative_path = f"{relative_path}/{file_name}"
                folder_item.addChild(
                    self._create_file_tree_item(
                        file_name,
                        file_relative_path,
                        initial_state,
                    )
                )

        if folder_item.childCount() == 0:
            folder_item.setChildIndicatorPolicy(
                QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless
            )

    def _refresh_item_check_symbol(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return

        name = item.data(0, Qt.ItemDataRole.UserRole + 1)
        symbol = _CHECK_SYMBOLS.get(_get_check_state(item), "☐")
        new_text = f"{symbol} 📄 {name}" if _is_file_item(item) else f"{symbol} {name}"
        if item.text(0) == new_text:
            return

        self.folder_tree_widget.blockSignals(True)
        item.setText(0, new_text)
        self.folder_tree_widget.blockSignals(False)

    def _handle_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if self.is_syncing_tree:
            return
        if not self._can_select_related_target():
            return

        new_state = (
            Qt.CheckState.Unchecked
            if _get_check_state(item) == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        _set_check_state(item, new_state)

        if _is_file_item(item):
            relative_path = item.data(0, Qt.ItemDataRole.UserRole)
            if new_state == Qt.CheckState.Checked:
                self.directly_checked_file_paths.add(relative_path)
            else:
                self.directly_checked_file_paths.discard(relative_path)

    def _refresh_memo_list(self) -> None:
        self.memo_list.blockSignals(True)
        self.memo_list.clear()
        if not self.memos:
            self._add_memo_list_placeholder()
        else:
            last_row = len(self.memos) - 1
            for row, memo in enumerate(self.memos):
                self._add_memo_list_item(
                    memo, row, is_first=row == 0, is_last=row == last_row
                )
        self.memo_list.blockSignals(False)

        if self.memos:
            self.memo_list.setCurrentRow(0)
        else:
            self._load_memo(-1)

    def _add_memo_list_placeholder(self) -> None:
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)

        label = QLabel("작성된 인계 내용이 없습니다.\n[인계 내용 추가] 버튼으로 시작하세요")
        label.setObjectName("memoListPlaceholder")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)

        item.setSizeHint(QSize(label.sizeHint().width(), 130))
        self.memo_list.addItem(item)
        self.memo_list.setItemWidget(item, label)

    def _refresh_memo_list_item_widget(self, row: int) -> None:
        item = self.memo_list.item(row)
        if item is None or not (0 <= row < len(self.memos)):
            return
        widget = self._build_memo_list_item_widget(
            self.memos[row],
            row,
            is_first=row == 0,
            is_last=row == len(self.memos) - 1,
        )
        self.memo_list.setItemWidget(item, widget)

    def _add_memo_list_item(
        self,
        memo: WorkMemo,
        row: int,
        *,
        is_first: bool,
        is_last: bool,
    ) -> None:
        item = QListWidgetItem()
        widget = self._build_memo_list_item_widget(
            memo, row, is_first=is_first, is_last=is_last
        )
        item.setSizeHint(QSize(widget.sizeHint().width(), 44))
        self.memo_list.addItem(item)
        self.memo_list.setItemWidget(item, widget)

    def _build_memo_list_item_widget(
        self,
        memo: WorkMemo,
        row: int,
        *,
        is_first: bool,
        is_last: bool,
    ) -> QWidget:
        title = memo.title or "제목 없음"
        updated_at = getattr(memo, "updatedat", "")
        created_at = getattr(memo, "createdat", "")
        if updated_at:
            timestamp_text = f"수정: {updated_at}"
        elif created_at:
            timestamp_text = f"생성: {created_at}"
        else:
            timestamp_text = ""

        html = (
            f'<span style="font-size:14px; font-weight:bold; color:#1A1A1A;">'
            f"{html_escape(title)}</span>"
        )
        if timestamp_text:
            html += (
                '<br><span style="font-size:11px; color:#999999;">'
                f"{html_escape(timestamp_text)}</span>"
            )

        label = QLabel(html)
        label.setObjectName("memoListItemLabel")
        label.setTextFormat(Qt.TextFormat.RichText)

        up_button = QPushButton("▲")
        up_button.setObjectName("memoRowMoveButton")
        up_button.setFixedSize(22, 22)
        up_button.setEnabled(not is_first)
        up_button.setCursor(Qt.CursorShape.PointingHandCursor)
        up_button.setToolTip("위로 이동")
        up_button.clicked.connect(lambda: self._move_memo_at_row(row, -1))

        down_button = QPushButton("▼")
        down_button.setObjectName("memoRowMoveButton")
        down_button.setFixedSize(22, 22)
        down_button.setEnabled(not is_last)
        down_button.setCursor(Qt.CursorShape.PointingHandCursor)
        down_button.setToolTip("아래로 이동")
        down_button.clicked.connect(lambda: self._move_memo_at_row(row, 1))

        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(8, 0, 6, 0)
        row_layout.setSpacing(4)
        row_layout.addWidget(label, 1)
        row_layout.addWidget(up_button)
        row_layout.addWidget(down_button)
        return row_widget

    def _add_memo(self) -> None:
        if not self._confirm_unsaved_changes():
            return

        new_memo = WorkMemo(title="", content="", linked_folders=[])
        self.memos.append(new_memo)

        # A full rebuild (rather than appending just the new row) is needed
        # here because the previous last row's ▼ button must switch from
        # disabled to enabled now that it's no longer the last memo.
        self._refresh_memo_list()
        self.memo_list.setCurrentRow(len(self.memos) - 1)

    def _delete_memo(self) -> None:
        row = self.memo_list.currentRow()
        if row < 0:
            return

        del self.memos[row]
        self.has_unsaved_changes = False
        self._refresh_memo_list()

    def _move_memo_at_row(self, row: int, offset: int) -> None:
        if row < 0 or row >= len(self.memos):
            return

        new_row = row + offset
        if new_row < 0 or new_row >= len(self.memos):
            return

        # Track the memo actually being edited (if any) by index, not by
        # object equality, so the editor stays bound to the right memo even
        # if the swap involves it and another memo happens to have identical
        # field values.
        if self.current_memo_index == row:
            target_row = new_row
        elif self.current_memo_index == new_row:
            target_row = row
        else:
            target_row = self.current_memo_index

        self.memos[row], self.memos[new_row] = self.memos[new_row], self.memos[row]
        self._refresh_memo_list()
        self.memo_list.setCurrentRow(target_row)

    def _select_memo(self, row: int) -> None:
        if self.is_reverting_selection:
            return

        previous_index = self.current_memo_index
        if not self._confirm_unsaved_changes():
            self.is_reverting_selection = True
            self.memo_list.setCurrentRow(previous_index)
            self.is_reverting_selection = False
            return

        self._load_memo(row)

    def _load_memo(self, row: int) -> None:
        self.autosave_timer.stop()
        self.current_memo_index = row

        self.is_loading = True
        if row < 0 or row >= len(self.memos):
            self.title_input.clear()
            self.content_input.clear()
            self._set_checked_relative_paths([], [])
            self._set_checked_simple_tree_identifiers(self.email_tree_widget, [])
            self._set_checked_simple_tree_identifiers(self.kakao_tree_widget, [])
            self.title_input.setEnabled(False)
            self.content_input.setEnabled(False)
            self.save_button.setEnabled(False)
        else:
            memo = self.memos[row]
            self.title_input.setEnabled(True)
            self.content_input.setEnabled(True)
            self.save_button.setEnabled(True)
            self.folder_tree_widget.setEnabled(True)
            self.title_input.setText(memo.title)
            self.content_input.setPlainText(memo.content)
            self._set_checked_relative_paths(memo.linked_folders, memo.linked_files)
            self._set_checked_simple_tree_identifiers(
                self.email_tree_widget, memo.linked_emails
            )
            self._set_checked_simple_tree_identifiers(
                self.kakao_tree_widget, memo.linked_kakao_files
            )
        self.is_loading = False
        self.has_unsaved_changes = False
        self.status_label.clear()

    def _save_current_memo(self, show_success_message: bool = True) -> bool:
        self.autosave_timer.stop()
        if (
            self.is_loading
            or self.current_memo_index < 0
            or self.current_memo_index >= len(self.memos)
        ):
            return True

        title = self.title_input.text().strip()
        if not title:
            QMessageBox.warning(self, "인계 내용 저장", "제목을 입력하세요.")
            return False

        memo = self.memos[self.current_memo_index]
        memo.title = title
        memo.content = self.content_input.toPlainText()
        memo.linked_folders = self._get_checked_relative_paths()
        memo.linked_files = sorted(self.directly_checked_file_paths)
        memo.linked_emails = self._get_checked_simple_tree_identifiers(
            self.email_tree_widget
        )
        memo.linked_kakao_files = self._get_checked_simple_tree_identifiers(
            self.kakao_tree_widget
        )
        self._refresh_memo_list_item_widget(self.current_memo_index)
        self.has_unsaved_changes = False
        print(
            f"[DBG1 save_memo] idx={self.current_memo_index} title={memo.title!r}"
            f" linked_emails={memo.linked_emails}"
            f" linked_kakao_files={memo.linked_kakao_files}"
        )
        self.status_label.clear()
        if show_success_message:
            QMessageBox.information(self, "인계 내용 저장", "인계 내용이 저장되었습니다.")
        return True

    def _autosave_current_memo(self) -> None:
        if (
            self.is_loading
            or not self.has_unsaved_changes
            or self.current_memo_index < 0
            or self.current_memo_index >= len(self.memos)
        ):
            return

        memo = self.memos[self.current_memo_index]
        memo.title = self.title_input.text().strip()
        memo.content = self.content_input.toPlainText()
        memo.linked_folders = self._get_checked_relative_paths()
        memo.linked_files = sorted(self.directly_checked_file_paths)
        memo.linked_emails = self._get_checked_simple_tree_identifiers(
            self.email_tree_widget
        )
        memo.linked_kakao_files = self._get_checked_simple_tree_identifiers(
            self.kakao_tree_widget
        )
        self._refresh_memo_list_item_widget(self.current_memo_index)
        self.has_unsaved_changes = False
        self.status_label.setText("임시 저장됨")
        self.autosave_status_timer.start()

    def _clear_autosave_status(self) -> None:
        if self.status_label.text() == "임시 저장됨":
            self.status_label.clear()

    def _save_word_and_close(self) -> None:
        # [알려주세요]와 [인수인계서 저장] 통합: 메모/연결자료 조건만 먼저 확인하고
        # (아직 한 번도 답변을 안 한 최초 실행도 통과해야 팝업이 뜬다), 통과하면
        # 알려주세요 팝업을 띄운다. 팝업에서 실제로 [저장하고 인수인계서 만들기]를
        # 눌러야만(=should_proceed_to_save) 답변 조건까지 포함한 최종 검사를 거쳐
        # 워드 문서를 생성한다. 팝업을 취소/닫기만 하면 답변은 보존된 채(자동저장)
        # 워드 생성 없이 메모 화면으로 돌아간다.
        if not self._save_current_memo(show_success_message=False):
            return
        if not self._validate_completion():
            return
        if not self._validate_linked_requirement():
            return

        qa_dialog = HandoverQADialog(handover_qa=self.handover_qa, parent=self)
        qa_dialog.exec()
        if not qa_dialog.should_proceed_to_save:
            return

        self._finalize_word_save()

    def _validate_linked_requirement(self) -> bool:
        if any(
            memo.linked_folders
            or memo.linked_files
            or memo.linked_emails
            or memo.linked_kakao_files
            for memo in self.memos
        ):
            return True
        QMessageBox.warning(
            self,
            "인수인계서 저장",
            "관련 폴더/이메일/메신저를 1개 이상 연결해주세요.",
        )
        return False

    def _finalize_word_save(self) -> None:
        if not self._validate_save_requirements():
            return
        if self.on_save_word is not None:
            self._set_all_dialog_buttons_enabled(False)
            success = self.on_save_word()
            if success is not None:
                self._set_all_dialog_buttons_enabled(True)
            if success:
                self.accept()
        else:
            self.accept()

    def _validate_save_requirements(self) -> bool:
        missing: list[str] = []
        if not self.memos:
            missing.append("인계 내용 1개 이상 작성해주세요.")
        if not any(
            memo.linked_folders
            or memo.linked_files
            or memo.linked_emails
            or memo.linked_kakao_files
            for memo in self.memos
        ):
            missing.append("관련 폴더/이메일/메신저를 1개 이상 연결해주세요.")
        answers = (self.handover_qa.answers + ["", "", "", "", ""])[:5]
        if not any(answer.strip() for answer in answers):
            missing.append("알려주세요에서 1개 이상 답변해 주세요.")
        if not missing:
            return True
        QMessageBox.warning(
            self,
            "인수인계서 저장",
            "인수인계서 저장에 필요한 항목을 확인해주세요.\n\n"
            + "\n".join(f"• {message}" for message in missing),
        )
        return False

    def _set_all_dialog_buttons_enabled(self, enabled: bool) -> None:
        for button in (
            self.add_button,
            self.save_button,
            self.delete_button,
            self.complete_button,
        ):
            button.setEnabled(enabled)
        # Disabling the list itself also disables the per-row ▲/▼ buttons
        # embedded in it, so reordering can't happen during a busy state.
        self.memo_list.setEnabled(enabled)

    def _complete_dialog(self) -> None:
        if self.has_unsaved_changes and not self._save_current_memo(
            show_success_message=False
        ):
            return

        if not self._validate_completion():
            return

        QMessageBox.information(self, "인계 내용 작성", "작성이 완료되었습니다.")
        self.accept()

    def _validate_completion(self) -> bool:
        if not self.memos:
            QMessageBox.warning(
                self,
                "인계 내용 작성",
                "최소 1개 이상의 인계 내용을 작성해야 합니다.",
            )
            return False

        incomplete_memos = [
            memo
            for memo in self.memos
            if not memo.title.strip() or not memo.content.strip()
        ]
        if incomplete_memos:
            QMessageBox.warning(
                self,
                "인계 내용 작성",
                "제목 또는 내용이 비어 있는 인계 내용이 있습니다. 확인해주세요.",
            )
            return False

        return True

    def _mark_dirty(self) -> None:
        if self.is_loading:
            return
        self.has_unsaved_changes = True
        self.status_label.clear()
        if 0 <= self.current_memo_index < len(self.memos):
            self.autosave_timer.start()

    def _confirm_unsaved_changes(self) -> bool:
        if not self.has_unsaved_changes:
            return True
        return self._save_current_memo(show_success_message=False)

    def _set_checked_relative_paths(
        self,
        relative_paths: list[str],
        checked_file_paths: list[str] | None = None,
    ) -> None:
        checked_paths = set(relative_paths)
        self.is_syncing_tree = True
        changed_items: list[QTreeWidgetItem] = []
        for index in range(self.folder_tree_widget.topLevelItemCount()):
            self._set_item_checked_paths(
                self.folder_tree_widget.topLevelItem(index),
                checked_paths,
                changed_items,
            )

        self.directly_checked_file_paths = set(checked_file_paths or [])
        for file_relative_path in self.directly_checked_file_paths:
            file_item = self._check_file_by_relative_path(file_relative_path)
            if file_item is not None:
                changed_items.append(file_item)

        # Only the ancestor chains of items that actually changed need their
        # checked/partial state recomputed, not the whole tree.
        for item in changed_items:
            self._update_parent_check_states(item.parent())
        self.is_syncing_tree = False

    def _check_file_by_relative_path(
        self,
        file_relative_path: str,
    ) -> QTreeWidgetItem | None:
        folder_relative_path = _get_parent_folder_relative_path(file_relative_path)
        folder_item = self._folder_items_by_relative_path.get(folder_relative_path)
        if folder_item is None:
            return None

        self._ensure_folder_files_loaded(folder_item)
        self._expand_ancestors(folder_item)
        folder_item.setExpanded(True)

        for index in range(folder_item.childCount()):
            file_item = folder_item.child(index)
            if file_item.data(0, Qt.ItemDataRole.UserRole) == file_relative_path:
                _set_check_state(file_item, Qt.CheckState.Checked)
                return file_item

        return None

    def _set_item_checked_paths(
        self,
        item: QTreeWidgetItem,
        checked_paths: set[str],
        changed_items: list[QTreeWidgetItem],
    ) -> None:
        relative_path = item.data(0, Qt.ItemDataRole.UserRole)
        if relative_path in checked_paths:
            _set_check_state(item, Qt.CheckState.Checked)
            self._set_child_check_states(item, Qt.CheckState.Checked)
            self._expand_ancestors(item)
            item.setExpanded(True)  # show the selected folder's contents, not just its parent
            changed_items.append(item)
            return

        _set_check_state(item, Qt.CheckState.Unchecked)
        for index in range(item.childCount()):
            self._set_item_checked_paths(
                item.child(index), checked_paths, changed_items
            )

    def _expand_ancestors(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()

    def _expand_checked_paths(self) -> None:
        for index in range(self.folder_tree_widget.topLevelItemCount()):
            self._expand_checked_paths_in_item(
                self.folder_tree_widget.topLevelItem(index)
            )

    def _expand_checked_paths_in_item(self, item: QTreeWidgetItem) -> None:
        if _get_check_state(item) != Qt.CheckState.Unchecked:
            self._expand_ancestors(item)
        for index in range(item.childCount()):
            self._expand_checked_paths_in_item(item.child(index))

    def _get_checked_relative_paths(self) -> list[str]:
        checked_paths: list[str] = []
        for index in range(self.folder_tree_widget.topLevelItemCount()):
            self._collect_checked_paths(
                self.folder_tree_widget.topLevelItem(index),
                checked_paths,
            )
        return checked_paths

    def _collect_checked_paths(
        self,
        item: QTreeWidgetItem,
        checked_paths: list[str],
    ) -> None:
        if not _is_file_item(item) and _get_check_state(item) == Qt.CheckState.Checked:
            checked_paths.append(item.data(0, Qt.ItemDataRole.UserRole))
            return  # children are cascade-checked but belong to this group, not separate ones
        for index in range(item.childCount()):
            self._collect_checked_paths(item.child(index), checked_paths)

    def _handle_tree_item_changed(
        self,
        item: QTreeWidgetItem,
        column: int,
    ) -> None:
        if column != 0 or self.is_syncing_tree:
            return

        self.is_syncing_tree = True
        self._set_child_check_states(item, _get_check_state(item))
        self._update_parent_check_states(item.parent())
        self.is_syncing_tree = False
        self._mark_dirty()

    def _set_child_check_states(
        self,
        item: QTreeWidgetItem,
        state: Qt.CheckState,
    ) -> None:
        if state == Qt.CheckState.PartiallyChecked:
            return

        for index in range(item.childCount()):
            child = item.child(index)
            _set_check_state(child, state)
            if _is_file_item(child):
                # A cascade from a parent folder is never a "direct" file
                # check, regardless of which way it toggled the file.
                self.directly_checked_file_paths.discard(
                    child.data(0, Qt.ItemDataRole.UserRole)
                )
            self._set_child_check_states(child, state)

    def _update_parent_check_states(self, item: QTreeWidgetItem | None) -> None:
        while item is not None:
            checked_count = 0
            partial_count = 0
            for index in range(item.childCount()):
                child_state = _get_check_state(item.child(index))
                if child_state == Qt.CheckState.Checked:
                    checked_count += 1
                elif child_state == Qt.CheckState.PartiallyChecked:
                    partial_count += 1

            if checked_count == item.childCount():
                _set_check_state(item, Qt.CheckState.Checked)
            elif checked_count == 0 and partial_count == 0:
                _set_check_state(item, Qt.CheckState.Unchecked)
            else:
                _set_check_state(item, Qt.CheckState.PartiallyChecked)

            item = item.parent()

    def closeEvent(self, event) -> None:
        if not self._confirm_close_with_unsaved_changes():
            event.ignore()
            return
        self._stop_autosave_timers()
        event.accept()

    def reject(self) -> None:
        if not self._confirm_close_with_unsaved_changes():
            return
        self._stop_autosave_timers()
        super().reject()

    def discard_and_close(self) -> None:
        """Close after an enclosing reset confirmation without a second prompt."""
        self.has_unsaved_changes = False
        self._stop_autosave_timers()
        super().reject()

    def _confirm_close_with_unsaved_changes(self) -> bool:
        if not self.has_unsaved_changes:
            return True
        reply = QMessageBox.question(
            self,
            "인계 내용 작성",
            "저장하지 않은 내용이 있습니다. 닫으시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _stop_autosave_timers(self) -> None:
        self.autosave_timer.stop()
        self.autosave_status_timer.stop()
