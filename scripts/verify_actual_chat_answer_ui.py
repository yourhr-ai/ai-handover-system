from __future__ import annotations

import argparse
import json
import time

from pywinauto import Desktop, keyboard


def descendants_with_suffix(root, suffix: str) -> list:
    return [
        item for item in root.descendants()
        if item.element_info.automation_id.endswith(suffix)
    ]


def ask(chatbot, question: str, timeout: float = 180) -> dict:
    question_input = chatbot.child_window(
        auto_id="QApplication.ChatbotDialog.QuestionLineEdit"
    ).wrapper_object()
    send_button = chatbot.child_window(title="전송", control_type="Button")
    before = len(descendants_with_suffix(chatbot, "relatedToggleButton"))
    question_input.set_edit_text(question)
    question_input.set_focus()
    keyboard.send_keys("{ENTER}")
    deadline = time.monotonic() + timeout
    saw_disabled = False
    while time.monotonic() < deadline:
        enabled = send_button.is_enabled()
        saw_disabled = saw_disabled or not enabled
        if saw_disabled and enabled:
            break
        time.sleep(0.25)
    answers = descendants_with_suffix(chatbot, "answerBody")
    after = len(descendants_with_suffix(chatbot, "relatedToggleButton"))
    return {
        "related_added": after > before,
        "answer": answers[-1].window_text() if answers else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, required=True)
    args = parser.parse_args()
    main_window = Desktop(backend="uia").window(handle=args.hwnd)
    chatbot = main_window.child_window(title="물어보기", control_type="Window")
    chatbot.wait("visible ready", timeout=10)

    high = ask(
        chatbot,
        "GAT글로벌 급여테이블(5. 급여테이블_251221)에서 직급별 급여 범위가 어떻게 나와?",
    )
    low_intent = ask(chatbot, "그거 있잖아, 그거 어떻게 하는 거야?")
    low_missing = ask(chatbot, "존재하지않는파일_20991231 내용 알려줘")

    search = chatbot.child_window(
        auto_id="QApplication.ChatbotDialog.chatSearchInput"
    ).wrapper_object()
    scroll = next(
        item for item in chatbot.descendants()
        if item.element_info.automation_id.endswith("QScrollBar")
    )
    scroll.iface_range_value.SetValue(scroll.iface_range_value.CurrentMaximum)
    search.set_edit_text("Associate 1년차")
    search.set_focus()
    search_before = float(scroll.iface_range_value.CurrentValue)
    keyboard.send_keys("{ENTER}")
    time.sleep(0.5)
    search_after = float(scroll.iface_range_value.CurrentValue)

    source_labels = [
        item for item in chatbot.descendants(control_type="Text")
        if "경로:" in item.window_text()
    ]

    question_input = chatbot.child_window(
        auto_id="QApplication.ChatbotDialog.QuestionLineEdit"
    ).wrapper_object()
    question_input.set_focus()
    feedback_buttons = descendants_with_suffix(chatbot, "feedbackDownButton")
    enabled_feedback = [button for button in feedback_buttons if button.is_enabled()]
    feedback_clicked = bool(enabled_feedback)
    if enabled_feedback:
        enabled_feedback[-1].click_input()
        time.sleep(0.3)
    focus_preserved = question_input.has_keyboard_focus()

    result = {
        "high": high,
        "low_intent": low_intent,
        "low_missing": low_missing,
        "search_scroll_before": search_before,
        "search_scroll_after": search_after,
        "search_moved": search_before != search_after,
        "feedback_clicked": feedback_clicked,
        "feedback_focus_preserved": focus_preserved,
        "visible_path_label_count": len(source_labels),
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if (
        high["related_added"]
        and not low_intent["related_added"]
        and not low_missing["related_added"]
        and result["search_moved"]
        and feedback_clicked
        and focus_preserved
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
