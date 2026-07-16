import sys
import multiprocessing
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STYLESHEET_PATH = PROJECT_ROOT / "style.qss"


def _load_stylesheet(stylesheet_path: Path = STYLESHEET_PATH) -> str:
    try:
        return stylesheet_path.read_text(encoding="utf-8")
    except OSError:
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


def main() -> int:
    qt_argv, dev_mode = _parse_dev_flag(sys.argv)
    app = QApplication(qt_argv)
    app.setStyleSheet(_load_stylesheet())
    if dev_mode:
        _start_qtreload_watch(app)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
