from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from pywinauto import Desktop, keyboard


def choose_folder(owner, folder: Path) -> None:
    owner.child_window(title="폴더 선택", control_type="Button").invoke()
    time.sleep(1)
    dialogs = Desktop(backend="win32").windows(
        process=owner.process_id(), class_name="#32770", visible_only=True
    )
    if not dialogs:
        raise RuntimeError("folder selection dialog not found")
    dialog = dialogs[-1]
    dialog.set_focus()
    keyboard.send_keys("^l")
    keyboard.send_keys(str(folder), with_spaces=True)
    keyboard.send_keys("{ENTER}")
    time.sleep(1)
    dialogs = Desktop(backend="win32").windows(
        process=owner.process_id(), class_name="#32770", visible_only=True
    )
    if dialogs:
        next(child for child in dialogs[-1].children() if child.control_id() == 1).click()


def warning_text(main_window) -> tuple[str, object]:
    warning = main_window.child_window(control_type="Window", title="인수인계서 저장")
    warning.wait("visible ready", timeout=10)
    texts = [item.window_text() for item in warning.descendants(control_type="Text")]
    return "\n".join(texts), warning


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    main_window = Desktop(backend="uia").window(handle=args.hwnd)

    choose_folder(main_window, args.folder.resolve())
    main_window.child_window(title="분석 시작", control_type="Button").invoke()
    memo_button = main_window.child_window(
        title="메모 작성 및 인수인계서 저장", control_type="Button"
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not memo_button.is_enabled():
        time.sleep(0.25)
    memo_button.invoke()
    memo = main_window.child_window(title="업무 메모 작성", control_type="Window")
    memo.wait("visible ready", timeout=20)

    title_input = next(
        item for item in memo.descendants()
        if item.element_info.automation_id.endswith("memoTitleInput")
    )
    if not title_input.is_enabled():
        memo.child_window(title="메모 추가", control_type="Button").invoke()
        time.sleep(0.3)
    title_input.set_edit_text("실제 앱 검증 메모")
    content = next(
        item for item in memo.descendants()
        if item.element_info.automation_id.endswith("memoContentInput")
    )
    content.set_edit_text("실제 앱에서 저장 조건을 검증합니다.")
    complete = memo.child_window(title="인수인계서 저장", control_type="Button")
    complete.invoke()
    first_text, first_warning = warning_text(main_window)
    first_warning.child_window(title="OK", control_type="Button").invoke()

    tree_items = memo.descendants(control_type="TreeItem")
    if not tree_items:
        raise RuntimeError("folder tree item not found")
    tree_items[0].set_focus()
    keyboard.send_keys("{SPACE}")
    memo.child_window(title="저장", control_type="Button").invoke()
    info = main_window.child_window(title="업무 메모 저장", control_type="Window")
    if info.exists(timeout=2):
        info.child_window(title="OK", control_type="Button").invoke()
    complete.invoke()
    second_text, second_warning = warning_text(main_window)
    second_warning.child_window(title="OK", control_type="Button").invoke()

    memo.child_window(title="알려주세요", control_type="Button").invoke()
    qa = memo.child_window(title="알려주세요", control_type="Window")
    qa.wait("visible ready", timeout=10)
    qa_answer = next(
        item for item in qa.descendants()
        if item.element_info.automation_id.endswith("handoverAnswerInput")
    )
    qa_answer.set_edit_text("실제 앱 검증 답변")
    qa.child_window(title="저장", control_type="Button").invoke()
    saved = qa.child_window(title="알려주세요", control_type="Window")
    if saved.exists(timeout=2):
        saved.child_window(title="OK", control_type="Button").invoke()
    qa.child_window(title="닫기", control_type="Button").invoke()

    complete.invoke()
    third_warning = main_window.child_window(title="인수인계서 저장", control_type="Window")
    third_blocked = third_warning.exists(timeout=3)
    save_dialogs = Desktop(backend="win32").windows(class_name="#32770", visible_only=True)
    save_dialog_opened = bool(save_dialogs)
    if save_dialog_opened:
        save_dialogs[-1].type_keys("{ESC}")

    result = {
        "memo_only_warning": first_text,
        "memo_only_has_link_warning": "관련 폴더/이메일/메신저" in first_text,
        "memo_only_has_qa_warning": "알려주세요" in first_text,
        "link_only_warning": second_text,
        "link_only_has_link_warning": "관련 폴더/이메일/메신저" in second_text,
        "link_only_has_qa_warning": "알려주세요" in second_text,
        "all_requirements_blocked": third_blocked,
        "save_dialog_opened": save_dialog_opened,
    }
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if (
        result["memo_only_has_link_warning"]
        and result["memo_only_has_qa_warning"]
        and not result["link_only_has_link_warning"]
        and result["link_only_has_qa_warning"]
        and not third_blocked
        and save_dialog_opened
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
