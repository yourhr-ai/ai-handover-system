"""GUI 없이 실제 챗봇 RAG 경로로 100개 품질 질문을 배치 실행한다."""

from __future__ import annotations

import argparse
import html
import re
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.api_config import load_api_key
from app.license_credits import consume_credits, precheck_action
from app.services.package_loader import load_packages_from_folder, merge_and_deduplicate_chunks
from app.services.rag_search import (
    build_chunk_search_index,
    embed_query,
    generate_answer,
    search_relevant_chunks,
)


DEFAULT_PACKAGE_FOLDER = Path(r"C:\Users\조두형\Desktop\인수인계")
DEFAULT_LICENSE_CODE = "PENDING-011-M1-B528604A"


def _questions(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in text.strip().splitlines() if line.strip())


QUESTION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("카테고리 1: 업무 개요 파악", _questions("""
조직진단이 뭐 하는 업무야?
채용 업무를 왜 하는 거야?
평가 담당자가 평소에 뭘 해?
견적 업무의 목표가 뭐야?
자문 업무는 매일 해? 매주 해?
마케팅 업무에서 제일 중요한 게 뭐야?
DM 업무 하려면 뭐가 필요해?
조직진단은 다른 팀이랑 관련 있어?
인터뷰 업무의 결과물이 뭐야?
규정 업무를 요약해줘
""")),
    ("카테고리 2: 절차/순서", _questions("""
채용 업무는 어떤 순서로 처리해?
조직진단할 때 제일 먼저 뭘 해야 해?
인터뷰 다음엔 뭘 해?
견적 업무 처리하는데 며칠 걸려?
자문 업무는 승인받아야 하는 단계가 있어?
채용 업무 체크리스트 알려줘
취업규칙 만들기 전에 확인해야 할 게 있어?
평가 업무는 누구 확인을 거쳐야 해?
조직진단 끝나면 뭘 해야 해?
급여테이블 작업에서 실수하기 쉬운 부분이 어디야?
""")),
    ("카테고리 3: 담당자/연락처", _questions("""
조직진단 관련해서 누구한테 물어봐야 해?
동우국제 담당자 연락처 알아?
견적 업무 승인권자가 누구야?
채용 문제 생기면 누구한테 보고해?
부장님이 무슨 일 담당했어?
마케팅 업무 관련 대화 상대가 누구누구야?
채움세무법인이랑 협력하는 게 있어?
이메일로 자주 연락하는 사람이 누구야?
카톡방에 누가 있었어?
자문 업무 관련 최근에 연락한 사람 알려줘
""")),
    ("카테고리 4: 일정/마감일", _questions("""
조직진단 마감일이 언제야?
다음 주에 채용 관련해서 뭐 해야 해?
이번 달에 정기적으로 하는 인사평가 업무가 뭐야?
네오비타 관련 최근 이벤트가 언제였어?
급여테이블은 매월 며칠까지 처리해야 해?
견적 업무 이번 분기 목표가 뭐야?
자문 업무 다음 마감이 언제야?
채용이랑 조직진단 일정이 겹치는 게 있어?
지금 가장 급한 업무가 뭐야?
이번 주에 확인해야 할 일정 알려줘
""")),
    ("카테고리 5: 파일/자료 위치", _questions("""
조직진단 관련 파일이 어디 있어?
채용 업무 가장 최근 파일이 뭐야?
동우국제 평가기획안_20260604_v2.1 어디 있어?
견적 업무 관련 폴더가 어디야?
취업규칙 양식 파일이 있어?
채움세무법인 계약서는 어디 있어?
조직진단 예전 자료도 있어?
마케팅 업무 참고자료가 몇 개야?
5. 신스타 급여테이블_20260214가 최신 버전 맞아?
자문 업무에 첨부된 파일 목록 알려줘
""")),
    ("카테고리 6: 수치/데이터 조회 (엑셀 중심)", _questions("""
신스타 급여테이블에서 나오는 급여 항목들을 알려줘
거래처명단(신) 시트에 등록된 거래처가 몇 개야?
채움세무법인 인사 자문 견적서에서 견적 금액이 얼마야?
HR 체크리스트에 어떤 항목들이 들어있어?
GAT글로벌 급여테이블(5. 급여테이블_251221)에서 직급별 급여 범위가 어떻게 나와?
에덴미술 진행일정(진행일정_V2)에 어떤 일정이 잡혀있어?
인사평가표(인사평가표 _ 인사고과표(다면평가) _ 240109)에 평가 항목이 뭐가 있어?
세움엔지니어링 직원정보(9. 세움_직원정보)에 직원이 몇 명 등록되어 있어?
라온글로벌의 연도별 급여(연도별 급여(2019년부터))에서 가장 최근 연도 급여가 얼마야?
DRM커뮤니케이션 직원별 업무 및 정보에서 어떤 직원이 어떤 업무를 맡고 있어?
""")),
    ("카테고리 7: 예외상황 대응", _questions("""
채용 업무에서 평소랑 다르게 처리해야 하는 경우가 있어?
조직진단 승인 안 나면 어떻게 해?
유니콘소프트가 컴플레인하면 어떻게 해?
견적 금액이 큰 경우엔 어떻게 달라져?
대표님이 자리 비우면 어떻게 처리해?
인터뷰에서 실수했을 때 어떻게 대응해?
긴급한 경우 전무님한테 연락해야 해?
자문 예산 초과하면 어떻게 해?
조직진단 기한을 못 맞추면 어떻게 해?
채용에서 특별 승인이 필요한 상황이 뭐야?
""")),
    ("카테고리 8: 최근 이슈/미해결", _questions("""
지금 진행 중인 이슈가 뭐야?
한국관광협회 관련 아직 답장 안 온 메일이 있어?
최근에 문제 생긴 게 있어?
카톡에서 아직 해결 안 된 얘기가 뭐야?
채움세무법인이랑 협의 중인 게 있어?
이번 주에 새로 생긴 이슈 있어?
과장님이 확인해달라고 한 게 있어?
최근 요청 사항이 뭐였어?
지금 대기 중인 승인 건이 있어?
가장 최근 대화 내용 요약해줘
""")),
    ("카테고리 9: 이력 추적", _questions("""
조직진단이 예전엔 어떻게 처리됐었어?
동우국제 계약 조건이 바뀐 적 있어?
채용 담당자가 바뀐 적 있어?
견적 가격이 언제 바뀌었어?
취업규칙이 언제부터 적용됐어?
동우국제 평가기획안이 예전 버전이랑 뭐가 달라졌어?
조직진단 이슈가 언제 처음 생겼어?
채용 업무가 원래 어느 팀 담당이었어?
과거에 비슷한 조직진단 문제가 있었어?
인사평가 프로세스가 바뀐 이력이 있어?
""")),
    ("카테고리 10: 모호한/자료 없는 질문 (할루시네이션 방지 검증)", _questions("""
이 회사 대표이사가 누구야?
내일 날씨 어때?
조직진단 계속해도 괜찮을까?
존재하지않는파일_20991231 내용 알려줘
이 회사 매출이 업계에서 몇 등이야?
김철수라는 사람이 누구야?
채용 업무를 그만두면 어떻게 돼?
다음 달 견적 예산이 얼마나 될까?
채움세무법인 계약 연장해도 될까?
회사 주가가 어때?
""")),
)


@dataclass(slots=True)
class TestResult:
    category: str
    number: int
    question: str
    answer: str
    elapsed_seconds: float
    credits_used: int | None
    has_sources: bool
    error: str = ""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-folder", type=Path, default=DEFAULT_PACKAGE_FOLDER)
    parser.add_argument("--license-code", default=DEFAULT_LICENSE_CODE)
    parser.add_argument("--limit", type=int, default=None, help="스모크 테스트용 질문 수")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def _flatten_questions(limit: int | None) -> list[tuple[str, int, str]]:
    items = [
        (category, number, question)
        for category, questions in QUESTION_GROUPS
        for number, question in enumerate(questions, start=1)
    ]
    if len(items) != 100:
        raise RuntimeError(f"질문은 100개여야 합니다. 현재 {len(items)}개입니다.")
    return items if limit is None else items[:max(0, limit)]


def _format_full_answer(result: dict[str, Any]) -> tuple[str, list[str]]:
    sections = [str(result.get("answer") or "").strip()]
    sources = [str(value) for value in result.get("sources", []) if str(value).strip()]
    related_lines: list[str] = []
    for item in result.get("related", []):
        if not isinstance(item, dict):
            continue
        related_answer = str(item.get("answer") or "").strip()
        if related_answer:
            related_lines.append(f"- [{item.get('confidence', '추정')}] {related_answer}")
        for source in item.get("sources", []):
            source_text = str(source).strip()
            if source_text and source_text not in sources:
                sources.append(source_text)
    if related_lines:
        sections.append("관련 자료:\n" + "\n".join(related_lines))
    if sources:
        sections.append("출처:\n" + "\n".join(f"- {source}" for source in sources))
    return "\n\n".join(filter(None, sections)), sources


def _has_source_reference(answer: str, sources: list[str]) -> bool:
    if sources:
        return True
    return bool(
        re.search(
            r"(?:출처|근거\s*자료|파일명|경로\s*:|\.(?:docx?|pptx?|xlsx?|txt|md|eml|pdf)\b)",
            answer,
            flags=re.IGNORECASE,
        )
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _credits_used(precheck: dict | None, consume: dict | None) -> int | None:
    if consume:
        for key in ("credits_used", "used_credits", "cost", "charged_amount"):
            value = _as_int(consume.get(key))
            if value is not None:
                return max(0, value)
    before = _as_int((precheck or {}).get("balance"))
    after = _as_int((consume or {}).get("balance_after"))
    if before is not None and after is not None:
        return max(0, before - after)
    return _as_int((precheck or {}).get("estimated_cost"))


def _cell(value: object) -> str:
    value = html.escape(str(value), quote=False).replace("|", "&#124;")
    return value.replace("\r\n", "<br>").replace("\n", "<br>")


def _category_averages(results: list[TestResult]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for result in results:
        if not result.error:
            grouped[result.category].append(result.elapsed_seconds)
    return {category: statistics.fmean(values) for category, values in grouped.items()}


def _write_report(
    path: Path,
    package_folder: Path,
    results: list[TestResult],
    expected_count: int,
) -> None:
    successful = [item for item in results if not item.error]
    average = statistics.fmean(item.elapsed_seconds for item in successful) if successful else 0.0
    source_ratio = sum(item.has_sources for item in successful) / len(successful) * 100 if successful else 0.0
    credits = [item.credits_used for item in successful if item.credits_used is not None]
    lines = [
        "# 챗봇 답변 품질 배치 테스트", "",
        f"- 실행 시각: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 패키지 경로: `{package_folder}`",
        f"- 진행: {len(results)}/{expected_count}",
        f"- 성공/실패: {len(successful)}/{len(results) - len(successful)}",
        f"- 평균 응답시간: {average:.2f}초",
        f"- 출처 표기 비율: {source_ratio:.1f}%",
        f"- 확인된 총 사용 크레딧: {sum(credits):,}", "",
        "## 카테고리별 평균 응답시간", "",
        "| 카테고리 | 평균 응답시간(초) |", "|---|---:|",
    ]
    averages = _category_averages(results)
    for category, _ in QUESTION_GROUPS:
        average_text = f"{averages[category]:.2f}" if category in averages else "-"
        lines.append(f"| {_cell(category)} | {average_text} |")

    grouped_results: dict[str, list[TestResult]] = defaultdict(list)
    for result in results:
        grouped_results[result.category].append(result)
    for category, _ in QUESTION_GROUPS:
        if not grouped_results[category]:
            continue
        lines.extend([
            "", f"## {category}", "",
            "| 번호 | 질문 | 답변 전문 | 응답시간(초) | 사용 크레딧 | 출처 표기 |",
            "|---:|---|---|---:|---:|:---:|",
        ])
        for result in grouped_results[category]:
            answer = result.answer
            if result.error:
                answer = f"[오류] {result.error}\n\n{answer}".strip()
            credit_text = "-" if result.credits_used is None else f"{result.credits_used:,}"
            lines.append(
                f"| {result.number} | {_cell(result.question)} | {_cell(answer)} | "
                f"{result.elapsed_seconds:.2f} | {credit_text} | "
                f"{'예' if result.has_sources else '아니오'} |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _print_summary(results: list[TestResult], output_path: Path) -> None:
    successful = [item for item in results if not item.error]
    average = statistics.fmean(item.elapsed_seconds for item in successful) if successful else 0.0
    source_ratio = sum(item.has_sources for item in successful) / len(successful) * 100 if successful else 0.0
    print("\n=== 배치 테스트 요약 ===")
    print(f"결과 파일: {output_path}")
    print(f"성공/실패: {len(successful)}/{len(results) - len(successful)}")
    print(f"평균 응답시간: {average:.2f}초")
    for category, seconds in _category_averages(results).items():
        print(f"- {category}: {seconds:.2f}초")
    print(f"출처 표기 비율: {source_ratio:.1f}%")


def main() -> int:
    args = _parse_args()
    package_folder = args.package_folder.expanduser().resolve()
    if not package_folder.is_dir():
        raise SystemExit(f"패키지 폴더가 없습니다: {package_folder}")
    api_key = load_api_key()
    if not api_key:
        raise SystemExit("OpenAI API 키가 설정되어 있지 않습니다.")
    questions = _flatten_questions(args.limit)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = (args.output or PROJECT_ROOT / "output" / f"chatbot_test_results_{timestamp}.md").resolve()

    load_started = time.perf_counter()
    packages = load_packages_from_folder(str(package_folder))
    merged = merge_and_deduplicate_chunks(packages)
    chunks = merged["chunks"]
    search_index = build_chunk_search_index(chunks)
    load_seconds = time.perf_counter() - load_started
    if not packages or not search_index.chunks:
        raise SystemExit("읽을 수 있는 패키지 또는 임베딩 청크가 없습니다.")
    print(
        f"패키지 {len(packages)}개, 중복 제거 후 {len(chunks):,}청크 "
        f"(검색 가능 {len(search_index.chunks):,}청크)를 {load_seconds:.2f}초에 로드했습니다."
    )
    print(f"질문 {len(questions)}개 실행, 중간 결과: {output_path}")

    results: list[TestResult] = []
    for sequence, (category, number, question) in enumerate(questions, start=1):
        started = time.perf_counter()
        precheck = None
        consume = None
        answer_text = ""
        has_sources = False
        error = ""
        answer_elapsed = 0.0
        try:
            precheck = precheck_action(args.license_code, "chat")
            if precheck is not None and precheck.get("allowed") is False:
                raise RuntimeError(f"크레딧 사전확인 거절: {precheck}")
            query_embedding = embed_query(question, api_key)
            relevant = search_relevant_chunks(
                query_embedding, query=question, search_index=search_index
            )
            answer_result = generate_answer(question, relevant, api_key, lambda _delta: None)
            usage = answer_result.setdefault("_usage", {})
            usage["embedding_tokens"] = int(getattr(query_embedding, "usage_tokens", 0))
            answer_text, sources = _format_full_answer(answer_result)
            has_sources = _has_source_reference(answer_text, sources)
            answer_elapsed = time.perf_counter() - started
            consume = consume_credits(
                args.license_code,
                "chat",
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                embedding_tokens=usage.get("embedding_tokens", 0),
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if answer_elapsed == 0.0:
                answer_elapsed = time.perf_counter() - started
        credit_count = _credits_used(precheck, consume)
        results.append(TestResult(
            category, number, question, answer_text, answer_elapsed, credit_count, has_sources, error
        ))
        _write_report(output_path, package_folder, results, len(questions))
        print(
            f"[{sequence:03d}/{len(questions):03d}] {'성공' if not error else '실패'} "
            f"{answer_elapsed:.2f}초, 크레딧 {credit_count if credit_count is not None else '?'}, "
            f"출처 {'예' if has_sources else '아니오'} | {question}",
            flush=True,
        )
    _print_summary(results, output_path)
    return 0 if all(not item.error for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
