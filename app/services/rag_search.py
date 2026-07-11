import json
import re
from datetime import datetime
from typing import Any

import numpy as np
from openai import APIError, OpenAI, RateLimitError

from app.config import GPT_MODEL

EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_MAX_RETRIES = 5
MAX_RELATED_ANSWERS = 2
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
    "질문에 직접 답하는 정보가 없으면 어떤 내용은 확인되지만 질문의 핵심 정보는 언급되지 않는다고 명확히 쓰세요. "
    "완전히 무관한 질문이면 primary만 확인불가로 두고 related는 빈 배열로 두세요. "
    '{"primary": {"confidence": "확실함|확인 필요|추정|확인불가", "answer": "...", "used_sources": [...]}, '
    '"related": [{"confidence": "확실함|확인 필요|추정", "answer": "...", "used_sources": [...]}]}'
)
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
    def __init__(self, values: list[float], query: str) -> None:
        super().__init__(values)
        self.query = query


def embed_query(query: str, api_key: str) -> list[float]:
    client = _create_openai_client(api_key)
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query,
    )
    return _QueryEmbedding(list(response.data[0].embedding), query)


def _create_openai_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, max_retries=OPENAI_MAX_RETRIES)


def _temporary_api_failure_answer() -> dict:
    return {
        "confidence": "추정",
        "answer": "일시적으로 답변을 생성하지 못했습니다. 잠시 후 다시 시도해주세요.",
        "sources": [],
    }


def search_relevant_chunks(
    query_embedding: list[float],
    chunks: list[dict],
    top_k: int = 20,
    min_similarity: float = 0.3,
    query: str = "",
) -> list[dict]:
    if not query_embedding or not chunks or top_k <= 0:
        return []

    query_vector = np.asarray(query_embedding, dtype=np.float32)
    if query_vector.ndim != 1:
        return []

    valid_chunks: list[dict] = []
    vectors: list[list[float]] = []
    for chunk in chunks:
        embedding = chunk.get("embedding")
        if embedding is None:
            embedding = chunk.get("embedding_vector")
        if not isinstance(embedding, list) or len(embedding) != len(query_vector):
            continue
        valid_chunks.append(chunk)
        vectors.append(embedding)

    if not vectors:
        return []

    matrix = np.asarray(vectors, dtype=np.float32)
    query_norm = np.linalg.norm(query_vector)
    matrix_norms = np.linalg.norm(matrix, axis=1)
    denominator = matrix_norms * query_norm
    similarities = np.divide(
        matrix @ query_vector,
        denominator,
        out=np.zeros_like(matrix_norms, dtype=np.float32),
        where=denominator != 0,
    )

    query_text = query or str(getattr(query_embedding, "query", ""))
    eligible_indices = np.flatnonzero(similarities >= min_similarity)
    exact_file_name_indices = _find_exact_file_name_keyword_indices(
        query_text,
        valid_chunks,
        similarities,
    )
    file_keyword_indices = _find_file_name_keyword_indices(query_text, valid_chunks)
    text_keyword_indices = _find_exact_text_keyword_indices(query_text, valid_chunks)
    content_keyword_indices = _find_content_keyword_indices(query_text, valid_chunks)
    top_indices = _build_additive_candidate_indices(
        eligible_indices,
        similarities,
        top_k,
        exact_file_name_indices,
        content_keyword_indices,
        max_total=30,
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
    api_key: str,
) -> dict:
    if not relevant_chunks:
        return {
            "confidence": "확인불가",
            "answer": "제공된 자료에서 확인되지 않습니다.",
            "sources": [],
        }

    client = _create_openai_client(api_key)
    try:
        response = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_message(query, relevant_chunks)},
            ],
            response_format={"type": "json_object"},
        )
    except RateLimitError:
        print("API 요청이 많아 잠시 대기 중입니다...")
        return _temporary_api_failure_answer()
    except APIError:
        return _temporary_api_failure_answer()

    raw_content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        parsed = {"confidence": "확인불가", "answer": raw_content, "used_sources": []}

    primary = _extract_primary_answer(parsed)
    confidence = _normalize_confidence(primary.get("confidence"))
    found = confidence != "확인불가" and bool(primary.get("found", True))
    answer = str(primary.get("answer") or "").strip()
    if not found or confidence == "확인불가":
        corrected = _correct_unavailable_placeholder_answer(query, answer, relevant_chunks)
        if corrected is not None:
            return corrected
        return {"confidence": "확인불가", "answer": answer, "sources": [], "related": []}

    sources = _normalize_sources(primary.get("used_sources"), relevant_chunks)
    related = _normalize_related_answers(parsed.get("related"), relevant_chunks)
    related = _supplement_related_from_filename_matches(
        query,
        relevant_chunks,
        sources,
        related,
    )
    return {
        "confidence": confidence,
        "answer": answer,
        "sources": sources,
        "related": related,
    }


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
    for index, chunk in enumerate(relevant_chunks, start=1):
        lines.extend(
            [
                f"[\uc790\ub8cc {index}]",
                f"\ud30c\uc77c\uba85: {chunk.get('file_name', '')}",
                f"\uacbd\ub85c: {chunk.get('source_path', '')}",
                f"\uc218\uc815\uc77c: {chunk.get('modified_at', '')}",
                f"\uc720\uc0ac\ub3c4: {chunk.get('similarity_score', 0):.4f}",
                "\ub0b4\uc6a9:",
                (
                    f"[\ucd9c\ucc98: {chunk.get('file_name', '')} | "
                    f"\uacbd\ub85c: {chunk.get('source_path', '')} | "
                    f"\uc218\uc815\uc77c: {chunk.get('modified_at', '')}]"
                ),
                (
                    f"[파일명: {chunk.get('file_name', '')}, "
                    f"수정일: {chunk.get('modified_at', '')}, "
                    f"유사도: {chunk.get('similarity_score', 0):.4f}]"
                ),
                str(chunk.get("chunk_text") or ""),
                "",
            ]
        )
    lines.extend(
        [
            "\ubc18\ub4dc\uc2dc JSON \uac1d\uccb4\ub85c\ub9cc \ub2f5\ud558\uc138\uc694.",
            (
                '{"primary": {"confidence": "확실함|확인 필요|추정|확인불가", '
                '"answer": "질문에 가장 직접적으로 답하는 내용", '
                '"used_sources": ["실제로 primary 답변에 사용한 파일명/이메일제목/메모제목"]}, '
                '"related": [{"confidence": "확실함|확인 필요|추정", '
                '"answer": "이 파일이 왜 관련 있는지와 실제 청크 내용 요약. 직접 답이 없으면 무엇은 확인되고 무엇은 언급되지 않는지 설명", '
                '"used_sources": ["실제로 related 항목에 사용한 파일명/이메일제목/메모제목"]}]}'
            ),
            (
                "답을 찾지 못했고 검색 자료도 질문과 무관하다면 "
                '{"primary": {"confidence": "확인불가", "answer": "제공된 자료에서 확인되지 않습니다.", "used_sources": []}, "related": []}'
            ),
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
    normalized = answer.strip()
    return not normalized or "확인되지" in normalized or "찾지 못" in normalized


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
) -> list[int]:
    query_terms = _extract_indexed_query_terms(query, chunks)
    if not query_terms:
        return []

    matches: list[tuple[int, float, int]] = []
    for index, chunk in enumerate(chunks):
        file_name = _chunk_file_name(chunk).casefold()
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


def _find_file_name_keyword_indices(query: str, chunks: list[dict]) -> list[int]:
    if not query:
        return []

    query_terms = _extract_indexed_query_terms(query, chunks)
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
        source_tokens = _source_tokens_for_text(f"{file_name} {source_path}")
        match_count = sum(1 for term in query_terms if term in source_tokens)
        if match_count > 0:
            matches.append((match_count, len(file_name), index))

    if not matches:
        return []

    matches.sort(key=lambda item: (-item[0], item[1], item[2]))
    best_count = matches[0][0]
    return [index for match_count, _, index in matches if match_count == best_count]


def _find_exact_text_keyword_indices(query: str, chunks: list[dict]) -> list[int]:
    terms = _extract_indexed_query_terms(query, chunks)
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
        source_tokens = _source_tokens_for_text(_chunk_source_text(chunk))
        chunk_text = str(chunk.get("chunk_text") or "").casefold()
        source_terms = [term for term in terms if term in source_tokens]
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


def _find_content_keyword_indices(query: str, chunks: list[dict]) -> list[int]:
    source_terms = _extract_indexed_query_terms(query, chunks)
    content_terms = _extract_content_boost_terms(query, chunks)
    if not source_terms or not content_terms:
        return []

    minimum_source_matches = max(1, len(source_terms) - 1)
    matches: list[tuple[int, int, int]] = []
    for index, chunk in enumerate(chunks):
        source_tokens = _source_tokens_for_chunk(chunk)
        source_match_count = sum(1 for term in source_terms if term in source_tokens)
        if source_match_count < minimum_source_matches:
            continue

        chunk_text = str(chunk.get("chunk_text") or "").casefold()
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


def _extract_indexed_query_terms(query: str, chunks: list[dict]) -> list[str]:
    source_index = _build_source_token_index(chunks)
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
    return f"{file_name} (\uacbd\ub85c: {source_path})"


def _is_internal_summary_chunk(chunk: dict) -> bool:
    source_path = str(chunk.get("source_path") or "")
    return source_path.startswith("keyword-summary:")


def _last_path_part(path: str) -> str:
    normalized = path.replace("\\", "/").rstrip("/")
    if not normalized:
        return ""
    return normalized.rsplit("/", 1)[-1]
