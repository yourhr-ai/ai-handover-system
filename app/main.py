import logging
import sys
import multiprocessing
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QSharedMemory
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QWidget

# `app.ui.main_window` pulls in a large dependency graph (docx, pptx, PIL,
# pdfminer, googleapiclient, ...) that takes real time to import. A duplicate
# launch must be detected and exited *before* paying that cost, so this is a
# deferred import inside main() rather than a top-level one - see the
# single-instance check below.


logger = logging.getLogger(__name__)

SINGLE_INSTANCE_KEY = "HandoverAnalyzer10Min-SingleInstance-v1"
ACTIVATION_SERVER_NAME = "HandoverAnalyzer10Min-Activate-v1"

if getattr(sys, "frozen", False):
    # PyInstaller onefile: __file__ resolves to a flattened path inside the
    # _MEIPASS extraction dir, so parent.parent no longer lands on the repo
    # root - style.qss is extracted directly under _MEIPASS instead.
    PROJECT_ROOT = Path(sys._MEIPASS)
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
STYLESHEET_PATH = PROJECT_ROOT / "style.qss"


def _load_stylesheet(stylesheet_path: Path = STYLESHEET_PATH) -> str:
    try:
        return stylesheet_path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("스타일시트를 찾을 수 없습니다: %s", stylesheet_path)
        return ""


def _reload_stylesheet(app: QApplication) -> None:
    app.setStyleSheet(_load_stylesheet())


def _connect_event(event: object, callback) -> bool:
    for method_name in ("connect", "subscribe", "append"):
        method = getattr(event, method_name, None)
        if callable(method):
            method(callback)
            return True
    return False


def _start_qtreload_watch(app: QApplication) -> None:
    try:
        from qtreload import QtReloadWidget
    except ImportError as exc:
        raise RuntimeError(
            "개발 모드(--dev)를 사용하려면 qtreload가 필요합니다. "
            "먼저 `py -m pip install -r requirements-dev.txt`를 실행하세요."
        ) from exc

    def on_stylesheet_changed(*_args: object) -> None:
        current_app = QApplication.instance()
        if current_app is not None:
            current_app.setStyleSheet(_load_stylesheet())
        print("style.qss 변경 감지 - 재적용 완료", flush=True)

    reload_widget = QtReloadWidget(
        ["app"],
        log_func=lambda message: print(f"qtreload: {message}", flush=True),
    )
    stylesheet_path = str(STYLESHEET_PATH)
    if stylesheet_path not in reload_widget._watcher.files():
        reload_widget._watcher.addPath(stylesheet_path)
        reload_widget._files_list.addItem(stylesheet_path)
    reload_widget.evt_stylesheet.connect(on_stylesheet_changed)
    reload_widget.hide()
    app._qtreload_widget = reload_widget
    print(f"qtreload 감시 시작: {STYLESHEET_PATH}", flush=True)


def _parse_dev_flag(argv: list[str]) -> tuple[list[str], bool]:
    dev_mode = "--dev" in argv
    return [argument for argument in argv if argument != "--dev"], dev_mode


def _close_splash() -> None:
    """Dismisses the PyInstaller onefile splash screen (see
    handover_analyzer.spec) the moment the real window is ready to show.
    `pyi_splash` only exists inside a frozen build, so this is a no-op when
    running from source."""
    if not getattr(sys, "frozen", False):
        return
    try:
        import pyi_splash
    except ImportError:
        return
    pyi_splash.close()


def _acquire_single_instance_lock() -> QSharedMemory | None:
    """Returns a held QSharedMemory handle if this is the only running
    instance, or None if another instance already holds it. Windows frees
    named shared memory automatically when the owning process exits (even on
    a crash/kill), so no separate stale-lock cleanup is needed here - only
    the attach/detach dance to clear a segment left behind by the unusual
    case where a previous instance shut down without releasing it cleanly."""
    shared_memory = QSharedMemory(SINGLE_INSTANCE_KEY)
    if shared_memory.attach():
        shared_memory.detach()
    if not shared_memory.create(1):
        return None
    return shared_memory


def _request_running_instance_activation() -> None:
    """Best-effort nudge to the already-running instance to raise its window.
    Silently gives up if it doesn't respond quickly - the important part
    (this duplicate process not opening a second window) already happened."""
    socket = QLocalSocket()
    socket.connectToServer(ACTIVATION_SERVER_NAME)
    if not socket.waitForConnected(200):
        return
    socket.write(b"activate")
    socket.waitForBytesWritten(200)
    socket.disconnectFromServer()


def _start_activation_server(window: QWidget) -> QLocalServer:
    """Listens for activation pings from duplicate launches so the existing
    window can be brought to front instead of silently doing nothing."""
    server = QLocalServer()
    QLocalServer.removeServer(ACTIVATION_SERVER_NAME)  # clears a stale socket from an unclean previous shutdown
    server.listen(ACTIVATION_SERVER_NAME)

    def _on_new_connection() -> None:
        connection = server.nextPendingConnection()
        if connection is None:
            return
        connection.readAll()
        window.showNormal()
        window.raise_()
        window.activateWindow()
        connection.disconnectFromServer()

    server.newConnection.connect(_on_new_connection)
    return server


def main() -> int:
    qt_argv, dev_mode = _parse_dev_flag(sys.argv)
    app = QApplication(qt_argv)

    instance_lock = _acquire_single_instance_lock()
    if instance_lock is None:
        # Another instance is already running: ask it to come to the front
        # and exit immediately without ever creating a window of our own.
        _request_running_instance_activation()
        _close_splash()
        return 0
    app._single_instance_lock = instance_lock  # keep alive for the app's lifetime

    app.setStyleSheet(_load_stylesheet())
    if dev_mode:
        _start_qtreload_watch(app)

    from app.ui.main_window import MainWindow

    window = MainWindow()
    app._activation_server = _start_activation_server(window)  # keep alive for the app's lifetime

    window.show()
    _close_splash()

    return app.exec()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
