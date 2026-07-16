from __future__ import annotations

import argparse
import json
import time

from pywinauto import Desktop


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, required=True)
    args = parser.parse_args()
    main_window = Desktop(backend="uia").window(handle=args.hwnd)
    memo = main_window.child_window(title="업무 메모 작성", control_type="Window")
    qa = next(
        item for item in memo.descendants(control_type="Window")
        if item.window_text() == "알려주세요"
        and any(
            child.element_info.automation_id.endswith("handoverAnswerInput")
            for child in item.descendants()
        )
    )
    answer = next(
        item for item in qa.descendants()
        if item.element_info.automation_id.endswith("handoverAnswerInput")
    )
    answer.set_edit_text("실제 앱 검증 답변")
    next(button for button in qa.descendants(control_type="Button") if button.window_text() == "저장").invoke()
    time.sleep(0.5)
    messages = [
        item for item in memo.descendants(control_type="Window")
        if item.window_text() == "알려주세요"
        and any(button.window_text() == "OK" for button in item.descendants(control_type="Button"))
    ]
    if messages:
        next(
            button for button in messages[-1].descendants(control_type="Button")
            if button.window_text() == "OK"
        ).invoke()
    next(button for button in qa.descendants(control_type="Button") if button.window_text() == "닫기").invoke()
    memo.child_window(title="인수인계서 저장", control_type="Button").invoke()
    blocked = main_window.child_window(
        title="인수인계서 저장", control_type="Window"
    ).exists(timeout=3)
    dialogs = Desktop(backend="win32").windows(class_name="#32770", visible_only=True)
    save_opened = bool(dialogs)
    if save_opened:
        dialogs[-1].type_keys("{ESC}")
    print(json.dumps({"blocked": blocked, "save_dialog_opened": save_opened}), flush=True)
    return 0 if not blocked and save_opened else 1


if __name__ == "__main__":
    raise SystemExit(main())
