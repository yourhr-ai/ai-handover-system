from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import time
from pathlib import Path

from pywinauto import Desktop, keyboard


ROOT = Path(__file__).resolve().parents[1]


def wait_window(process_id: int, title: str, timeout: float = 20):
    window = Desktop(backend="uia").window(process=process_id, title=title)
    window.wait("visible ready", timeout=timeout)
    return window


def choose_folder(window, folder: Path) -> None:
    window.child_window(title="폴더 선택", control_type="Button").invoke()
    time.sleep(1)
    keyboard.send_keys("^l")
    keyboard.send_keys(str(folder), with_spaces=True)
    keyboard.send_keys("{ENTER}")
    time.sleep(1)
    folder_dialog = Desktop(backend="win32").windows(
        process=window.process_id(),
        title="폴더 선택",
        class_name="#32770",
        visible_only=True,
    )[-1]
    next(
        child for child in folder_dialog.children() if child.control_id() == 1
    ).click()
    time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument(
        "--source-folder",
        type=Path,
        default=ROOT / ".tmp_chatbot_small_package",
    )
    args = parser.parse_args()

    window = wait_window(args.pid, "인수인계 프로그램")
    choose_folder(window, args.source_folder.resolve())
    window.child_window(title="인수인계패키지 생성", control_type="Button").invoke()

    deadline = time.time() + 20
    extension_dialog = None
    warning_texts: list[str] = []
    while time.time() < deadline:
        texts = [
            child.window_text() for child in window.descendants()
            if child.window_text()
        ]
        warning_texts.extend(text for text in texts if "인수인계서를 먼저" in text)
        candidate = window.child_window(
            title="인수인계패키지로 만들 파일 선택", control_type="Window"
        )
        if candidate.exists(timeout=0.1):
            extension_dialog = candidate
            break
        time.sleep(0.25)

    result = {
        "package_extension_dialog_opened": extension_dialog is not None,
        "report_prerequisite_warning_count": len(set(warning_texts)),
        "process_responding": not bool(ctypes.windll.user32.IsHungAppWindow(int(window.handle))),
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if extension_dialog is not None:
        keyboard.send_keys("{ESC}")
    return 0 if result["package_extension_dialog_opened"] and not warning_texts else 1


if __name__ == "__main__":
    raise SystemExit(main())
