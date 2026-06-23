from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import openai

from src.core.prompt_builder import PromptBuilder
from src.core.summarizer import TextSummarizer

if TYPE_CHECKING:
    from src.config.settings import Settings

_OUTPUT_DIR   = Path(__file__).resolve().parents[2] / "output"
_DEBUG_PATH   = _OUTPUT_DIR / "debug_prompt.txt"
_INPUT_PATH   = _OUTPUT_DIR / "final_report_input.txt"
_OUTPUT_PATH  = _OUTPUT_DIR / "final_report_output.txt"


def _save_debug(messages: list[dict]) -> None:
    """AI에 전달되는 최종 메시지를 output/debug_prompt.txt 에 저장한다."""
    try:
        _DEBUG_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines: list[str] = [
            f"[디버그 저장 시각] {timestamp}",
            "=" * 60,
        ]
        for i, msg in enumerate(messages, 1):
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            lines.append(f"\n[{i}] ROLE: {role}")
            lines.append("-" * 40)
            lines.append(content)
            lines.append("")
        lines.append("=" * 60)
        _DEBUG_PATH.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        print(f"[디버그] 프롬프트 저장 완료: {_DEBUG_PATH}")
    except Exception as exc:
        print(f"[디버그] 저장 실패: {exc}")


def _save_final_report_input(messages: list[dict]) -> None:
    """GPT 실제 입력 전문을 output/final_report_input.txt 에 저장한다."""
    try:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total_chars = sum(len(m.get("content", "")) for m in messages)
        lines = [
            f"# GPT 실제 입력 (final_report_input.txt)",
            f"# 저장 시각: {timestamp}",
            f"# 총 글자 수: {total_chars:,}자",
            f"# 메시지 수: {len(messages)}개",
            "",
        ]
        for i, msg in enumerate(messages, 1):
            role    = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            lines.append(f"[{i}] ROLE: {role}  ({len(content):,}자)")
            lines.append("=" * 60)
            lines.append(content)
            lines.append("")
        _INPUT_PATH.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        print(f"[GPT 입력] output/final_report_input.txt 저장  ({total_chars:,}자)")
    except Exception as exc:
        print(f"[경고] final_report_input.txt 저장 실패: {exc}")


def _save_final_report_output(result_md: str) -> None:
    """GPT 원본 응답을 output/final_report_output.txt 에 저장한다."""
    try:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = (
            f"# GPT 원본 응답 (final_report_output.txt)\n"
            f"# 저장 시각: {timestamp}\n"
            f"# 응답 글자 수: {len(result_md):,}자\n\n"
        )
        _OUTPUT_PATH.write_text(header + result_md, encoding="utf-8", newline="\n")
        print(f"[GPT 응답] output/final_report_output.txt 저장  ({len(result_md):,}자)")
    except Exception as exc:
        print(f"[경고] final_report_output.txt 저장 실패: {exc}")


class AIClient:
    """Wrapper around the OpenAI API."""

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._client = openai.OpenAI(api_key=settings.api_key)
        self._summarizer = TextSummarizer()
        self._prompt_builder = PromptBuilder()

    def analyze(
        self, job_desc: str, eval_text: str, light_mode: bool = False
    ) -> str:
        """Run the full analysis pipeline and return markdown report."""
        if self._summarizer.needs_summary(eval_text):
            eval_text = self._summarizer.summarize(eval_text, self)

        messages = self._prompt_builder.build(job_desc, eval_text, light_mode=light_mode)
        _save_debug(messages)
        return self._call_api(messages)

    def analyze_with_draft(
        self, job_desc: str, draft_md: str, light_mode: bool = False
    ) -> str:
        """규칙기반 초안을 GPT가 검토·보강하여 최종 보고서를 생성한다.

        고객사 요약 원문 대신 draft_md 만 전달하므로 입력 토큰이 크게 절감된다.
        """
        messages = self._prompt_builder.build_enhance(
            job_desc, draft_md, light_mode=light_mode
        )
        _save_debug(messages)
        _save_final_report_input(messages)
        result = self._call_api(messages)
        _save_final_report_output(result)
        return result

    def _call_api(self, messages: list[dict]) -> str:
        response = self._client.chat.completions.create(
            model=self._settings.model,
            messages=messages,
        )
        return response.choices[0].message.content or ""
