import hashlib
import json
import os
import re

from app.api_config import load_api_key
from app.config import GPT_MODEL
from app.services.analysis_result import AnalyzedFile, WorkMemo
from app.services.file_content_extractor import extract_file_summary
from app.services.report_writer import get_combined_priority_review_files_for_memo

CONTENT_SUMMARY_FILE_LIMIT = 2

SYSTEM_PROMPT = """당신은 퇴사자의 업무를 인수받는 후임자를 돕는 인수인계 작성 보조 AI입니다.

아래 3가지를 한국어로, 각 항목 형식에 맞춰 작성하세요.

1) 현황 요약 (150~200자):
   우선 확인 파일에 '내용 요약'이 제공된 경우, 그 안의 구체적인 항목
   (작업 단계, 일정, 진행 상태, 항목명, 수치 등)을 반드시 활용해서
   작성하세요. 단순히 '~파일이 있다', '~업무가 진행 중이다' 같은
   표면적 설명만으로는 부족합니다.

   좋은 예: '견적서(6/22 수정) 기준 1단계 업무 운영체계 정비는 7/6까지,
   2단계 채용·성장체계 구축은 7/9까지 진행 예정이며, 팀장 미팅(6/16)과
   조직진단 보고서 작성(6/17)은 완료된 것으로 보입니다.'

   나쁜 예: '최근 수정된 견적서 파일을 근거로 여러 업무가 진행되고
   있는 상태입니다.'

   '내용 요약'이 제공되지 않은 파일만 있는 경우에는 파일명과 날짜
   기준으로 추정 가능한 수준까지만 작성하세요.

2) 주의사항 및 예상 할일 (3~5개 항목, 마크다운 불릿 리스트):
   메모와 우선 확인 파일을 근거로, 후임자가 주의해야 할 점과 예상되는 다음 행동을
   구분 없이 섞어서 하나의 목록으로 작성하세요.
   주의할 점은 사실과 추론을 구분해서, 불확실한 부분은 '확인 필요'를 명시하세요.
   예상되는 다음 행동을 적을 때는 우선 확인 파일 중 가장 최근 파일의 종류/목적을
   근거로 구체적으로 제안하세요.
   예: '견적서가 가장 최근 파일이면 -> 고객에게 견적 제출 여부 또는 의사결정 확인 필요'
       '기획안이 가장 최근 파일이면 -> 기획안 의사결정 여부 확인 필요'
   이런 식으로 파일 종류와 최신성을 근거로 구체적인 확인 행동을 제안하세요.

3) 메일에서 확인된 내용 (250~350자):
   메일 정보가 제공된 경우, 단순히 발신자/날짜만 나열하지 말고 아래를 포함해서
   맥락이 드러나게 email_summary 항목에 작성하세요:
   - 누가 누구에게 무엇을 요청했거나 전달했는지
   - 메일 본문에 나온 구체적인 항목, 일정, 금액 등이 있다면 인용하듯 활용
   - 메일이 여러 개면 시간 순서나 주제별로 흐름을 알 수 있게 정리
   - 메일 내용이 짧거나 불충분하면 그 점도 언급
     (예: '구체적인 내용은 확인이 필요합니다')

   나쁜 예: '채움세무법인에서 6/20에 견적 관련 메일을 보냈습니다.'
   좋은 예: '채움세무법인 김OO 담당자가 6/20 메일로 인사 자문 견적서 수정본을
   전달했으며, 기존 견적 대비 조직진단 항목이 추가되었다고 안내했습니다.
   회신 기한은 언급되지 않아 확인이 필요합니다.'

   메일 정보가 제공되지 않았으면 이 항목은 빈 문자열로 두세요.
   메일 정보가 제공되었지만 이 업무와 직접 관련된 내용이 없으면 빈 문자열로 두지 말고
   "선택된 메일에서 이 업무와 직접 관련된 내용은 확인되지 않습니다."처럼 관련 내용이
   확인되지 않는다는 명시적 문장으로 작성하세요.

4) 메신저(카톡)에서 확인된 사항 (공백 제외 최대 500자):
   선택된 카카오톡 대화 정보가 제공된 경우, 대화의 흐름과 맥락을 구체적으로
   설명하고, kakao_summary 항목에 마크다운 형식(필요시 불릿 리스트나 줄바꿈
   활용)으로 작성하세요. 업무 담당자가 인수인계 받을 때 반드시 알아야 할 내용
   위주로 정리하세요 (예: 합의된 일정, 결정된 사항, 미해결 이슈, 상대방의
   구체적 요청, 다음 행동이 필요한 부분 등). 단순 잡담이나 인사말, 업무와
   무관한 내용은 제외하되, 업무 관련 흐름이 있다면 단편적으로 끊지 말고
   전후 맥락이 이어지게 작성하세요.
   대화 정보가 없으면 이 항목은 빈 문자열로 두세요.
   대화 정보가 제공되었지만 이 업무와 직접 관련된 내용이 전혀 없으면 빈 문자열로 두지 말고
   "선택된 대화에서 이 업무와 직접 관련된 내용은 확인되지 않습니다."처럼 관련 내용이
   확인되지 않는다는 명시적 문장으로 작성하세요.

5) 자료 간 연결 및 타임라인 (200~400자 또는 마크다운 불릿 리스트, cross_check 항목):
   제공된 파일/메일/카톡을 시간순으로 연결했을 때 드러나는 업무의 흐름을
   정리하세요.
   예: '6/20 메일로 견적서 수신 -> 6/22 카톡에서 2안 진행 합의 -> 6/25
   견적서_v3 파일 수정'처럼 타임라인 형태로 작성하세요.
   자료 간 불일치가 있으면 반드시 지적하세요.
   예: '메일에서는 7/15 마감으로 안내되었으나(출처: 6/20 메일), 카톡에서
   7/22로 연기 합의됨(출처: 7/2 카톡). 최신 기준은 카톡의 7/22로 보입니다.'
   흐름을 종합했을 때 현재 시점에서 다음에 해야 할 행동이 무엇으로 보이는지
   1~2개 제안하세요 (반드시 근거 출처를 표기하세요).
   자료가 한 종류뿐이거나 교차 확인할 정보가 없으면 다른 내용을 지어내지 말고
   "자료 간 교차 확인이 가능한 내용이 없습니다."라고만 작성하세요.
   이 항목에도 아래 출처 표기 규칙을 동일하게 적용하세요.

당신은 추출 제공된 파일 요약 외의 파일 내용은 알 수 없습니다.
요약이 제공되지 않은 파일은 파일명과 날짜로만 추정하세요.
추측을 사실처럼 단정하지 말고, 추론에는 '~로 보입니다', '~일 가능성이 있습니다' 같은
표현을 사용하세요.

[출처 표기 규칙 - status_summary, precautions_and_tasks, email_summary,
kakao_summary, cross_check 모든 항목에 공통 적용]
구체적인 사실을 언급할 때마다 문장 끝에 근거 출처를 괄호로 표기하세요.
- 파일 근거: (출처: 파일명)
- 메일 근거: (출처: M/D 발신자 메일)
- 카톡 근거: (출처: M/D 카톡)
출처를 표기할 수 없는 내용은 쓰지 마세요. 다만 여러 자료를 종합한 추론은
허용하되, 반드시 '~로 보입니다(출처: A파일, 6/20 메일)'처럼 추론임을 드러내는
표현과 종합에 사용한 출처들을 함께 표기하세요.

좋은 예: '견적서 수정본이 6/20 전달되었고(출처: 6/20 김OO 메일), 조직진단
항목이 추가된 상태입니다(출처: 견적서_v3.xlsx).'
나쁜 예: '견적 관련 논의가 진행 중이며 곧 계약이 체결될 것으로 예상됩니다.'
(출처 없음 + 자료에 없는 예측)

반드시 아래 JSON 형식으로만 응답하세요.
{
  "status_summary": "현황 요약 텍스트",
  "precautions_and_tasks": "마크다운 불릿 리스트 형식의 주의사항 및 예상 할일",
  "email_summary": "메일 요약 텍스트 (메일 정보가 없으면 빈 문자열, 메일 정보가 있지만 관련 내용이 없으면 관련 내용이 확인되지 않는다는 명시적 문장)",
  "kakao_summary": "카톡 대화 요약 텍스트 (대화 정보가 없으면 빈 문자열, 대화 정보가 있지만 관련 내용이 없으면 관련 내용이 확인되지 않는다는 명시적 문장)",
  "cross_check": "자료 간 연결 및 타임라인 텍스트 또는 마크다운 불릿 리스트 (교차 확인할 내용이 없으면 '자료 간 교차 확인이 가능한 내용이 없습니다.')"
}"""


def analyze_memo_with_ai(
    memo: WorkMemo,
    priority_review_files: list[AnalyzedFile],
    root_folder_path: str,
    parsed_emails: list[dict] | None = None,
) -> dict[str, str | list] | None:
    api_key = load_api_key()
    if not api_key:
        return None

    user_message = _build_user_message(
        memo, priority_review_files, root_folder_path, parsed_emails
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content)
        result = {
            "status_summary": str(parsed.get("status_summary", "")),
            # precautions_and_tasks/cross_check may legitimately come back as a
            # JSON array instead of a markdown string - keep the original type
            # so the Word renderer can tell the two cases apart.
            "precautions_and_tasks": parsed.get("precautions_and_tasks", ""),
            "email_summary": str(parsed.get("email_summary", "")),
            "kakao_summary": str(parsed.get("kakao_summary", "")),
            "cross_check": parsed.get("cross_check", ""),
        }
        print(
            f"[AI분석-인수인계서][DBG3 gpt_result] title={memo.title!r}"
            f" email_summary={result['email_summary']!r:.80}"
            f" kakao_summary={result['kakao_summary']!r:.80}"
        )
        return result
    except Exception as exc:
        import traceback
        print(f"[AI분석-인수인계서][DBG3 gpt_EXCEPTION] title={memo.title!r} error={exc!r}")
        traceback.print_exc()
        return None


_REQUIRED_AI_KEYS = (
    "status_summary",
    "precautions_and_tasks",
    "email_summary",
    "kakao_summary",
    "cross_check",
)


def get_or_refresh_ai_result(
    memo: WorkMemo,
    all_files: list[AnalyzedFile],
    root_folder_path: str,
    parsed_emails: list[dict] | None = None,
) -> dict[str, str | list] | None:
    content_hash = compute_memo_content_hash(memo)
    cache_valid = (
        memo.ai_result is not None
        and memo.ai_result_content_hash == content_hash
        and all(k in memo.ai_result for k in _REQUIRED_AI_KEYS)
    )
    print(
        f"[AI분석-인수인계서][DBG2 cache] title={memo.title!r}"
        f" cache_hit={cache_valid}"
        f" has_result={memo.ai_result is not None}"
        f" hash_match={memo.ai_result_content_hash == content_hash}"
        f" linked_emails={memo.linked_emails}"
        f" linked_kakao={memo.linked_kakao_files}"
    )
    if cache_valid:
        return memo.ai_result

    priority_review_files = get_combined_priority_review_files_for_memo(
        all_files,
        memo,
    )
    ai_result = analyze_memo_with_ai(
        memo, priority_review_files, root_folder_path, parsed_emails
    )
    memo.ai_result = ai_result
    memo.ai_result_content_hash = content_hash
    return ai_result


# Bump this whenever the prompt/input-construction logic changes in a way
# that would make a previously cached ai_result stale even though the memo
# content itself didn't change - forces one-time re-analysis on next save.
_CONTENT_HASH_VERSION = "v2"


def compute_memo_content_hash(memo: WorkMemo) -> str:
    hash_source = "\x1f".join(
        [
            _CONTENT_HASH_VERSION,
            memo.title,
            memo.content,
            "\x1e".join(memo.linked_folders),
            "\x1e".join(memo.linked_files),
            "\x1e".join(memo.linked_emails),
            "\x1e".join(memo.linked_kakao_files),
        ]
    )
    return hashlib.sha256(hash_source.encode("utf-8")).hexdigest()


_EMAIL_BODY_MAX_CHARS = 300
_EMAIL_INCLUDE_LIMIT = 10
_KAKAO_LINES_PER_FILE = 50
_KAKAO_CONTEXT_WINDOW = 5

# Common Korean particles, longest first so a token isn't stripped of only
# part of a compound particle (e.g. "에서부터" before "에서").
_PARTICLE_SUFFIXES = (
    "으로부터", "에서부터", "에게서", "한테서",
    "이라서", "이라는", "라는", "이라고", "라고",
    "에서", "에게", "한테", "까지", "부터", "이나", "으로",
    "이고", "이며",
    "은", "는", "이", "가", "을", "를", "에", "의", "도", "만", "과", "와", "로", "나",
)


def _extract_keywords(text: str) -> list[str]:
    """Pull rough keyword candidates out of the memo title/content.

    This is a plain heuristic, not real morphological analysis: split on
    whitespace/punctuation, keep tokens of length >= 2, and strip a known
    Korean particle suffix if the token ends with one. It cannot handle
    irregular verb conjugation, stems that change form, or particles the
    list doesn't cover, so it will both miss real keywords and occasionally
    keep a token that still has a particle attached. Good enough to bias
    which emails/kakao lines/files get shown to the model - not a substitute
    for a real tokenizer.
    """
    raw_tokens = re.split(r"[^\w가-힣]+", text)
    keywords: list[str] = []
    seen: set[str] = set()
    for raw_token in raw_tokens:
        token = raw_token.strip()
        if len(token) < 2:
            continue
        stripped = token
        for suffix in _PARTICLE_SUFFIXES:
            if len(token) - len(suffix) >= 2 and token.endswith(suffix):
                stripped = token[: -len(suffix)]
                break
        key = stripped.casefold()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(stripped)
    return keywords


def _text_matches_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords or not text:
        return False
    lowered = text.casefold()
    return any(keyword.casefold() in lowered for keyword in keywords)


def _find_first_keyword_index(text: str, keywords: list[str]) -> int | None:
    lowered = text.casefold()
    best_index: int | None = None
    for keyword in keywords:
        idx = lowered.find(keyword.casefold())
        if idx != -1 and (best_index is None or idx < best_index):
            best_index = idx
    return best_index


def _select_relevant_emails(emails: list[dict], keywords: list[str]) -> list[dict]:
    keyword_matches = []
    others = []
    for email in emails:
        haystack = f"{email.get('subject') or ''} {email.get('body') or ''}"
        if _text_matches_keywords(haystack, keywords):
            keyword_matches.append(email)
        else:
            others.append(email)

    keyword_matches.sort(key=lambda e: e.get("date") or "", reverse=True)
    others.sort(key=lambda e: e.get("date") or "", reverse=True)

    selected = keyword_matches[:_EMAIL_INCLUDE_LIMIT]
    if len(selected) < _EMAIL_INCLUDE_LIMIT:
        selected.extend(others[: _EMAIL_INCLUDE_LIMIT - len(selected)])

    # keyword hits and filler are merged back into a single most-recent-first
    # order for presentation, same as before this change.
    selected.sort(key=lambda e: e.get("date") or "", reverse=True)
    return selected


def _truncate_email_body(body: str, keywords: list[str]) -> str:
    if len(body) <= _EMAIL_BODY_MAX_CHARS:
        return body

    match_index = _find_first_keyword_index(body, keywords)
    if match_index is None:
        return body[:_EMAIL_BODY_MAX_CHARS] + "...(이하 생략)"

    half = _EMAIL_BODY_MAX_CHARS // 2
    start = max(0, match_index - half)
    end = min(len(body), start + _EMAIL_BODY_MAX_CHARS)
    start = max(0, end - _EMAIL_BODY_MAX_CHARS)

    window = body[start:end]
    if start > 0:
        window = "...(전략) " + window
    if end < len(body):
        window = window + " ...(후략)"
    return window


def _merge_index_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


_KAKAO_GAP_MARKER = {"_gap_marker": True}


def _select_relevant_kakao_messages(messages: list[dict], keywords: list[str]) -> list[dict]:
    """Pick up to _KAKAO_LINES_PER_FILE messages from one file's chronological log.

    Keyword hits pull in a +/- _KAKAO_CONTEXT_WINDOW block of surrounding
    context (merged where blocks overlap); remaining budget is filled with
    the most recent messages. Returned in original chronological order, with
    _KAKAO_GAP_MARKER entries where the kept lines aren't contiguous.
    """
    if not messages:
        return []

    match_indices = [
        index
        for index, msg in enumerate(messages)
        if _text_matches_keywords(str(msg.get("message") or ""), keywords)
    ]

    blocks = _merge_index_ranges(
        [
            (
                max(0, index - _KAKAO_CONTEXT_WINDOW),
                min(len(messages) - 1, index + _KAKAO_CONTEXT_WINDOW),
            )
            for index in match_indices
        ]
    )
    # Prefer the most recent keyword context first; only whole blocks that
    # fit the remaining budget are included.
    blocks.sort(key=lambda block: block[1], reverse=True)

    included: set[int] = set()
    for start, end in blocks:
        block_size = end - start + 1
        if len(included) + block_size > _KAKAO_LINES_PER_FILE:
            continue
        included.update(range(start, end + 1))

    if len(included) < _KAKAO_LINES_PER_FILE:
        for index in range(len(messages) - 1, -1, -1):
            if len(included) >= _KAKAO_LINES_PER_FILE:
                break
            included.add(index)

    ordered_indices = sorted(included)
    selected: list[dict] = []
    previous_index: int | None = None
    for index in ordered_indices:
        if previous_index is not None and index != previous_index + 1:
            selected.append(dict(_KAKAO_GAP_MARKER))
        selected.append(messages[index])
        previous_index = index
    return selected


def _select_summary_target_paths(
    sorted_files: list[AnalyzedFile], keywords: list[str]
) -> set[str]:
    keyword_matched_paths = {
        file.relative_path
        for file in sorted_files
        if _text_matches_keywords(file.file_name, keywords)
    }
    summary_targets = [
        file for file in sorted_files if file.relative_path in keyword_matched_paths
    ][:CONTENT_SUMMARY_FILE_LIMIT]

    if len(summary_targets) < CONTENT_SUMMARY_FILE_LIMIT:
        remaining_needed = CONTENT_SUMMARY_FILE_LIMIT - len(summary_targets)
        filler = [
            file
            for file in sorted_files
            if file.relative_path not in keyword_matched_paths
        ][:remaining_needed]
        summary_targets.extend(filler)

    return {file.relative_path for file in summary_targets}


def _build_user_message(
    memo: WorkMemo,
    priority_review_files: list[AnalyzedFile],
    root_folder_path: str,
    parsed_emails: list[dict] | None = None,
) -> str:
    sorted_files = sorted(
        priority_review_files,
        key=lambda file: file.modified_timestamp,
        reverse=True,
    )
    keywords = _extract_keywords(f"{memo.title} {memo.content}")
    print(f"[AI분석-인수인계서][DBG5 keywords] title={memo.title!r} keywords={keywords}")

    lines = [
        f"메모 제목: {memo.title}",
        f"메모 내용: {memo.content}",
        "",
        "우선 확인 파일 (최신순):",
    ]

    if not sorted_files:
        lines.append("- 없음")
    else:
        summary_target_paths = _select_summary_target_paths(sorted_files, keywords)
        for file in sorted_files:
            summary = (
                _safe_extract_summary(root_folder_path, file.relative_path)
                if file.relative_path in summary_target_paths
                else None
            )
            lines.append(f"- 파일명: {file.file_name} / 수정일시: {file.modified_at}")
            if summary:
                lines.append(f"  내용 요약: {summary}")

    print(
        f"[AI분석-인수인계서][DBG2b build_msg] title={memo.title!r}"
        f" linked_emails={memo.linked_emails}"
        f" parsed_emails_count={len(parsed_emails) if parsed_emails else 0}"
        f" linked_kakao={memo.linked_kakao_files}"
    )
    if memo.linked_emails and parsed_emails:
        linked_set = set(memo.linked_emails)
        candidates = [e for e in parsed_emails if e.get("source_file", "") in linked_set]
        print(f"[AI분석-인수인계서][DBG2b email_match] title={memo.title!r} linked_set={linked_set} matched_count={len(candidates)}")
        matched = _select_relevant_emails(candidates, keywords)
        matched_keyword_count = sum(
            1
            for e in matched
            if _text_matches_keywords(f"{e.get('subject') or ''} {e.get('body') or ''}", keywords)
        )
        print(
            f"[AI분석-인수인계서][DBG5 email_select] title={memo.title!r}"
            f" selected={len(matched)} keyword_matched={matched_keyword_count}"
        )

        if matched:
            lines.append("")
            lines.append("다음은 이 메모와 관련된 메일 정보입니다:")
            for email in matched:
                date = email.get("date") or ""
                sender = email.get("sender") or ""
                subject = email.get("subject") or "(제목 없음)"
                body = email.get("body") or ""
                if body:
                    body = _truncate_email_body(body, keywords)
                lines.append(f"- 날짜: {date} / 발신자: {sender} / 제목: {subject}")
                if body:
                    lines.append(f"  본문: {body}")

    if memo.linked_kakao_files:
        from app.services.kakao_file_handler import process_kakao_files

        kakao_messages, _ = process_kakao_files(memo.linked_kakao_files)
        if kakao_messages:
            file_messages: dict[str, list[dict]] = {}
            for msg in kakao_messages:
                file_messages.setdefault(msg["source_file"], []).append(msg)

            selected: list[dict] = []
            for file_path in memo.linked_kakao_files:
                msgs = file_messages.get(file_path, [])
                selected.extend(_select_relevant_kakao_messages(msgs, keywords))

            print(
                f"[AI분석-인수인계서][DBG5 kakao_select] title={memo.title!r}"
                f" selected_lines={sum(1 for m in selected if not m.get('_gap_marker'))}"
            )

            if selected:
                lines.append("")
                lines.append("다음은 이 메모와 관련된 카카오톡 대화입니다:")
                for msg in selected:
                    if msg.get("_gap_marker"):
                        lines.append("...(중략)...")
                        continue
                    date = msg.get("date") or ""
                    time = msg.get("time") or ""
                    sender = msg.get("sender") or ""
                    message = msg.get("message") or ""
                    lines.append(f"- [{date} {time}] {sender}: {message}")

    return "\n".join(lines)


def _safe_extract_summary(root_folder_path: str, relative_path: str) -> str | None:
    if not root_folder_path:
        return None
    absolute_path = os.path.join(root_folder_path, *relative_path.split("/"))
    return extract_file_summary(absolute_path)
