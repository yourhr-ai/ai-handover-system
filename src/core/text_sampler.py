from __future__ import annotations

"""
텍스트 샘플링 유틸리티.

긴 문서에서 앞/중간/뒤를 대표 샘플링하여 최대 글자수 내로 줄인다.
파일당 최대 5,000자 제한으로 토큰 비용을 절감한다.

샘플링 비율:
  - 앞부분: 60% (3,000자)  — 문서 목적, 제목, 개요가 주로 앞에 위치
  - 중간:   20% (1,000자)  — 본문 내용 대표
  - 끝부분: 20% (1,000자)  — 결론, 다음 단계, 담당자 정보
"""

MAX_CHARS_PER_FILE = 5_000
_ELLIPSIS = "\n[...중략...]\n"

FRONT_RATIO = 0.60
MID_RATIO = 0.20
# END_RATIO = 1 - FRONT_RATIO - MID_RATIO = 0.20


def sample_text(text: str, max_chars: int = MAX_CHARS_PER_FILE) -> tuple[str, int]:
    """
    텍스트를 max_chars 이내로 샘플링한다.

    Returns:
        (sampled_text, original_char_count)
        원본이 max_chars 이내면 원본 그대로 반환한다.
    """
    original_len = len(text)
    if original_len <= max_chars:
        return text, original_len

    front_len = int(max_chars * FRONT_RATIO)
    mid_len = int(max_chars * MID_RATIO)
    end_len = max_chars - front_len - mid_len  # 나머지를 끝부분에 할당

    front = text[:front_len]

    mid_start = (original_len - mid_len) // 2
    mid = text[mid_start : mid_start + mid_len]

    end = text[-end_len:] if end_len > 0 else ""

    sampled = front + _ELLIPSIS + mid + _ELLIPSIS + end
    return sampled, original_len


def reduction_pct(original: int, sampled: int) -> float:
    """샘플링으로 인한 문자 절감률 (0.0 ~ 100.0)"""
    if original <= 0:
        return 0.0
    return max(0.0, (1.0 - sampled / original) * 100.0)
