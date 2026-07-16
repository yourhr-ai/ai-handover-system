from __future__ import annotations

import argparse
import ctypes
import json
import time
from pathlib import Path

from pywinauto import Desktop, keyboard


WM_NULL = 0x0000
SMTO_ABORTIFHUNG = 0x0002


def choose_folder(owner, folder: Path) -> None:
    owner.child_window(title="패키지 폴더 선택", control_type="Button").invoke()
    time.sleep(1)
    keyboard.send_keys("^l")
    keyboard.send_keys(str(folder), with_spaces=True)
    keyboard.send_keys("{ENTER}")
    time.sleep(1)
    dialogs = Desktop(backend="win32").windows(
        title_re=".*폴더 선택", class_name="#32770", visible_only=True
    )
    dialog = dialogs[-1]
    next(child for child in dialog.children() if child.control_id() == 1).click()


def is_responsive(hwnd: int) -> bool:
    result = ctypes.c_size_t()
    return bool(
        ctypes.windll.user32.SendMessageTimeoutW(
            hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG, 250, ctypes.byref(result)
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--skip-load", action="store_true")
    args = parser.parse_args()

    initial_main_window = Desktop(backend="uia").window(
        process=args.pid, title="인수인계 프로그램"
    ).wrapper_object()
    main_window = Desktop(backend="uia").window(handle=initial_main_window.handle)
    extension = main_window.child_window(
        title="인수인계패키지로 만들 파일 선택", control_type="Window"
    )
    if extension.exists(timeout=0.5):
        extension.child_window(title="취소", control_type="Button").invoke()

    chatbot = main_window.child_window(title="물어보기", control_type="Window")
    if not chatbot.exists(timeout=1):
        main_window.child_window(title="물어보기", control_type="Button").invoke()
    chatbot.wait("visible ready", timeout=60)

    if not args.skip_load:
        choose_folder(chatbot, args.folder.resolve())
    started = time.perf_counter()
    samples = 0
    hung_samples = 0
    longest_sample = 0.0
    while time.perf_counter() - started < args.timeout:
        sample_started = time.perf_counter()
        responsive = is_responsive(int(chatbot.handle))
        elapsed = time.perf_counter() - sample_started
        samples += 1
        longest_sample = max(longest_sample, elapsed)
        if not responsive:
            hung_samples += 1
        folder_button = chatbot.child_window(
            title="패키지 폴더 선택", control_type="Button"
        )
        question_control = chatbot.child_window(
            auto_id="QApplication.ChatbotDialog.QuestionLineEdit"
        )
        if folder_button.is_enabled() and question_control.is_enabled():
            break
        time.sleep(0.1)

    search_input = chatbot.child_window(
        auto_id="QApplication.ChatbotDialog.chatSearchInput"
    ).wrapper_object()
    question_input = chatbot.child_window(
        auto_id="QApplication.ChatbotDialog.QuestionLineEdit"
    ).wrapper_object()
    scroll = next(
        item for item in chatbot.descendants()
        if item.element_info.automation_id.endswith("QScrollBar")
    )

    search_input.set_edit_text("검색결과없는문자열_20991231")
    search_input.set_focus()
    search_before = search_input.rectangle()
    scroll_before = float(scroll.iface_range_value.CurrentValue)
    for _ in range(4):
        keyboard.send_keys("{ENTER}")
        time.sleep(0.15)
    scroll_after = float(scroll.iface_range_value.CurrentValue)
    search_after = search_input.rectangle()
    search_still_focused = search_input.has_keyboard_focus()

    question_input.set_edit_text("")
    question_input.set_focus()
    blank_before = question_input.rectangle()
    keyboard.send_keys("{ENTER}")
    time.sleep(0.2)
    blank_after = question_input.rectangle()

    result = {
        "load_completed": folder_button.is_enabled() and question_input.is_enabled(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "responsiveness_samples": samples,
        "hung_samples": hung_samples,
        "longest_send_message_seconds": round(longest_sample, 3),
        "failed_search_scroll_before": scroll_before,
        "failed_search_scroll_after": scroll_after,
        "failed_search_control_unchanged": search_before == search_after,
        "failed_search_focus_preserved": search_still_focused,
        "blank_enter_control_unchanged": blank_before == blank_after,
        "blank_enter_focus_preserved": question_input.has_keyboard_focus(),
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if (
        result["load_completed"]
        and hung_samples == 0
        and longest_sample < 1.0
        and scroll_before == scroll_after
        and search_still_focused
        and result["blank_enter_focus_preserved"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
