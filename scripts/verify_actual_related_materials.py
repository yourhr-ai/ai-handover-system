from __future__ import annotations

import argparse
import json
import time

from pywinauto import Desktop, keyboard


def related_buttons(chatbot) -> list:
    return [
        item for item in chatbot.descendants()
        if item.element_info.automation_id.endswith("relatedToggleButton")
    ]


def answer_texts(chatbot) -> list[str]:
    return [
        item.window_text() for item in chatbot.descendants()
        if item.element_info.automation_id.endswith("answerBody")
    ]


def ask(chatbot, question: str, timeout: float = 180) -> tuple[int, int, str]:
    question_input = chatbot.child_window(
        auto_id="QApplication.ChatbotDialog.QuestionLineEdit"
    ).wrapper_object()
    send_button = chatbot.child_window(title="전송", control_type="Button")
    before = len(related_buttons(chatbot))
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
    answers = answer_texts(chatbot)
    return before, len(related_buttons(chatbot)), answers[-1] if answers else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int)
    args = parser.parse_args()
    if args.hwnd:
        main_window = Desktop(backend="uia").window(handle=args.hwnd)
    else:
        initial = Desktop(backend="uia").window(
            process=args.pid, title="인수인계 프로그램"
        ).wrapper_object()
        main_window = Desktop(backend="uia").window(handle=initial.handle)
    chatbot = main_window.child_window(title="물어보기", control_type="Window")
    chatbot.wait("visible ready", timeout=10)

    known = ask(
        chatbot,
        "GAT글로벌 급여테이블(5. 급여테이블_251221)에서 직급별 급여 범위가 어떻게 나와?",
    )
    unknown = ask(chatbot, "존재하지않는파일_20991231 내용 알려줘")
    result = {
        "known_related_added": known[1] > known[0],
        "known_answer": known[2],
        "unknown_related_added": unknown[1] > unknown[0],
        "unknown_answer": unknown[2],
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["known_related_added"] and not result["unknown_related_added"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
