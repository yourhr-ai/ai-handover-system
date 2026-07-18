import unittest
from unittest.mock import patch

from app.services.rag_search import (
    SYSTEM_PROMPT,
    _build_user_message,
    _confidence_from_chunks,
    build_chunk_search_index,
    generate_answer,
    search_relevant_chunks,
)


def _fake_call_chat_stream(pieces, *, prompt_tokens: int = 0, completion_tokens: int = 0):
    """Stand-in for ai_proxy_client.call_chat_stream: feeds `pieces` to on_delta
    one at a time, then returns the same shape the real proxy call returns."""

    def fake(*_args, on_delta=None, **_kwargs):
        for piece in pieces:
            if on_delta is not None:
                on_delta(piece)
        return {
            "content": "".join(pieces),
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }

    return fake


class RagSearchStreamingAndCacheTests(unittest.TestCase):
    def test_system_prompt_requests_only_concise_answer_body(self):
        self.assertIn("답변 본문만 작성", SYSTEM_PROMPT)
        self.assertIn("JSON, 신뢰도", SYSTEM_PROMPT)
        self.assertIn("핵심 근거 1~2개", SYSTEM_PROMPT)
        self.assertIn("장황하게 설명하지 말고", SYSTEM_PROMPT)

    def test_generate_answer_streams_body_then_returns_metadata(self):
        payload = "노트북 금액은 ₩1,000,000입니다."
        pieces = [payload[index : index + 7] for index in range(0, len(payload), 7)]
        chunks = [
            {
                "chunk_text": "품목: 노트북, 금액: ₩1,000,000",
                "file_name": "매출.xlsx",
                "source_path": "C:/업무/매출.xlsx",
                "modified_at": "2026-07-13",
                "similarity_score": 0.9,
            }
        ]
        deltas: list[str] = []
        with patch(
            "app.services.rag_search.call_chat_stream",
            side_effect=_fake_call_chat_stream(pieces, prompt_tokens=321, completion_tokens=45),
        ):
            result = generate_answer("노트북 금액은?", chunks, "test", deltas.append)

        self.assertEqual("".join(deltas), "노트북 금액은 ₩1,000,000입니다.")
        self.assertEqual(result["confidence"], "확실함")
        self.assertEqual(result["answer"], "노트북 금액은 ₩1,000,000입니다.")
        self.assertEqual(result["_usage"], {"prompt_tokens": 321, "completion_tokens": 45})
        self.assertTrue(result["sources"])
        self.assertEqual(result["related"], [])

    def test_confidence_is_calculated_from_top_similarity(self):
        self.assertEqual(_confidence_from_chunks([{"similarity_score": 0.7}]), "확실함")
        self.assertEqual(_confidence_from_chunks([{"similarity_score": 0.48}]), "추정")
        self.assertEqual(_confidence_from_chunks([{"similarity_score": 0.36}]), "확인 필요")

    def test_unrelated_results_skip_model_and_sources(self):
        chunks = [{
            "chunk_text": "전혀 다른 내용",
            "file_name": "이미지.zip",
            "source_path": "기타/이미지.zip",
            "similarity_score": 0.71,
        }]
        result = generate_answer("존재하지않는파일_20991231 내용 알려줘", chunks, "test")
        self.assertEqual(result["answer"], "관련된 자료를 찾을 수 없습니다.")
        self.assertEqual(result["sources"], [])

    def test_low_confidence_hides_sources_even_with_partial_filename_match(self):
        chunks = [{
            "chunk_text": "내용 자동 추출 제외",
            "file_name": "급여테이블_20260214.xlsx",
            "source_path": "업무/급여테이블_20260214.xlsx",
            "similarity_score": 0.31,
        }]
        with patch(
            "app.services.rag_search.call_chat_stream",
            side_effect=_fake_call_chat_stream(["해당 파일은 원문 확인이 필요합니다."]),
        ):
            result = generate_answer("급여테이블_20260214 파일 알려줘", chunks, "test")
        self.assertEqual(result["confidence"], "확인 필요")
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["related"], [])

    def test_unavailable_model_answer_removes_unrelated_local_metadata(self):
        chunks = [{
            "chunk_text": "근로계약서와 휴가 규정",
            "file_name": "취업규칙.docx",
            "source_path": "규정/취업규칙.docx",
            "similarity_score": 0.72,
        }]
        with patch(
            "app.services.rag_search.call_chat_stream",
            side_effect=_fake_call_chat_stream(["제공된 자료에서 확인되지 않습니다."]),
        ):
            result = generate_answer("내일 날씨 어때?", chunks, "test")
        self.assertEqual(result["answer"], "관련된 자료를 찾을 수 없습니다.")
        self.assertEqual(result["sources"], [])

    def test_high_confidence_sources_do_not_depend_on_answer_wording(self):
        # Related-material visibility is determined only by local confidence,
        # not by phrases emitted by the model.
        chunks = [{
            "chunk_text": "관련 내용이 없습니다",
            "file_name": "휴가규정.docx",
            "source_path": "인사/휴가규정.docx",
            "similarity_score": 0.6,
        }]
        with patch(
            "app.services.rag_search.call_chat_stream",
            side_effect=_fake_call_chat_stream(["제공된 자료에서 확인되지 않습니다."]),
        ):
            result = generate_answer("휴가규정 파일 알려줘", chunks, "test")
        self.assertEqual(result["answer"], "제공된 자료에서 확인되지 않습니다.")
        self.assertEqual(result["confidence"], "확실함")
        self.assertTrue(result["sources"])

    def test_external_fact_query_skips_model_even_with_high_similarity(self):
        chunks = [{
            "chunk_text": "회사 매출과 주식매수선택권",
            "file_name": "회사현황.xlsx",
            "source_path": "회사/회사현황.xlsx",
            "similarity_score": 0.82,
        }]
        result = generate_answer("회사 주가가 어때?", chunks, "test")
        self.assertEqual(result["answer"], "관련된 자료를 찾을 수 없습니다.")
        self.assertEqual(result["sources"], [])

    def test_cached_search_matches_uncached_search(self):
        chunks = [
            {
                "chunk_id": "one",
                "embedding": [1.0, 0.0],
                "chunk_text": "노트북 금액",
                "source_metadata": {"file_name": "매출.xlsx", "source_path": "매출.xlsx"},
            },
            {
                "chunk_id": "two",
                "embedding": [0.0, 1.0],
                "chunk_text": "다른 내용",
                "source_metadata": {"file_name": "기타.txt", "source_path": "기타.txt"},
            },
        ]
        query_embedding = [1.0, 0.0]
        index = build_chunk_search_index(chunks)

        cached = search_relevant_chunks(
            query_embedding,
            query="매출 노트북",
            search_index=index,
        )
        uncached = search_relevant_chunks(query_embedding, chunks, query="매출 노트북")

        self.assertEqual(cached, uncached)
        self.assertEqual(cached[0]["file_name"], "매출.xlsx")

    def test_prompt_groups_file_metadata_with_exact_path_and_omits_similarity(self):
        chunks = [
            {
                "chunk_text": "가" * 700,
                "file_name": "매출.xlsx",
                "source_path": "C:/매우/긴/전체/경로/매출.xlsx",
                "modified_at": "2026-07-13",
                "similarity_score": 0.9,
            }
            for _ in range(12)
        ]
        prompt = _build_user_message("질문", chunks)

        self.assertEqual(prompt.count("[파일 1] 매출.xlsx"), 1)
        self.assertIn("경로: C:/매우/긴/전체/경로/매출.xlsx", prompt)
        self.assertNotIn("유사도:", prompt)
        self.assertLess(len(prompt), 10_000)


if __name__ == "__main__":
    unittest.main()
