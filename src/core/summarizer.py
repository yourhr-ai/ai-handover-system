from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.ai_client import AIClient

MAX_CHARS = 12_000


class TextSummarizer:
    """Summarise evaluation material if it exceeds MAX_CHARS."""

    def needs_summary(self, text: str) -> bool:
        return len(text) > MAX_CHARS

    def summarize(self, text: str, client: "AIClient") -> str:
        """Return a condensed version of *text* using the AI client."""
        chunk_size = MAX_CHARS
        chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

        summaries: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            messages = [
                {
                    "role": "system",
                    "content": (
                        "당신은 업무 문서를 요약하는 전문가입니다. "
                        "아래 텍스트에서 업무 인수인계에 필요한 핵심 정보만 추출하여 "
                        "간결하게 요약하십시오. 중요한 수치, 날짜, 담당자, 프로젝트명은 반드시 포함하십시오."
                    ),
                },
                {
                    "role": "user",
                    "content": f"[청크 {i}/{len(chunks)}]\n{chunk}",
                },
            ]
            summaries.append(client._call_api(messages))

        if len(summaries) == 1:
            return summaries[0]

        combined = "\n\n".join(f"[요약 {i}]\n{s}" for i, s in enumerate(summaries, 1))
        final_messages = [
            {
                "role": "system",
                "content": "여러 청크의 요약본을 하나의 일관된 요약으로 통합하십시오.",
            },
            {"role": "user", "content": combined},
        ]
        return client._call_api(final_messages)
