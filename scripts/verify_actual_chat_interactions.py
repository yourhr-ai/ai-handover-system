from __future__ import annotations

import argparse
import json
import time

from pywinauto import Desktop, keyboard


def with_suffix(root, suffix: str) -> list:
    return [
        item for item in root.descendants()
        if item.element_info.automation_id.endswith(suffix)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, required=True)
    args = parser.parse_args()
    main_window = Desktop(backend="uia").window(handle=args.hwnd)
    chatbot_spec = main_window.child_window(title="물어보기", control_type="Window")
    if not chatbot_spec.exists(timeout=1):
        main_window.child_window(title="물어보기", control_type="Button").invoke()
    chatbot_spec.wait("visible ready", timeout=60)
    chatbot = chatbot_spec.wrapper_object()

    search = next(
        item for item in chatbot.descendants()
        if item.element_info.automation_id.endswith("chatSearchInput")
    )
    scroll = next(
        item for item in chatbot.descendants()
        if item.element_info.automation_id.endswith("QScrollBar")
    )
    scroll.iface_range_value.SetValue(scroll.iface_range_value.CurrentMaximum)
    search.set_edit_text("Associate 1년차")
    search.set_focus()
    before = float(scroll.iface_range_value.CurrentValue)
    keyboard.send_keys("{ENTER}")
    time.sleep(0.6)
    after = float(scroll.iface_range_value.CurrentValue)

    toggles = with_suffix(chatbot, "relatedToggleButton")
    if toggles:
        toggles[-1].click_input()
        time.sleep(0.3)
    visible_paths = [
        item.window_text() for item in chatbot.descendants(control_type="Text")
        if "경로:" in item.window_text() and item.is_visible()
    ]

    question = next(
        item for item in chatbot.descendants()
        if item.element_info.automation_id.endswith("QuestionLineEdit")
    )
    focus_target = question if question.is_enabled() else search
    focus_target.click_input()
    time.sleep(0.2)
    focus_before = focus_target.has_keyboard_focus()
    downs = [item for item in with_suffix(chatbot, "feedbackDownButton") if item.is_enabled()]
    clicked = bool(downs)
    if clicked:
        downs[-1].invoke()
        time.sleep(1.2)
    try:
        focus_after = focus_target.has_keyboard_focus()
    except Exception:
        focus_after = False

    result = {
        "search_scroll_before": before,
        "search_scroll_after": after,
        "search_moved": before != after,
        "search_result": next(
            (
                item.window_text() for item in chatbot.descendants()
                if item.element_info.automation_id.endswith("chatSearchResultLabel")
            ),
            "",
        ),
        "related_expanded": bool(visible_paths),
        "visible_path_sample": visible_paths[-1] if visible_paths else "",
        "feedback_clicked": clicked,
        "focus_target": focus_target.element_info.automation_id,
        "focus_before": focus_before,
        "focus_after": focus_after,
    }
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result["search_moved"] and clicked and focus_before and focus_after else 1


if __name__ == "__main__":
    raise SystemExit(main())
