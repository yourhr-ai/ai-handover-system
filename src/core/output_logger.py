from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.customer_summarizer import CustomerSummary
    from src.core.document_summarizer import DocumentInfo, DocumentSummary
    from src.core.project_summarizer import ProjectSummary

_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"


@dataclass
class _ExtractedRecord:
    display_name: str
    score: int
    label: str
    char_count: int
    sampled_count: int = 0      # 5,000자 제한 후 글자 수


@dataclass
class _ExcludedRecord:
    display_name: str
    reason: str                 # [30일 초과] | [이미지 파일] | [이미지 PDF] | [구버전 문서] 등


@dataclass
class _ErrorRecord:
    display_name: str
    error: str


class OutputLogger:
    """분석 파이프라인 전 과정을 기록하고 output/ 디렉터리에 저장한다."""

    def __init__(self) -> None:
        self._extracted: list[_ExtractedRecord] = []
        self._excluded: list[_ExcludedRecord] = []
        self._errors: list[_ErrorRecord] = []

        # 파일 수 카운터
        self.total_files: int = 0
        self.noise_filtered: int = 0      # 노이즈 파일 제외 (Step 0.5)
        self.noise_sys: int = 0           # 시스템 파일 제외
        self.noise_log: int = 0           # 로그/임시 파일 제외
        self.noise_lib: int = 0           # 라이브러리 파일 제외
        self.date_filtered: int = 0       # 30일 초과 제외
        self.image_filtered: int = 0      # 이미지 파일 제외
        self.image_pdf_filtered: int = 0  # 이미지 PDF 제외
        self.dedup_filtered: int = 0      # 구버전 문서 제외

        # 문자 수 통계
        self._original_chars: int = 0           # 추출 원문 총 글자 수
        self._sampled_chars: int = 0            # 5,000자 제한 후 글자 수
        self._summary_chars: int = 0            # 문서 요약 후 글자 수
        self._project_summary_chars: int = 0   # 프로젝트 요약 후 글자 수
        self._customer_summary_chars: int = 0  # 고객사 요약 후 글자 수

        # 프로젝트 통계
        self.project_count: int = 0
        self.similar_filtered: int = 0    # 유사 문서 제거 수
        self.limit_filtered: int = 0      # 상한선 제거 수

        # 고객사 통계 (Step 8.7 이후 설정)
        self._customer_count: int = 0

        # 프로젝트 품질 통계 (Step 8.5 이후 설정)
        self._project_critical_counts: dict[str, int] = {}    # {project_key: n_critical}
        self._project_status_map: dict[str, str] = {}         # {project_key: current_status}
        self._project_incomplete_map: dict[str, bool] = {}    # {project_key: has_incomplete}
        self._project_risk_map: dict[str, bool] = {}          # {project_key: has_risks}
        self._selected_project_count: int = 0
        self._selected_critical_count: int = 0

        # 사전요약 관련
        self._top_docs: list = []

        # 분석 상태
        self._analysis_status: str = "정상 완료"
        self._cancelled_at: str = ""
        self._cancelled_time: str = ""
        self._processed_count: int = 0
        self._remaining_count: int = 0
        self._run_mode: str = "정밀 모드"  # 실행 모드

        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 기록 메서드 ────────────────────────────────────────────────────
    def record_extracted(
        self,
        display_name: str,
        score: int,
        label: str,
        char_count: int,
        sampled_count: int = 0,
    ) -> None:
        self._extracted.append(
            _ExtractedRecord(display_name, score, label, char_count, sampled_count or char_count)
        )

    def record_excluded(self, display_name: str, reason: str) -> None:
        self._excluded.append(_ExcludedRecord(display_name, reason))

    def record_error(self, display_name: str, error: str) -> None:
        self._errors.append(_ErrorRecord(display_name, error))

    def set_customer_stats(self, customer_count: int, customer_summary_chars: int) -> None:
        """고객사 통계를 설정한다."""
        self._customer_count = customer_count
        self._customer_summary_chars = customer_summary_chars

    def set_char_stats(
        self,
        original_chars: int,
        sampled_chars: int,
        summary_chars: int,
        project_summary_chars: int = 0,
        customer_summary_chars: int = 0,
    ) -> None:
        self._original_chars = original_chars
        self._sampled_chars = sampled_chars
        self._summary_chars = summary_chars
        self._project_summary_chars = project_summary_chars
        if customer_summary_chars:
            self._customer_summary_chars = customer_summary_chars

    def set_project_stats(
        self, project_count: int, similar_filtered: int, limit_filtered: int
    ) -> None:
        self.project_count = project_count
        self.similar_filtered = similar_filtered
        self.limit_filtered = limit_filtered

    def set_project_quality_stats(
        self,
        project_summaries: "list[ProjectSummary]",
        selected_keys: "set[str] | None" = None,
    ) -> None:
        """프로젝트 품질 통계를 기록한다."""
        for ps in project_summaries:
            key = ps.project_key
            self._project_critical_counts[key] = len(ps.critical_info)
            self._project_status_map[key] = ps.current_status
            self._project_incomplete_map[key] = (
                bool(ps.incomplete_work) and ps.incomplete_work != "[정보 부족]"
            )
            self._project_risk_map[key] = (
                bool(ps.risks) and ps.risks != "[정보 부족]"
            )
        if selected_keys is not None:
            self._selected_project_count = len(selected_keys)
            self._selected_critical_count = sum(
                self._project_critical_counts.get(k, 0) for k in selected_keys
            )
        else:
            self._selected_project_count = len(project_summaries)
            self._selected_critical_count = sum(self._project_critical_counts.values())

    def set_top_docs(self, docs: "list[DocumentInfo]") -> None:
        self._top_docs = sorted(docs, key=lambda d: d.score, reverse=True)[:50]

    def set_run_mode(self, mode: str) -> None:
        """실행 모드를 기록한다 (라이트 모드 / 정밀 모드)."""
        self._run_mode = mode

    def set_cancelled(
        self, aborted_at: str, processed: int = 0, remaining: int = 0
    ) -> None:
        self._analysis_status = "사용자 중단"
        self._cancelled_at = aborted_at
        self._cancelled_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._processed_count = processed
        self._remaining_count = remaining

    # ── 통계 프로퍼티 ──────────────────────────────────────────────────
    @property
    def _total_original_chars(self) -> int:
        return self._original_chars or sum(r.char_count for r in self._extracted)

    @property
    def _total_sampled_chars(self) -> int:
        return self._sampled_chars or sum(r.sampled_count for r in self._extracted)

    def get_summary_dict(self) -> dict:
        orig = self._total_original_chars
        sampled = self._total_sampled_chars
        doc_summ = self._summary_chars or sampled
        proj_summ = self._project_summary_chars or doc_summ
        cust_summ = self._customer_summary_chars or proj_summ

        def pct(a: int, b: int) -> float:
            return max(0.0, (1 - b / a) * 100) if a > 0 else 0.0

        before_noise = self.total_files
        after_noise = self.total_files - self.noise_filtered
        noise_pct = (self.noise_filtered / before_noise * 100) if before_noise > 0 else 0.0

        return {
            "total_files": self.total_files,
            "noise_filtered": self.noise_filtered,
            "noise_sys": self.noise_sys,
            "noise_log": self.noise_log,
            "noise_lib": self.noise_lib,
            "noise_before": before_noise,
            "noise_after": after_noise,
            "noise_pct": noise_pct,
            "date_filtered": self.date_filtered,
            "image_filtered": self.image_filtered,
            "image_pdf_filtered": self.image_pdf_filtered,
            "dedup_filtered": self.dedup_filtered,
            "similar_filtered": self.similar_filtered,
            "limit_filtered": self.limit_filtered,
            "project_count": self.project_count,
            "customer_count": self._customer_count,
            "success_files": len(self._extracted),
            "failed_files": len(self._errors),
            "excluded_files": len(self._excluded),
            "original_chars": orig,
            "sampled_chars": sampled,
            "doc_summary_chars": doc_summ,
            "project_summary_chars": proj_summ,
            "customer_summary_chars": cust_summ,
            "char_reduction_pct": pct(orig, sampled),
            "doc_summary_reduction_pct": pct(orig, doc_summ),
            "project_summary_reduction_pct": pct(orig, proj_summ),
            "customer_summary_reduction_pct": pct(orig, cust_summ),
            "est_tokens": cust_summ // 2,
            "est_token_reduction_pct": pct(orig // 2, cust_summ // 2),
        }

    # ── 파일 출력 ──────────────────────────────────────────────────────
    def write_all(self) -> None:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._write_extracted(ts)
        self._write_excluded(ts)
        self._write_errors(ts)
        self._write_summary(ts)
        print(f"[로그] output/ 로그 파일 저장 완료 ({ts})")

    def write_project_summaries(
        self,
        project_summaries: "list[ProjectSummary]",
        selected_keys: "set[str] | None" = None,
    ) -> None:
        """프로젝트별 요약 결과를 project_summaries.txt에 저장한다."""
        log_path = _OUTPUT_DIR / "project_summaries.txt"
        print(f"[DEBUG] project_summaries.txt 저장 시작: {log_path}")
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sep = "═" * 60
        lines = [f"[프로젝트별 요약]  {ts}", sep, ""]

        total_critical = 0
        total_incomplete = 0
        total_risks = 0

        for i, ps in enumerate(project_summaries, 1):
            selected_tag = " ✓ GPT 전달" if (selected_keys is None or ps.project_key in selected_keys) else " (미선택)"
            critical_count = len(ps.critical_info)
            has_incomplete = ps.incomplete_work and ps.incomplete_work != "[정보 부족]"
            has_risks = ps.risks and ps.risks != "[정보 부족]"

            total_critical += critical_count
            if has_incomplete:
                total_incomplete += 1
            if has_risks:
                total_risks += 1

            lines += [
                f"[프로젝트 {i}]  {ps.project_key}{selected_tag}",
                f"  분석 문서    : {ps.doc_count}개",
                f"  유사 제거    : {ps.excluded_similar_count}개",
                f"  상한 제거    : {ps.excluded_limit_count}개",
                f"  요약 길이    : {ps.summary_chars}자",
                f"  중요 정보    : {critical_count}개",
                "─" * 60,
            ]

            # 구조화 필드
            struct_rows = [
                ("프로젝트명",       ps.project_name),
                ("고객사명",         ps.client_name),
                ("프로젝트 목적",    ps.project_purpose),
                ("주요 이해관계자",  ps.stakeholders),
                ("주요 문제",        ps.main_issues),
                ("주요 의사결정",    ps.key_decisions),
                ("주요 산출물",      ps.key_outputs),
                ("현재 진행상태",    ps.current_status),
                ("미완료 업무",      ps.incomplete_work),
                ("예상 리스크",      ps.risks),
                ("후임자 확인사항",  ps.successor_notes),
            ]
            for label, value in struct_rows:
                lines.append(f"{label}: {value}")

            if ps.critical_info:
                lines += ["", "[중요 정보]"]
                for j, info in enumerate(ps.critical_info, 1):
                    lines.append(f"  {j}. {info}")

            lines += [
                "",
                f"관련 파일: {', '.join(ps.related_files[:8])}",
                "",
                sep,
                "",
            ]

        total_chars = sum(p.summary_chars for p in project_summaries)
        n_selected = len(selected_keys) if selected_keys is not None else len(project_summaries)
        lines += [
            f"총 프로젝트 수          : {len(project_summaries)}개",
            f"GPT 전달 프로젝트 수    : {n_selected}개",
            f"총 중요 정보 수         : {total_critical}개",
            f"미완료 업무 보유 프로젝트: {total_incomplete}개",
            f"리스크 보유 프로젝트    : {total_risks}개",
            f"프로젝트 요약 총 글자   : {total_chars:,}자",
        ]

        (_OUTPUT_DIR / "project_summaries.txt").write_text(
            "\n".join(lines), encoding="utf-8", newline="\n"
        )
        print("[로그] output/project_summaries.txt 저장 완료")

    def write_document_summaries(self, summaries: "list[DocumentSummary]") -> None:
        log_path = _OUTPUT_DIR / "document_summaries.txt"
        print(f"[DEBUG] document_summaries.txt 저장 시작: {log_path}")
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sep = "─" * 60
        lines = [f"[문서별 요약 결과]  {ts}", "=" * 60, ""]

        for i, s in enumerate(summaries, 1):
            ai_tag = "AI 요약" if s.ai_summarized else "룰 기반 발췌"
            current_tag = " ★현재 진행 업무★" if s.is_current_work else ""
            lines += [
                f"[{i}] {s.display_name}  [{s.score}점]  ({ai_tag}){current_tag}",
                f"     생성일: {s.created_dt}  |  수정일: {s.modified_dt}",
                f"     업무상태: {s.work_status}",
                f"     원문: {s.original_chars:,}자  →  요약: {s.summary_chars:,}자",
                sep,
                s.summary_text,
                "",
            ]
            if s.excerpt:
                lines += [
                    f"[원문 발췌 — {len(s.excerpt):,}자]",
                    s.excerpt[:500] + ("..." if len(s.excerpt) > 500 else ""),
                    "",
                ]

        ai_cnt = sum(1 for s in summaries if s.ai_summarized)
        rule_cnt = len(summaries) - ai_cnt
        orig_total = sum(s.original_chars for s in summaries)
        summ_total = sum(s.summary_chars for s in summaries)
        reduction = max(0.0, (1 - summ_total / orig_total) * 100) if orig_total > 0 else 0.0

        lines += [
            "=" * 60,
            f"총 요약 문서 수  : {len(summaries):>8,}개",
            f"  AI 요약        : {ai_cnt:>8,}개",
            f"  룰 기반 발췌   : {rule_cnt:>8,}개",
            f"원문 총 문자 수  : {orig_total:>8,}자",
            f"요약 총 문자 수  : {summ_total:>8,}자",
            f"문자 절감률      : {reduction:>8.1f}%",
        ]
        (_OUTPUT_DIR / "document_summaries.txt").write_text(
            "\n".join(lines), encoding="utf-8", newline="\n"
        )
        print("[로그] output/document_summaries.txt 저장 완료")

    def write_customer_summaries(
        self,
        customer_summaries: "list[CustomerSummary]",
    ) -> None:
        """고객사별 요약 결과를 customer_summaries.txt에 저장한다."""
        log_path = _OUTPUT_DIR / "customer_summaries.txt"
        print(f"[DEBUG] customer_summaries.txt 저장 시작: {log_path}")
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sep = "═" * 60
        lines = [f"[고객사별 요약]  {ts}", sep, ""]

        total_chars = 0
        total_critical = 0

        for i, cs in enumerate(customer_summaries, 1):
            total_chars += cs.summary_chars
            total_critical += len(cs.critical_info)
            lines += [
                f"[고객사 {i}] {cs.customer_name}",
                f"  포함 프로젝트 수  : {cs.project_count}개",
                f"  현재 상태         : {cs.current_status}",
                f"  요약 길이         : {cs.summary_chars:,}자",
                f"  중요 정보 수      : {len(cs.critical_info)}개",
                f"  관련 프로젝트     : {', '.join(cs.project_keys)}",
                sep,
                "",
                "[고객사요약 전문]",
                cs.summary_text,
                "",
            ]

            if cs.critical_info:
                lines += ["[수집된 중요 정보]"]
                for j, info in enumerate(cs.critical_info, 1):
                    lines.append(f"  {j}. {info}")
                lines.append("")

            lines += [sep, ""]

        lines += [
            "─" * 60,
            f"총 고객사 수    : {len(customer_summaries):>6,}개",
            f"총 요약 글자 수 : {total_chars:>10,}자",
            f"총 중요 정보 수 : {total_critical:>6,}개",
        ]

        log_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        print(f"[로그] output/customer_summaries.txt 저장 완료  ({len(customer_summaries)}개 고객사)")

    def _write_extracted(self, ts: str) -> None:
        lines = [f"[추출 파일 목록]  {ts}", "=" * 60, ""]
        orig_total = sum(r.char_count for r in self._extracted)
        samp_total = sum(r.sampled_count for r in self._extracted)
        for r in sorted(self._extracted, key=lambda x: x.score, reverse=True):
            trunc = f" → {r.sampled_count:,}자(5,000자 제한)" if r.sampled_count < r.char_count else ""
            lines.append(
                f"[{r.score:3d}점/{r.label}]  {r.display_name}  "
                f"({r.char_count:,}자{trunc})"
            )
        lines += [
            "",
            f"총 {len(self._extracted)}개  |  원문 {orig_total:,}자  →  샘플 {samp_total:,}자",
        ]
        (_OUTPUT_DIR / "extracted_files.txt").write_text("\n".join(lines), encoding="utf-8", newline="\n")

    def _write_excluded(self, ts: str) -> None:
        lines = [f"[제외 파일 목록]  {ts}", "=" * 60, ""]
        for r in self._excluded:
            lines.append(f"{r.reason}  {r.display_name}")
        lines += ["", f"총 {len(self._excluded)}개 파일 제외"]
        (_OUTPUT_DIR / "excluded_files.txt").write_text("\n".join(lines), encoding="utf-8", newline="\n")

    def _write_errors(self, ts: str) -> None:
        lines = [f"[추출 오류 파일]  {ts}", "=" * 60, ""]
        for r in self._errors:
            lines += [f"  파일명: {r.display_name}", f"  오류  : {r.error}", ""]
        lines.append(f"총 {len(self._errors)}개 파일 오류")
        (_OUTPUT_DIR / "extraction_errors.txt").write_text("\n".join(lines), encoding="utf-8", newline="\n")

    def _write_summary(self, ts: str) -> None:
        s = self.get_summary_dict()

        def _pct(v: float) -> str:
            return f"{v:>9.1f}%"

        lines = [
            f"[분석 요약]  {ts}",
            "=" * 60,
            f"실행 모드             : {self._run_mode}",
            f"분석 상태             : {self._analysis_status}",
        ]

        if self._analysis_status == "사용자 중단":
            lines += [
                f"중단 시각             : {self._cancelled_time}",
                f"중단 위치             : {self._cancelled_at}",
                f"처리 완료 파일        : {self._processed_count:>8,}개",
                f"남은 파일             : {self._remaining_count:>8,}개",
            ]

        lines += [
            "─" * 60,
            "[노이즈 제거 통계]",
            f"  노이즈 제거 전      : {s['noise_before']:>8,}개",
            f"  시스템 파일 제외    : {s['noise_sys']:>8,}개",
            f"  로그/임시 파일 제외 : {s['noise_log']:>8,}개",
            f"  라이브러리 파일 제외: {s['noise_lib']:>8,}개",
            f"  노이즈 제거 후      : {s['noise_after']:>8,}개",
            f"  노이즈 제거율       : {s['noise_pct']:>8.1f}%",
            "─" * 60,
            f"수집 총 파일 수       : {s['total_files']:>8,}개",
            f"  노이즈 파일 제외    : {s['noise_filtered']:>8,}개",
            f"  30일 초과 제외      : {s['date_filtered']:>8,}개",
            f"  이미지 파일 제외    : {s['image_filtered']:>8,}개",
            f"  이미지 PDF 제외     : {s['image_pdf_filtered']:>8,}개",
            f"  구버전 문서 제외    : {s['dedup_filtered']:>8,}개",
            f"  유사 문서 제거      : {s['similar_filtered']:>8,}개",
            f"  상한선 초과 제외    : {s['limit_filtered']:>8,}개",
            f"최종 분석 대상        : {s['success_files']:>8,}개",
            f"프로젝트 수           : {s['project_count']:>8,}개",
            f"고객사 수             : {s['customer_count']:>8,}개",
            f"추출 실패             : {s['failed_files']:>8,}개",
            "─" * 60,
            f"원문 문자 수          : {s['original_chars']:>8,}자",
            f"5,000자 제한 후       : {s['sampled_chars']:>8,}자",
            f"문서요약 후           : {s['doc_summary_chars']:>8,}자",
            f"프로젝트요약 후       : {s['project_summary_chars']:>8,}자",
            f"고객사요약 후         : {s['customer_summary_chars']:>8,}자   ← 최종 GPT 전달",
            f"문자 절감률 (샘플링)  : {_pct(s['char_reduction_pct'])}",
            f"문자 절감률 (문서요약): {_pct(s['doc_summary_reduction_pct'])}",
            f"문자 절감률 (프로젝트): {_pct(s['project_summary_reduction_pct'])}",
            f"문자 절감률 (고객사)  : {_pct(s['customer_summary_reduction_pct'])}",
            f"예상 토큰 수          : {s['est_tokens']:>8,}  (고객사요약 기준)",
            f"예상 토큰 절감률      : {_pct(s['est_token_reduction_pct'])}",
        ]

        # 프로젝트 품질 통계
        if self._project_critical_counts:
            total_critical = sum(self._project_critical_counts.values())
            n_with_incomplete = sum(1 for v in self._project_incomplete_map.values() if v)
            n_with_risks = sum(1 for v in self._project_risk_map.values() if v)
            n_current = sum(
                1 for v in self._project_status_map.values() if "현재" in v
            )

            lines += [
                "─" * 60,
                "[프로젝트 품질 통계]",
                f"  GPT 전달 프로젝트 수  : {self._selected_project_count:>6}개",
                f"  총 중요 정보 수       : {total_critical:>6}개",
                f"  선택된 중요 정보 수   : {self._selected_critical_count:>6}개",
                f"  현재 진행 중 프로젝트 : {n_current:>6}개",
                f"  미완료 업무 보유      : {n_with_incomplete:>6}개 프로젝트",
                f"  리스크 보유           : {n_with_risks:>6}개 프로젝트",
            ]

            if self._project_critical_counts:
                lines += ["", "  프로젝트별 중요 정보 수:"]
                for key, cnt in sorted(
                    self._project_critical_counts.items(), key=lambda x: -x[1]
                ):
                    status = self._project_status_map.get(key, "")
                    lines.append(f"    {key}  →  중요정보 {cnt}개  [{status}]")

        if self._top_docs:
            lines += ["", "─" * 60, "[Top 50 우선 분석 문서]", ""]
            for rank, doc in enumerate(self._top_docs, 1):
                cur = " ★" if doc.is_current_work else "  "
                lines.append(
                    f"  {rank:>2}위{cur}  [{doc.score:3d}점]  "
                    f"수정:{doc.modified_dt}  {doc.display_name}"
                )

        (_OUTPUT_DIR / "analysis_summary.txt").write_text("\n".join(lines), encoding="utf-8", newline="\n")
