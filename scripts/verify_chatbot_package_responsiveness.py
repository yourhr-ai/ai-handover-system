"""Manual/local verifier for the real MainWindow -> chatbot package-load path."""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import _load_stylesheet
from app.ui.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--cancel-after", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    # Keep verifier windows visually identical to the production entry point.
    # New GUI verification scripts should apply the global stylesheet
    # immediately after QApplication is created using this same pattern.
    app.setStyleSheet(_load_stylesheet())
    window = MainWindow()
    window.show()
    app.processEvents()

    started = time.perf_counter()
    last_tick = started
    max_gap = 0.0
    heartbeat_count = 0
    move_count = 0
    hung_samples = 0
    long_gaps: list[dict[str, object]] = []
    result: dict[str, object] = {}
    result["stylesheet_loaded"] = bool(app.styleSheet().strip())
    result["stylesheet_characters"] = len(app.styleSheet())
    result["workflow_progress_visible"] = window.workflow_progress.isVisible()

    window.chatbot_button.click()
    dialog = window._chatbot_dialog
    if dialog is None:
        raise RuntimeError("chatbot dialog did not open")

    with patch.object(QFileDialog, "getExistingDirectory", return_value=str(args.folder)):
        dialog.select_folder_button.click()

    worker = dialog.package_load_worker
    if worker is None:
        raise RuntimeError("package load worker did not start")

    def heartbeat() -> None:
        nonlocal last_tick, max_gap, heartbeat_count, move_count, hung_samples
        now = time.perf_counter()
        max_gap = max(max_gap, now - last_tick)
        if now - last_tick >= 1.0:
            long_gaps.append(
                {
                    "seconds": round(now - last_tick, 3),
                    "status": dialog.status_label.text(),
                }
            )
        last_tick = now
        heartbeat_count += 1
        move_count += 1
        dialog.move(80 + (move_count % 20), 80 + (move_count % 12))
        try:
            if ctypes.windll.user32.IsHungAppWindow(int(dialog.winId())):
                hung_samples += 1
        except (AttributeError, OSError):
            pass

    heartbeat_timer = QTimer()
    heartbeat_timer.setInterval(100)
    heartbeat_timer.timeout.connect(heartbeat)
    heartbeat_timer.start()

    def finish() -> None:
        result.update(
            elapsed_seconds=round(time.perf_counter() - started, 3),
            packages=len(dialog.packages),
            chunks=len(dialog.chunks),
            question_enabled=dialog.question_input.isEnabled(),
            status=dialog.status_label.text(),
            heartbeat_count=heartbeat_count,
            max_event_loop_gap_seconds=round(max_gap, 3),
            move_count=move_count,
            hung_samples=hung_samples,
            long_event_loop_gaps=long_gaps,
            canceled="취소" in dialog.status_label.text(),
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        app.quit()

    worker.finished.connect(lambda: QTimer.singleShot(0, finish))
    if args.cancel_after > 0:
        QTimer.singleShot(int(args.cancel_after * 1000), dialog.cancel_load_button.click)
    QTimer.singleShot(int(args.timeout * 1000), dialog.cancel_load_button.click)
    QTimer.singleShot(int((args.timeout + 30) * 1000), finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
