from __future__ import annotations

import argparse
import json
import time

from pywinauto import Desktop


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, required=True)
    args = parser.parse_args()
    main = Desktop(backend="uia").window(handle=args.hwnd)
    memo = main.child_window(title="업무 메모 작성", control_type="Window")
    for _ in range(4):
        ok_buttons = [
            button for button in memo.descendants(control_type="Button")
            if button.window_text() == "OK" and button.is_visible()
        ]
        if not ok_buttons:
            break
        ok_buttons[-1].invoke()
        time.sleep(0.2)
    qa_windows = [
        window for window in memo.descendants(control_type="Window")
        if window.window_text() == "알려주세요"
        and any(button.window_text() == "닫기" for button in window.descendants(control_type="Button"))
    ]
    if qa_windows:
        next(
            button for button in qa_windows[-1].descendants(control_type="Button")
            if button.window_text() == "닫기"
        ).invoke()
        time.sleep(0.2)
    warning = main.child_window(title="인수인계서 저장", control_type="Window")
    if warning.exists(timeout=1):
        warning.child_window(title="OK", control_type="Button").invoke()
    item = memo.descendants(control_type="TreeItem")[0]
    if item.window_text().startswith("☐"):
        item.click_input(coords=(10, max(1, item.rectangle().height() // 2)))
    next(
        button for button in memo.descendants(control_type="Button")
        if button.window_text() == "저장" and button.is_visible()
    ).invoke()
    info = main.child_window(title="업무 메모 저장", control_type="Window")
    if info.exists(timeout=2):
        info.child_window(title="OK", control_type="Button").invoke()
    memo.child_window(title="인수인계서 저장", control_type="Button").invoke()
    time.sleep(1)
    blocked = main.child_window(title="인수인계서 저장", control_type="Window").exists(timeout=2)
    dialogs = Desktop(backend="win32").windows(class_name="#32770", visible_only=True)
    save_opened = bool(dialogs)
    if save_opened:
        dialogs[-1].type_keys("{ESC}")
    print(json.dumps({"blocked": blocked, "save_dialog_opened": save_opened}), flush=True)
    return 0 if not blocked and save_opened else 1


if __name__ == "__main__":
    raise SystemExit(main())
