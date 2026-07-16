from __future__ import annotations

import argparse
import json
import time

from pywinauto import Desktop


def close_ok(root) -> None:
    for _ in range(5):
        buttons = [
            button for button in root.descendants(control_type="Button")
            if button.window_text() == "OK" and button.is_visible()
        ]
        if not buttons:
            return
        buttons[-1].invoke()
        time.sleep(0.2)


def edit_answer(memo, text: str) -> None:
    memo.child_window(title="알려주세요", control_type="Button").invoke()
    qa = next(
        window for window in memo.descendants(control_type="Window")
        if window.window_text() == "알려주세요"
        and any(child.element_info.automation_id.endswith("handoverAnswerInput") for child in window.descendants())
    )
    answer = next(
        child for child in qa.descendants()
        if child.element_info.automation_id.endswith("handoverAnswerInput")
    )
    answer.set_edit_text(text)
    next(button for button in qa.descendants(control_type="Button") if button.window_text() == "저장").invoke()
    time.sleep(0.3)
    close_ok(memo)
    next(button for button in qa.descendants(control_type="Button") if button.window_text() == "닫기").invoke()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, required=True)
    args = parser.parse_args()
    dialogs = Desktop(backend="win32").windows(class_name="#32770", visible_only=True)
    if dialogs:
        dialogs[-1].type_keys("{ESC}")
    main = Desktop(backend="uia").window(handle=args.hwnd)
    memo = main.child_window(title="업무 메모 작성", control_type="Window")
    close_ok(memo)
    edit_answer(memo, "")
    memo.child_window(title="인수인계서 저장", control_type="Button").invoke()
    warning = main.child_window(title="인수인계서 저장", control_type="Window")
    warning.wait("visible", timeout=5)
    text = "\n".join(item.window_text() for item in warning.descendants(control_type="Text"))
    close_ok(memo)
    edit_answer(memo, "실제 앱 검증 답변")
    memo.child_window(title="인수인계서 저장", control_type="Button").invoke()
    time.sleep(1)
    save_opened = bool(Desktop(backend="win32").windows(class_name="#32770", visible_only=True))
    print(json.dumps({
        "link_only_warning": text,
        "has_link_warning": "관련 폴더/이메일/메신저" in text,
        "has_qa_warning": "알려주세요" in text,
        "save_dialog_opened": save_opened,
    }, ensure_ascii=True), flush=True)
    return 0 if "알려주세요" in text and "관련 폴더/이메일/메신저" not in text and save_opened else 1


if __name__ == "__main__":
    raise SystemExit(main())
