import re
import time
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Callable

import numpy as np

from app.config import CHAT_MODEL, CHAT_REASONING_EFFORT
from app.services.ai_proxy_client import (
    AiProxyError,
    InsufficientCreditsError,
    call_chat_stream,
    call_embeddings,
)

MAX_RELATED_ANSWERS = 1
DEFAULT_TOP_K = 12
MAX_SEARCH_RESULTS = 16
ANSWER_MAX_COMPLETION_TOKENS = 500
CONFIDENCE_CERTAIN_THRESHOLD = 0.55
CONFIDENCE_ESTIMATED_THRESHOLD = 0.42
UNRELATED_SIMILARITY_THRESHOLD = 0.34
FILENAME_PARTIAL_MATCH_THRESHOLD = 0.58
SYSTEM_PROMPT = (
    "\ub2f9\uc2e0\uc740 \ud1f4\uc0ac\uc790\uc758 \uc5c5\ubb34\ub97c \uc778\uc218\ubc1b\uc740 \ud6c4\uc784\uc790\ub97c \ub3d5\ub294 AI "
    "\uc5b4\uc2dc\uc2a4\ud134\ud2b8\uc785\ub2c8\ub2e4. \uc544\ub798 \uc81c\uacf5\ub41c \uc790\ub8cc(\uad00\ub828 \ubb38\uc11c/\uc774\uba54\uc77c/"
    "\uce74\ud1a1/\uba54\ubaa8 \uc870\uac01)\ub9cc\uc744 \uadfc\uac70\ub85c \ub2f5\ubcc0\ud558\uc138\uc694. \uc790\ub8cc\uc5d0 \uc5c6\ub294 "
    "\ub0b4\uc6a9\uc740 \ucd94\uce21\ud558\uc9c0 \ub9d0\uace0 '\uc81c\uacf5\ub41c \uc790\ub8cc\uc5d0\uc11c \ud655\uc778\ub418\uc9c0 "
    "\uc54a\uc2b5\ub2c8\ub2e4'\ub77c\uace0 \ub2f5\ud558\uc138\uc694. \ub2f5\ubcc0 \ub9c8\uc9c0\ub9c9\uc5d0 \uc5b4\ub5a4 \uc790\ub8cc(\ud30c\uc77c\uba85)\ub97c "
    "\uadfc\uac70\ub85c \ub2f5\ud588\ub294\uc9c0 \ucd9c\ucc98\ub97c \uba85\uc2dc\ud558\uc138\uc694. "
    "\uc81c\uacf5\ub41c \uc790\ub8cc \uac01\uac01\uc5d0\ub294 \ucd9c\ucc98 \ud30c\uc77c\uba85, \uacbd\ub85c, \uc218\uc815\uc77c\uc774 \ud45c\uc2dc\ub418\uc5b4 "
    "\uc788\uc2b5\ub2c8\ub2e4. \ub2f5\ubcc0 \uc2dc \uad00\ub828\ub41c \ud30c\uc77c\uc758 \uacbd\ub85c\uc640 \uc218\uc815\uc77c\uc744 \ud568\uaed8 "
    "\uc5b8\uae09\ud558\uc138\uc694(\uc608: '~\ud30c\uc77c(\uacbd\ub85c: ~, \uc218\uc815\uc77c: ~)\uc774 \uac00\uc7a5 \ucd5c\uadfc "
    "\uc790\ub8cc\uc785\ub2c8\ub2e4'). \uc5ec\ub7ec \uc790\ub8cc(\uba54\ubaa8, \ud30c\uc77c, \uc774\uba54\uc77c \ub4f1)\uac00 \ud568\uaed8 "
    "\uc81c\uacf5\ub41c \uacbd\uc6b0, \ud558\ub098\ub9cc \ubcf4\uc9c0 \ub9d0\uace0 \uc804\uccb4\ub97c \uc885\ud569\ud574\uc11c \ud604\uc7ac "
    "\uc9c4\ud589 \uc0c1\ud669\uc744 \ud310\ub2e8\ud558\uc138\uc694. "
    "\uc81c\uacf5\ub41c \uc790\ub8cc \uc911 \uc9c8\ubb38\uacfc \uad00\ub828\ub41c \ub0b4\uc6a9\uc774 \uc870\uae08\uc774\ub77c\ub3c4 "
    "\uc788\uc73c\uba74 found\ub97c true\ub85c \ud558\uace0, \uc790\ub8cc\uc5d0\uc11c \ud655\uc778\ub418\ub294 \ubc94\uc704\ub9cc "
    "\ub2f5\ubcc0\ud558\uc138\uc694. \uc81c\uacf5\ub41c \uc790\ub8cc\uac00 \uc9c8\ubb38\uacfc \uc644\uc804\ud788 \ubb34\uad00\ud560 "
    "\ub54c\ub9cc found\ub97c false\ub85c \ud558\uc138\uc694. "
    "\uc81c\uacf5\ub41c \uc790\ub8cc \uc911 \uc2e4\uc81c\ub85c \ub2f5\ubcc0\uc5d0 \uc0ac\uc6a9\ud55c \uac83\ub9cc used_sources\uc5d0 "
    "\ub098\uc5f4\ud558\uc138\uc694. \ub2f5\uc744 \ucc3e\uc9c0 \ubabb\ud588\ub2e4\uba74 found\ub97c false\ub85c, "
    "used_sources\ub294 \ube48 \ubc30\uc5f4\ub85c \ud558\uc138\uc694. "
    "답변할 때 아래 4가지 등급 중 하나를 판단해서 답변 앞에 표시하세요. "
    "[확실함] - 실제 문서 내용에 명시적으로 나온 정보를 근거로 답할 때. "
    "[확인 필요] - 파일명이나 제목으로만 관련성을 알 수 있고, 실제 내용은 확인 못한 경우 "
    "(예: '이 파일은 지원 대상이 아니라 내용을 자동으로 읽지 않았습니다'라고 표시된 자료를 근거로 답할 때). "
    "[추정] - 관련 있어 보이지만 질문에 직접적으로 답하는 내용은 아닌 경우. "
    "[확인불가] - 제공된 자료에서 전혀 찾을 수 없는 경우. "
    "여러 버전의 문서가 함께 제공된 경우, 질문에서 구체적으로 묻는 키워드(예: 특정 조항명, 특정 항목)가 "
    "실제로 어느 청크 텍스트에 그대로 등장하는지 하나하나 확인하고, 그 내용이 명시된 청크를 찾아서 정확히 인용하세요. "
    "단순히 관련 있어 보인다고 확인 없이 넘어가지 말고, 질문의 핵심 단어가 텍스트에 실제로 있는지 검토한 뒤 답변하세요. "
    '응답 형식은 반드시 {"confidence": "확실함|확인 필요|추정|확인불가", '
    '"answer": "...", "used_sources": [...]} JSON 객체로 하세요.'
)
SYSTEM_PROMPT += (
    "\n\n응답은 가능하면 아래 JSON 스키마를 사용하세요. "
    "질문에 가장 직접적으로 답하는 자료는 primary에 넣고, "
    "검색된 자료 중 질문과 관련은 있지만 직접적인 답은 아니거나 다른 파일/관점에서 나온 보조 정보는 related에 넣으세요. "
    "related는 최대 2개까지만 작성하고, 가장 관련도 높은 자료만 고르세요. "
    "related 항목은 실제 검색된 자료에 근거해야 하며 지어내지 마세요. "
    "related의 answer에는 단순히 '관련 자료로 검색됨'이라고 쓰지 말고, 이 파일이 왜 관련 있는지와 "
    "청크 텍스트에 실제로 어떤 내용이 담겨 있는지 1~2문장으로 구체적으로 요약하세요. "
    "목록형 답변은 검색 결과 개수나 top_k와 무관하게 최종 출력 단계에서만 간결하게 요약하세요. "
    "검색된 청크와 파일은 답변 근거를 판단할 때 그대로 모두 검토하되, 검색 자체를 줄이거나 생략하지 마세요. "
    "파일 목록이나 항목을 나열할 때는 primary.answer와 각 related(관련 자료)의 answer 모두에서 "
    "관련도가 높은 순으로 최대 5개까지만 구체적으로 나열하세요. 5개를 초과하면 더 나열하지 말고, "
    "반드시 '외 N개 파일이 더 있습니다. 전체 목록은 원본 폴더에서 확인하실 수 있습니다'라고 "
    "남은 개수를 요약하세요. primary.answer와 related 양쪽에 이 규칙을 각각 적용하세요. "
    "질문에 직접 답하는 정보가 없으면 어떤 내용은 확인되지만 질문의 핵심 정보는 언급되지 않는다고 명확히 쓰세요. "
    "완전히 무관한 질문이면 primary만 확인불가로 두고 related는 빈 배열로 두세요. "
    '{"primary": {"confidence": "확실함|확인 필요|추정|확인불가", "answer": "...", "used_sources": [...]}, '
    '"related": [{"confidence": "확실함|확인 필요|추정", "answer": "...", "used_sources": [...]}]}'
)
# The model writes only the answer body. Confidence, sources, and related material
# are deterministic UI metadata assembled from the retrieved chunks below.
SYSTEM_PROMPT = """당신은 퇴사자의 업무를 인수받은 후임자를 돕는 AI 어시스턴트입니다.
제공된 자료만 근거로 질문에 대한 답변 본문만 작성하세요. JSON, 신뢰도,
출처 목록, 관련 자료 목록은 작성하지 마세요. 파일 경로나 수정일을 임의로
추측하지 말고, 자료에 없는 내용은 지어내지 마세요.

동일한 근거를 여러 번 반복하지 말고 결론과 핵심 근거 1~2개만 간결하게
제시하세요. 확인되지 않는 내용은 장황하게 설명하지 말고
'제공된 자료에서 확인되지 않습니다.'라고 짧게 답하세요. 목록이 필요해도
핵심 항목을 최대 5개까지만 제시하세요."""

LOCATION_QUERY_KEYWORDS = ("\uc704\uce58", "\uacbd\ub85c", "\uc5b4\ub514", "\ud3f4\ub354")
DATE_RANKING_QUERY_KEYWORDS = (
    "\ucd5c\uadfc",
    "\uac00\uc7a5 \ucd5c\uadfc",
    "\ucd5c\uc2e0",
    "\uc5b8\uc81c",
    "\uc5bc\ub9c8 \uc804",
    "\uc218\uc815\ub41c",
    "\uc791\uc5c5\ub41c",
)
QUERY_STOPWORDS = {
    "\uc704\uce58",
    "\uacbd\ub85c",
    "\uc5b4\ub514",
    "\ud3f4\ub354",
    "\uc788\ub294",
    "\uc54c\ub824\uc918",
    "\uc54c\ub824\uc8fc\uc138\uc694",
    "\ud30c\uc77c",
    "\uad00\ub828",
    "\ucd5c\uadfc",
    "\uc9c4\ud589",
    "\uc0c1\ud669",
    "\ubb50\uc57c",
}
GENERIC_QUERY_TERMS = {
    "프로젝트",
    "결과물",
    "어디까지",
    "나왔어",
    "진행상황",
    "진행",
    "상황",
    "최신",
    "버전",
    "파일",
    "자료",
    "찾아줘",
    "알려줘",
    "관련",
    "있어",
    "가장",
    "제일",
    "받은",
    "몇",
    "개나",
    "개",
    "작업",
    "작업한",
    "완료",
    "완료된",
    "진행됐어",
    "진행된",
    "됐어",
    "했어",
    "인가",
    "어때",
    "각각",
    "세부내용",
    "세부",
    "내용",
    "이메일",
    "메일",
    "카톡",
    "카카오톡",
    "메신저",
    "대화",
}
MEDIA_EMAIL_TERMS = {"이메일", "메일"}
MEDIA_KAKAO_TERMS = {"카톡", "카카오톡", "메신저", "대화"}
KOREAN_PARTICLE_SUFFIXES = (
    "으로",
    "에서",
    "에게",
    "까지",
    "부터",
    "처럼",
    "보다",
    "으로",
    "이랑",
    "랑",
    "하고",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "에",
    "의",
    "와",
    "과",
    "도",
)
NOISE_TERM_SUFFIXES = (
    "됐어",
    "했어",
    "있어",
    "인가",
    "뭐야",
    "어때",
    "된",
    "한",
)


class _QueryEmbedding(list):
    def __init__(self, values: list[float], query: str, usage_tokens: int = 0) -> None:
        super().__init__(values)
        self.query = query
        self.usage_tokens = usage_tokens


@dataclass(slots=True)
class ChunkSearchIndex:
    chunks: list[dict]
    matrix: np.ndarray
    matrix_norms: np.ndarray
    source_token_index: dict[str, set[str]]
    source_tokens: list[set[str]]
    file_names_casefold: list[str]
    chunk_texts_casefold: list[str]


def build_chunk_search_index(
    chunks: list[dict],
    *,
    progress_callback: Callable[[str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    discard_embeddings: bool = False,
) -> ChunkSearchIndex:
    """Build immutable per-package search data that can be reused for every question."""
    dimension_counts: dict[int, int] = {}
    for chunk_index, chunk in enumerate(chunks, start=1):
        if chunk_index % 500 == 0:
            if cancel_check is not None and cancel_check():
                raise InterruptedError("package load cancelled")
            if progress_callback is not None:
                progress_callback("index", chunk_index)
            time.sleep(0.001)
        embedding = chunk.get("embedding")
        if embedding is None:
            embedding = chunk.get("embedding_vector")
        if not isinstance(embedding, list) or not embedding:
            continue
        dimension_counts[len(embedding)] = dimension_counts.get(len(embedding), 0) + 1

    embedding_size = max(dimension_counts, key=dimension_counts.get) if dimension_counts else 0
    valid_chunks = [
        chunk
        for chunk in chunks
        if embedding_size > 0
        and isinstance(chunk.get("embedding") or chunk.get("embedding_vector"), list)
        and len(chunk.get("embedding") or chunk.get("embedding_vector")) == embedding_size
    ]

    if valid_chunks:
        matrix = np.empty((len(valid_chunks), embedding_size), dtype=np.float32)
        matrix_norms = np.empty((len(valid_chunks),), dtype=np.float32)
        batch_size = 256
        for start in range(0, len(valid_chunks), batch_size):
            if cancel_check is not None and cancel_check():
                raise InterruptedError("package load cancelled")
            end = min(start + batch_size, len(valid_chunks))
            batch = np.asarray(
                [
                    chunk.get("embedding") or chunk.get("embedding_vector")
                    for chunk in valid_chunks[start:end]
                ],
                dtype=np.float32,
            )
            matrix[start:end] = batch
            matrix_norms[start:end] = np.linalg.norm(batch, axis=1)
            # The dense float32 matrix is the runtime representation. Keeping the
            # original JSON lists (Python float objects) multiplies memory usage and
            # can make Windows page heavily enough for the GUI to appear hung.
            if discard_embeddings:
                for chunk in valid_chunks[start:end]:
                    chunk.pop("embedding", None)
                    chunk.pop("embedding_vector", None)
            if progress_callback is not None:
                progress_callback("matrix", end)
            time.sleep(0.001)
    else:
        matrix = np.empty((0, 0), dtype=np.float32)
        matrix_norms = np.empty((0,), dtype=np.float32)

    source_tokens: list[set[str]] = []
    file_names_casefold: list[str] = []
    chunk_texts_casefold: list[str] = []
    for source_index, chunk in enumerate(valid_chunks, start=1):
        source_tokens.append(_source_tokens_for_chunk(chunk))
        file_names_casefold.append(_chunk_file_name(chunk).casefold())
        chunk_texts_casefold.append(
            str(chunk.get("chunk_text") or "").casefold()
        )
        if source_index % 100 == 0:
            if cancel_check is not None and cancel_check():
                raise InterruptedError("package load cancelled")
            if progress_callback is not None:
                progress_callback("metadata", source_index)
            time.sleep(0.001)

    source_token_index: dict[str, set[str]] = {}
    for source_index, (chunk, tokens) in enumerate(
        zip(valid_chunks, source_tokens), start=1
    ):
        if source_index % 100 == 0:
            if cancel_check is not None and cancel_check():
                raise InterruptedError("package load cancelled")
            if progress_callback is not None:
                progress_callback("metadata", source_index)
            time.sleep(0.001)
        source_key = str(
            chunk.get("source_path")
            or chunk.get("source_file")
            or chunk.get("chunk_id")
            or ""
        )
        if _is_internal_summary_chunk(chunk):
            continue
        for token in tokens:
            source_token_index.setdefault(token, set()).add(source_key)

    return ChunkSearchIndex(
        chunks=valid_chunks,
        matrix=matrix,
        matrix_norms=matrix_norms,
        source_token_index=source_token_index,
        source_tokens=source_tokens,
        file_names_casefold=file_names_casefold,
        chunk_texts_casefold=chunk_texts_casefold,
    )


def embed_query(query: str, license_code: str) -> list[float]:
    result = call_embeddings(license_code, [query])
    tokens = int(result.get("usage", {}).get("total_tokens", 0) or 0)
    return _QueryEmbedding(result["embeddings"][0], query, tokens)


def _temporary_api_failure_answer() -> dict:
    return {
        "confidence": "추정",
        "answer": "일시적으로 답변을 생성하지 못했습니다. 잠시 후 다시 시도해주세요.",
        "sources": [],
    }


def search_relevant_chunks(
    query_embedding: list[float],
    chunks: list[dict] | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_similarity: float = 0.3,
    query: str = "",
    search_index: ChunkSearchIndex | None = None,
) -> list[dict]:
    if search_index is None:
        search_index = build_chunk_search_index(chunks or [])
    if not query_embedding or not search_index.chunks or top_k <= 0:
        return []

    query_vector = np.asarray(query_embedding, dtype=np.float32)
    if (
        query_vector.ndim != 1
        or search_index.matrix.ndim != 2
        or search_index.matrix.shape[1] != len(query_vector)
    ):
        return []

    valid_chunks = search_index.chunks
    matrix = search_index.matrix
    query_norm = np.linalg.norm(query_vector)
    denominator = search_index.matrix_norms * query_norm
    similarities = np.divide(
        matrix @ query_vector,
        denominator,
        out=np.zeros_like(search_index.matrix_norms, dtype=np.float32),
        where=denominator != 0,
    )

    query_text = query or str(getattr(query_embedding, "query", ""))
    query_terms = _extract_indexed_query_terms(
        query_text,
        valid_chunks,
        source_index=search_index.source_token_index,
    )
    eligible_indices = np.flatnonzero(similarities >= min_similarity)
    exact_file_name_indices = _find_exact_file_name_keyword_indices(
        query_text,
        valid_chunks,
        similarities,
        query_terms=query_terms,
        file_names_casefold=search_index.file_names_casefold,
    )
    file_keyword_indices = _find_file_name_keyword_indices(
        query_text,
        valid_chunks,
        query_terms=query_terms,
        source_tokens=search_index.source_tokens,
    )
    content_keyword_indices = _find_content_keyword_indices(
        query_text,
        valid_chunks,
        query_terms=query_terms,
        source_tokens=search_index.source_tokens,
        chunk_texts_casefold=search_index.chunk_texts_casefold,
    )
    top_indices = _build_additive_candidate_indices(
        eligible_indices,
        similarities,
        top_k,
        exact_file_name_indices,
        content_keyword_indices,
        max_total=MAX_SEARCH_RESULTS,
    )
    if not top_indices:
        return []

    results = [
        _build_search_result(valid_chunks[int(index)], float(similarities[int(index)]))
        for index in top_indices
    ]
    summary = _build_keyword_summary_result(query_text, file_keyword_indices, valid_chunks)
    if summary is not None:
        return [summary, *results]
    return results


def is_date_ranking_query(query: str) -> bool:
    normalized = query.casefold()
    return any(keyword in normalized for keyword in DATE_RANKING_QUERY_KEYWORDS)


def answer_date_ranking_query(
    query: str,
    all_chunks: list[dict],
    top_n: int = 5,
) -> dict:
    requested_count = _extract_requested_count(query) or top_n
    records_by_source: dict[str, dict] = {}
    for chunk in all_chunks:
        record = _build_date_source_record(chunk)
        if record is None:
            continue
        key = record["source_path"] or record["file_name"]
        current = records_by_source.get(key)
        if current is None or record["_modified_dt"] > current["_modified_dt"]:
            records_by_source[key] = record

    records = list(records_by_source.values())
    if not _is_memo_query(query):
        records = [record for record in records if not _is_memo_record(record)]
    media_filter = _get_media_filter(query)
    if media_filter is not None:
        records = [record for record in records if media_filter(record)]
    keyword_terms = _extract_indexed_query_terms_from_records(query, records)
    if keyword_terms:
        records = _filter_records_by_best_token_match(records, keyword_terms)
        if not records:
            return {
                "confidence": "확인불가",
                "answer": "질문과 일치하는 자료를 찾지 못했습니다.",
                "sources": [],
            }

    ranked = sorted(
        records,
        key=lambda record: record["_modified_dt"],
        reverse=True,
    )[:requested_count]
    sources = [
        _format_source(
            {
                "file_name": record["file_name"],
                "source_path": record["source_path"],
            }
        )
        for record in ranked
    ]
    return {
        "confidence": "확실함",
        "answer": _format_date_ranking_answer(query, ranked),
        "sources": sources,
    }


def is_compound_query(query: str) -> bool:
    return len(_split_compound_query(query)) >= 2


def answer_compound_query(
    query: str,
    all_chunks: list[dict],
    api_key: str,
) -> dict:
    parts = _split_compound_query(query)
    if len(parts) < 2:
        return {
            "confidence": "확인불가",
            "answer": "질문과 일치하는 자료를 찾지 못했습니다.",
            "sources": [],
        }

    section_lines: list[str] = []
    all_sources: list[str] = []
    confidences: list[str] = []
    for index, part in enumerate(parts, start=1):
        if is_date_ranking_query(part):
            result = answer_date_ranking_query(part, all_chunks)
        else:
            query_embedding = embed_query(part, api_key)
            relevant_chunks = search_relevant_chunks(query_embedding, all_chunks, query=part)
            result = generate_answer(part, relevant_chunks, api_key)
        confidence = str(result.get("confidence") or "추정")
        answer = str(result.get("answer") or "").strip()
        section_lines.append(f"{index}. {part}: [{confidence}] {answer}")
        confidences.append(confidence)
        for source in result.get("sources", []):
            if source not in all_sources:
                all_sources.append(source)

    return {
        "confidence": _merge_confidences(confidences),
        "answer": "\n".join(section_lines),
        "sources": all_sources,
    }


def generate_answer(
    query: str,
    relevant_chunks: list[dict],
    license_code: str,
    on_answer_delta: Callable[[str], None] | None = None,
) -> dict:
    if not relevant_chunks or not _has_relevant_evidence(query, relevant_chunks):
        return _not_found_answer()

    try:
        response = call_chat_stream(
            license_code,
            "chat",
            CHAT_MODEL,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_message(query, relevant_chunks)},
            ],
            max_tokens=ANSWER_MAX_COMPLETION_TOKENS,
            reasoning_effort=CHAT_REASONING_EFFORT,
            on_delta=on_answer_delta,
        )
    except InsufficientCreditsError:
        raise
    except AiProxyError:
        return _temporary_api_failure_answer()

    answer = response["content"].strip()
    usage = response.get("usage", {}) or {}
    usage_result = {
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
    }
    if not answer:
        answer = "제공된 자료에서 확인되지 않습니다."
    if (
        _looks_unavailable_answer(answer)
        and _best_filename_match(query, relevant_chunks) < FILENAME_PARTIAL_MATCH_THRESHOLD
        and _best_query_overlap(query, relevant_chunks) < 0.34
    ):
        unavailable = _not_found_answer()
        unavailable["_usage"] = usage_result
        return unavailable
    confidence = _confidence_from_chunks(relevant_chunks)
    if confidence in {"확인 필요", "확인불가", "확인 불가"}:
        sources: list[str] = []
        related: list[dict] = []
    else:
        primary_chunks, related_chunks = _split_local_evidence(query, relevant_chunks)
        sources = _unique_sources(primary_chunks)
        related = _build_local_related(related_chunks)
    return {
        "confidence": confidence,
        "answer": answer,
        "sources": sources,
        "related": related,
        "_usage": usage_result,
    }


def _not_found_answer() -> dict:
    return {
        "confidence": "확인불가",
        "answer": "관련된 자료를 찾을 수 없습니다.",
        "sources": [],
        "related": [],
        "_usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }


def _confidence_from_chunks(chunks: list[dict]) -> str:
    top_score = max((float(chunk.get("similarity_score") or 0) for chunk in chunks), default=0.0)
    if top_score >= CONFIDENCE_CERTAIN_THRESHOLD:
        return "확실함"
    if top_score >= CONFIDENCE_ESTIMATED_THRESHOLD:
        return "추정"
    return "확인 필요"


def _has_relevant_evidence(query: str, chunks: list[dict]) -> bool:
    if _is_external_fact_query(query):
        return False
    real_chunks = [chunk for chunk in chunks if not _is_internal_summary_chunk(chunk)]
    top_score = max((float(chunk.get("similarity_score") or 0) for chunk in real_chunks), default=0.0)
    filename_match = _best_filename_match(query, real_chunks)
    if _looks_like_explicit_file_query(query) and filename_match < FILENAME_PARTIAL_MATCH_THRESHOLD:
        return False
    return filename_match >= FILENAME_PARTIAL_MATCH_THRESHOLD or top_score >= UNRELATED_SIMILARITY_THRESHOLD


def _is_external_fact_query(query: str) -> bool:
    normalized = re.sub(r"\s+", " ", query.casefold())
    return (
        "날씨" in normalized
        or "주가" in normalized
        or ("업계" in normalized and ("몇 등" in normalized or "순위" in normalized))
    )


def _looks_like_explicit_file_query(query: str) -> bool:
    return bool(
        re.search(r"\.[a-z0-9]{1,5}\b", query, flags=re.IGNORECASE)
        or re.search(r"[_-].*\d{4,}", query)
        or ("파일" in query and any(char.isdigit() for char in query))
    )


def _best_query_overlap(query: str, chunks: list[dict]) -> float:
    stopwords = set(QUERY_STOPWORDS) | set(GENERIC_QUERY_TERMS)
    stopwords.update({"회사", "이", "그", "어때", "얼마", "몇", "누구", "다음", "이번"})
    terms = [
        term
        for term in (_strip_common_suffix(value) for value in _extract_query_terms(query))
        if len(term) >= 2 and term not in stopwords and not _is_noise_term(term)
    ]
    terms = _deduplicate_terms(terms)
    if not terms:
        return 0.0
    searchable = " ".join(
        f"{chunk.get('file_name', '')} {str(chunk.get('chunk_text') or '')[:5000]}".casefold()
        for chunk in chunks
    )
    return sum(term.casefold() in searchable for term in terms) / len(terms)


def _best_filename_match(query: str, chunks: list[dict]) -> float:
    query_key = _filename_match_key(query)
    if not query_key:
        return 0.0
    best = 0.0
    for chunk in chunks:
        name_key = _filename_match_key(str(chunk.get("file_name") or ""))
        if not name_key:
            continue
        if name_key in query_key or query_key in name_key:
            return 1.0
        best = max(best, SequenceMatcher(None, query_key, name_key).ratio())
    return best


def _filename_match_key(value: str) -> str:
    value = re.sub(r"\.[a-z0-9]{1,5}$", "", value.casefold())
    value = re.sub(r"(?:내용|알려줘|알려주세요|파일|시트|에서|에는|의)", "", value)
    return re.sub(r"[^0-9a-z가-힣]", "", value)


def _split_local_evidence(query: str, chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    unique: list[dict] = []
    seen: set[str] = set()
    for chunk in chunks:
        if _is_internal_summary_chunk(chunk):
            continue
        source = _format_source(chunk)
        if not source or source in seen:
            continue
        seen.add(source)
        unique.append(chunk)
    unique.sort(
        key=lambda chunk: (
            _best_filename_match(query, [chunk]),
            _best_query_overlap(query, [chunk]),
            float(chunk.get("similarity_score") or 0),
        ),
        reverse=True,
    )
    return unique[:3], unique[3:4]


def _build_local_related(chunks: list[dict]) -> list[dict]:
    return [
        {
            "confidence": _confidence_from_chunks([chunk]),
            "answer": _format_source(chunk),
            "sources": [],
        }
        for chunk in chunks[:MAX_RELATED_ANSWERS]
    ]


def _extract_streamed_primary_answer(raw_content: str) -> str:
    """Return the complete, currently decodable prefix of the primary answer JSON string."""
    primary_match = re.search(r'"primary"\s*:\s*\{', raw_content)
    search_start = primary_match.end() if primary_match else 0
    answer_match = re.search(r'"answer"\s*:\s*"', raw_content[search_start:])
    if answer_match is None:
        return ""

    start = search_start + answer_match.end()
    decoded: list[str] = []
    index = start
    escapes = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    while index < len(raw_content):
        character = raw_content[index]
        if character == '"':
            break
        if character != "\\":
            decoded.append(character)
            index += 1
            continue
        if index + 1 >= len(raw_content):
            break
        escape = raw_content[index + 1]
        if escape == "u":
            code = raw_content[index + 2 : index + 6]
            if len(code) < 4 or not all(
                character in "0123456789abcdefABCDEF" for character in code
            ):
                break
            decoded.append(chr(int(code, 16)))
            index += 6
            continue
        decoded.append(escapes.get(escape, escape))
        index += 2
    return "".join(decoded)


def _build_search_result(chunk: dict, similarity_score: float) -> dict:
    metadata = chunk.get("source_metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    source_path = str(
        metadata.get("source_path")
        or chunk.get("source_file")
        or chunk.get("chunk_id")
        or ""
    )
    file_name = str(
        metadata.get("file_name")
        or _last_path_part(source_path)
        or chunk.get("source_file")
        or "\uc54c \uc218 \uc5c6\ub294 \ucd9c\ucc98"
    )
    modified_at = str(metadata.get("modified_at") or chunk.get("modified_at") or "")
    return {
        "chunk_text": str(chunk.get("chunk_text") or ""),
        "source_path": source_path,
        "file_name": file_name,
        "modified_at": modified_at,
        "extraction_status": str(metadata.get("extraction_status") or ""),
        "similarity_score": similarity_score,
    }


def _build_keyword_summary_result(
    query: str,
    keyword_indices: list[int],
    chunks: list[dict],
) -> dict | None:
    query_terms = _extract_indexed_query_terms(query, chunks)
    if not query_terms or not keyword_indices:
        return None

    records_by_source: dict[str, dict] = {}
    for index in keyword_indices:
        chunk = chunks[int(index)]
        result = _build_search_result(chunk, 1.0)
        source_key = result["source_path"] or result["file_name"]
        if source_key not in records_by_source:
            records_by_source[source_key] = result

    if len(records_by_source) <= 10:
        return None

    records = sorted(records_by_source.values(), key=lambda record: record["file_name"])
    preview_lines = [
        f"- {record['file_name']} (경로: {record['source_path']})"
        for record in records[:80]
    ]
    omitted = len(records) - len(preview_lines)
    if omitted > 0:
        preview_lines.append(f"- 외 {omitted}개")

    keyword_text = ", ".join(query_terms)
    return {
        "chunk_text": (
            f"키워드 [{keyword_text}]와 관련된 파일은 총 {len(records)}개입니다.\n"
            + "\n".join(preview_lines)
        ),
        "source_path": f"keyword-summary:{keyword_text}",
        "file_name": f"키워드 [{keyword_text}] 관련 파일 목록",
        "modified_at": "",
        "similarity_score": 1.0,
    }


def _build_user_message(query: str, relevant_chunks: list[dict]) -> str:
    lines = ["\uc9c8\ubb38:", query, "", "\uad00\ub828 \uc790\ub8cc:"]
    if not relevant_chunks:
        lines.append("(\uac80\uc0c9\ub41c \uad00\ub828 \uc790\ub8cc \uc5c6\uc74c)")
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for chunk in relevant_chunks:
        key = (
            str(chunk.get("file_name") or ""),
            str(chunk.get("source_path") or ""),
            str(chunk.get("modified_at") or ""),
        )
        grouped.setdefault(key, []).append(chunk)

    chunk_number = 0
    for file_number, ((file_name, source_path, modified_at), file_chunks) in enumerate(
        grouped.items(), start=1
    ):
        lines.extend(
            [
                f"[파일 {file_number}] {file_name}",
                f"경로: {source_path}" if source_path else "경로: 확인되지 않음",
                f"수정일: {modified_at}" if modified_at else "수정일: 확인되지 않음",
            ]
        )
        for chunk in file_chunks:
            chunk_number += 1
            lines.extend([f"[청크 {chunk_number}]", str(chunk.get("chunk_text") or "")])
        lines.append("")
    lines.extend(
        [
            "답변 본문만 작성하세요. JSON, 신뢰도, 출처, 관련 자료 목록은 쓰지 마세요.",
            "파일을 언급해야 한다면 위에 제공된 정확한 파일명·경로만 사용하고 경로를 추측하지 마세요.",
        ]
    )
    return "\n".join(lines)


def _extract_primary_answer(parsed: Any) -> dict:
    if isinstance(parsed, dict) and isinstance(parsed.get("primary"), dict):
        return parsed["primary"]
    if isinstance(parsed, dict):
        return parsed
    return {"confidence": "확인불가", "answer": "", "used_sources": []}


def _normalize_related_answers(value: Any, chunks: list[dict]) -> list[dict]:
    if not isinstance(value, list):
        return []

    related: list[dict] = []
    for item in value[:MAX_RELATED_ANSWERS]:
        if not isinstance(item, dict):
            continue
        confidence = _normalize_confidence(item.get("confidence"))
        if confidence == "확인불가":
            continue
        answer = str(item.get("answer") or "").strip()
        if not answer:
            continue
        sources = _normalize_sources(item.get("used_sources"), chunks)
        if not sources:
            continue
        related.append(
            {
                "confidence": confidence,
                "answer": answer,
                "sources": sources,
            }
        )
    return related


def _supplement_related_from_filename_matches(
    query: str,
    chunks: list[dict],
    primary_sources: list[str],
    related: list[dict],
) -> list[dict]:
    if len(related) >= MAX_RELATED_ANSWERS:
        return related[:MAX_RELATED_ANSWERS]

    used_sources = set(primary_sources)
    for item in related:
        used_sources.update(item.get("sources", []))

    query_terms = _extract_indexed_query_terms(query, chunks)
    if not query_terms:
        return related

    candidates: list[tuple[int, float, dict]] = []
    for chunk in chunks:
        if _is_internal_summary_chunk(chunk):
            continue
        source = _format_source(chunk)
        if not source or source in used_sources:
            continue
        file_name = str(chunk.get("file_name") or "").casefold()
        match_count = sum(1 for term in query_terms if term in file_name)
        if match_count < 2:
            continue
        candidates.append((match_count, float(chunk.get("similarity_score") or 0), chunk))

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    seen_sources = set(used_sources)
    for _, _, chunk in candidates:
        source = _format_source(chunk)
        if not source or source in seen_sources:
            continue
        seen_sources.add(source)
        file_name = str(chunk.get("file_name") or "").strip()
        related.append(
            {
                "confidence": "추정",
                "answer": _build_related_summary_from_chunk(query, chunk, file_name),
                "sources": [source],
            }
        )
        if len(related) >= MAX_RELATED_ANSWERS:
            break
    return related[:MAX_RELATED_ANSWERS]


def _build_related_summary_from_chunk(query: str, chunk: dict, file_name: str) -> str:
    text = _compact_text(str(chunk.get("chunk_text") or ""))
    query_terms = _extract_indexed_query_terms(query, [chunk])
    matched_terms = [
        term
        for term in query_terms
        if term.casefold() in text.casefold() or term.casefold() in file_name.casefold()
    ][:3]

    if text:
        preview = _truncate_text(text, 180)
        if matched_terms:
            return (
                f"{file_name}에는 {', '.join(matched_terms)}와 관련된 내용으로 "
                f"'{preview}'가 포함되어 있습니다. 다만 primary 답변의 직접 근거는 아니므로, "
                "질문의 핵심 정보가 명시되어 있는지는 원문에서 함께 확인하세요."
            )
        return (
            f"{file_name}에는 '{preview}' 내용이 포함되어 있습니다. "
            "질문과 같은 주제권의 보조 자료이지만 primary 답변의 직접 근거는 아닙니다."
        )

    return (
        f"{file_name}은 파일명이 질문 키워드와 일치하지만, 검색된 청크에 요약할 본문 텍스트가 없습니다. "
        "내용 자동 추출 여부를 원문에서 확인하세요."
    )


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _normalize_confidence(value: Any) -> str:
    confidence = str(value or "").strip()
    allowed = {"확실함", "확인 필요", "추정", "확인불가"}
    return confidence if confidence in allowed else "추정"


def _correct_unavailable_placeholder_answer(query: str, answer: str, chunks: list[dict]) -> dict | None:
    real_chunks = _target_chunks_for_placeholder_correction(query, chunks)
    strong_chunks = [
        chunk
        for chunk in real_chunks
        if float(chunk.get("similarity_score") or 0) >= 0.5
    ]
    if not strong_chunks:
        return None
    if not all(_is_placeholder_chunk(chunk) for chunk in strong_chunks):
        return None

    sources = _unique_sources(strong_chunks)[:10]
    file_names = _unique_file_names(strong_chunks)[:10]
    if not file_names:
        return None
    file_list = ", ".join(file_names)
    corrected_answer = (
        f"{file_list}이(가) 검색되었으나 자동으로 내용을 읽을 수 없는 형식입니다. "
        "제목을 참고하시고 원본 파일을 직접 확인하세요."
    )
    return {
        "confidence": "확인 필요",
        "answer": corrected_answer if _looks_unavailable_answer(answer) else answer,
        "sources": sources,
    }


def _target_chunks_for_placeholder_correction(query: str, chunks: list[dict]) -> list[dict]:
    real_chunks = [chunk for chunk in chunks if not _is_internal_summary_chunk(chunk)]
    target_terms = _extract_indexed_query_terms(query, real_chunks)
    if not target_terms:
        return real_chunks

    matched = _filter_chunks_by_best_source_token_match(real_chunks, target_terms)
    return matched or real_chunks


def _is_placeholder_chunk(chunk: dict) -> bool:
    status = str(chunk.get("extraction_status") or "")
    if status in {"not_in_whitelist", "skipped_large_file"}:
        return True
    text = str(chunk.get("chunk_text") or "")
    return (
        "지원 대상이 아니라 내용을 자동으로 읽지 않았습니다" in text
        or "용량이 커서 내용을 자동으로 읽지 않았습니다" in text
        or "자동으로 내용을 읽을 수 없는 형식" in text
    )


def _looks_unavailable_answer(answer: str) -> bool:
    normalized = _compact_text(answer)
    if not normalized:
        return True
    stripped = normalized.rstrip(".!?。 ")
    unavailable_messages = (
        "제공된 자료에서 확인되지 않습니다",
        "관련된 자료를 찾을 수 없습니다",
        "질문과 일치하는 자료를 찾지 못했습니다",
    )
    return any(
        stripped == message.rstrip(".!?。 ") for message in unavailable_messages
    )


def _unique_file_names(chunks: list[dict]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        file_name = str(chunk.get("file_name") or "").strip()
        if not file_name or file_name in seen:
            continue
        seen.add(file_name)
        names.append(file_name)
    return names


def _normalize_sources(value: Any, chunks: list[dict]) -> list[str]:
    if not isinstance(value, list):
        return []
    source_lookup = _build_source_lookup(chunks)
    sources: list[str] = []
    seen: set[str] = set()
    for item in value:
        source = str(item).strip()
        if not source:
            continue
        formatted = _resolve_source(source, source_lookup, chunks)
        if not formatted:
            continue
        if formatted in seen:
            continue
        seen.add(formatted)
        sources.append(formatted)
    if not sources and any(_is_internal_summary_chunk(chunk) for chunk in chunks):
        return _unique_sources(chunks)[:10]
    return sources


def _unique_sources(chunks: list[dict]) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        if _is_internal_summary_chunk(chunk):
            continue
        source = _format_source(chunk)
        if not source or source in seen:
            continue
        seen.add(source)
        sources.append(source)
    return sources


def _build_additive_candidate_indices(
    eligible_indices: np.ndarray,
    similarities: np.ndarray,
    top_k: int,
    exact_file_name_indices: list[int],
    content_keyword_indices: list[int],
    max_total: int = 30,
) -> list[int]:
    base_indices = sorted(
        (int(index) for index in eligible_indices),
        key=lambda index: float(similarities[index]),
        reverse=True,
    )[:top_k]

    exact_indices = _sort_indices_by_similarity(
        exact_file_name_indices,
        similarities,
    )[:5]
    content_indices = _sort_indices_by_similarity(
        content_keyword_indices,
        similarities,
    )[:10]
    return _merge_index_lists_limited(
        max_total,
        content_indices[:5],
        base_indices,
        exact_indices,
    )


def _sort_indices_by_similarity(indices: list[int], similarities: np.ndarray) -> list[int]:
    return sorted(
        (int(index) for index in indices),
        key=lambda index: float(similarities[index]),
        reverse=True,
    )


def _merge_index_lists_limited(max_count: int, *index_lists: list[int]) -> list[int]:
    merged: list[int] = []
    seen: set[int] = set()
    for index_list in index_lists:
        for index in index_list:
            if index in seen:
                continue
            seen.add(index)
            merged.append(index)
            if len(merged) >= max_count:
                return merged
    return merged


def _merge_candidate_indices(
    eligible_indices: np.ndarray,
    keyword_indices: list[int],
    similarities: np.ndarray,
    top_k: int,
    chunks: list[dict] | None = None,
    content_boost_terms: list[str] | None = None,
    priority_indices: list[int] | None = None,
    secondary_priority_indices: list[int] | None = None,
) -> list[int]:
    keyword_set = set(keyword_indices)
    priority_set = set(priority_indices or [])
    secondary_priority_set = set(secondary_priority_indices or [])
    combined = set(int(index) for index in eligible_indices)
    combined.update(keyword_set)
    content_boost_terms = content_boost_terms or []

    def sort_key(index: int) -> tuple[int, int, int, int, float]:
        content_match_count = 0
        if chunks is not None and content_boost_terms:
            chunk_text = str(chunks[index].get("chunk_text") or "").casefold()
            content_match_count = sum(1 for term in content_boost_terms if term in chunk_text)
        return (
            index in priority_set,
            index in secondary_priority_set,
            index in keyword_set,
            content_match_count,
            float(similarities[index]),
        )

    ranked = sorted(
        combined,
        key=sort_key,
        reverse=True,
    )
    return ranked[:top_k]


def _extract_content_boost_terms(query: str, chunks: list[dict]) -> list[str]:
    source_terms = set(_extract_indexed_query_terms(query, chunks))
    return [
        term
        for term in _extract_keyword_match_terms(query)
        if term not in source_terms
    ]


def _find_exact_file_name_keyword_indices(
    query: str,
    chunks: list[dict],
    similarities: np.ndarray,
    limit: int = 12,
    *,
    query_terms: list[str] | None = None,
    file_names_casefold: list[str] | None = None,
) -> list[int]:
    query_terms = (
        query_terms
        if query_terms is not None
        else _extract_indexed_query_terms(query, chunks)
    )
    if not query_terms:
        return []

    matches: list[tuple[int, float, int]] = []
    for index, chunk in enumerate(chunks):
        file_name = (
            file_names_casefold[index]
            if file_names_casefold is not None
            else _chunk_file_name(chunk).casefold()
        )
        match_count = sum(1 for term in query_terms if term in file_name)
        if match_count > 0:
            matches.append((match_count, float(similarities[index]), index))

    if not matches:
        return []

    best_count = max(match_count for match_count, _, _ in matches)
    exact_matches = [
        (similarity, index)
        for match_count, similarity, index in matches
        if match_count == best_count
    ]
    exact_matches.sort(key=lambda item: item[0], reverse=True)
    return [index for _, index in exact_matches[:limit]]


def _find_file_name_keyword_indices(
    query: str,
    chunks: list[dict],
    *,
    query_terms: list[str] | None = None,
    source_tokens: list[set[str]] | None = None,
) -> list[int]:
    if not query:
        return []

    query_terms = (
        query_terms
        if query_terms is not None
        else _extract_indexed_query_terms(query, chunks)
    )
    if not query_terms:
        return []

    matches: list[tuple[int, int, int]] = []
    for index, chunk in enumerate(chunks):
        metadata = chunk.get("source_metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        source_path = str(
            metadata.get("source_path")
            or chunk.get("source_file")
            or chunk.get("chunk_id")
            or ""
        )
        file_name = str(
            metadata.get("file_name")
            or _last_path_part(source_path)
            or chunk.get("source_file")
            or ""
        )
        chunk_source_tokens = (
            source_tokens[index]
            if source_tokens is not None
            else _source_tokens_for_text(f"{file_name} {source_path}")
        )
        match_count = sum(1 for term in query_terms if term in chunk_source_tokens)
        if match_count > 0:
            matches.append((match_count, len(file_name), index))

    if not matches:
        return []

    matches.sort(key=lambda item: (-item[0], item[1], item[2]))
    best_count = matches[0][0]
    return [index for match_count, _, index in matches if match_count == best_count]


def _find_exact_text_keyword_indices(
    query: str,
    chunks: list[dict],
    *,
    query_terms: list[str] | None = None,
    source_tokens: list[set[str]] | None = None,
    chunk_texts_casefold: list[str] | None = None,
) -> list[int]:
    terms = (
        query_terms
        if query_terms is not None
        else _extract_indexed_query_terms(query, chunks)
    )
    if len(terms) < 2:
        return []
    source_matched = _filter_chunks_by_best_source_token_match(chunks, terms)
    if not source_matched:
        return []

    matches: list[tuple[int, int]] = []
    source_matched_ids = {id(chunk) for chunk in source_matched}
    for index, chunk in enumerate(chunks):
        if id(chunk) not in source_matched_ids:
            continue
        chunk_source_tokens = (
            source_tokens[index]
            if source_tokens is not None
            else _source_tokens_for_text(_chunk_source_text(chunk))
        )
        chunk_text = (
            chunk_texts_casefold[index]
            if chunk_texts_casefold is not None
            else str(chunk.get("chunk_text") or "").casefold()
        )
        source_terms = [term for term in terms if term in chunk_source_tokens]
        content_terms = [
            term
            for term in terms
            if term not in source_terms
            and term in chunk_text
        ]
        if source_terms and content_terms:
            matches.append((len(source_terms) + len(content_terms), index))

    matches.sort(key=lambda item: (-item[0], item[1]))
    return [index for _, index in matches]


def _find_content_keyword_indices(
    query: str,
    chunks: list[dict],
    *,
    query_terms: list[str] | None = None,
    source_tokens: list[set[str]] | None = None,
    chunk_texts_casefold: list[str] | None = None,
) -> list[int]:
    matched_source_terms = (
        query_terms if query_terms is not None else _extract_indexed_query_terms(query, chunks)
    )
    source_term_set = set(matched_source_terms)
    content_terms = [
        term for term in _extract_keyword_match_terms(query) if term not in source_term_set
    ]
    if not matched_source_terms or not content_terms:
        return []

    minimum_source_matches = max(1, len(matched_source_terms) - 1)
    matches: list[tuple[int, int, int]] = []
    for index, chunk in enumerate(chunks):
        chunk_source_tokens = (
            source_tokens[index]
            if source_tokens is not None
            else _source_tokens_for_chunk(chunk)
        )
        source_match_count = sum(
            1 for term in matched_source_terms if term in chunk_source_tokens
        )
        if source_match_count < minimum_source_matches:
            continue

        chunk_text = (
            chunk_texts_casefold[index]
            if chunk_texts_casefold is not None
            else str(chunk.get("chunk_text") or "").casefold()
        )
        content_match_count = sum(1 for term in content_terms if term in chunk_text)
        if content_match_count <= 0:
            continue
        matches.append((content_match_count, source_match_count, index))

    matches.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [index for _, _, index in matches]


def _merge_index_lists(*index_lists: list[int]) -> list[int]:
    merged: list[int] = []
    seen: set[int] = set()
    for index_list in index_lists:
        for index in index_list:
            if index in seen:
                continue
            seen.add(index)
            merged.append(index)
    return merged


def _extract_indexed_query_terms(
    query: str,
    chunks: list[dict],
    *,
    source_index: dict[str, set[str]] | None = None,
) -> list[str]:
    source_index = (
        source_index if source_index is not None else _build_source_token_index(chunks)
    )
    return [
        term
        for term in _extract_keyword_match_terms(query)
        if term in source_index
    ]


def _extract_indexed_query_terms_from_records(query: str, records: list[dict]) -> list[str]:
    source_index: dict[str, set[str]] = {}
    for record in records:
        source_key = str(record.get("source_path") or record.get("file_name") or "")
        for token in _source_tokens_for_text(f"{record.get('file_name', '')} {record.get('source_path', '')}"):
            source_index.setdefault(token, set()).add(source_key)
    return [
        term
        for term in _extract_keyword_match_terms(query)
        if term in source_index
    ]


def _build_source_token_index(chunks: list[dict]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for chunk in chunks:
        if _is_internal_summary_chunk(chunk):
            continue
        source_key = str(chunk.get("source_path") or chunk.get("source_file") or chunk.get("chunk_id") or "")
        for token in _source_tokens_for_chunk(chunk):
            index.setdefault(token, set()).add(source_key)
    return index


def _source_tokens_for_chunk(chunk: dict) -> set[str]:
    return _source_tokens_for_text(f"{_chunk_file_name(chunk)} {_chunk_source_path(chunk)}")


def _chunk_file_name(chunk: dict) -> str:
    metadata = chunk.get("source_metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    source_path = _chunk_source_path(chunk)
    file_name = str(
        metadata.get("file_name")
        or chunk.get("file_name")
        or _last_path_part(source_path)
        or ""
    )
    return file_name


def _chunk_source_path(chunk: dict) -> str:
    metadata = chunk.get("source_metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    return str(
        metadata.get("source_path")
        or chunk.get("source_path")
        or chunk.get("source_file")
        or ""
    )


def _source_tokens_for_text(value: str) -> set[str]:
    normalized = value.casefold()
    normalized = re.sub(r"[\s_()\[\]{}.,;:+/\\|\"'<>!?~]+", " ", normalized)
    tokens: set[str] = set()
    for raw_token in normalized.split():
        token = _strip_common_suffix(raw_token.strip())
        if len(token) < 2:
            continue
        if token in QUERY_STOPWORDS or token in GENERIC_QUERY_TERMS:
            continue
        if token in {"docx", "pptx", "ppt", "txt", "pdf", "xlsx", "xls", "hwp", "hwpx", "eml", "zip"}:
            continue
        if token.isdigit():
            continue
        tokens.add(token)
    return tokens


def _filter_chunks_by_best_source_token_match(chunks: list[dict], terms: list[str]) -> list[dict]:
    scored: list[tuple[int, int, dict]] = []
    for index, chunk in enumerate(chunks):
        source_tokens = _source_tokens_for_chunk(chunk)
        match_count = sum(1 for term in terms if term in source_tokens)
        if match_count > 0:
            scored.append((match_count, index, chunk))
    if not scored:
        return []
    best_count = max(match_count for match_count, _, _ in scored)
    return [chunk for match_count, _, chunk in scored if match_count == best_count]


def _filter_records_by_best_token_match(records: list[dict], terms: list[str]) -> list[dict]:
    scored: list[tuple[int, int, dict]] = []
    for index, record in enumerate(records):
        source_tokens = _source_tokens_for_text(f"{record.get('file_name', '')} {record.get('source_path', '')}")
        match_count = sum(1 for term in terms if term in source_tokens)
        if match_count > 0:
            scored.append((match_count, index, record))
    if not scored:
        return []
    best_count = max(match_count for match_count, _, _ in scored)
    return [record for match_count, _, record in scored if match_count == best_count]


def _chunk_source_text(chunk: dict) -> str:
    metadata = chunk.get("source_metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    source_path = str(metadata.get("source_path") or chunk.get("source_path") or chunk.get("source_file") or "")
    file_name = str(metadata.get("file_name") or chunk.get("file_name") or _last_path_part(source_path) or "")
    return f"{file_name} {source_path}".casefold()


def _extract_query_terms(query: str) -> list[str]:
    terms: list[str] = []
    current: list[str] = []
    for char in query.casefold():
        if char.isalnum():
            current.append(char)
            continue
        if current:
            terms.append("".join(current))
            current = []
    if current:
        terms.append("".join(current))
    return [
        term
        for term in terms
        if len(term) >= 2 and term not in QUERY_STOPWORDS
    ]


def _extract_keyword_match_terms(query: str) -> list[str]:
    terms: list[str] = []
    for term in _extract_query_terms(query):
        normalized = _strip_common_suffix(term)
        if len(normalized) < 2:
            continue
        if _is_noise_term(normalized):
            continue
        if normalized in QUERY_STOPWORDS or normalized in GENERIC_QUERY_TERMS:
            continue
        if any(char.isdigit() for char in normalized):
            continue
        terms.append(normalized)
    return _deduplicate_terms(terms)


def _split_compound_query(query: str) -> list[str]:
    normalized = query.strip()
    if not normalized:
        return []
    if "각각" not in normalized and "그리고" not in normalized:
        return []
    if not any(ending in normalized for ending in ("알려줘", "보여줘", "설명해줘", "요약해줘")):
        return []

    cleaned = re.sub(r"(각각|관련\s*자료|자료)\s*(알려줘|보여줘|설명해줘|요약해줘)?\??$", "", normalized).strip()
    raw_parts = re.split(r"\s+그리고\s+|이랑|랑|\s+와\s+|\s+과\s+", cleaned)
    parts = [_clean_compound_part(part) for part in raw_parts]
    return [part for part in parts if len(part) >= 2]


def _clean_compound_part(part: str) -> str:
    cleaned = part.strip(" ?.,")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _merge_confidences(confidences: list[str]) -> str:
    order = {"확인불가": 0, "확인 필요": 1, "추정": 2, "확실함": 3}
    if not confidences:
        return "확인불가"
    return min(confidences, key=lambda confidence: order.get(confidence, 2))


def _extract_requested_count(query: str) -> int | None:
    digits = ""
    for char in query:
        if char.isdigit():
            digits += char
            continue
        if digits:
            break
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    return value if value > 0 else None


def _extract_date_filter_terms(query: str) -> list[str]:
    stopwords = set(QUERY_STOPWORDS)
    stopwords.update(DATE_RANKING_QUERY_KEYWORDS)
    stopwords.update(GENERIC_QUERY_TERMS)
    stopwords.update({"가장", "개", "목록", "수정", "작업", "버전", "뭐야"})
    terms: list[str] = []
    for term in _extract_query_terms(query):
        normalized = _strip_common_suffix(term)
        if len(normalized) < 2:
            continue
        if _is_noise_term(normalized):
            continue
        if normalized in stopwords:
            continue
        if any(char.isdigit() for char in normalized):
            continue
        terms.append(normalized)
    return _deduplicate_terms(terms)


def _strip_common_suffix(term: str) -> str:
    normalized = term.casefold().strip()
    for suffix in KOREAN_PARTICLE_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix) + 1:
            return normalized[: -len(suffix)]
    return normalized


def _is_noise_term(term: str) -> bool:
    if term in GENERIC_QUERY_TERMS or term in QUERY_STOPWORDS:
        return True
    return any(term.endswith(suffix) for suffix in NOISE_TERM_SUFFIXES)


def _deduplicate_terms(terms: list[str]) -> list[str]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        deduplicated.append(term)
    return deduplicated


def _matches_all_terms(record: dict, terms: list[str]) -> bool:
    if not terms:
        return False
    target = f"{record.get('file_name', '')} {record.get('source_path', '')}".casefold()
    return all(term in target for term in terms)


def _get_media_filter(query: str):
    normalized = query.casefold()
    if any(term in normalized for term in MEDIA_EMAIL_TERMS):
        return _is_email_record
    if any(term in normalized for term in MEDIA_KAKAO_TERMS):
        return _is_kakao_record
    return None


def _is_email_record(record: dict) -> bool:
    target = f"{record.get('file_name', '')} {record.get('source_path', '')}".casefold()
    return ".eml" in target


def _is_kakao_record(record: dict) -> bool:
    target = f"{record.get('file_name', '')} {record.get('source_path', '')}".casefold()
    return ".txt" in target and any(term in target for term in ("카톡", "카카오톡", "kakao"))


def _is_memo_query(query: str) -> bool:
    return "메모" in query.casefold()


def _is_memo_record(record: dict) -> bool:
    source_path = str(record.get("source_path") or "")
    return source_path.startswith("memo:")


def _build_date_source_record(chunk: dict) -> dict | None:
    metadata = chunk.get("source_metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    source_path = str(
        metadata.get("source_path")
        or chunk.get("source_file")
        or chunk.get("chunk_id")
        or ""
    )
    file_name = str(
        metadata.get("file_name")
        or _last_path_part(source_path)
        or chunk.get("source_file")
        or ""
    )
    if not file_name:
        return None
    modified_at = str(metadata.get("modified_at") or chunk.get("modified_at") or "")
    return {
        "file_name": _display_date_file_name(file_name, source_path),
        "source_path": source_path,
        "modified_at": modified_at,
        "_modified_dt": _parse_modified_at(modified_at),
    }


def _display_date_file_name(file_name: str, source_path: str) -> str:
    if source_path.startswith("memo:") and not file_name.startswith("[메모]"):
        return f"[메모] {file_name}"
    return file_name


def _parse_modified_at(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min
    candidates = [text, text.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is not None:
                return parsed.replace(tzinfo=None)
            return parsed
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is not None:
                return parsed.replace(tzinfo=None)
            return parsed
        except ValueError:
            pass
    return datetime.min


def _format_date_ranking_answer(query: str, records: list[dict]) -> str:
    if not records:
        return "수정일시가 기록된 자료를 찾지 못했습니다."
    lines = ["수정일시 기준으로 정렬한 결과입니다:"]
    for index, record in enumerate(records, start=1):
        lines.append(
            f"{index}. {record['file_name']} "
            f"(경로: {record['source_path']}, 수정일시: {record['modified_at']})"
        )
    return "\n".join(lines)


def _build_source_lookup(chunks: list[dict]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for chunk in chunks:
        if _is_internal_summary_chunk(chunk):
            continue
        formatted = _format_source(chunk)
        file_name = str(chunk.get("file_name") or "").strip()
        source_path = str(chunk.get("source_path") or "").strip()
        if file_name:
            lookup.setdefault(file_name, formatted)
        if source_path:
            lookup.setdefault(source_path, formatted)
        lookup.setdefault(formatted, formatted)
    return lookup


def _resolve_source(source: str, source_lookup: dict[str, str], chunks: list[dict]) -> str:
    if source in source_lookup:
        return source_lookup[source]
    for chunk in chunks:
        if _is_internal_summary_chunk(chunk):
            continue
        file_name = str(chunk.get("file_name") or "").strip()
        source_path = str(chunk.get("source_path") or "").strip()
        if file_name and file_name in source:
            return _format_source(chunk)
        if source_path and source_path in source:
            return _format_source(chunk)
    return ""


def _format_source(chunk: dict) -> str:
    file_name = str(chunk.get("file_name") or "").strip()
    source_path = str(chunk.get("source_path") or "").strip()
    if not file_name or source_path.startswith("keyword-summary:"):
        return ""
    modified_at = str(chunk.get("modified_at") or "").strip()
    metadata = [f"경로: {source_path or '확인되지 않음'}"]
    if modified_at:
        metadata.append(f"수정일: {modified_at}")
    return f"{file_name} ({', '.join(metadata)})"


def _is_internal_summary_chunk(chunk: dict) -> bool:
    source_path = str(chunk.get("source_path") or "")
    return source_path.startswith("keyword-summary:")


def _last_path_part(path: str) -> str:
    normalized = path.replace("\\", "/").rstrip("/")
    if not normalized:
        return ""
    return normalized.rsplit("/", 1)[-1]
