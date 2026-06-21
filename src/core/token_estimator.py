from __future__ import annotations

"""
토큰 수 및 비용 추정 유틸리티.

한국어 텍스트 기준:
  1 토큰 ≈ 1.7 글자 (BPE 기준 한글은 영어보다 글자당 토큰 소비가 높음)
  → 1 글자 ≈ 0.6 토큰

주의: 실제 토큰 수는 모델/프롬프트 구조에 따라 다를 수 있습니다.
      이 값은 예산 계획 목적의 추정치입니다.
"""

# ── 토큰 변환 상수 ────────────────────────────────────────────────────
_CHARS_PER_TOKEN = 1.7       # 1 토큰당 평균 글자 수 (한국어 기준)
_TOKEN_PER_CHAR = 1 / _CHARS_PER_TOKEN  # ≈ 0.59

# ── 가격 상수 (gpt-5.5 기준 추정치, 2026년 기준) ─────────────────────
# 실제 가격은 OpenAI 요금표에서 확인하세요: https://openai.com/pricing
_INPUT_PRICE_PER_M_TOKENS = 15.0    # $15 / 1M input tokens
_OUTPUT_PRICE_PER_M_TOKENS = 60.0   # $60 / 1M output tokens

# ── 예상 출력 토큰 ────────────────────────────────────────────────────
# 업무복원 보고서: 약 4,000~8,000 토큰 출력 예상
EST_OUTPUT_TOKENS = 6_000


def chars_to_tokens(char_count: int) -> int:
    """글자 수를 토큰 수로 변환한다 (한국어 기준 추정)."""
    return max(1, int(char_count * _TOKEN_PER_CHAR))


def tokens_to_chars(token_count: int) -> int:
    """토큰 수를 글자 수로 역산한다."""
    return int(token_count * _CHARS_PER_TOKEN)


def estimate_cost(
    input_tokens: int,
    output_tokens: int = EST_OUTPUT_TOKENS,
) -> float:
    """입력/출력 토큰으로 예상 비용($)을 계산한다."""
    return (
        input_tokens / 1_000_000 * _INPUT_PRICE_PER_M_TOKENS
        + output_tokens / 1_000_000 * _OUTPUT_PRICE_PER_M_TOKENS
    )


def build_cost_info(eval_text: str, output_tokens: int = EST_OUTPUT_TOKENS) -> dict:
    """
    eval_text를 기준으로 예상 토큰/비용 정보를 반환한다.

    Returns dict with:
        input_tokens, output_tokens, total_tokens,
        cost_usd, cost_str, chars
    """
    chars = len(eval_text)
    input_tokens = chars_to_tokens(chars)
    cost = estimate_cost(input_tokens, output_tokens)
    total = input_tokens + output_tokens

    return {
        "chars": chars,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "cost_usd": cost,
        "cost_str": f"${cost:.3f}",
    }
