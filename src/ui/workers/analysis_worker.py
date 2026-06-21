from __future__ import annotations

"""
업무복원 분석 워커 — 12단계 최적화 파이프라인

Step 1.   30일 날짜 필터
Step 2.   이미지 확장자 필터
Step 3.   중복 문서 제거 (파일명 기반)
Step 4.   점수 계산 + 예상 시간 계산
Step 5.   텍스트 추출 + 이미지 PDF 제외
Step 6.   5,000자 샘플링
Step 7.   문서 단위 사전요약
Step 8.   프로젝트 그룹화 + 유사 문서 제거 + 상한선 + 구조화 프로젝트 요약
Step 8.5. 프로젝트 선택 화면 → 사용자 선택 대기 (GUI 스레드)
Step 8.7. 고객사 그룹화 + AI 고객사 요약 생성
Step 9.   토큰/비용 계산 → 사용자 승인 요청 (GUI 스레드)
Step 10.  AI 업무복원 분석 (고객사 요약 기반)

목표: 1,245개 파일 → 128개 문서 → 12개 프로젝트 → 선택 → 8개 고객사 → GPT
"""

import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.config.settings import Settings
from src.core.ai_client import AIClient
from src.core.action_plan_engine import ActionPlanEngine, write_action_plan_report
from src.core.analysis_quality_validator import (
    AnalysisQualityValidator,
    write_analysis_quality_report,
)
from src.core.business_context_engine import (
    BusinessContextEngine,
    write_business_context_report,
)
from src.core.category_discovery_engine import (
    CategoryDiscoveryEngine,
    write_category_discovery_report,
)
from src.core.deduplicator import deduplicate
from src.core.document_family import (
    DocumentFamilyEngine,
    write_document_families_report,
)
from src.core.document_summarizer import DocumentInfo, DocumentSummarizer
from src.core.document_value_score import (
    calculate_document_value_score,
    write_document_value_score_report,
)
from src.core.extractor import FileExtractor
from src.core.file_classifier import FileClassifier, THRESHOLD_MUST
from src.core.output_logger import OutputLogger
from src.core.report_reconstruction import (
    reconstruct_report_units,
    write_report_reconstruction_report,
)
from src.core.customer_summarizer import (
    CustomerSummarizer,
    build_eval_from_customer_summaries,
    extract_customer_name_from_key,
)
from src.core.project_summarizer import ProjectSummarizer
from src.core.representative_document_selector import (
    RepresentativeDocumentSelector,
    write_representative_documents_report,
)
from src.core.text_sampler import sample_text, MAX_CHARS_PER_FILE
from src.core.token_estimator import build_cost_info, EST_OUTPUT_TOKENS
from src.core.work_cluster_engine import (
    WorkClusterEngine,
    write_work_clusters_report,
    write_work_unit_detection_report,
)
from src.core.work_status_engine import WorkStatusEngine, write_work_status_report
from src.core.work_unit_resolver import WorkUnitResolver, write_work_unit_resolver_report
from src.core.work_unit_normalizer import WorkUnitNormalizer, write_work_unit_normalizer_report

# ── 상수 ──────────────────────────────────────────────────────────────
_DATE_WINDOW_DAYS = 30
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
_IMAGE_PDF_MIN_CHARS = 80
_ZIP_EXTS = {".zip"}

# 시간 예측 상수 (초)
_EST_EXTRACT_SEC = 0.25
_EST_DOC_SUMMARY_AI_BATCH = 3.5   # 3개 묶음 1회 AI call
_EST_PROJ_SUMMARY_SEC = 4.0       # 프로젝트 1개당
_EST_MAIN_AI_SEC = 45

# ── 노이즈 제거 규칙 ──────────────────────────────────────────────────
# 절대 제외 폴더: 위치·깊이 무관하게 항상 노이즈로 판정
_NOISE_FOLDERS_STRICT: frozenset[str] = frozenset({
    "node_modules", ".venv", "venv", "site-packages",
    "__pycache__", ".git", ".svn", ".cache", ".next", "coverage",
})

# 맥락 의존 제외 폴더: 의미 있는 비드라이브 부모 폴더가 2개 이상일 때만 제외
# → 프로젝트 루트 폴더명이 이 목록에 해당하더라도 보존됨
#   (예) C:\projects\output\ → meaningful_parents=['projects'] = 1 → 보존
#        C:\dev\my-project\output\ → meaningful_parents=['dev','my-project'] = 2 → 제외
_NOISE_FOLDERS_CONTEXT: frozenset[str] = frozenset({
    # 빌드/컴파일
    "dist", "build", "vendor",
    # 임시/로그
    "tmp", "temp", "logs", "log", "debug",
    # 시스템 결과물 폴더 (output 포함)
    "output", "outputs",
    "report", "reports",
    "result", "results",
    "analysis", "analyses",
    "cache", ".cache",
})

# 기존 코드 호환용 합집합
_NOISE_FOLDERS = _NOISE_FOLDERS_STRICT | _NOISE_FOLDERS_CONTEXT

# 파일명(확장자 제외) 완전 일치 제외 목록
_NOISE_FILENAMES: frozenset[str] = frozenset({
    "LICENSE", "COPYING", "NOTICE", "ThirdPartyNotice", "ThirdPartyNotices",
    "README", "CHANGELOG",
    "package-lock", "yarn.lock", "pnpm-lock",
    "build-output", "build-stderr",
    # 이 시스템이 생성하는 output/ 파일들 (완전 일치)
    "analysis_summary", "category_score", "customer_summaries",
    "debug_prompt", "document_summaries", "excluded_files", "extracted_files",
    "extraction_errors", "final_eval_input", "project_mapping",
    "project_summaries", "report_validation", "work_structure",
    "draft_report", "final_report_input", "final_report_output",
    "error", "requirements",
    # 추가: 자동 생성 보고서 파일명
    "test_report", "test_mode_analysis",
    "workflow_report",
    "project_priority", "project_importance",
    "noise_filter_report",
    "customer_count_report",
})

# 파일명(확장자 제외) 에 이 문자열이 포함되면 노이즈로 판정 (부분 일치, 소문자)
_NOISE_FILENAME_CONTAINS: frozenset[str] = frozenset({
    "build-output", "build-stderr", "tmp-", "temp-",
    "package-lock", "yarn.lock", "pnpm-lock",
    ".cache", "-cache",
    "node-modules", "node_modules",
})

# 파일명(소문자) 이 정확히 이 값이면 노이즈 (단음절·비업무 단어)
_NOISE_SINGLE_WORD_STEMS: frozenset[str] = frozenset({
    # 한국어 단일 명사 노이즈
    "메모", "memo", "notes", "note", "temp", "tmp",
    "개발", "마케팅", "기타", "참고", "임시", "초안", "draft",
    "test", "테스트", "sample", "샘플", "example",
    # 흔한 텍스트 단파일
    "readme", "license", "copying", "notice", "changelog",
    "requirements", "setup", "makefile", "dockerfile",
    "gitignore", "editorconfig", "prettierrc", "eslintrc",
})

# 노이즈로 간주할 확장자 (파일명 분석과 무관하게 제외)
_NOISE_EXTENSIONS: frozenset[str] = frozenset({
    ".env", ".lock", ".log", ".tmp", ".cache",
    ".idea", ".iml", ".class", ".pyc", ".pyo",
    ".map", ".d.ts",
})

# 확장자 우선순위 (높을수록 우선, 파이프라인 필터용)
_EXT_PRIORITY: dict[str, int] = {
    ".docx": 5, ".xlsx": 5, ".pptx": 5, ".pdf": 5, ".hwp": 5, ".hwpx": 5,
    ".md": 4,
    ".txt": 3, ".csv": 3,
    ".js": 1, ".ts": 1, ".tsx": 1, ".jsx": 1,
    ".json": 1, ".yaml": 1, ".yml": 1, ".xml": 1, ".py": 1,
}

# 산출물 점수 가중치 (주요 산출물 선정용, 음수 허용)
# ※ 업무 문서 (docx/xlsx/pdf 등) 최우선, 코드 파일 (py/ts/js)은 중간 점수,
#   시스템 자동 생성 파일 (txt/log/json 등)은 낮은 점수
_EXT_DELIVERABLE_WEIGHT: dict[str, int] = {
    # 업무 문서 (최우선)
    ".docx": 30, ".xlsx": 25, ".pptx": 25, ".hwp": 25, ".hwpx": 25,
    ".pdf": 20,
    ".md": 15,
    ".csv": 5,
    # 코드 파일 (개발 프로젝트의 실제 산출물 → 중간 점수)
    ".py":  15,
    ".ts":  15, ".tsx": 15,
    ".js":  10, ".jsx": 10,
    # 설정/데이터 파일 (낮은 점수)
    ".json": -5, ".yaml": -5, ".yml": -5, ".xml": -5,
    # 시스템 자동 생성 텍스트 (낮은 점수)
    ".txt": -20, ".log": -30,
}

# 파일명(stem 소문자) 에 이 문자열이 포함되면 산출물 점수 +20 (업무 문서 성격)
_FNAME_BONUS_PATTERNS: tuple[str, ...] = (
    "설계서", "명세서", "기획안", "화면설계", "요구사항",
    "사양서", "정의서", "계획서", "제안서",
    "api", "erd", "spec", "design", "proposal", "manual", "guide", "가이드",
)

# 파일명(stem 소문자) 에 이 문자열이 포함되면 산출물 점수 -30 (시스템 산출물)
_FNAME_STRONG_PENALTY_PATTERNS: tuple[str, ...] = (
    "test_report", "test_mode_analysis",
    "noise_filter", "noise_filter_report",
    "workflow_report",
    "category_score",
    "project_mapping", "project_priority", "project_importance",
    "analysis_summary",
    "draft_report", "final_report",
    "debug_prompt",
    "document_summaries", "project_summaries", "customer_summaries",
    "customer_count_report",
    "excluded_files", "extracted_files", "extraction_errors",
    "report_validation", "final_eval_input",
)

# 파일명이 이 단어 단독이거나 밑줄 경계로 시작/끝나면 산출물 점수 -15
# ※ 코드 확장자(.py/.ts/.js 등)는 이 패널티에서 면제됨
_FNAME_WEAK_PENALTY_WORDS: tuple[str, ...] = (
    "report", "summary", "test", "output",
    # "analysis", "debug" 는 코드 모듈명(analysis_worker, debug_utils)과 충돌하여 제거
)

# 약 패널티를 면제하는 코드 확장자
_CODE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".cs", ".java", ".cpp", ".c", ".go", ".rs", ".rb", ".swift", ".kt",
})

# 키워드 추출 시 제거할 노이즈 단어 (시스템어·연도·버전·보고서 시스템 단어)
_KW_NOISE_WORDS: frozenset[str] = frozenset({
    # 기술 시스템어
    "node", "modules", "runtime", "dist", "build", "vendor", "license",
    "system", "index", "loader", "compiled", "package", "setup",
    "requirements", "cache", "temp", "tmp",
    # 보고서·분석 시스템 키워드
    "report", "test", "output", "summary", "debug", "analysis",
    "handover", "filter", "workflow", "noise", "draft",
    "category", "project", "customer", "document",
    # 경로 컴포넌트 (dev, src, docs 등 의미 없는 경로 조각)
    "dev", "src", "docs", "doc", "lib", "libs", "bin",
    "proj", "projects", "apps", "pkg",
    "review", "main", "core", "utils", "util", "common",
    # 연도·버전
    "2023", "2024", "2025", "2026", "2027",
    "v1", "v10", "v11", "v12", "v2", "v3", "v4",
})


def is_noise_path(abs_path: str) -> tuple[bool, str]:
    """파일 경로가 노이즈(시스템/라이브러리/빌드/로그/임시) 파일인지 판정한다.

    판정 순서:
      1. 경로 중 노이즈 폴더 포함 여부 (폴더 부분만 검사)
      2. 파일명(확장자 제외) 완전 일치
      3. 파일명(소문자) 에 노이즈 패턴 부분 포함 여부

    Returns:
        (is_noise, reason_str)
    """
    p = Path(abs_path)
    parts = p.parts

    # 1. 폴더 제외 규칙
    for i, folder_part in enumerate(parts[:-1]):
        fl = folder_part.lower()
        # 절대 제외 (위치 무관)
        if fl in _NOISE_FOLDERS_STRICT:
            return True, f"[노이즈 폴더] {folder_part}"
        # 맥락 의존 제외: 드라이브 루트를 제외한 의미 있는 부모가 2개 이상일 때만 제외
        # 예) C:\dev\ai-handover-system\build\ → 부모 ['dev','ai-handover-system'] = 2개 → 제외
        #    C:\dev\build\ → 부모 ['dev'] = 1개 → 프로젝트 루트이므로 보존
        if fl in _NOISE_FOLDERS_CONTEXT:
            # 의미 있는 부모 카운트: 드라이브 루트(C:\), 슬래시, 단일 구분자 제외
            meaningful_parents = [
                pt for pt in parts[:i]
                if len(pt) > 1
                and pt not in ("/", "\\")
                and not (len(pt) <= 3 and pt[1:2] == ":")  # Windows drive root
            ]
            if len(meaningful_parents) >= 2:
                return True, f"[노이즈 폴더] {folder_part}"

    stem_lower = p.stem.lower()
    ext_lower  = p.suffix.lower()

    # 2. 노이즈 확장자 (무조건 제외)
    if ext_lower in _NOISE_EXTENSIONS:
        return True, f"[노이즈 확장자] {ext_lower}"

    # 3. 파일명 완전 일치
    if stem_lower in {n.lower() for n in _NOISE_FILENAMES}:
        return True, f"[노이즈 파일명] {p.stem}"

    # 4. 단어 노이즈 (단음절·비업무 단어 정확 일치)
    if stem_lower in _NOISE_SINGLE_WORD_STEMS:
        return True, f"[단어노이즈] {p.stem}"

    # 5. 파일명 부분 포함 (tmp-salary-out, build-output2 등 변형 대응)
    full_lower = (stem_lower + ext_lower)
    for pattern in _NOISE_FILENAME_CONTAINS:
        if pattern.lower() in full_lower:
            return True, f"[노이즈 패턴] {pattern}"

    return False, ""


def _deliverable_score(abs_path: str) -> int:
    """파일의 산출물 점수를 반환한다 (업무 관련성 + 확장자 + 파일명 가중치).

    노이즈 파일이면 최솟값(-9999)을 반환하여 후순위로 밀어낸다.

    점수 구성:
      확장자 기본점수 (_EXT_DELIVERABLE_WEIGHT)
      + 파일명 보너스 (+20, 설계서·명세서·기획안·API·ERD 등)
      + 강 패널티 (-30, test_report·noise_filter·analysis_summary 등)
      + 약 패널티 (-15, report·summary·debug·analysis·test 단어 단독)
    """
    is_n, _ = is_noise_path(abs_path)
    if is_n:
        return -9999
    p = Path(abs_path)
    ext = p.suffix.lower()
    stem_lower = p.stem.lower()

    score = _EXT_DELIVERABLE_WEIGHT.get(ext, 0)

    # 업무 문서 성격 보너스
    for pat in _FNAME_BONUS_PATTERNS:
        if pat in stem_lower:
            score += 20
            break

    # 시스템 산출물 강 패널티
    for pat in _FNAME_STRONG_PENALTY_PATTERNS:
        if pat in stem_lower:
            score -= 30
            break
    else:
        # 일반 시스템 키워드 약 패널티 (단어 단독 또는 밑줄 경계)
        # 코드 파일(.py/.ts 등)은 면제 — analysis_worker.py, debug_utils.ts 등 보호
        if ext not in _CODE_EXTENSIONS:
            for word in _FNAME_WEAK_PENALTY_WORDS:
                if (
                    stem_lower == word
                    or stem_lower.startswith(word + "_")
                    or stem_lower.endswith("_" + word)
                ):
                    score -= 15
                    break

    return score


def _noise_folder_reason(abs_path: str) -> str:
    """노이즈 폴더 제외 이유 반환 (비노이즈이면 빈 문자열)."""
    is_n, reason = is_noise_path(abs_path)
    return reason if is_n else ""


def _ext_priority(abs_path: str) -> int:
    """파일 확장자 우선순위 점수 반환 (높을수록 업무 문서 가능성 높음)."""
    ext = Path(abs_path).suffix.lower()
    return _EXT_PRIORITY.get(ext, 2)


def _fmt_date(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return "알 수 없음"


def _fmt_finish_time(remaining_sec: int) -> str:
    """현재 시각 + remaining_sec → 예상 종료 시각 (HH:MM:SS)"""
    finish = datetime.now().timestamp() + remaining_sec
    return datetime.fromtimestamp(finish).strftime("%H:%M:%S")


def _fmt_duration(seconds: int) -> str:
    if seconds <= 0:
        return "잠시 후"
    if seconds < 60:
        return f"약 {seconds}초"
    m, s = divmod(seconds, 60)
    return f"{m}분 {s:02d}초" if s else f"{m}분"


def _estimate(n_extract: int, n_high: int, n_medium: int, n_projects: int) -> dict:
    est_extract = n_extract * _EST_EXTRACT_SEC
    est_doc_sum = ((n_high + 2) // 3) * _EST_DOC_SUMMARY_AI_BATCH + n_medium * 0.01
    est_proj_sum = n_projects * _EST_PROJ_SUMMARY_SEC
    est_ai = float(_EST_MAIN_AI_SEC)
    est_total = est_extract + est_doc_sum + est_proj_sum + est_ai
    return {
        "est_extract_sec": max(1, int(est_extract)),
        "est_doc_summary_sec": max(1, int(est_doc_sum)),
        "est_proj_summary_sec": max(1, int(est_proj_sum)),
        "est_ai_sec": int(est_ai),
        "est_total_sec": max(1, int(est_total)),
    }


class AnalysisWorker(QThread):
    """10단계 업무복원 분석 파이프라인 (취소 + 사용자 승인 지원)."""

    finished = Signal(str)
    error = Signal(str)
    progress = Signal(str)
    extract_warnings = Signal(list)
    stats_ready = Signal(dict)
    time_estimated = Signal(dict)
    eta_updated = Signal(dict)       # {"stage", "done", "total", "remaining_sec", "expected_finish"}
    cancelled = Signal(dict)
    projects_ready = Signal(list)    # 프로젝트 선택 화면 표시 요청 (list[dict])
    approval_needed = Signal(dict)   # 사용자 승인 요청 (차단 후 재개)

    def __init__(
        self,
        settings: Settings,
        job_desc: str,
        file_display_map: dict[str, str],
        test_mode: bool = False,
        light_mode: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._job_desc = job_desc
        self._file_display_map = file_display_map
        self._test_mode = test_mode
        self._light_mode = light_mode and not test_mode  # test_mode 우선
        if test_mode:
            print("\n" + "=" * 60)
            print("[테스트 모드] GPT 호출 없이 규칙 기반으로 실행합니다.")
            print("[테스트 모드] 비용: $0  |  출력: output/test_report.docx")
            print("=" * 60 + "\n")
        elif light_mode:
            print("\n" + "=" * 60)
            print("[라이트 모드] 빠른 분석 / 저비용 모드로 실행합니다.")
            print("[라이트 모드] 문서샘플 2,000자 / 문서요약 300자 / 5개 섹션")
            print("=" * 60 + "\n")
        self.cancel_requested: bool = False
        self._approval_event = threading.Event()
        self._approval_granted: bool = False
        # 프로젝트 선택 동기화
        self._projects_event = threading.Event()
        self._selected_project_keys: list[str] | None = None
        self._project_summaries: list = []   # ProjectSummary 목록 (run() 중 저장)

    def request_cancel(self) -> None:
        self.cancel_requested = True
        self._approval_event.set()    # 승인 대기 중이면 즉시 해제
        self._projects_event.set()    # 프로젝트 선택 대기 중이면 즉시 해제
        print("[워커] 취소 요청 수신")

    def grant_approval(self, approved: bool) -> None:
        """UI 스레드에서 호출: 분석 진행(True) 또는 취소(False)"""
        self._approval_granted = approved
        self._approval_event.set()

    def set_selected_project_keys(self, keys: list[str] | None) -> None:
        """UI 스레드에서 호출: 선택된 프로젝트 키 목록. None이면 취소."""
        self._selected_project_keys = keys
        self._projects_event.set()

    def _cancelled(self) -> bool:
        return self.cancel_requested

    def _emit_eta(self, stage: str, done: int, total: int, remaining_sec: int) -> None:
        self.eta_updated.emit({
            "stage": stage,
            "done": done,
            "total": total,
            "remaining_sec": remaining_sec,
            "expected_finish": _fmt_finish_time(remaining_sec),
        })

    def _emit_cancelled(
        self, logger: OutputLogger, aborted_at: str, processed: int = 0, remaining: int = 0
    ) -> None:
        logger.set_cancelled(aborted_at, processed, remaining)
        logger.write_all()
        print(f"\n[워커] 분석 중단  위치: {aborted_at}  완료: {processed}  남은: {remaining}")
        self.cancelled.emit({"aborted_at": aborted_at, "processed": processed, "remaining": remaining})

    @staticmethod
    def _write_error_log(current_step: str, exc: BaseException) -> None:
        """오류 발생 시 output/error.log에 상세 정보를 저장한다."""
        output_dir = Path(__file__).resolve().parents[3] / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tb_text = traceback.format_exc()
        lines = [
            f"[{ts}]",
            "",
            f"현재 Step:",
            f"  {current_step}",
            "",
            f"Exception:",
            f"  {type(exc).__name__}: {exc}",
            "",
            "Traceback:",
            tb_text,
            "=" * 60,
            "",
        ]
        log_path = output_dir / "error.log"
        try:
            existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        except Exception:
            existing = ""
        log_path.write_text(existing + "\n".join(lines), encoding="utf-8")
        print(f"[오류 로그] output/error.log 저장 완료: {log_path}")

    # ── 메인 파이프라인 ────────────────────────────────────────────────
    def run(self) -> None:
        _step = "초기화"          # 현재 실행 중인 Step (오류 로그용)
        logger: OutputLogger | None = None
        try:
            logger = OutputLogger()
            classifier = FileClassifier()
            extractor = FileExtractor()
            items = list(self._file_display_map.items())
            logger.total_files = len(items)
            now = time.time()
            cutoff = now - _DATE_WINDOW_DAYS * 86400

            # ══ Step 0.5: 노이즈 파일 제거 (시스템/라이브러리/빌드/로그) ═
            _step = "Step 0.5: 노이즈 파일 제거"
            before_noise = len(items)
            self.progress.emit(f"노이즈 파일 제거 중... (총 {before_noise:,}개)")
            clean_items: list[tuple[str, str]] = []
            noise_sys = noise_log = noise_lib = 0
            for abs_path, display_name in items:
                is_n, reason = is_noise_path(abs_path)
                if is_n:
                    logger.record_excluded(display_name, reason)
                    logger.noise_filtered += 1
                    if "폴더" in reason:
                        if any(kw in reason for kw in ("node_modules", "venv", "site-packages", "vendor")):
                            noise_lib += 1
                        elif any(kw in reason for kw in ("logs", "log", "debug", "output", "tmp", "temp")):
                            noise_log += 1
                        else:
                            noise_sys += 1
                    else:
                        noise_sys += 1
                else:
                    clean_items.append((abs_path, display_name))
            items = clean_items
            logger.noise_sys = noise_sys
            logger.noise_log = noise_log
            logger.noise_lib = noise_lib
            after_noise = len(items)
            noise_total = before_noise - after_noise
            noise_pct = (noise_total / before_noise * 100) if before_noise > 0 else 0.0
            print(
                f"[Step 0.5] 노이즈 제거 완료: {before_noise:,}개 → {after_noise:,}개 "
                f"(제외 {noise_total:,}개 / {noise_pct:.1f}%)"
            )

            # ══ Step 1: 30일 날짜 필터 ═══════════════════════════════
            _step = "Step 1: 30일 날짜 필터"
            print(f"\n[Step 1] 날짜 필터 시작  (총 {len(items):,}개 파일)")
            self.progress.emit(f"날짜 필터 적용 중... (총 {len(items):,}개)")
            date_ok: list[tuple[str, str, float, float]] = []
            for abs_path, display_name in items:
                if self._cancelled():
                    self._emit_cancelled(logger, "날짜 필터")
                    return
                try:
                    stat = Path(abs_path).stat()
                    mtime, ctime = stat.st_mtime, stat.st_ctime
                except OSError:
                    mtime = ctime = now
                if mtime >= cutoff or ctime >= cutoff:
                    date_ok.append((abs_path, display_name, mtime, ctime))
                else:
                    days = int((now - mtime) / 86400)
                    logger.record_excluded(display_name, f"[30일 초과] 마지막 수정 {days}일 전")
                    logger.date_filtered += 1

            print(f"[Step 1] 날짜 필터 완료: {len(items):,}개 → {len(date_ok):,}개 (제외 {logger.date_filtered:,})")

            # ══ Step 2: 이미지 확장자 필터 ═══════════════════════════
            _step = "Step 2: 이미지 확장자 필터"
            print(f"[Step 2] 이미지 필터 시작")
            non_image: list[tuple[str, str, float, float]] = []
            for abs_path, display_name, mtime, ctime in date_ok:
                if self._cancelled():
                    self._emit_cancelled(logger, "이미지 필터")
                    return
                ext = Path(abs_path).suffix.lower()
                if ext in _IMAGE_EXTS:
                    logger.record_excluded(display_name, f"[이미지 파일] {ext}")
                    logger.image_filtered += 1
                else:
                    non_image.append((abs_path, display_name, mtime, ctime))

            print(f"[Step 2] 이미지 필터 완료: 이미지 {logger.image_filtered:,}개 제외 → 남은 {len(non_image):,}개")

            # ══ Step 3: 중복 문서 제거 (파일명 기반) ════════════════
            _step = "Step 3: 중복 문서 제거"
            print(f"[Step 3] 중복 문서 제거 시작  ({len(non_image):,}개)")
            self.progress.emit(f"중복 문서 감지 중... ({len(non_image):,}개)")
            dedup_result = deduplicate([(p, d, m) for p, d, m, _ in non_image])
            keep_set = set(dedup_result.keep)
            winner_map = {dup: winner for dup, winner in dedup_result.duplicates}
            for abs_path, display_name, mtime, ctime in non_image:
                if abs_path not in keep_set:
                    winner_name = Path(winner_map.get(abs_path, "")).name
                    logger.record_excluded(display_name, f"[구버전 문서] 최신본: {winner_name}")
                    logger.dedup_filtered += 1
            after_dedup = [(p, d, m, c) for p, d, m, c in non_image if p in keep_set]

            print(f"[Step 3] 중복 제거 완료: 구버전 {logger.dedup_filtered:,}개 제외 → 남은 {len(after_dedup):,}개")

            # ══ Step 4: 점수 계산 + 예상 시간 ════════════════════════
            _step = "Step 4: 업무 관련성 점수 계산"
            print(f"[Step 4] 점수 계산 시작  ({len(after_dedup):,}개)")
            self.progress.emit(f"업무 관련성 점수 계산 중... ({len(after_dedup):,}개)")
            scored: list[tuple[str, str, float, float, object]] = []
            for abs_path, display_name, mtime, ctime in after_dedup:
                if self._cancelled():
                    self._emit_cancelled(logger, "점수 계산")
                    return
                sr = classifier.score(abs_path, display_name, "", mtime, ctime)
                scored.append((abs_path, display_name, mtime, ctime, sr))
            scored.sort(key=lambda x: x[4].final_score, reverse=True)

            n_targets = len(scored)
            n_high = sum(1 for *_, sr in scored if sr.final_score >= THRESHOLD_MUST)
            n_medium = n_targets - n_high
            # 프로젝트 수: 상위 폴더 종류 수 추정
            n_projects_est = max(1, len({
                d.split("/")[0] if "/" in d else "기타"
                for _, d, _, _, _ in scored
            }))
            est = _estimate(n_targets, n_high, n_medium, n_projects_est)

            self.time_estimated.emit({
                "total_files": len(items),
                "date_filtered": logger.date_filtered,
                "image_filtered": logger.image_filtered,
                "dedup_filtered": logger.dedup_filtered,
                "analysis_targets": n_targets,
                **est,
            })
            print(
                f"[Step 4] 점수 계산 완료: {n_targets:,}개 (높음 {n_high} / 중간 {n_medium})\n"
                f"         예상 합계: {_fmt_duration(est['est_total_sec'])}"
            )

            # ══ Step 4.5: 비지원 확장자 사전 제거 ═══════════════════
            _step = "Step 4.5: 비지원 확장자 필터"
            _supported_exts = FileExtractor.SUPPORTED | {".zip"}
            unsupported_items: list[tuple] = []
            supported_items:   list[tuple] = []
            for _item in scored:
                _ext = Path(_item[0]).suffix.lower()
                if _ext not in _supported_exts:
                    unsupported_items.append(_item)
                else:
                    supported_items.append(_item)

            for abs_path, display_name, *_ in unsupported_items:
                _ext = Path(abs_path).suffix.lower()
                logger.record_excluded(display_name, f"[미지원 형식] {_ext}")
            if unsupported_items:
                _bad_exts = sorted({Path(x[0]).suffix.lower() for x in unsupported_items})
                print(
                    f"[Step 4.5] 미지원 확장자 제외: {len(unsupported_items)}개 "
                    f"({', '.join(_bad_exts)})"
                )
            scored = supported_items

            # ══ Step 5: 텍스트 추출 + 이미지 PDF 제외 ═══════════════
            _step = "Step 5: 텍스트 추출 + 이미지 PDF 제외"
            print(f"[Step 5] 텍스트 추출 시작  ({len(scored):,}개)")
            extract_times:  list[float] = []
            extracted_raw:  list[tuple[str, str, float, float, object, str]] = []
            failed:         list[str] = []                       # UI 경고용 (기존 형식)
            failed_details: list[tuple[str, str, str]] = []     # (display_name, ext, exc_msg)
            total_extract = len(scored)

            for idx, (abs_path, display_name, mtime, ctime, sr) in enumerate(scored, 1):
                if self._cancelled():
                    self._emit_cancelled(logger, "텍스트 추출", processed=idx - 1, remaining=total_extract - idx + 1)
                    return

                name = Path(abs_path).name
                ext  = Path(abs_path).suffix.lower()
                t0   = time.time()

                if extract_times and idx % 10 == 0:
                    avg = sum(extract_times) / len(extract_times)
                    remaining_sec = int(
                        (total_extract - idx) * avg
                        + est["est_doc_summary_sec"]
                        + est["est_proj_summary_sec"]
                        + est["est_ai_sec"]
                    )
                    self._emit_eta("텍스트 추출", idx, total_extract, remaining_sec)

                self.progress.emit(f"텍스트 추출 ({idx}/{total_extract})  [{sr.final_score}점] {name}")
                try:
                    raw_text = extractor.extract(abs_path)
                    extract_times.append(time.time() - t0)
                    is_pdf = ext == ".pdf"
                    if is_pdf and len(raw_text.strip()) < _IMAGE_PDF_MIN_CHARS:
                        logger.record_excluded(display_name, "[이미지 PDF] 텍스트 추출 실패")
                        logger.image_pdf_filtered += 1
                    else:
                        extracted_raw.append((abs_path, display_name, mtime, ctime, sr, raw_text))
                except Exception as exc:
                    exc_msg = str(exc)
                    failed.append(f"{display_name} ({exc_msg})")
                    failed_details.append((display_name, ext, exc_msg))
                    logger.record_error(display_name, exc_msg)
                    extract_times.append(time.time() - t0)

            # ── Step 5 완료: 오류 파일 즉시 저장 ──────────────────────
            _save_extraction_errors_txt(failed_details)
            if failed:
                self.extract_warnings.emit(failed)
            print(
                f"[Step 5] 추출 완료: 성공 {len(extracted_raw):,}개 "
                f"이미지PDF {logger.image_pdf_filtered:,}개 실패 {len(failed):,}개"
            )

            # ── Step 5: 성공 0건 → 즉시 종료 (진짜 원인 노출) ─────────
            if not extracted_raw:
                top_n = failed_details[:5]
                err_lines = [
                    "분석 가능한 문서를 추출하지 못했습니다.",
                    "",
                    f"성공: 0개",
                    f"실패: {len(failed_details)}개",
                ]
                if top_n:
                    err_lines.append("")
                    err_lines.append("대표 오류:")
                    for fname, _ext, emsg in top_n:
                        err_lines.append(f"  * {fname} : {emsg}")
                err_lines += [
                    "",
                    "자세한 내용은 output/extraction_errors.txt 를 확인하세요.",
                ]
                self.error.emit("\n".join(err_lines))
                print(f"[Step 5] 추출 성공 0건 → 분석 중단  (실패 {len(failed_details)}건)")
                return

            if self._cancelled():
                self._emit_cancelled(logger, "텍스트 추출 완료 후", processed=len(extracted_raw))
                return

            # ══ Step 6: 5,000자 샘플링 ═══════════════════════════════
            _step = "Step 6: 5,000자 샘플링"
            print(f"[Step 6] 5,000자 샘플링 시작  ({len(extracted_raw):,}개)")
            original_total = sampled_total = 0
            extracted: list[tuple[str, str, float, float, object, str]] = []
            for abs_path, display_name, mtime, ctime, sr, raw in extracted_raw:
                sampled, orig_len = sample_text(raw, MAX_CHARS_PER_FILE)
                original_total += orig_len
                sampled_total += len(sampled)
                extracted.append((abs_path, display_name, mtime, ctime, sr, sampled))
                logger.record_extracted(display_name, sr.final_score, sr.label, orig_len, len(sampled))

            print(
                f"[Step 6] 5,000자 샘플링: 원문 {original_total:,}자 → "
                f"샘플 {sampled_total:,}자 "
                f"({max(0,(1-sampled_total/original_total)*100):.1f}% 절감)"
                if original_total else "[Step 6] 샘플링할 파일 없음"
            )

            # ══ Step 7: 문서 단위 사전요약 ════════════════════════════
            _step = "Step 7: 문서 단위 사전요약"
            print(f"[Step 7] 문서요약 시작  ({len(extracted):,}개 문서)")
            if self._cancelled():
                logger.set_char_stats(original_total, sampled_total, 0, 0)
                self._emit_cancelled(logger, "문서 요약 시작 전", processed=len(extracted))
                return

            _step = "Step 7-1: DocumentInfo 객체 생성"
            print(f"[Step 7-1] DocumentInfo 생성 시작")
            doc_infos: list[DocumentInfo] = []
            for abs_path, display_name, mtime, ctime, sr, sampled in extracted:
                doc_infos.append(DocumentInfo(
                    abs_path=abs_path,
                    display_name=display_name,
                    score=sr.final_score,
                    relevance=sr.relevance_score,
                    modified_dt=_fmt_date(mtime),
                    created_dt=_fmt_date(ctime),
                    work_status=sr.work_status,
                    is_current_work=sr.is_current_work,
                    text=sampled,
                ))
            print(f"[Step 7-1] DocumentInfo 생성 완료: {len(doc_infos):,}개")

            n_high_final = sum(1 for d in doc_infos if d.score >= THRESHOLD_MUST)
            n_med_final = len(doc_infos) - n_high_final
            doc_summary_times: list[float] = []

            def _doc_prog(msg: str) -> None:
                self.progress.emit(msg)
                if doc_summary_times:
                    done_b = len(doc_summary_times)
                    avg_b = sum(doc_summary_times) / done_b
                    rem_b = max(0, (n_high_final + 2) // 3 - done_b)
                    remaining_sec = int(
                        rem_b * avg_b
                        + est["est_proj_summary_sec"]
                        + est["est_ai_sec"]
                    )
                    self._emit_eta("문서 요약", done_b * 3, n_high_final, remaining_sec)
                doc_summary_times.append(time.time())

            _step = "Step 7-2: 문서요약 (AI 또는 테스트 모드)"
            if self._test_mode:
                print(f"[Step 7-2][테스트] 규칙 기반 문서요약 생성 ({len(doc_infos)}개)")
                doc_summaries = [_test_doc_summary(d) for d in doc_infos]
                print(f"[Step 7-2][테스트] 규칙 기반 문서요약 완료: {len(doc_summaries)}개")
            else:
                print(
                    f"[Step 7-2] AI 문서요약 호출 시작  "
                    f"(AI 대상 {n_high_final}개 / 룰기반 {n_med_final}개)"
                )
                doc_summarizer = DocumentSummarizer(self._settings)
                doc_summaries = doc_summarizer.summarize_all(
                    doc_infos,
                    progress_cb=_doc_prog,
                    cancel_fn=self._cancelled,
                    light_mode=self._light_mode,
                )
                print(f"[Step 7-2] AI 문서요약 호출 완료: {len(doc_summaries):,}개 결과")

            if self._cancelled():
                logger.set_char_stats(original_total, sampled_total, 0, 0)
                self._emit_cancelled(logger, "문서 요약 중", processed=len(doc_summaries), remaining=len(doc_infos) - len(doc_summaries))
                return

            doc_summary_chars = sum(s.summary_chars for s in doc_summaries)
            print(f"[Step 7] 문서요약 완료: {len(doc_summaries):,}개  {doc_summary_chars:,}자")

            _step = "Step 7-3: Document Value Score"
            summary_by_name = {
                s.display_name: s.summary_text for s in doc_summaries
            }
            dvs_rows = []
            for doc in doc_infos:
                try:
                    stat_mtime = Path(doc.abs_path).stat().st_mtime
                except OSError:
                    stat_mtime = None
                dvs_rows.append(
                    calculate_document_value_score(
                        file_path=doc.abs_path,
                        display_name=doc.display_name,
                        classifier_score=doc.score,
                        deliverable_score=_deliverable_score(doc.abs_path),
                        modified_time=stat_mtime,
                        summary_text=summary_by_name.get(doc.display_name, ""),
                        metadata={"modified_dt": doc.modified_dt},
                    )
                )
            dvs_path = write_document_value_score_report(dvs_rows)
            print(f"[Step 7-3] Document Value Score saved: {dvs_path}")

            _step = "Step 7-4: 대표문서 선정"
            rep_selector = RepresentativeDocumentSelector()
            docs_by_project: dict[str, list] = {}
            for doc_summary in doc_summaries:
                parts = doc_summary.display_name.replace("\\", "/").split("/")
                project_key = parts[0] if len(parts) >= 2 else "기타 (최상위 파일)"
                docs_by_project.setdefault(project_key, []).append(doc_summary)

            family_engine = DocumentFamilyEngine()
            family_results = {
                project_key: family_engine.group_document_families(project_docs)
                for project_key, project_docs in docs_by_project.items()
            }
            family_path = write_document_families_report(family_results)
            print(f"[Step 7-4] 문서군 분석 결과 저장: {family_path}")

            work_cluster_engine = WorkClusterEngine()
            work_clusters = work_cluster_engine.group_work_clusters(doc_summaries)
            cluster_path = write_work_clusters_report(work_clusters)
            print(f"[Step 7-5] 업무 클러스터 저장: {cluster_path}")
            work_unit_detection = work_cluster_engine.detect_work_unit_mode(
                doc_summaries,
                work_clusters,
            )
            detection_path = write_work_unit_detection_report(work_unit_detection)
            print(
                f"[Step 7-6] 업무 단위 구조 판정: "
                f"{work_unit_detection.mode} ({detection_path})"
            )
            work_unit_result = WorkUnitResolver().resolve(
                work_clusters=work_clusters,
                document_families=family_results,
                representative_documents=None,
                project_summaries=None,
            )
            resolver_path = write_work_unit_resolver_report(work_unit_result)
            print(f"[Step 7-7] 실행 단위 Resolver 저장: {resolver_path}")

            normalized_work_units = WorkUnitNormalizer().normalize_work_units(
                work_unit_result,
                work_clusters=work_clusters,
                document_families=family_results,
            )
            normalizer_path = write_work_unit_normalizer_report(normalized_work_units)
            print(f"[Step 7-8] 실행 단위 Normalizer 저장: {normalizer_path}")

            category_result = CategoryDiscoveryEngine().discover(
                user_job_categories=self._job_desc,
                document_summaries=doc_summaries,
                work_clusters=work_clusters,
                representative_docs=None,
                project_summaries=None,
            )
            category_path = write_category_discovery_report(category_result)
            print(f"[Step 7-9] 업무 카테고리 자동 발견 저장: {category_path}")

            representative_results = {
                project_key: rep_selector.select_representative_documents(
                    project_key, project_docs
                )
                for project_key, project_docs in docs_by_project.items()
            }
            rep_path = write_representative_documents_report(representative_results)
            print(f"[Step 7-4] 대표문서 선정 결과 저장: {rep_path}")

            # ══ Step 8: 프로젝트 그룹화 + 유사도 + 상한선 + 프로젝트 요약 ═
            _step = "Step 8: 프로젝트 그룹화 + 요약"
            print(f"[Step 8] 프로젝트요약 시작  (문서 {len(doc_summaries):,}개)")
            if self._cancelled():
                self._emit_cancelled(logger, "프로젝트 요약 시작 전", processed=len(doc_summaries))
                return

            _step = "Step 8-1: 프로젝트 그룹화"
            print(f"[Step 8-1] 프로젝트 그룹화 시작")
            proj_start = time.time()
            proj_summarizer = ProjectSummarizer(self._settings)

            _step = "Step 8-2: 프로젝트요약 (AI 또는 테스트 모드)"
            if self._test_mode:
                print(f"[Step 8-2][테스트] 규칙 기반 프로젝트요약 생성")
                project_summaries, similar_excluded, limit_excluded = \
                    _test_project_summaries(proj_summarizer, doc_summaries)
                print(f"[Step 8-2][테스트] 규칙 기반 프로젝트요약 완료: {len(project_summaries)}개")
            else:
                print(f"[Step 8-2] AI 프로젝트요약 생성 시작")
                project_summaries, similar_excluded, limit_excluded = proj_summarizer.summarize_all(
                    doc_summaries,
                    progress_cb=lambda msg: self.progress.emit(msg),
                    cancel_fn=self._cancelled,
                    light_mode=self._light_mode,
                )
                print(f"[Step 8-2] AI 프로젝트요약 완료: {len(project_summaries)}개")

            for ps in project_summaries:
                rep_result = representative_results.get(ps.project_key)
                if not rep_result:
                    continue
                if not ps.representative_docs:
                    ps.representative_docs = [
                        d.display_name for d in rep_result.representative_docs
                    ]
                if not ps.supporting_docs:
                    ps.supporting_docs = [
                        d.display_name for d in rep_result.supporting_docs
                    ]
                if not ps.reference_docs:
                    ps.reference_docs = [
                        d.display_name for d in rep_result.reference_docs
                    ]

            report_reconstruction = reconstruct_report_units(
                work_unit_result,
                normalized_work_units,
                project_summaries=project_summaries,
                doc_summaries=doc_summaries,
            )
            reconstruction_path = write_report_reconstruction_report(report_reconstruction)
            print(f"[Step 8-2R] 보고서 재구성 저장: {reconstruction_path}")

            action_engine = ActionPlanEngine()
            action_plans = {}
            for ps in project_summaries:
                action_plan = action_engine.build_action_plan(
                    ps,
                    ps.representative_docs,
                    ps.supporting_docs,
                )
                ps.action_plan = action_plan
                ps.priority_tasks = action_plan.priority_tasks
                ps.action_plan_risks = action_plan.risks
                action_plans[ps.project_key] = action_plan
            action_path = write_action_plan_report(action_plans)
            print(f"[Step 8-3] 후임자 행동계획 저장: {action_path}")

            work_statuses = WorkStatusEngine().infer_from_work_clusters(
                work_clusters=work_clusters,
                representative_results=representative_results,
                document_families=family_results,
                action_plans=action_plans,
            )
            status_path = write_work_status_report(work_statuses)
            print(f"[Step 8-4] 업무 상태 추론 저장: {status_path}")

            business_contexts = BusinessContextEngine().build_contexts(
                project_summaries=project_summaries,
                representative_results=representative_results,
                action_plans=action_plans,
                work_statuses=work_statuses,
            )
            business_context_path = write_business_context_report(business_contexts)
            print(f"[Step 8-5] 비즈니스 컨텍스트 저장: {business_context_path}")

            quality_report = AnalysisQualityValidator().validate(
                document_families=family_results,
                work_clusters=work_clusters,
                representative_results=representative_results,
                work_statuses=work_statuses,
                work_unit_detection=work_unit_detection,
            )
            quality_path = write_analysis_quality_report(quality_report)
            print(
                f"[Step 8-6] 분석 품질 검증 저장: "
                f"{quality_path} (score {quality_report.overall_score})"
            )

            if self._cancelled():
                self._emit_cancelled(logger, "프로젝트 요약 중", processed=len(project_summaries))
                return

            similar_count = len(similar_excluded)
            limit_count = len(limit_excluded)
            proj_summary_chars = sum(ps.summary_chars for ps in project_summaries)
            print(
                f"[Step 8] 프로젝트요약 완료: {len(project_summaries)}개 프로젝트\n"
                f"         유사 제거 {similar_count}개  상한 제거 {limit_count}개\n"
                f"         소요: {time.time()-proj_start:.1f}초  요약: {proj_summary_chars:,}자"
            )

            _step = "Step 8-3: 프로젝트요약 로그 기록"
            print(f"[Step 8-3] 프로젝트요약 로그 기록 시작")
            # 로그에 제외 파일 기록
            for dn in similar_excluded:
                logger.record_excluded(dn, "[유사 문서 제거]")
                logger.similar_filtered += 1
            for dn in limit_excluded:
                logger.record_excluded(dn, "[프로젝트 상한선 초과]")
                logger.limit_filtered += 1
            logger.set_project_stats(len(project_summaries), similar_count, limit_count)
            print(f"[Step 8-3] 프로젝트요약 로그 기록 완료")

            # 워커에 프로젝트 요약 목록 저장 (선택 후 필터링에 사용)
            self._project_summaries = project_summaries

            # ── Step 8 완료: 프로젝트 0개 → 즉시 종료 ────────────────
            if not project_summaries:
                self.error.emit(
                    "분석할 프로젝트를 찾지 못했습니다.\n\n"
                    f"문서는 {len(doc_summaries)}개 추출되었으나 "
                    "프로젝트로 그룹화되지 않았습니다.\n\n"
                    "확인 사항:\n"
                    "  · 업무분장 입력란에 업무 목록을 입력했는지 확인\n"
                    "  · 더 많은 파일을 포함하거나 다른 폴더를 선택해 다시 시도"
                )
                print("[Step 8] 프로젝트 0개 → 분석 중단")
                return

            # ══ Step 8.5: 프로젝트 선택 화면 → 사용자 선택 대기 ═════
            _step = "Step 9: 프로젝트 선택 다이얼로그"
            if self._test_mode:
                # 테스트 모드: 전체 프로젝트 자동 선택 (다이얼로그 스킵)
                self._selected_project_keys = [
                    ps.project_key for ps in project_summaries
                ]
                selected_keys_set = set(self._selected_project_keys)
                print(
                    f"\n[TEST MODE] 프로젝트 선택 다이얼로그 생략\n"
                    f"[TEST MODE] 자동 선택 프로젝트 수: {len(self._selected_project_keys)}\n"
                )
            else:
                print(f"[Step 9] 프로젝트 선택 다이얼로그 요청  ({len(project_summaries)}개 프로젝트)")
                projects_data = [
                    {
                        "project_key":        ps.project_key,
                        "project_name":       ps.project_name,
                        "client_name":        ps.client_name,
                        "doc_count":          ps.doc_count,
                        "critical_info_count": len(ps.critical_info),
                        "current_status":     ps.current_status,
                        "summary_chars":      ps.summary_chars,
                    }
                    for ps in project_summaries
                ]

                self._projects_event.clear()
                self._selected_project_keys = None
                self.projects_ready.emit(projects_data)

                self.progress.emit("프로젝트 선택 대기 중...")
                self._projects_event.wait(timeout=600)  # 최대 10분 대기

                if self._cancelled() or self._selected_project_keys is None:
                    self._emit_cancelled(logger, "프로젝트 선택 취소")
                    return

                # 방어 코드: None 또는 빈 리스트 보호
                selected_keys_set = set(self._selected_project_keys or [])

            # 선택된 프로젝트만 필터링
            selected_summaries = [
                ps for ps in project_summaries if ps.project_key in selected_keys_set
            ]
            if not selected_summaries:
                self.error.emit("선택된 프로젝트가 없습니다.")
                return

            selected_critical_count = sum(
                len(ps.critical_info) for ps in selected_summaries
            )
            logger.set_project_quality_stats(project_summaries, selected_keys=selected_keys_set)
            print(
                f"[Step 8.5] 프로젝트 선택 완료: "
                f"{len(selected_summaries)}/{len(project_summaries)}개 선택  "
                f"중요정보 {selected_critical_count}개"
            )

            # ══ Step 8.7: 고객사 그룹화 + AI 고객사 요약 ════════════
            _step = "Step 8.7: 고객사 그룹화 + 고객사 요약"
            print(
                f"[Step 8.7] 고객사 요약 시작  "
                f"(선택된 프로젝트 {len(selected_summaries)}개 → 고객사 그룹화)"
            )
            if self._cancelled():
                self._emit_cancelled(logger, "고객사 요약 시작 전")
                return

            cust_summarizer = CustomerSummarizer(self._settings)
            if self._test_mode:
                print(f"[Step 8.7][테스트] 규칙 기반 고객사요약 생성")
                groups = cust_summarizer.group_by_customer(selected_summaries)
                customer_summaries = [_test_customer_summary(g) for g in groups]
                print(f"[Step 8.7][테스트] 규칙 기반 고객사요약 완료: {len(customer_summaries)}개")
            else:
                customer_summaries = cust_summarizer.summarize_all(
                    selected_summaries,
                    progress_cb=lambda msg: self.progress.emit(msg),
                    cancel_fn=self._cancelled,
                    light_mode=self._light_mode,
                )

            if self._cancelled():
                self._emit_cancelled(logger, "고객사 요약 중")
                return

            cust_summary_chars = sum(cs.summary_chars for cs in customer_summaries)
            cust_critical_count = sum(len(cs.critical_info) for cs in customer_summaries)
            print(
                f"[Step 8.7] 고객사 요약 완료: {len(customer_summaries)}개 고객사  "
                f"{cust_summary_chars:,}자  중요정보 {cust_critical_count}개"
            )

            # 고객사 통계 로거에 기록
            logger.set_customer_stats(len(customer_summaries), cust_summary_chars)

            # ══ Step 8.8: 규칙기반 초안 생성 ════════════════════════
            # 테스트 모드에서도, 정밀/라이트 모드에서도 항상 초안을 생성한다.
            # 정밀/라이트 모드: draft_report.md → GPT 보강 입력으로 활용
            # 테스트 모드: test_report.docx 생성에 사용
            _step = "Step 8.8: 규칙기반 초안 생성"
            self.progress.emit("[규칙기반] 업무 체계 초안 생성 중...")
            print(f"[Step 8.8] 규칙기반 초안 생성 시작")

            draft_md = _build_draft_report(
                customer_summaries, project_summaries, doc_summaries,
                job_desc=self._job_desc,
                report_reconstruction=report_reconstruction,
            )

            if not self._test_mode:
                _save_draft_report_md(draft_md)

            # 초안 품질 메트릭 (승인창 표시용)
            _draft_groups  = _parse_job_desc_hierarchy(self._job_desc)
            _draft_cat_map = _map_projects_to_categories(
                _draft_groups, project_summaries, customer_summaries
            )
            _draft_quality    = _tm_quality_scores(_draft_cat_map, doc_summaries, project_summaries)
            _draft_cat_count  = len([k for k in _draft_cat_map if not k.startswith("__")])

            print(
                f"[Step 8.8] 규칙기반 초안 완료  "
                f"({len(draft_md):,}자  카테고리 {_draft_cat_count}개  "
                f"분류 정확도 {_draft_quality['분류_정확도']}점)"
            )

            # ══ Step 9: 토큰/비용 계산 + 사용자 승인 ════════════════
            _step = "Step 9: 비용 확인 다이얼로그"
            print(
                f"[Step 10] 비용 확인 다이얼로그 요청  "
                f"(고객사 {len(customer_summaries)}개)"
            )
            # 고객사 요약 원문은 로그용으로 보존
            eval_text = build_eval_from_customer_summaries(
                self._file_display_map, customer_summaries, doc_summaries
            )
            # GPT 실제 입력 = draft_md (토큰 절감)
            gpt_input_text = draft_md if not self._test_mode else eval_text
            cost_info = build_cost_info(gpt_input_text, EST_OUTPUT_TOKENS)
            est_ai_sec_remaining = est["est_ai_sec"]

            _run_mode_str = (
                "라이트 모드" if self._light_mode else "정밀 모드"
            )
            approval_payload = {
                **cost_info,
                "run_mode":                    _run_mode_str,
                "project_count":               len(project_summaries),
                "selected_project_count":      len(selected_summaries),
                "customer_count":              len(customer_summaries),
                "selected_critical_info_count": cust_critical_count,
                "selected_input_tokens":       cost_info["input_tokens"],
                "selected_cost_str":           cost_info["cost_str"],
                "doc_count":                   len(doc_summaries),
                "est_time_str":                _fmt_duration(est_ai_sec_remaining),
                # 규칙기반 초안 품질 (승인창 표시용)
                "draft_cat_count":             _draft_cat_count,
                "draft_class_acc":             _draft_quality["분류_정확도"],
                "draft_chars":                 len(draft_md),
            }

            print(
                f"[Step 9] 토큰 계산\n"
                f"         입력 토큰: {cost_info['input_tokens']:,}\n"
                f"         출력 토큰: {cost_info['output_tokens']:,}\n"
                f"         예상 비용: {cost_info['cost_str']}\n"
            )

            if self._test_mode:
                # 테스트 모드: 비용 다이얼로그 스킵, 자동 승인
                print(
                    f"[Step 9][테스트] 비용 다이얼로그 스킵 (테스트 모드 자동 승인)\n"
                    f"         입력 토큰: {cost_info['input_tokens']:,}  (실제 호출 없음)"
                )
            else:
                # 사용자 승인 요청 (GUI 스레드에서 다이얼로그 표시)
                self._approval_event.clear()
                self._approval_granted = False
                self.approval_needed.emit(approval_payload)

                # 승인 대기 (취소 가능)
                self.progress.emit("사용자 승인 대기 중...")
                self._approval_event.wait(timeout=300)  # 최대 5분 대기

                if not self._approval_granted or self._cancelled():
                    self._emit_cancelled(logger, "사용자 취소 (비용 확인 후)")
                    return

            # ══ Step 10: 로그 저장 + AI 업무복원 분석 ════════════════
            _step = "Step 11: 로그 저장 (write_all)"
            print(f"[Step 11] GPT 분석 시작 — 먼저 로그 파일 저장")
            logger.set_char_stats(
                original_total, sampled_total,
                doc_summary_chars, proj_summary_chars, cust_summary_chars
            )
            logger.set_run_mode(
                "라이트 모드" if self._light_mode else "정밀 모드"
            )
            logger.set_top_docs(doc_infos)
            print(f"[Step 11-1] analysis_summary.txt / extracted / excluded 저장 시작")
            logger.write_all()
            print(f"[Step 11-1] write_all 완료")

            _step = "Step 11-2: document_summaries.txt 저장"
            print(f"[Step 11-2] document_summaries.txt 저장 시작  ({len(doc_summaries)}개 문서요약)")
            logger.write_document_summaries(doc_summaries)
            print(f"[Step 11-2] document_summaries.txt 저장 완료")

            _step = "Step 11-3: project_summaries.txt 저장"
            print(f"[Step 11-3] project_summaries.txt 저장 시작  ({len(project_summaries)}개 프로젝트)")
            logger.write_project_summaries(project_summaries, selected_keys=selected_keys_set)
            print(f"[Step 11-3] project_summaries.txt 저장 완료")

            _step = "Step 11-4: customer_summaries.txt 저장"
            print(f"[Step 11-4] customer_summaries.txt 저장 시작  ({len(customer_summaries)}개 고객사)")
            logger.write_customer_summaries(customer_summaries)
            print(f"[Step 11-4] customer_summaries.txt 저장 완료")

            _step = "Step 11-5: GPT AI 분석 호출"

            stats = logger.get_summary_dict()
            self.stats_ready.emit(stats)

            print(f"[Step 11-4] GPT 분석 호출 시작")
            self._emit_eta("AI 업무복원 분석", 0, 1, _EST_MAIN_AI_SEC)
            self.progress.emit(f"AI 업무복원 분석 중... (예상 {_fmt_duration(_EST_MAIN_AI_SEC)} 소요)")

            if self._cancelled():
                self._emit_cancelled(logger, "AI 분석 직전")
                return

            if not eval_text and not self._job_desc:
                self.error.emit("분석 가능한 텍스트가 없습니다.")
                return

            if self._test_mode:
                # 테스트 모드: GPT 호출 없이 규칙 기반 보고서 생성
                _step = "Step 11-5 (테스트): 규칙 기반 보고서 생성 + test_report.docx 저장"
                print("[Step 11-5][테스트] 규칙 기반 보고서 생성 시작")
                result_md = _build_test_report_md(
                    customer_summaries, project_summaries, doc_summaries,
                    job_desc=self._job_desc,
                    report_reconstruction=report_reconstruction,
                )
                test_docx_path = _save_test_report_docx(result_md)
                print(f"[Step 11-5][테스트] 완료  →  {test_docx_path}")
            else:
                # 정밀/라이트 모드: 규칙기반 초안을 GPT가 검토·보강
                _step = "Step 11-5: 규칙기반 초안 → GPT 보강"
                client = AIClient(self._settings)
                result_md = client.analyze_with_draft(
                    self._job_desc, draft_md, light_mode=self._light_mode
                )

                if self._cancelled():
                    self._emit_cancelled(logger, "AI 응답 수신 후 (결과 폐기)")
                    return

            # ── 보고서 고객사 검증 ────────────────────────────────
            _validate_report_customers(result_md, customer_summaries, project_summaries)

            self.finished.emit(result_md)

        except Exception as exc:
            tb_text = traceback.format_exc()
            print(f"\n[오류] {_step} 에서 예외 발생")
            print(tb_text)

            # error.log 저장 (항상 실행)
            try:
                self._write_error_log(_step, exc)
            except Exception as log_err:
                print(f"[경고] error.log 저장 실패: {log_err}")

            if self.cancel_requested:
                self.cancelled.emit({"aborted_at": _step, "processed": 0, "remaining": 0})
            else:
                self.error.emit(
                    f"[{_step}] 오류가 발생했습니다.\n\n"
                    f"{type(exc).__name__}: {exc}\n\n"
                    f"자세한 내용은 output/error.log를 확인하세요."
                )


# ── 테스트 모드 헬퍼 ───────────────────────────────────────────────────
def _test_doc_summary(info: "DocumentInfo") -> "DocumentSummary":
    """테스트 모드용 규칙 기반 문서 요약 생성."""
    from src.core.document_summarizer import DocumentSummary
    excerpt = (info.text[:400] if info.text else "(내용 없음)").strip()
    summary = (
        f"[테스트 요약] {info.display_name}\n"
        f"점수: {info.score}점 | 날짜: {info.modified_dt} | 상태: {info.work_status}\n"
        f"--- 내용 발췌 ---\n{excerpt}"
    )
    return DocumentSummary(
        display_name=info.display_name,
        score=info.score,
        work_status=info.work_status,
        is_current_work=info.is_current_work,
        modified_dt=info.modified_dt,
        created_dt=info.created_dt,
        ai_summarized=False,
        summary_text=summary,
        excerpt=excerpt[:200],
        original_chars=len(info.text),
        summary_chars=len(summary),
    )


def _test_project_summaries(
    proj_summarizer: "ProjectSummarizer",
    doc_summaries: list,
) -> tuple[list, list, list]:
    """테스트 모드용 규칙 기반 프로젝트 요약 생성.
    그룹화·유사도·상한선은 실제 로직을 사용하고, AI 호출만 생략한다.
    """
    from src.core.project_summarizer import ProjectSummary

    groups = proj_summarizer.group_documents(doc_summaries)
    all_similar: list[str] = []
    all_limit: list[str] = []

    for g in groups:
        proj_summarizer.remove_similar(g)
        proj_summarizer.apply_limit(g)
        all_similar.extend(g.excluded_similar)
        all_limit.extend(g.excluded_limit)

    project_summaries: list[ProjectSummary] = []
    for g in groups:
        if not g.docs:
            continue
        # 문서 이름 목록으로 간단한 요약 생성
        doc_names = "\n".join(f"  - {d.display_name}" for d in g.docs[:5])
        summary_text = (
            f"[테스트 요약] 프로젝트: {g.project_key}\n"
            f"현재 진행상태: [확인 필요]\n"
            f"고객사명: [확인 필요]\n"
            f"프로젝트 목적: [테스트 모드 — AI 미생성]\n"
            f"관련 문서 ({len(g.docs)}개):\n{doc_names}"
        )
        ps = ProjectSummary(
            project_key=g.project_key,
            doc_count=len(g.docs),
            excluded_similar_count=len(g.excluded_similar),
            excluded_limit_count=len(g.excluded_limit),
            summary_text=summary_text,
            related_files=[d.display_name for d in g.docs[:10]],
            summary_chars=len(summary_text),
            project_name=g.project_key,
            client_name="[확인 필요]",
            current_status="[확인 필요]",
            incomplete_work="[테스트 모드]",
        )
        project_summaries.append(ps)

    return project_summaries, all_similar, all_limit


def _test_customer_summary(group: "CustomerGroup") -> "CustomerSummary":
    """테스트 모드용 규칙 기반 고객사 요약 생성."""
    from src.core.customer_summarizer import CustomerSummary

    proj_names = "\n".join(f"  - {ps.project_key}" for ps in group.projects)
    all_critical: list[str] = []
    for ps in group.projects:
        all_critical.extend(ps.critical_info)

    summary_text = (
        f"[테스트 고객사 요약] {group.customer_name}\n"
        f"현재 상태: [확인 필요]\n"
        f"진행 중 프로젝트: [테스트 모드 — AI 미생성]\n"
        f"완료 프로젝트: [테스트 모드 — AI 미생성]\n"
        f"주요 산출물: [테스트 모드 — AI 미생성]\n"
        f"미완료 업무: [테스트 모드 — AI 미생성]\n"
        f"향후 액션: [테스트 모드 — AI 미생성]\n"
        f"주의사항: [테스트 모드 — AI 미생성]\n"
        f"후임자 인수 포인트: [테스트 모드 — AI 미생성]\n"
        f"중요 의사결정 이력: [테스트 모드 — AI 미생성]\n"
        f"\n포함 프로젝트 ({len(group.projects)}개):\n{proj_names}"
    )
    return CustomerSummary(
        customer_name=group.customer_name,
        project_count=len(group.projects),
        summary_text=summary_text,
        summary_chars=len(summary_text),
        current_status="[확인 필요]",
        incomplete_work="[테스트 모드]",
        critical_info=list(dict.fromkeys(all_critical)),
        project_keys=[ps.project_key for ps in group.projects],
    )


## ── 테스트 모드: 업무 체계 분류 엔진 ──────────────────────────────

# 도메인 지식 기반 키워드 확장표 (상위 카테고리명에서 확장에만 사용)
# 주의: 너무 일반적인 단어(개발, 시스템, 서비스)는 의도적으로 제외
_DOMAIN_EXPAND: dict[str, set[str]] = {
    # ── 기존 도메인 ──────────────────────────────────────────────────────
    "인사":    {"평가제도", "생활가이드", "직급체계", "인센티브", "조직진단",
                "인사규정", "복무규정", "취업규칙", "채용규정", "인사평가",
                "직급", "승진", "역량평가", "직무기술", "급여체계",
                "법인", "협회", "무역", "건설", "글로벌"},
    "컨설팅":  {"컨설팅", "수립", "진단", "개선방안", "도입"},
    "마케팅":  {"마케팅", "브런치", "블로그", "dm", "광고", "sns", "콘텐츠",
                "캠페인", "홍보", "인스타", "유튜브", "쇼츠", "키워드", "크롤링"},
    "개발":    {"코딩", "소프트웨어", "api", "배포", "backend", "frontend"},
    "바이브":  {"바이브코딩", "vibe", "handover", "cursor"},
    "영업":    {"제안서", "견적서", "수주", "입찰"},
    "운영":    {"총무", "경영지원", "행정지원"},
    # ── 범용 도메인 확장 ─────────────────────────────────────────────────
    "회계":    {"결산", "세무신고", "재무제표", "손익", "예산편성", "원가분석",
                "감사보고서", "부가세", "법인세", "소득세", "매출", "매입"},
    "재무":    {"결산", "재무계획", "투자", "자금", "예산", "원가", "손익분석"},
    "세무":    {"세무신고", "부가세", "법인세", "세금계산서", "절세", "신고"},
    "디자인":  {"시안", "목업", "와이어프레임", "브랜드", "로고", "ui", "ux",
                "가이드라인", "컬러", "폰트", "레이아웃", "프로토타입",
                "figma", "sketch", "psd", "illustrator"},
    "브랜드":  {"브랜딩", "ci", "bi", "로고", "브랜드아이덴티티", "컬러"},
    "기획":    {"전략기획", "사업계획", "시장분석", "로드맵", "기획안",
                "현황분석", "경쟁분석", "swot", "목표수립"},
    "전략":    {"전략", "중장기", "로드맵", "방향성", "성장전략"},
    "법무":    {"계약검토", "법무자문", "특허출원", "상표등록", "컴플라이언스",
                "법률자문", "소송", "분쟁", "nda", "mou"},
    "구매":    {"발주", "구매", "조달", "납품", "협력사", "입찰", "견적"},
    "총무":    {"시설관리", "비품구매", "행정지원", "경비", "차량관리"},
    "행정":    {"행정", "문서관리", "서무", "규정", "지침"},
    "교육":    {"교육", "훈련", "연수", "커리큘럼", "강의", "학습"},
    "영업":    {"제안서", "견적서", "수주", "입찰", "납품", "고객관리"},
    "cs":      {"cs", "고객서비스", "상담", "클레임", "voc", "고객만족"},
}

# 하위 업무 키워드에서 제외할 너무 일반적인 단어 (오매핑 방지)
_GENERIC_CHILD_TOKENS: set[str] = {
    "개발", "설계", "구축", "작성", "관리", "운영", "업무", "시스템",
    "서비스", "관련", "지원", "처리", "분석", "진행", "수행", "담당",
}

# ── 카테고리 지문(fingerprint): 산출물/파일명 기반 분류 ──────────────
# 파일명 키워드 hit당 +5점, 내용 키워드 hit당 +3점
# 각 키워드는 프로젝트 내 최초 1회만 카운트 (중복 점수 방지)
_CATEGORY_FINGERPRINTS: dict[str, dict[str, set[str]]] = {
    # ── 기존 도메인 ──────────────────────────────────────────────────────
    "인사": {
        "filenames": {
            "평가", "성과평가", "생활가이드", "직급", "승진", "인센티브",
            "채용", "면접", "조직진단", "근로계약", "취업규칙", "복리후생",
            "역량", "kpi", "okr", "인사", "평가양식", "평가표", "직무기술",
            "급여", "연봉", "인사규정", "복무규정", "직무", "조직도",
            "핵심가치", "비전", "미션", "인재상",
        },
        "content": {
            "인사평가", "연봉", "직원", "조직", "직무", "핵심가치",
            "인사제도", "평가등급", "승진기준", "채용공고", "인센티브",
            "성과급", "역량개발", "직급체계", "호봉",
        },
    },
    "개발": {
        "filenames": {
            "supabase", "nextjs", "next", "cursor", "typescript", "javascript",
            "python", "react", "api", "backend", "frontend", "deploy",
            "github", "vercel", "prisma", "docker", "schema", "migration",
        },
        "content": {
            "supabase", "nextjs", "typescript", "cursor", "github", "vercel",
            "database", "endpoint", "api", "코드", "배포", "컴포넌트",
            "리포지토리", "브랜치", "커밋", "pull", "push",
        },
    },
    "마케팅": {
        "filenames": {
            "블로그", "브런치", "dm", "광고", "쇼츠", "키워드", "콘텐츠",
            "유튜브", "인스타", "sns", "크롤링", "홍보", "캠페인",
        },
        "content": {
            "블로그", "광고", "콘텐츠", "마케팅", "sns", "유튜브",
            "팔로워", "조회수", "클릭률", "전환율", "ctr", "roas",
        },
    },
    "영업": {
        "filenames": {
            "제안서", "견적서", "수주", "입찰", "계약서", "미팅",
            "제안", "협약", "mou",
        },
        "content": {
            "제안서", "견적", "수주", "계약", "영업", "고객사", "발주",
        },
    },
    # ── 범용 도메인 지문 (신규) ──────────────────────────────────────────
    "회계": {
        "filenames": {
            "결산", "재무", "회계", "세무", "손익", "예산", "원가", "감사",
            "세금계산서", "매출", "매입", "계정", "분개", "시산표",
            "대차대조표", "손익계산서", "현금흐름", "법인세", "부가세", "소득세",
        },
        "content": {
            "결산", "회계", "세무신고", "재무제표", "손익", "예산편성",
            "감사보고서", "부가세", "법인세", "원가계산",
        },
    },
    "디자인": {
        "filenames": {
            "시안", "목업", "와이어프레임", "브랜드", "로고", "ui", "ux",
            "디자인", "가이드라인", "컬러", "폰트", "레이아웃", "프로토타입",
            "figma", "sketch", "psd", "ai", "indd", "브랜딩",
        },
        "content": {
            "시안", "디자인", "브랜딩", "ui", "ux", "레이아웃",
            "컬러팔레트", "타이포그래피", "프로토타입",
        },
    },
    "기획": {
        "filenames": {
            "기획안", "기획서", "제안서", "전략", "로드맵", "계획서",
            "현황분석", "시장분석", "경쟁분석", "swot", "사업계획",
            "중장기", "방향성",
        },
        "content": {
            "기획", "전략", "로드맵", "시장조사", "사업계획", "swot",
            "목표", "kpi", "성과지표", "방향성",
        },
    },
    "법무": {
        "filenames": {
            "계약서", "협약서", "mou", "nda", "법무", "소송", "분쟁",
            "지식재산", "특허", "상표", "라이선스", "준법", "컴플라이언스",
        },
        "content": {
            "계약", "법무", "소송", "특허", "상표", "계약서", "협약",
            "법률", "준법", "컴플라이언스",
        },
    },
    "총무": {
        "filenames": {
            "총무", "행정", "시설", "비품", "구매", "발주", "재고",
            "청소", "경비", "차량", "행사", "서무",
        },
        "content": {
            "총무", "행정", "시설관리", "비품", "구매요청", "발주", "재고관리",
        },
    },
    "구매": {
        "filenames": {
            "발주서", "구매요청", "견적", "납품", "협력사", "단가", "조달",
        },
        "content": {
            "발주", "구매", "조달", "납품", "협력사", "단가협상",
        },
    },
    "교육": {
        "filenames": {
            "교육", "훈련", "연수", "커리큘럼", "강의", "학습", "교재",
            "과정", "강좌", "수강",
        },
        "content": {
            "교육과정", "연수", "커리큘럼", "학습목표", "강의계획",
        },
    },
}


# ── 폴더명 특화 키워드 ──────────────────────────────────────────────────────
# _CATEGORY_FINGERPRINTS 의 filenames/content 와 별개로 "폴더명"에 특화된 키워드.
# 폴더 컴포넌트에서 매칭 시 20점, 파일명 컴포넌트에서 매칭 시 8점.
_CATEGORY_FOLDER_KEYWORDS: dict[str, set[str]] = {
    # ── 기존 도메인 ──────────────────────────────────────────────────────
    "인사": {
        # 한국 법인/협회 등 B2B 회사 유형 접미사
        "세무법인", "세무사", "법인", "협회", "조합", "공단", "재단", "진흥원", "진흥",
        "국제", "무역", "건설", "바이오", "테크", "랩스", "코리아",
        # HR 산출물 폴더명
        "인사컨설팅", "생활가이드", "평가제도", "직급체계", "인사제도",
        "조직진단", "취업규칙", "복무규정", "채용면접", "직급", "인센티브체계",
    },
    "개발": {
        # 특정 개발 프로젝트 이름 (하이픈 포함 그대로)
        "hr-ai-review", "hr-evaluation", "ai-handover", "handover",
        "cursor", "codex", "supabase", "바이브코딩", "vibe",
        "github", "frontend", "backend", "deploy",
    },
    "마케팅": {
        "블로그", "브런치", "마케팅", "광고", "크롤링", "sns",
        "dm", "유튜브", "인스타그램", "인스타",
    },
    "영업": {
        "제안서", "견적서", "수주",
    },
    # ── 범용 도메인 폴더 키워드 (신규) ──────────────────────────────────
    "회계": {
        "결산", "세무", "회계", "재무", "손익", "예산", "원가",
        "세금계산서", "부가세", "법인세",
    },
    "디자인": {
        "시안", "디자인", "브랜드", "로고", "ui", "ux",
        "figma", "목업", "가이드라인",
    },
    "기획": {
        "기획", "전략", "계획서", "제안", "로드맵", "사업계획",
    },
    "법무": {
        "계약서", "법무", "mou", "nda", "협약서", "특허",
    },
    "총무": {
        "총무", "행정", "시설", "비품", "서무",
    },
    "구매": {
        "발주", "구매", "조달", "협력사",
    },
    "교육": {
        "교육", "연수", "훈련", "커리큘럼",
    },
}


def _detect_category_fingerprint(cat_name: str) -> str | None:
    """카테고리명에서 매칭할 지문(fingerprint) 타입을 탐지한다.

    반환값은 _CATEGORY_FINGERPRINTS 와 _CATEGORY_FOLDER_KEYWORDS 의 키와 일치한다.
    매칭되지 않으면 None 을 반환하며, 점수에서 지문 항목이 0점으로 처리된다.
    """
    name = cat_name.lower()
    # ── 기존 도메인 ────────────────────────────────────────────────────
    if any(k in name for k in ("인사", "hr", "컨설팅", "중소기업", "노무",
                                "채용", "평가", "보상", "급여")):
        return "인사"
    # 디자인을 개발보다 먼저 검사 (디자인 시스템 오탐 방지)
    if any(k in name for k in ("디자인", "design", "ux", "브랜드",
                                "브랜딩", "시각", "그래픽", "ui디자인")):
        return "디자인"
    if any(k in name for k in ("개발", "코딩", "바이브", "vibe", "ai", "it",
                                "소프트웨어", "플랫폼", "솔루션")):
        return "개발"
    if any(k in name for k in ("마케팅", "marketing", "광고", "콘텐츠",
                                "sns", "홍보", "브랜드마케팅")):
        return "마케팅"
    if any(k in name for k in ("영업", "sales", "수주", "제안", "bd")):
        return "영업"
    # ── 범용 도메인 (신규) ─────────────────────────────────────────────
    if any(k in name for k in ("회계", "재무", "세무", "결산", "accounting",
                                "finance", "세금", "원가", "감사")):
        return "회계"
    if any(k in name for k in ("ui", "ux")):
        return "디자인"
    if any(k in name for k in ("기획", "전략", "planning", "strategy",
                                "사업기획", "경영기획", "bm")):
        return "기획"
    if any(k in name for k in ("법무", "legal", "계약", "compliance",
                                "특허", "상표", "ip")):
        return "법무"
    if any(k in name for k in ("총무", "행정", "admin", "시설", "서무",
                                "경영지원", "일반관리")):
        return "총무"
    if any(k in name for k in ("구매", "조달", "procurement", "발주",
                                "협력사", "공급망")):
        return "구매"
    if any(k in name for k in ("교육", "훈련", "연수", "training",
                                "learning", "커리큘럼")):
        return "교육"
    return None


def _org_folder_score(folder_part: str) -> int:
    """폴더명이 고객사·기관·조직명 패턴에 해당하면 점수를 반환한다.

    도메인 무관 범용 판정: 어떤 카테고리에서도 동일하게 적용된다.

    패턴①: 한글 + 회사/기관 유형 접미사 → +20점
    패턴②: 연월 + 한국어 이름 패턴 → +12점
    패턴③: 영문 소문자 + 하이픈 조합 (hr-ai-review 류 프로젝트명) → +10점
    """
    import re
    f = folder_part.strip()

    # ① 회사/기관 유형 접미사 (범용)
    if re.search(
        r"[가-힣]{2,}(법인|세무|회계|협회|조합|재단|공단|진흥원|진흥|"
        r"건설|무역|바이오|테크|랩스|코리아|국제|그룹|홀딩스|파트너스|"
        r"에이전시|컨설팅|솔루션|서비스|시스템|플랫폼|네트웍스|네트워크)",
        f,
    ):
        return 20

    # ② 연월 + 이름 (한국어·영문 혼합 허용 — 예: "2026년 6월 A사")
    if re.search(r"\d{4}년?\s*\d{1,2}월?\s*\S{2,}", f):
        return 12

    # ③ 영문 하이픈 프로젝트명 (예: hr-ai-review, shopping-mall-renewal)
    if re.match(r"^[a-z][a-z0-9]*(-[a-z0-9]+){1,}$", f.lower()) and len(f) >= 6:
        return 10

    return 0


def _company_folder_score(folder_part: str) -> int:
    """하위 호환 별칭 — _org_folder_score 를 호출한다."""
    return _org_folder_score(folder_part)


def _score_path_for_category(
    folder_kw: set[str],
    fp: dict,
    fp_type: str | None,
    related_files: list[str],
    summary_text: str,
    max_files: int = 20,
) -> tuple[int, dict[str, list[str]]]:
    """경로 전체를 폴더/파일/내용 별로 분석하여 카테고리 점수를 계산한다.

    우선순위:
      폴더 컴포넌트 × folder_kw    : +20점/hit
      폴더 컴포넌트 × fp filenames : + 6점/hit
      폴더 컴포넌트 (회사명 패턴)   : +25/+15점/hit (인사 전용)
      파일 컴포넌트 × fp filenames : + 5점/hit
      파일 컴포넌트 × folder_kw    : + 8점/hit
      내용 × fp content            : + 3점/hit

    Returns:
        (total_score, detail_dict) – detail_dict 키: "folder", "file", "content"
    """
    import re as _re

    details: dict[str, list[str]] = {"folder": [], "file": [], "content": []}
    total = 0

    seen_folder: set[tuple[str, str]] = set()
    seen_file:   set[tuple[str, str]] = set()
    seen_fp_fn:  set[str] = set()
    seen_fp_ct:  set[str] = set()

    fp_filenames = fp.get("filenames", set()) if fp else set()
    fp_content   = fp.get("content",   set()) if fp else set()

    for fpath in related_files[:max_files]:
        parts = _re.split(r"[/\\]", fpath.replace("\\", "/"))
        for i, part in enumerate(parts):
            if not part.strip():
                continue
            pl = part.lower().strip()
            is_folder = (i < len(parts) - 1)

            if is_folder:
                # ① 폴더 특화 키워드 (20점/hit) ─────────────────────────
                for kw in folder_kw:
                    kwl = kw.lower()
                    key = (pl[:30], kwl)
                    if kwl in pl and key not in seen_folder:
                        seen_folder.add(key)
                        total += 20
                        details["folder"].append(f"+20(폴더:{part}∋{kw})")

                # ② 지문 파일명 키워드를 폴더명에도 적용 (6점/hit) ──────
                for kw in fp_filenames:
                    key = (pl[:30], kw)
                    if kw in pl and key not in seen_folder:
                        seen_folder.add(key)
                        total += 6
                        details["folder"].append(f"+6(폴더지문:{part}∋{kw})")

                # ③ 고객사·기관·프로젝트명 패턴 감지 (범용, 도메인 무관) ──
                org_pts = _org_folder_score(part)
                if org_pts > 0:
                    c_key = ("__org__", pl[:30])
                    if c_key not in seen_folder:
                        seen_folder.add(c_key)
                        total += org_pts
                        details["folder"].append(
                            f"+{org_pts}(조직명패턴:{part})"
                        )
            else:
                # ④ 파일명 지문 키워드 (5점/hit) ──────────────────────
                for kw in fp_filenames:
                    key = (pl[:30], kw)
                    if kw in pl and key not in seen_file:
                        seen_file.add(key)
                        total += 5
                        details["file"].append(f"+5(파일지문:{part}∋{kw})")

                # ⑤ 폴더 특화 키워드를 파일명에서도 탐지 (8점/hit) ────
                for kw in folder_kw:
                    kwl = kw.lower()
                    key = (pl[:30], kwl)
                    if kwl in pl and key not in seen_file:
                        seen_file.add(key)
                        total += 8
                        details["file"].append(f"+8(파일명:{part}∋{kw})")

    # ⑥ 내용 지문 (3점/hit, 저가중) ──────────────────────────────────────
    content_lower = summary_text[:500].lower()
    for kw in fp_content:
        if kw in content_lower and kw not in seen_fp_ct:
            seen_fp_ct.add(kw)
            total += 3
            details["content"].append(f"+3(내용:{kw})")

    return total, details


def _common_prefix(names: list[str]) -> str:
    """파일명 목록에서 공통 접두어(한국어·영문 토큰 기준)를 반환한다.

    예) ["쇼핑몰_UI_v1", "쇼핑몰_UI_v2", "쇼핑몰_시안"] → "쇼핑몰"
    """
    import re
    if not names:
        return ""
    tokenize = lambda s: re.findall(r"[가-힣a-zA-Z0-9]+", s.lower())
    token_lists = [tokenize(n) for n in names if n]
    if not token_lists:
        return ""
    first = token_lists[0]
    common: list[str] = []
    for i, tok in enumerate(first):
        if all(len(tl) > i and tl[i] == tok for tl in token_lists[1:]):
            common.append(tok)
        else:
            break
    return " ".join(common)


def _file_structure_score(related_files: list[str]) -> int:
    """파일 구조 기반 범용 프로젝트 신호 점수를 반환한다.

    특정 도메인 키워드에 의존하지 않고 파일 구성으로 판단:
      - 다양한 확장자 혼재 → 복합 산출물 프로젝트 (+10)
      - 공통 접두어 파일 3개 이상 → 단일 프로젝트 집중 (+10)
      - 최근 수정일(30일 이내) 파일 보유 → 활성 프로젝트 (+5)
    """
    import re
    from pathlib import Path
    import datetime

    DELIVERABLE_EXTS = {".docx", ".xlsx", ".pptx", ".pdf", ".hwp", ".hwpx"}
    CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx"}

    score = 0
    stems: list[str] = []
    exts: set[str] = set()
    has_recent = False
    today = datetime.date.today()

    for fpath in related_files[:30]:
        p = Path(fpath)
        ext = p.suffix.lower()
        exts.add(ext)
        stems.append(re.sub(r"[_\-\s]+v?\d+$", "", p.stem.lower()))

        try:
            mtime = datetime.date.fromtimestamp(p.stat().st_mtime)
            if (today - mtime).days <= 30:
                has_recent = True
        except Exception:
            pass

    # 다양한 확장자 (문서 2종 이상)
    doc_exts = exts & DELIVERABLE_EXTS
    if len(doc_exts) >= 2:
        score += 10

    # 공통 접두어 파일 3개 이상
    prefix = _common_prefix(stems)
    if prefix and len(prefix) >= 2:
        count = sum(1 for s in stems if s.startswith(prefix))
        if count >= 3:
            score += 10

    # 최근 활성 프로젝트
    if has_recent:
        score += 5

    return score


def _parse_job_desc_hierarchy(job_desc: str) -> list[dict]:
    """업무분장을 상위/하위 계층 구조로 파싱한다.

    반환 형식:
      [{"name": "상위업무", "children": ["하위업무1", "하위업무2"]}, ...]
    """
    import re

    # 상위 업무 패턴 (숫자 번호, 특수 기호, 들여쓰기 없는 줄)
    RE_PARENT = re.compile(
        r"^\s*(?:\d+[.)]\s+|[■□▶◆◇★☆]\s*)(.+)"
    )
    # 하위 업무 패턴 (불릿/대시/화살표/탭)
    RE_CHILD = re.compile(
        r"^\s*[*\-•└◦]\s+(.+)"
    )
    # 구분선 패턴 (건너뜀)
    RE_SEP = re.compile(r"^[\s\-=─━_]{3,}$")

    lines = job_desc.splitlines()
    groups: list[dict] = []
    current: dict | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped or RE_SEP.match(stripped):
            continue

        # ① 상위 업무 패턴 (번호·특수기호)
        m = RE_PARENT.match(line)
        if m:
            current = {"name": m.group(1).strip(), "children": []}
            groups.append(current)
            continue

        # ② 하위 업무 패턴 (불릿·대시)
        m = RE_CHILD.match(line)
        if m:
            name = m.group(1).strip()
            if current is not None:
                current["children"].append(name)
            else:
                # 부모 없이 하위 업무가 등장하면 임시 부모로 추가
                current = {"name": name, "children": []}
                groups.append(current)
            continue

        # ③ 들여쓰기로 판단
        indent = len(line) - len(line.lstrip())
        if indent >= 2 and current is not None:
            current["children"].append(stripped)
        else:
            # 들여쓰기 없는 일반 텍스트 → 상위 업무
            current = {"name": stripped, "children": []}
            groups.append(current)

    return groups or [{"name": "기타", "children": []}]


def _expand_keywords(name: str) -> set[str]:
    """카테고리/업무명에서 매칭용 키워드 집합을 구성한다."""
    import re
    words = set(re.findall(r"[가-힣a-zA-Z]{2,}", name.lower()))
    for domain, extras in _DOMAIN_EXPAND.items():
        if domain in name:
            words.update(extras)
    return words


def _extract_folder_names(related_files: list[str]) -> str:
    """related_files 경로에서 폴더명만 추출하여 하나의 문자열로 반환."""
    import re
    folders: set[str] = set()
    for fpath in related_files[:12]:
        parts = re.split(r"[/\\]", fpath.replace("\\", "/"))
        for part in parts[:-1]:  # 파일명 제외, 폴더명만
            p = part.strip()
            if p:
                folders.add(p.lower())
    return " ".join(folders)


def _extract_file_names(related_files: list[str]) -> str:
    """related_files 경로에서 파일명만 추출."""
    import re
    names: list[str] = []
    for fpath in related_files[:12]:
        fname = re.split(r"[/\\]", fpath.replace("\\", "/"))[-1]
        names.append(fname.lower())
    return " ".join(names)


def _keyword_score(keywords: set[str], text: str) -> int:
    """키워드 집합과 텍스트의 단어 교집합 수를 반환."""
    import re
    if not keywords or not text:
        return 0
    words = set(re.findall(r"[가-힣a-zA-Z]{2,}", text))
    return len(keywords & words)


def _map_projects_to_categories(
    groups: list[dict],
    project_summaries: list,
    customer_summaries: list,
) -> dict[str, dict]:
    """
    프로젝트를 업무 카테고리에 매핑한다.

    매칭 우선순위 (점수 가중치):
      프로젝트명 × 하위업무 키워드 : 30점/단어
      폴더명     × 하위업무 키워드 : 15점/단어
      파일명     × 하위업무 키워드 :  8점/단어
      프로젝트명 × 상위업무 키워드 : 10점/단어
      폴더명     × 상위업무 키워드 :  5점/단어
      파일명     × 상위업무 키워드 :  3점/단어
      경로 지문 (폴더/파일/내용)   : 가변 (도메인 지문 테이블 기준)
      파일 구조 신호               : 최대 +25점 (범용, 도메인 무관)
      조직명 패턴                  : 최대 +20점 (범용, 도메인 무관)
      (summary_text 는 노이즈가 많으므로 의도적으로 제외)
    """
    import re

    # ── 카테고리별 키워드 구성 ─────────────────────────────────────────
    cat_data: dict[str, dict] = {}
    for g in groups:
        parent = g["name"]
        parent_kw = _expand_keywords(parent)   # 상위업무: 도메인 확장 O

        # 하위업무: 도메인 확장 없이 원시 토큰만 (너무 일반적인 단어는 제외)
        child_raw_kw: set[str] = set()
        for child in g.get("children", []):
            tokens = set(re.findall(r"[가-힣a-zA-Z]{2,}", child.lower()))
            tokens -= _GENERIC_CHILD_TOKENS   # 일반 토큰 제거
            child_raw_kw.update(tokens)

        cat_data[parent] = {
            "children":     g.get("children", []),
            "parent_kw":    parent_kw,
            "child_raw_kw": child_raw_kw,
            "customers":    set(),
            "projects":     [],
            "doc_count":    0,
            "_mapping_log": [],   # 디버그용
        }

    cat_data["기타"] = {
        "children":     [],
        "parent_kw":    set(),
        "child_raw_kw": set(),
        "customers":    set(),
        "projects":     [],
        "doc_count":    0,
        "_mapping_log": [],
    }

    # ── project_key → customer_name 역매핑 ───────────────────────────
    proj_to_customer: dict[str, str] = {}
    for cs in customer_summaries:
        for pkey in cs.project_keys:
            proj_to_customer[pkey] = cs.customer_name

    # ── 전체 점수 로그 (category_score.txt 용) ───────────────────────
    _all_score_logs: list[dict] = []

    # ── 프로젝트별 우선순위 기반 매핑 ────────────────────────────────
    for ps in project_summaries:
        # 우선순위별 컨텐츠 준비 (job desc 키워드 매칭용)
        p_proj    = (ps.project_key + " " + ps.project_name).lower()
        p_fold    = _extract_folder_names(ps.related_files)
        p_file    = _extract_file_names(ps.related_files)
        p_summary = ps.summary_text[:500].lower()

        # 파일 구조 신호 (범용, 도메인 무관) — 카테고리 루프 전에 1회 계산
        struct_score = _file_structure_score(ps.related_files)

        cat_scores:       dict[str, int]            = {}
        cat_reasons:      dict[str, str]            = {}
        cat_path_details: dict[str, dict]           = {}  # 경로 분석 상세 (로그용)

        for cat_name, data in cat_data.items():
            if cat_name == "기타":
                continue

            score        = 0
            reason_parts: list[str] = []

            # ── A. 하위업무 키워드 × 프로젝트명 (최우선: 30점/단어) ──
            ck = data["child_raw_kw"]
            s = _keyword_score(ck, p_proj)
            if s:
                score += s * 30
                reason_parts.append(f"프로젝트명×하위({s})")

            # ── B. 상위업무 키워드 × 프로젝트명 (10점/단어) ───────────
            pk = data["parent_kw"]
            s = _keyword_score(pk, p_proj)
            if s:
                score += s * 10
                reason_parts.append(f"프로젝트명×상위({s})")

            # ── C. 경로 전체 분석 (폴더 > 파일 > 내용) ──────────────
            #    _score_path_for_category 가 폴더명/파일명/회사명패턴/
            #    지문 키워드를 개별 가중치로 계산한다.
            fp_type  = _detect_category_fingerprint(cat_name)
            fp       = _CATEGORY_FINGERPRINTS.get(fp_type, {}) if fp_type else {}
            folder_kw = _CATEGORY_FOLDER_KEYWORDS.get(fp_type, set()) if fp_type else set()

            path_score, path_detail = _score_path_for_category(
                folder_kw, fp, fp_type,
                ps.related_files, p_summary,
            )
            score += path_score
            cat_path_details[cat_name] = path_detail

            folder_total = sum(
                int(x.split("(")[0][1:]) for x in path_detail["folder"]
            ) if path_detail["folder"] else 0
            file_total = sum(
                int(x.split("(")[0][1:]) for x in path_detail["file"]
            ) if path_detail["file"] else 0
            content_total = sum(
                int(x.split("(")[0][1:]) for x in path_detail["content"]
            ) if path_detail["content"] else 0

            if folder_total:
                reason_parts.append(f"폴더점수+{folder_total}")
            if file_total:
                reason_parts.append(f"파일점수+{file_total}")
            if content_total:
                reason_parts.append(f"내용점수+{content_total}")

            # ── D. 하위/상위 키워드 × 폴더명·파일명 (job desc 보조) ──
            s = _keyword_score(ck, p_fold)
            if s:
                score += s * 15
                reason_parts.append(f"폴더명×하위({s})")
            s = _keyword_score(ck, p_file)
            if s:
                score += s * 8
                reason_parts.append(f"파일명×하위({s})")
            s = _keyword_score(pk, p_fold)
            if s:
                score += s * 5
                reason_parts.append(f"폴더명×상위({s})")
            s = _keyword_score(pk, p_file)
            if s:
                score += s * 3
                reason_parts.append(f"파일명×상위({s})")

            # ── E. 파일 구조 신호 (범용, 모든 카테고리 동등 적용) ────────
            # 파일명 다양성·공통 접두어·최근 수정일 등 도메인 무관 신호
            if struct_score > 0:
                score += struct_score
                reason_parts.append(f"구조신호+{struct_score}")

            cat_scores[cat_name]  = score
            cat_reasons[cat_name] = " + ".join(reason_parts) if reason_parts else "0점"

        # 최고 점수 카테고리 선택 (모두 0이면 기타)
        best_cat   = "기타"
        best_score = 0
        if cat_scores:
            top_cat = max(cat_scores, key=lambda k: cat_scores[k])
            if cat_scores[top_cat] > 0:
                best_cat   = top_cat
                best_score = cat_scores[top_cat]
        best_reason = cat_reasons.get(best_cat, "모두 0점 → 기타")

        cat_data[best_cat]["projects"].append(ps)
        cat_data[best_cat]["doc_count"] += ps.doc_count

        # 고객사 추가: 1) proj_to_customer (AI 고객사 요약 기반 정확 매핑)
        #              2) project_key 직접 파싱 (Fallback — 고객사 누락 방지)
        cs_name = proj_to_customer.get(ps.project_key)
        if not cs_name:
            derived = extract_customer_name_from_key(ps.project_key)
            # 개발 프로젝트 key(hr-ai-review 등)는 회사명이 아니므로 제외
            if derived and derived != ps.project_key:
                cs_name = derived
        if cs_name:
            cat_data[best_cat]["customers"].add(cs_name)

        # 매핑 로그 (project_mapping.txt 용)
        cat_data[best_cat]["_mapping_log"].append(
            (ps.project_key, best_reason, best_score)
        )

        # 전체 점수 로그 (category_score.txt 용) – 경로 분석 상세 포함
        _all_score_logs.append({
            "project_key":    ps.project_key,
            "related_files":  ps.related_files[:5],    # 샘플 경로 (최대 5개)
            "scores":         cat_scores,
            "winner":         best_cat,
            "reason":         best_reason,
            "path_details":   cat_path_details,        # {cat_name: {folder,file,content}}
        })

    # 전체 점수 로그를 반환 데이터에 포함
    cat_data["__score_logs__"] = _all_score_logs

    # ── 고객사 보강 ───────────────────────────────────────────────────
    all_assigned: set[str] = set()
    for k, data in cat_data.items():
        if not k.startswith("__"):
            all_assigned.update(data["customers"])

    for cs in customer_summaries:
        if cs.customer_name not in all_assigned:
            for cat_name, data in cat_data.items():
                if cat_name.startswith("__"):
                    continue
                for ps in data["projects"]:
                    if ps.project_key in cs.project_keys:
                        data["customers"].add(cs.customer_name)
                        break

    # 빈 기타 제거
    if not cat_data["기타"]["projects"]:
        del cat_data["기타"]

    return cat_data


def _format_category_tree(cat_name: str, data: dict) -> str:
    """업무 체계 맵 트리 문자열 생성 (계층 구조만 — 통계 제외).

    출력 예시:
        **중소기업 인사컨설팅**
        ├ 고객 미팅
        ├ 제안
        ├ 견적
        └ 고객 응대
    """
    children = data.get("children", [])
    lines = [f"**{cat_name}**"]
    for i, child in enumerate(children):
        prefix = "└" if i == len(children) - 1 else "├"
        lines.append(f"{prefix} {child}")
    return "\n".join(lines)


def _save_noise_filter_report(all_files: list, cat_items: list) -> None:
    """output/noise_filter_report.txt — 노이즈 제거 현황을 저장한다."""
    import datetime
    from pathlib import Path as _Path

    output_dir = _Path(__file__).resolve().parents[3] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    noise_files: list[tuple[str, str]] = []
    clean_files: list[str] = []
    for fpath in all_files:
        is_n, reason = is_noise_path(fpath)
        if is_n:
            noise_files.append((fpath, reason))
        else:
            clean_files.append(fpath)

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pct = len(noise_files) / max(1, len(all_files)) * 100
    lines = [
        "# 노이즈 파일 제거 보고서",
        f"# 생성일시: {ts}",
        "",
        f"전체 파일: {len(all_files)}개",
        f"노이즈 제거: {len(noise_files)}개 ({pct:.1f}%)",
        f"정상 파일: {len(clean_files)}개",
        "",
        "─" * 50,
        "[제외된 노이즈 파일]",
        "",
    ]
    for fpath, reason in sorted(noise_files, key=lambda x: x[1]):
        lines.append(f"  {reason}  →  {fpath}")
    lines += ["", "─" * 50, "[카테고리별 정상 파일 수]", ""]
    for cat_name, data in cat_items:
        ps_count = len(data["projects"])
        file_count = sum(len(ps.related_files) for ps in data["projects"])
        clean_in_cat = sum(
            1 for ps in data["projects"]
            for f in ps.related_files
            if not is_noise_path(f)[0]
        )
        lines.append(f"  {cat_name}: 프로젝트 {ps_count}개 / 전체파일 {file_count}개 / 정상파일 {clean_in_cat}개")

    (_Path(output_dir) / "noise_filter_report.txt").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(f"[TEST MODE] output/noise_filter_report.txt 저장 ({len(noise_files)}개 노이즈 제거)")


def _save_customer_count_report(cat_items: list) -> None:
    """output/customer_count_report.txt — 카테고리별 고객사 집계 결과를 저장한다."""
    import datetime
    from pathlib import Path as _Path

    output_dir = _Path(__file__).resolve().parents[3] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 카테고리별 고객사 집계 보고서",
        f"# 생성일시: {ts}",
        "",
    ]
    for cat_name, data in cat_items:
        customers = sorted(data["customers"])
        lines += [
            f"[{cat_name}]",
            f"  고객사 수 (Unique): {len(customers)}개",
            f"  고객사 목록: {', '.join(customers) or '없음'}",
            f"  프로젝트 수: {len(data['projects'])}개",
            "",
        ]

    (_Path(output_dir) / "customer_count_report.txt").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(f"[TEST MODE] output/customer_count_report.txt 저장 완료")


def _save_work_structure_txt(cat_map: dict, job_desc: str) -> None:
    """output/work_structure.txt 에 업무 체계 트리를 저장한다."""
    from pathlib import Path
    import datetime

    output_dir = Path(__file__).resolve().parents[3] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "work_structure.txt"

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# 업무 체계 구조 (테스트 모드)",
        f"# 생성일시: {ts}",
        "",
        "== 업무분장 원문 ==",
        job_desc.strip() if job_desc.strip() else "(입력 없음)",
        "",
        "== 업무 체계 맵 ==",
        "",
    ]

    for cat_name, data in cat_map.items():
        if cat_name.startswith("__"):
            continue
        children = data.get("children", [])
        customers = sorted(data["customers"])
        n_proj = len(data["projects"])
        n_doc  = data["doc_count"]

        lines.append(cat_name)
        for i, child in enumerate(children):
            prefix = "└" if i == len(children) - 1 else "├"
            lines.append(f"{prefix} {child}")
        lines.append(
            f"  -> 고객사 {len(customers)}개 / 프로젝트 {n_proj}개 / 문서 {n_doc}개"
        )
        if customers:
            lines.append(f"  -> 사례: {', '.join(customers)}")
        lines.append("")

    content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    print(f"[TEST MODE] output/work_structure.txt 저장  ({len(content):,}자)")


def _save_project_mapping_txt(cat_map: dict) -> None:
    """output/project_mapping.txt 에 프로젝트별 매핑 결과와 근거를 저장한다."""
    from pathlib import Path
    import datetime

    output_dir = Path(__file__).resolve().parents[3] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "project_mapping.txt"

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# 프로젝트 매핑 결과 (테스트 모드)",
        f"# 생성일시: {ts}",
        f"# 가중치: 프로젝트명×하위(30) > 폴더명×하위(15) > 파일명×하위(8)",
        f"#         프로젝트명×상위(10) > 폴더명×상위(5)  > 파일명×상위(3)",
        "",
    ]

    for cat_name, data in cat_map.items():
        if cat_name.startswith("__"):
            continue
        for proj_key, reason, score in data.get("_mapping_log", []):
            lines.append(f"{proj_key}")
            lines.append(f"→ {cat_name}")
            lines.append(f"  (근거: {reason}, 점수: {score})")
            lines.append("")

    content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    print(f"[TEST MODE] output/project_mapping.txt 저장  ({len(content):,}자)")


def _save_category_score_txt(cat_map: dict) -> None:
    """output/category_score.txt 에 프로젝트별 전체 카테고리 점수를 저장한다.

    폴더/파일/내용 별 점수 분류 상세를 포함한다.
    """
    from pathlib import Path
    import datetime

    score_logs = cat_map.get("__score_logs__", [])
    if not score_logs:
        return

    output_dir = Path(__file__).resolve().parents[3] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "category_score.txt"

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cat_names = [k for k in cat_map if not k.startswith("__")]

    lines = [
        "# 카테고리별 점수 상세 (폴더명 우선순위 반영)",
        f"# 생성일시: {ts}",
        f"# 분류 카테고리: {', '.join(cat_names)}",
        "# 점수 기준:",
        "#   폴더 특화키워드 히트: +20점  |  폴더 지문 히트: +6점  |  회사명 패턴: +15~25점",
        "#   파일 지문 히트: +5점  |  파일 특화키워드 히트: +8점",
        "#   내용 지문 히트: +3점",
        "#   job desc 하위키워드×프로젝트명: +30점/단어  |  상위키워드×프로젝트명: +10점/단어",
        "",
    ]

    for entry in score_logs:
        pk           = entry["project_key"]
        scores       = entry["scores"]
        winner       = entry["winner"]
        reason       = entry["reason"]
        path_details = entry.get("path_details", {})
        sample_paths = entry.get("related_files", [])

        lines.append(f"{'='*60}")
        lines.append(f"프로젝트: {pk}")

        # 샘플 경로 표시
        if sample_paths:
            lines.append("  경로 샘플:")
            for fp in sample_paths[:3]:
                lines.append(f"    {fp}")

        lines.append("")

        # 카테고리별 점수 + 경로 분석 상세
        for cat_name in cat_names:
            s    = scores.get(cat_name, 0)
            mark = " ◀ 선택" if cat_name == winner else ""
            lines.append(f"  [{cat_name}] {s}점{mark}")

            pd = path_details.get(cat_name, {})
            if pd.get("folder"):
                lines.append(f"    폴더점수: {', '.join(pd['folder'][:5])}")
            if pd.get("file"):
                lines.append(f"    파일점수: {', '.join(pd['file'][:5])}")
            if pd.get("content"):
                lines.append(f"    내용점수: {', '.join(pd['content'][:3])}")

        lines.append(f"  → 최종: {winner}  (근거: {reason})")
        lines.append("")

    content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    print(f"[TEST MODE] output/category_score.txt 저장  ({len(content):,}자)")


# ── 테스트 모드 보고서 헬퍼 함수 ────────────────────────────────────────────

def _tm_doc_by_project(doc_summaries: list) -> dict:
    """DocumentSummary 목록을 project_key(최상위 폴더) 기준으로 그룹화한다."""
    result: dict = {}
    for ds in doc_summaries:
        parts = ds.display_name.replace("\\", "/").split("/")
        pkey = parts[0] if len(parts) >= 2 else "기타 (최상위 파일)"
        result.setdefault(pkey, []).append(ds)
    return result


def _tm_classify_work_progress(
    cat_items: list,
    doc_by_project: dict,
) -> tuple:
    """최근 수정일 기준으로 진행 중(7일 이내) / 최근 완료(8~30일) 프로젝트를 분류한다.

    Returns:
        (in_progress, recent_done) – 각 항목은 (cat_name, project_key, max_date) 튜플
    """
    import datetime
    today = datetime.date.today()

    # 프로젝트별 최근 수정일 계산
    proj_max_dt: dict = {}
    for pkey, docs in doc_by_project.items():
        dates = []
        for ds in docs:
            try:
                d = datetime.date.fromisoformat(ds.modified_dt)
                dates.append(d)
            except (ValueError, AttributeError, TypeError):
                pass
        if dates:
            proj_max_dt[pkey] = max(dates)

    in_progress: list = []
    recent_done: list = []

    for cat_name, data in cat_items:
        for ps in data["projects"]:
            pkey  = ps.project_key
            max_dt = proj_max_dt.get(pkey)
            if max_dt is None:
                continue
            delta = (today - max_dt).days
            if delta <= 7:
                in_progress.append((cat_name, pkey, max_dt))
            elif delta <= 30:
                recent_done.append((cat_name, pkey, max_dt))

    in_progress.sort(key=lambda x: x[2], reverse=True)
    recent_done.sort(key=lambda x: x[2], reverse=True)
    return in_progress, recent_done


def _tm_extract_filenames(related_files: list, max_n: int = 20) -> list:
    """related_files에서 업무 산출물 파일명을 점수 기반으로 추출한다.

    선정 기준 (내림차순):
      1. 노이즈 파일 제외 (LICENSE, requirements, build-output*, tmp-* 등)
      2. 산출물 점수 = _EXT_DELIVERABLE_WEIGHT (docx+30 > pdf+20 > txt-20)
      3. 같은 점수 내에서는 원본 순서 유지
    """
    import re as _re
    from pathlib import Path as _Path

    # (deliverable_score, original_index, fpath)
    candidates: list[tuple[int, int, str]] = []
    for idx, fpath in enumerate(related_files):
        score = _deliverable_score(fpath)
        if score == -9999:          # 노이즈 → 완전 제외
            continue
        candidates.append((score, idx, fpath))

    # 산출물 점수 내림차순 (같은 점수는 원본 순서)
    candidates.sort(key=lambda x: (-x[0], x[1]))

    seen: set = set()
    result = []
    for _, _, fpath in candidates:
        fname = _re.split(r"[/\\]", fpath.replace("\\", "/"))[-1]
        fname_noext = _re.sub(r"\.[^.]+$", "", fname).strip()
        if fname_noext and fname_noext not in seen:
            seen.add(fname_noext)
            result.append(fname_noext)
        if len(result) >= max_n:
            break
    return result


def _tm_top_keywords(texts: list, n: int = 20) -> list:
    """여러 텍스트에서 한국어·영어 키워드 빈도 TOP N을 반환한다."""
    import re as _re
    from collections import Counter

    STOPWORDS = {
        "있는", "하는", "이후", "관련", "기준", "문서", "파일", "자료",
        "보고서", "결과", "내용", "정리", "작성", "운영", "관리", "진행",
        "완료", "담당", "업무", "지원", "처리", "분석", "수행",
        "the", "and", "for", "with", "this", "that", "from",
    }

    counter: Counter = Counter()
    for text in texts:
        words = _re.findall(r"[가-힣]{2,}|[a-zA-Z]{3,}", text)
        for w in words:
            wl = w.lower()
            if wl not in STOPWORDS and len(w) >= 2:
                counter[wl] += 1

    return [word for word, _ in counter.most_common(n)]


def _tm_issue_keywords(related_files: list) -> list:
    """파일명에서 이슈·문제 관련 키워드를 추출한다."""
    import re as _re

    ISSUE_PATTERNS = [
        "권고사직", "성과평가", "조직진단", "연봉인상", "인센티브",
        "이슈", "문제", "갈등", "지연", "변경", "긴급", "미완", "보류",
        "승진", "퇴직", "해고", "분쟁", "클레임", "불만", "수정요청",
        "재작업", "오류", "수정", "항의",
    ]

    found: list = []
    for fpath in related_files:
        fname = _re.split(r"[/\\]", fpath.replace("\\", "/"))[-1].lower()
        for kw in ISSUE_PATTERNS:
            if kw.lower() in fname and kw not in found:
                found.append(kw)
    return found


def _tm_procedures(fp_type: str | None, children: list) -> list:
    """카테고리 유형별 규칙 기반 수행 절차 목록을 반환한다.

    업무분장에 하위 업무가 있으면 그것을 번호 형 절차로 사용한다.
    """
    if children:
        return [f"{i}. {c}" for i, c in enumerate(children, 1)]

    PROCEDURES: dict = {
        "인사": [
            "1. 고객사 초기 미팅 (요구사항 파악 및 현황 진단)",
            "2. 요구사항 정리 및 프로젝트 범위 확정",
            "3. 제안서 및 견적서 작성 → 고객사 승인",
            "4. 인사제도·평가제도 설계 (초안 작성)",
            "5. 고객사 검토 및 피드백 반영",
            "6. 최종 결과물 납품 (가이드·양식·규정 등)",
            "7. 사후 운영 지원 및 관리",
        ],
        "개발": [
            "1. 요구사항 정의 및 기술 스택 결정",
            "2. DB 스키마 및 API 설계",
            "3. 프론트엔드·백엔드 개발",
            "4. 테스트 및 QA",
            "5. 배포 (Vercel·Supabase 등)",
            "6. 유지보수 및 추가 기능 개발",
        ],
        "마케팅": [
            "1. 타겟 고객 및 키워드 분석",
            "2. 콘텐츠 기획 및 소재 준비",
            "3. 블로그·SNS·광고 콘텐츠 제작",
            "4. 채널 배포 (브런치·인스타·유튜브 등)",
            "5. 성과 분석 (조회수·클릭률·전환율)",
            "6. 결과 피드백 반영 및 다음 사이클 계획",
        ],
        "영업": [
            "1. 잠재 고객 발굴 및 초기 접촉",
            "2. 미팅 설정 및 니즈 파악",
            "3. 제안서·견적서 작성",
            "4. 협상 및 계약 체결",
            "5. 계약 후 프로젝트 인계 및 관리",
        ],
    }
    return PROCEDURES.get(fp_type or "", [
        "1. 업무 목표 및 범위 설정",
        "2. 자료 수집 및 현황 파악",
        "3. 핵심 업무 수행",
        "4. 결과물 검토 및 수정",
        "5. 완료 처리 및 기록",
    ])


def _tm_cautions(fp_type: str | None) -> list:
    """카테고리 유형별 규칙 기반 주의사항 목록을 반환한다."""
    CAUTIONS: dict = {
        "인사": [
            "고객사별 결과물(양식·규정·가이드)이 혼동되지 않도록 폴더 관리 철저히",
            "평가양식·직급체계는 버전 관리 필수 (연도·수정일 명기)",
            "고객사 개인정보(직원 명단·연봉 등) 외부 유출 절대 금지",
            "제안서 및 견적서 최신 버전 사용 여부 항상 확인",
            "고객사 피드백 히스토리를 별도 기록·보관",
        ],
        "개발": [
            "Git 커밋 및 브랜치 전략 문서화 후 인수",
            "환경변수(.env) 및 API 키 별도 안전 보관 필수",
            "DB 마이그레이션 이력 반드시 확인",
            "Supabase·Vercel 대시보드 접근 권한 이전",
            "미완성 기능 목록 및 알려진 버그 목록 인수인계",
        ],
        "마케팅": [
            "SNS·광고 계정 로그인 정보 안전 이전",
            "진행 중 광고 캠페인 중단 없이 인수 처리",
            "콘텐츠 예약 발행 일정 확인",
            "채널별 성과 KPI 목표치 인수인계",
        ],
        "영업": [
            "현재 진행 중 협상 상태 및 다음 액션 인수",
            "고객사 담당자 연락처 및 관계 히스토리 이전",
            "미청구 건 및 정산 예정 건 목록 확인",
        ],
    }
    return CAUTIONS.get(fp_type or "", [
        "관련 문서 최신 버전 확인",
        "핵심 연락처 및 이해관계자 파악",
        "미완료 업무 목록 별도 작성",
    ])


def _tm_customer_keyword_freq(
    related_files: list,
    customer_names: list,
) -> list:
    """고객사명이 파일 경로에 등장하는 빈도를 반환한다.

    Args:
        related_files: 분석 대상 파일 경로 목록
        customer_names: 집계할 고객사명 목록 (완전 일치·부분 일치)

    Returns:
        [(고객사명, 등장 파일 수), ...] 내림차순 정렬
    """
    from collections import Counter
    counter: Counter = Counter()
    for cname in customer_names:
        if not cname or len(cname) < 2:
            continue
        for fpath in related_files:
            if cname in fpath:
                counter[cname] += 1
    return counter.most_common()


def _tm_keyword_frequency(
    related_files: list,
    n: int = 20,
    customer_names: list | None = None,
) -> list:
    """관련 파일명·폴더명에서 키워드 빈도 TOP N을 반환한다.

    30일 제한 없이 전체 related_files 를 기준으로 집계한다.
    시스템어·연도·버전·고객사명은 키워드에서 제외한다.

    Args:
        related_files: 분석 대상 파일 경로 목록
        n: 상위 N개 반환
        customer_names: 제외할 고객사명 목록 (키워드가 아닌 고객사 목록으로 별도 관리)

    Returns:
        list of (keyword, count) tuples, sorted by count descending
    """
    import re as _re
    from collections import Counter

    # 일반 불용어
    STOPWORDS = {
        "있는", "하는", "이후", "관련", "기준", "문서", "파일", "자료",
        "보고서", "결과", "내용", "정리", "작성", "운영", "관리", "진행",
        "완료", "담당", "업무", "지원", "처리", "분석", "수행", "기타",
        "the", "and", "for", "with", "this", "that", "from",
    }
    FILE_EXT = {"docx", "xlsx", "pptx", "hwp", "hwpx", "pdf", "txt", "zip", "csv"}

    # 노이즈 단어 (시스템어·연도·버전) + 동적 고객사명
    noise_lower: set[str] = set(w.lower() for w in _KW_NOISE_WORDS)
    if customer_names:
        for cname in customer_names:
            # 고객사명은 전체 단어 및 일부 토큰 모두 제거
            for token in _re.findall(r"[가-힣]{2,}|[a-zA-Z]{3,}", cname):
                noise_lower.add(token.lower())

    # 노이즈 폴더 파일은 키워드 집계에서 건너뜀
    counter: Counter = Counter()
    for fpath in related_files:
        is_n, _ = is_noise_path(fpath)
        if is_n:
            continue
        parts = _re.split(r"[/\\]", fpath.replace("\\", "/"))
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                part = _re.sub(r"\.[^.]+$", "", part)  # 확장자 제거
            # 연도 패턴 제거 (4자리 숫자)
            part = _re.sub(r"\b(19|20)\d{2}\b", "", part)
            # 버전 패턴 제거 (v1, v2.0 등)
            part = _re.sub(r"\bv\d+(\.\d+)*\b", "", part, flags=_re.IGNORECASE)
            words = _re.findall(r"[가-힣]{2,}|[a-zA-Z]{3,}", part)
            for w in words:
                wl = w.lower()
                if (
                    wl not in STOPWORDS
                    and wl not in FILE_EXT
                    and wl not in noise_lower
                    and len(w) >= 2
                ):
                    counter[w] += 1

    # 대소문자 통합
    merged: Counter = Counter()
    for word, cnt in counter.items():
        merged[word.lower()] += cnt

    return merged.most_common(n)


def _tm_recent_folders(ps_list: list, doc_by_project: dict, days: int = 30) -> list:
    """최근 N일 이내 수정된 파일이 있는 프로젝트 폴더 목록을 반환한다.

    Returns:
        list of project_key strings (중복 없음, 최근 수정일 내림차순)
    """
    import datetime
    today = datetime.date.today()
    result: list = []   # (project_key, max_modified_date)

    for ps in ps_list:
        pkey = ps.project_key
        docs = doc_by_project.get(pkey, [])
        max_dt = None
        for ds in docs:
            try:
                dt = datetime.date.fromisoformat(ds.modified_dt)
                if (today - dt).days <= days:
                    if max_dt is None or dt > max_dt:
                        max_dt = dt
            except (ValueError, AttributeError, TypeError):
                pass
        if max_dt is not None:
            result.append((pkey, max_dt))

    # 최근 수정일 내림차순
    result.sort(key=lambda x: x[1], reverse=True)
    return [pkey for pkey, _ in result]


def _calc_project_importance(
    ps,
    docs: list,
    related_files: list | None = None,
) -> dict:
    """프로젝트 중요도 점수를 산출한다 (0~100).

    항목별 점수:
      수정일  : 7일내 +30 / 30일내 +20 / 그외 +0   (최대 30)
      문서수  : 1~3개 +5 / 4~10개 +10 / 11개+ +20  (최대 20)
      산출물수: 1~3개 +5 / 4~10개 +10 / 11개+ +15  (최대 15)
      규모    : 확장자 다양성 × 3 + 폴더깊이 × 2    (최대 15)
      진행중  : 최근 수정 파일 있으면 +20             (최대 20)
    합계 최대 100점 → min(100, total) 로 클램핑.

    Returns:
        {
            "score": int,           # 0~100
            "last_modified": str,   # 최근 수정일 (YYYY-MM-DD 또는 "알 수 없음")
            "breakdown": dict,      # 항목별 점수 상세
        }
    """
    import datetime
    from pathlib import Path as _Path

    today     = datetime.date.today()
    files     = related_files or getattr(ps, "related_files", [])
    breakdown = {}

    # ── 최근 수정일 (파일 mtime) ────────────────────────────────────────
    last_mod: datetime.date | None = None
    for fpath in files[:30]:
        try:
            mtime = datetime.date.fromtimestamp(_Path(fpath).stat().st_mtime)
            if last_mod is None or mtime > last_mod:
                last_mod = mtime
        except Exception:
            pass

    # doc modified_dt 에서도 보완
    if last_mod is None:
        for ds in docs:
            try:
                dt = datetime.date.fromisoformat(ds.modified_dt)
                if last_mod is None or dt > last_mod:
                    last_mod = dt
            except Exception:
                pass

    days_ago = (today - last_mod).days if last_mod else 9999
    date_score = 30 if days_ago <= 7 else (20 if days_ago <= 30 else 0)
    breakdown["수정일"] = date_score

    # ── 문서 수 ────────────────────────────────────────────────────────
    doc_count  = getattr(ps, "doc_count", 0) or len(docs)
    doc_score  = 20 if doc_count >= 11 else (10 if doc_count >= 4 else (5 if doc_count >= 1 else 0))
    breakdown["문서수"] = doc_score

    # ── 산출물 수 (업무 문서 확장자 파일) ──────────────────────────────
    DELIV_EXTS = {".docx", ".xlsx", ".pptx", ".pdf", ".hwp", ".hwpx"}
    deliv_count = sum(1 for f in files if _Path(f).suffix.lower() in DELIV_EXTS)
    del_score   = 15 if deliv_count >= 11 else (10 if deliv_count >= 4 else (5 if deliv_count >= 1 else 0))
    breakdown["산출물수"] = del_score

    # ── 프로젝트 규모 (확장자 다양성 + 폴더 깊이) ──────────────────────
    exts       = {_Path(f).suffix.lower() for f in files[:30] if _Path(f).suffix}
    doc_ext_n  = len(exts & DELIV_EXTS)
    depth_max  = max(
        (len(f.replace("\\", "/").split("/")) for f in files[:20] if f),
        default=0,
    )
    scale_score = min(15, doc_ext_n * 3 + max(0, depth_max - 3) * 2)
    breakdown["규모"] = scale_score

    # ── 진행 중 여부 ────────────────────────────────────────────────────
    active_score = 20 if date_score > 0 else 0
    breakdown["진행중"] = active_score

    total = sum(breakdown.values())
    score = min(100, total)

    return {
        "score":         score,
        "last_modified": last_mod.isoformat() if last_mod else "알 수 없음",
        "breakdown":     breakdown,
    }


def _save_project_importance_txt(rows: list) -> None:
    """output/project_importance.txt — 프로젝트 중요도 점수 로그를 저장한다."""
    import datetime
    from pathlib import Path as _Path

    output_dir = _Path(__file__).resolve().parents[3] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "project_importance.txt"

    ts    = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 프로젝트 중요도 점수",
        f"# 생성일시: {ts}",
        "",
        f"{'프로젝트':<30} {'카테고리':<20} {'중요도':>5}  근거",
        "-" * 85,
    ]
    for r in rows:
        bd  = r.get("importance_breakdown", {})
        bd_str = "  ".join(f"{k}:{v}점" for k, v in bd.items())
        lines.append(
            f"{r['name']:<30} {r['category']:<20} {r.get('importance_score', 0):>5}점"
        )
        if bd_str:
            lines.append(f"    {bd_str}")
        lines.append(f"    최근 수정일: {r.get('last_modified', '?')}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[TEST MODE] output/project_importance.txt 저장 완료 ({len(rows)}개 프로젝트)")


def _tm_project_priority_list(
    cat_items: list,
    doc_by_project: dict,
    project_summaries: list,
) -> list:
    """프로젝트 단위로 후임자 우선순위 목록을 생성한다.

    각 항목:
        {
            "name": str,            # project_key
            "category": str,        # 업무 카테고리명
            "last_modified": str,   # 'YYYY-MM-DD'
            "priority": int,        # 1 (7일내) / 2 (30일내) / 3 (그외)
            "deliverables": list,   # 주요 산출물 파일명 (최대 5개, 점수 순)
            "successor_notes": str, # ProjectSummary.successor_notes
        }

    정렬 기준: priority ASC, last_modified DESC
    """
    import datetime
    today = datetime.date.today()

    # project_key → ProjectSummary 맵
    ps_map = {ps.project_key: ps for ps in project_summaries}

    rows: list[dict] = []
    for cat_name, data in cat_items:
        for ps in data["projects"]:
            pkey = ps.project_key
            docs = doc_by_project.get(pkey, [])

            # 최근 수정일 계산
            max_dt: datetime.date | None = None
            for ds in docs:
                try:
                    dt = datetime.date.fromisoformat(ds.modified_dt)
                    if max_dt is None or dt > max_dt:
                        max_dt = dt
                except (ValueError, AttributeError, TypeError):
                    pass

            if max_dt is None:
                continue                # 날짜 정보 없는 프로젝트 제외

            delta = (today - max_dt).days
            if delta <= 7:
                priority = 1
            elif delta <= 30:
                priority = 2
            else:
                priority = 3

            # 주요 산출물 (노이즈 제외, 산출물 점수 순)
            deliverables = _tm_extract_filenames(ps.related_files, max_n=5)

            # successor_notes (ProjectSummary 에서 가져옴)
            full_ps = ps_map.get(pkey)
            successor_notes = (
                full_ps.successor_notes
                if full_ps and full_ps.successor_notes not in ("[정보 부족]", "")
                else ""
            )

            # 중요도 점수 산출
            imp = _calc_project_importance(ps, docs)

            rows.append({
                "name": pkey,
                "category": cat_name,
                "last_modified": max_dt.isoformat(),
                "priority": priority,
                "deliverables": deliverables,
                "successor_notes": successor_notes,
                "importance_score":     imp["score"],
                "importance_breakdown": imp["breakdown"],
            })

    # 정렬: 중요도 점수 내림차순 (동일 점수는 최근 수정일 우선)
    rows.sort(key=lambda r: (-r["importance_score"], r["last_modified"]), reverse=False)
    rows.sort(key=lambda r: -r["importance_score"])
    return rows


def _save_extraction_errors_txt(
    failed_details: list[tuple[str, str, str]],
) -> None:
    """output/extraction_errors.txt — 텍스트 추출 실패 파일을 즉시 저장한다.

    Args:
        failed_details: [(display_name, ext, exception_message), ...]
    """
    import datetime
    from pathlib import Path as _Path

    output_dir = _Path(__file__).resolve().parents[3] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "extraction_errors.txt"

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "[추출 오류 파일]  " + ts,
        "=" * 60,
        f"",
        f"총 {len(failed_details)}개 파일 오류",
        "",
    ]
    for display_name, ext, exc_msg in failed_details:
        # extractor 선택 매핑
        ext_map = {
            ".pdf":  "pdfplumber",
            ".docx": "python-docx",
            ".xlsx": "openpyxl",
            ".txt":  "built-in read_text",
            ".hwp":  "HwpExtractor (olefile)",
            ".hwpx": "HwpExtractor (ZIP+XML)",
            ".zip":  "zipfile + 내부 재귀",
        }
        extractor_name = ext_map.get(ext.lower(), f"지원 없음 ({ext})")
        lines += [
            f"파일명   : {display_name}",
            f"확장자   : {ext}",
            f"Extractor: {extractor_name}",
            f"오류     : {exc_msg}",
            "-" * 40,
        ]

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[Step 5] output/extraction_errors.txt 저장 완료 ({len(failed_details)}개 오류)")


def _save_project_priority_txt(rows: list) -> None:
    """output/project_priority.txt — 후임자 우선 확인 프로젝트 목록을 저장한다."""
    import datetime
    from pathlib import Path as _Path

    output_dir = _Path(__file__).resolve().parents[3] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "project_priority.txt"

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 후임자 우선 확인 프로젝트",
        f"# 생성일시: {ts}",
        "",
    ]
    for r in rows:
        pri_label = {1: "★우선순위 1 (7일내)", 2: "◆우선순위 2 (30일내)", 3: "◇우선순위 3 (30일초과)"}.get(r["priority"], "")
        lines += [
            f"[{pri_label}] {r['name']}",
            f"  카테고리   : {r['category']}",
            f"  최근수정일 : {r['last_modified']}",
        ]
        if r["deliverables"]:
            lines.append(f"  주요산출물 : {', '.join(r['deliverables'])}")
        if r["successor_notes"]:
            lines.append(f"  후임자메모 : {r['successor_notes'][:120]}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[TEST MODE] output/project_priority.txt 저장 완료 ({len(rows)}개 프로젝트)")


def _tm_quality_scores(cat_map: dict, doc_summaries: list, project_summaries: list) -> dict:
    """테스트 모드 품질 점수를 계산한다."""
    score_logs = cat_map.get("__score_logs__", [])
    total_projects = len(project_summaries)

    # 분류 정확도: non-기타 비율
    other_count = len(cat_map.get("기타", {}).get("projects", []))
    classified   = total_projects - other_count
    class_acc    = round(classified / total_projects * 100) if total_projects else 0

    # 프로젝트 매핑 점수: 평균 winning score 정규화 (50점 기준 100%)
    winner_scores = [
        e["scores"].get(e["winner"], 0)
        for e in score_logs if e["winner"] != "기타"
    ]
    avg_score    = sum(winner_scores) / len(winner_scores) if winner_scores else 0
    mapping_sc   = min(100, round(avg_score / 50 * 100))

    # 문서 활용도: 요약 내용 있는 문서 비율
    total_docs   = len(doc_summaries)
    with_content = sum(
        1 for ds in doc_summaries
        if ds.summary_text and len(ds.summary_text) > 20
    )
    doc_util     = round(with_content / total_docs * 100) if total_docs else 0

    # 기타 비율
    other_ratio  = round(other_count / total_projects * 100) if total_projects else 0

    return {
        "분류_정확도":    class_acc,
        "프로젝트_매핑":  mapping_sc,
        "문서_활용도":    doc_util,
        "기타_비율":      other_ratio,
        "총_프로젝트":    total_projects,
        "기타_프로젝트":  other_count,
        "총_문서":        total_docs,
    }


def _save_test_mode_analysis(
    cat_map: dict,
    doc_by_project: dict,
    quality: dict,
    in_progress: list,
    recent_done: list,
) -> None:
    """output/test_mode_analysis.txt 를 생성한다."""
    from pathlib import Path
    import datetime
    import re as _re

    output_dir = Path(__file__).resolve().parents[3] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "test_mode_analysis.txt"

    ts        = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cat_items = [(k, v) for k, v in cat_map.items() if not k.startswith("__")]

    lines = [
        "# 테스트 모드 분석 결과 (규칙 기반)",
        f"# 생성일시: {ts}",
        "",
        "=" * 60,
        "## 품질 점수",
        "=" * 60,
        f"분류 정확도   : {quality['분류_정확도']}점",
        f"프로젝트 매핑 : {quality['프로젝트_매핑']}점",
        f"문서 활용도   : {quality['문서_활용도']}점",
        f"기타 비율     : {quality['기타_비율']}%",
        f"총 프로젝트   : {quality['총_프로젝트']}개",
        f"기타 분류     : {quality['기타_프로젝트']}개",
        f"총 문서       : {quality['총_문서']}개",
        "",
        "=" * 60,
        "## 업무 카테고리",
        "=" * 60,
    ]

    for cat_name, data in cat_items:
        fp_type = _detect_category_fingerprint(cat_name)
        lines.append(f"\n[{cat_name}]")
        lines.append(f"  분류유형    : {fp_type or '기타'}")
        lines.append(f"  프로젝트 수 : {len(data['projects'])}개")
        lines.append(f"  문서 수     : {data['doc_count']}개")
        lines.append(f"  고객사      : {', '.join(sorted(data['customers'])) or '없음'}")
        lines.append("  프로젝트 목록:")
        for ps in data["projects"][:10]:
            lines.append(f"    - {ps.project_key}")

        # 주요 산출물 파일명
        all_files = [f for ps in data["projects"] for f in ps.related_files]
        out_files = _tm_extract_filenames(all_files, 10)
        if out_files:
            lines.append("  주요 산출물:")
            for f in out_files:
                lines.append(f"    - {f}")

        # 이슈 키워드
        issues = _tm_issue_keywords(all_files)
        if issues:
            lines.append(f"  이슈 키워드: {', '.join(issues)}")

    lines += [
        "",
        "=" * 60,
        "## 진행 중 업무 (최근 7일)",
        "=" * 60,
    ]
    for cat_name, pkey, dt in in_progress[:15]:
        lines.append(f"  [{cat_name}] {pkey} — {dt}")
    if not in_progress:
        lines.append("  (해당 없음)")

    lines += [
        "",
        "=" * 60,
        "## 최근 완료 업무 (8~30일)",
        "=" * 60,
    ]
    for cat_name, pkey, dt in recent_done[:15]:
        lines.append(f"  [{cat_name}] {pkey} — {dt}")
    if not recent_done:
        lines.append("  (해당 없음)")

    lines += [
        "",
        "=" * 60,
        "## 전체 주요 키워드 (파일명 기반)",
        "=" * 60,
    ]
    all_file_names = []
    for _, data in cat_items:
        for ps in data["projects"]:
            for fpath in ps.related_files:
                all_file_names.append(
                    _re.split(r"[/\\]", fpath.replace("\\", "/"))[-1]
                )
    top_kw = _tm_top_keywords(all_file_names, 30)
    lines.append("  " + ", ".join(top_kw))

    content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    print(f"[TEST MODE] output/test_mode_analysis.txt 저장  ({len(content):,}자)")


# 보고서 상단 1회 표시용 GPT 추가 제공 안내 블록
_GPT_NOTICE_BLOCK = """\
> **[라이트 이상 모드 추가 제공 항목]**
>
> - 업무 목적
> - 업무 설명
> - 리스크 분석
> - 핵심 노하우
> - 후임자 행동계획"""

# 각 섹션 내 단문 플레이스홀더 (반복 방지)
_GPT_PLACEHOLDER_SHORT = "\n> *[라이트 이상 모드에서 제공]*\n"

# 이전 호환용 별칭 (외부 참조 코드가 있을 경우 대비)
_GPT_PLACEHOLDER_BLOCK = _GPT_PLACEHOLDER_SHORT


def _append_report_bullets(lines: list[str], items: list[str]) -> None:
    if not items:
        lines.append("* (없음)")
        return
    for item in items:
        text = str(item).strip()
        if text:
            lines.append(f"* {text}")


_REPORT_CONTAINER_NAMES = {
    "고객사", "결과물", "회의록", "기타", "자료", "문서",
    "documents", "document", "results", "result", "outputs", "output", "misc",
}


def _report_reconstruction_units(report_reconstruction) -> list:
    if report_reconstruction is None:
        return []
    units = list(getattr(report_reconstruction, "normalized_units", []) or [])
    visible = [
        unit for unit in units
        if not _is_report_container_name(getattr(unit, "unit_name", ""))
    ]
    if visible:
        return visible
    if getattr(report_reconstruction, "fallback_used", False):
        return units
    return []


def _cat_items_from_report_units(report_units: list) -> list:
    cat_items: list = []
    for unit in report_units:
        projects = list(getattr(unit, "projects", []) or [])
        document_names = list(getattr(unit, "document_names", []) or [])
        if not document_names:
            document_names = [
                f for ps in projects
                for f in (getattr(ps, "related_files", []) or [])
            ]
        cat_items.append((
            getattr(unit, "unit_name", ""),
            {
                "projects": projects,
                "doc_count": len(document_names),
                "customers": set(),
                "children": [],
                "document_names": document_names,
                "unit_type": getattr(unit, "unit_type", ""),
                "confidence": getattr(unit, "confidence", 0),
            },
        ))
    return cat_items


def _cat_item_files(data: dict) -> list[str]:
    files = list(data.get("document_names", []) or [])
    if files:
        return files
    return [
        f for ps in data.get("projects", [])
        for f in (getattr(ps, "related_files", []) or [])
    ]


def _report_file_label(path: str) -> str:
    return re.split(r"[/\\]", str(path).replace("\\", "/"))[-1]


def _merge_plan_items(plans: list, attr: str, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for plan in plans:
        for item in getattr(plan, attr, []) or []:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
            if len(merged) >= limit:
                return merged
    return merged


def _is_report_container_name(value: str) -> bool:
    normalized = re.sub(r"[\s_\-]+", "", str(value or "").lower())
    blocked = {
        re.sub(r"[\s_\-]+", "", name.lower())
        for name in _REPORT_CONTAINER_NAMES
    }
    return normalized in blocked


def _build_test_report_md(
    customer_summaries: list,
    project_summaries: list,
    doc_summaries: list,
    job_desc: str = "",
    report_reconstruction=None,
) -> str:
    """테스트 모드용 규칙 기반 보고서 마크다운 생성.

    업무분장(job_desc)을 우선 기준으로 업무 카테고리를 추론하고
    고객사를 카테고리 하위 사례로 배치한다.
    """
    import datetime
    import re as _re
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── 카테고리 매핑 ─────────────────────────────────────────────────────
    groups  = _parse_job_desc_hierarchy(job_desc)
    cat_map = _map_projects_to_categories(groups, project_summaries, customer_summaries)

    # 디버그 로그 저장
    _save_work_structure_txt(cat_map, job_desc)
    _save_project_mapping_txt(cat_map)
    _save_category_score_txt(cat_map)

    cat_items = [(k, v) for k, v in cat_map.items() if not k.startswith("__")]
    reconstructed_units = _report_reconstruction_units(report_reconstruction)
    if reconstructed_units:
        cat_items = _cat_items_from_report_units(reconstructed_units)

    # ── 보조 데이터 ────────────────────────────────────────────────────────
    doc_by_project         = _tm_doc_by_project(doc_summaries)
    in_progress, recent_done = _tm_classify_work_progress(cat_items, doc_by_project)
    quality                = _tm_quality_scores(cat_map, doc_summaries, project_summaries)

    # 분석 로그 저장
    _save_test_mode_analysis(cat_map, doc_by_project, quality, in_progress, recent_done)

    # 노이즈 제거 + 고객사 카운트 보고서
    all_related = [
        f for _, d in cat_items
        for f in _cat_item_files(d)
    ]
    _save_noise_filter_report(all_related, cat_items)
    _save_customer_count_report(cat_items)

    print(f"\n[TEST MODE] 업무 카테고리 매핑 결과:")
    for cat_name, data in cat_items:
        children = data.get("children", [])
        children_str = (
            f" [{', '.join(children[:3])}{'...' if len(children) > 3 else ''}]"
            if children else ""
        )
        print(
            f"  {cat_name}{children_str}: "
            f"프로젝트 {len(data['projects'])}개 "
            f"/ 문서 {data['doc_count']}개"
        )
    print(f"  [품질] 분류 정확도 {quality['분류_정확도']}점 / 기타 비율 {quality['기타_비율']}%")
    print()

    # ══ 섹션 생성 ════════════════════════════════════════════════════════════

    # ── 섹션 1: 담당업무 개요 ────────────────────────────────────────────
    ov_lines = ["주요 업무는 다음과 같이 구분됩니다:\n"]
    for i, (cat_name, data) in enumerate(cat_items, 1):
        ov_lines.append(
            f"{i}. **{cat_name}**"
            f" — 업무 {1}개"
            f" / 문서 {data['doc_count']}개"
        )
    ov_lines.append(
        f"\n최근 30일 기준 분석 현황:\n"
        f"- 업무: **{len(cat_items)}개**\n"
        f"- 프로젝트: **{len(project_summaries)}개**\n"
        f"- 문서: **{len(doc_summaries)}개**"
    )
    overview = "\n".join(ov_lines)

    # ── 섹션 2: 업무 체계 맵 ─────────────────────────────────────────────
    work_map_blocks = [_format_category_tree(n, d) for n, d in cat_items]
    work_map = "\n\n".join(work_map_blocks) or "(데이터 없음)"

    # ── 섹션 3: 현재 진행 중 업무 ────────────────────────────────────────
    ip_lines: list[str] = []
    if in_progress:
        ip_lines.append("**진행 중** (최근 7일 이내 수정):\n")
        for cat_name, pkey, dt in in_progress[:10]:
            label = cat_name if reconstructed_units else pkey
            ip_lines.append(f"- {label}  [{cat_name}]  —  수정일: {dt}")
    else:
        ip_lines.append("(최근 7일 이내 수정된 프로젝트 없음)")

    rd_lines: list[str] = []
    if recent_done:
        rd_lines.append("\n**최근 완료** (8~30일 이내 수정):\n")
        for cat_name, pkey, dt in recent_done[:10]:
            label = cat_name if reconstructed_units else pkey
            rd_lines.append(f"- {label}  [{cat_name}]  —  수정일: {dt}")

    progress_section = "\n".join(ip_lines + rd_lines)

    # ── 섹션 4: 업무 카테고리별 상세 설명 ───────────────────────────────
    # 최근 사용 폴더 = 최근 30일 이내 수정 파일이 있는 프로젝트 폴더
    cat_detail_blocks: list[str] = []
    for cat_name, data in cat_items:
        ps_list  = data["projects"]
        children = data.get("children", [])

        block = [f"### {cat_name}"]
        if children:
            block.append(f"\n**주요 하위 업무**: {', '.join(children)}")

        if reconstructed_units:
            related_docs = _tm_extract_filenames(_cat_item_files(data), 8)
            if related_docs:
                block.append("\n**주요 관련 문서**:")
                for doc_name in related_docs:
                    block.append(f"- {doc_name}")
            else:
                block.append("\n**주요 관련 문서**: (문서 정보 없음)")
        else:
            recent_folders = _tm_recent_folders(ps_list, doc_by_project, days=30)
            if recent_folders:
                block.append("\n**최근 사용 폴더** (최근 30일 이내 수정 파일 존재):")
                for folder in recent_folders[:8]:
                    block.append(f"- {folder}")
                if len(recent_folders) > 8:
                    block.append(f"- … (외 {len(recent_folders)-8}개)")
            else:
                block.append("\n**최근 사용 폴더**: (최근 30일 이내 수정 파일 없음)")

        block.append(_GPT_PLACEHOLDER_BLOCK)
        cat_detail_blocks.append("\n".join(block))
    cat_detail = "\n\n---\n\n".join(cat_detail_blocks) or "(카테고리 없음)"

    # ── 섹션 5: 업무별 수행 절차 (산출물 기반 자동 추론 → 카테고리 템플릿 fallback)
    from src.core.workflow_templates import (
        render_workflow, get_workflow, infer_workflow_from_files,
    )
    import datetime as _dt

    proc_blocks: list[str] = []
    wf_log_lines: list[str] = [
        "# 카테고리별 업무 흐름 보고서",
        f"# 생성일시: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for cat_name, data in cat_items:
        # 카테고리 내 모든 프로젝트 파일 집계 → 파일 기반 흐름 추론에 활용
        cat_all_files = _cat_item_files(data)
        flow_md = render_workflow(cat_name, related_files=cat_all_files)
        proc_blocks.append(f"### {cat_name}\n\n{flow_md}")

        # 로그: 추론 방식 기록
        inferred_steps = infer_workflow_from_files(cat_all_files)
        template_steps = get_workflow(cat_name)
        source = "파일기반추론" if inferred_steps else ("카테고리템플릿" if template_steps else "기본fallback")
        active_steps   = inferred_steps or template_steps or []
        wf_log_lines += [
            f"[{cat_name}]",
            f"  추론방식: {source}",
            f"  단계 수: {len(active_steps)}",
        ]
        for i, s in enumerate(active_steps, 1):
            wf_log_lines.append(f"    {i}. {s}")
        wf_log_lines.append("")

    procedures_section = "\n\n".join(proc_blocks) or "(데이터 없음)"

    # workflow_report.txt 저장
    try:
        from pathlib import Path as _WFPath
        _wf_dir = _WFPath(__file__).resolve().parents[3] / "output"
        _wf_dir.mkdir(parents=True, exist_ok=True)
        (_wf_dir / "workflow_report.txt").write_text(
            "\n".join(wf_log_lines), encoding="utf-8"
        )
        print(f"[TEST MODE] output/workflow_report.txt 저장 완료")
    except Exception as _e:
        print(f"[WARN] workflow_report.txt 저장 실패: {_e}")

    # ── 섹션 6: 업무별 주요 산출물 ──────────────────────────────────────
    output_blocks: list[str] = []
    for cat_name, data in cat_items:
        all_files = _cat_item_files(data)
        out_files = _tm_extract_filenames(all_files, 20)
        block = [f"### {cat_name}\n"]
        for f in out_files:
            block.append(f"- {f}")
        if not out_files:
            block.append("(파일 정보 없음)")
        output_blocks.append("\n".join(block))
    outputs_section = "\n\n".join(output_blocks) or "(데이터 없음)"

    # ── 섹션 7: 최근 30일 최다 키워드 (고객사 키워드 / 업무 키워드 분리) ─
    # 전체 related_files 기준 / 시스템어·연도·버전 제외
    all_customer_names = [cs.customer_name for cs in customer_summaries]
    kw_blocks: list[str] = []
    for cat_name, data in cat_items:
        all_files = _cat_item_files(data)
        cat_customers = list(data["customers"])

        # [고객사 키워드]: 고객사명이 파일 경로에 등장하는 빈도
        cust_kw = _tm_customer_keyword_freq(
            all_files,
            customer_names=all_customer_names + cat_customers,
        )

        # [업무 키워드]: 고객사명을 제외한 파일명/폴더명 기반 키워드
        biz_kw = _tm_keyword_frequency(
            all_files, n=20,
            customer_names=all_customer_names + cat_customers,
        )

        block = [f"### {cat_name}\n"]

        # 고객사 키워드 서브섹션
        block.append("**[고객사 키워드]**")
        if cust_kw:
            for cname, cnt in cust_kw[:10]:
                block.append(f"- {cname} ({cnt})")
        else:
            block.append("- (고객사 파일 미확인)")

        block.append("")

        # 업무 키워드 서브섹션
        block.append("**[업무 키워드]**")
        if biz_kw:
            for word, cnt in biz_kw:
                block.append(f"- {word} ({cnt})")
        else:
            block.append("- (키워드 추출 불가)")

        kw_blocks.append("\n".join(block))
    kw_section = "\n\n".join(kw_blocks) or "(데이터 없음)"

    # ── 섹션 8: 업무별 주의사항 ──────────────────────────────────────────
    caution_blocks: list[str] = []
    for cat_name, data in cat_items:
        fp_type  = _detect_category_fingerprint(cat_name)
        cautions = _tm_cautions(fp_type)
        block    = [f"### {cat_name}\n"]
        for c in cautions:
            block.append(f"- {c}")
        caution_blocks.append("\n".join(block))
    cautions_section = "\n\n".join(caution_blocks) or "(데이터 없음)"

    # ── 섹션 9: 업무별 핵심 노하우 ──────────────────────────────────────
    # 노하우는 GPT 영역 → 공통 플레이스홀더 표시
    knowhow_blocks: list[str] = []
    for cat_name, _ in cat_items:
        knowhow_blocks.append(f"### {cat_name}\n{_GPT_PLACEHOLDER_BLOCK}")
    knowhow_section = "\n\n".join(knowhow_blocks) or "(데이터 없음)"

    # ── 섹션 10: 후임자 우선 확인사항 (중요도 점수 기준 정렬) ──────────
    priority_rows = _tm_project_priority_list(cat_items, doc_by_project, project_summaries)
    _save_project_priority_txt(priority_rows)       # output/project_priority.txt
    _save_project_importance_txt(priority_rows)     # output/project_importance.txt

    cl_lines: list[str] = []
    if reconstructed_units:
        for cat_name, data in cat_items[:15]:
            files = _tm_extract_filenames(_cat_item_files(data), 5)
            cl_lines.append(f"#### {cat_name}")
            cl_lines.append(f"- 문서 수: **{data['doc_count']}개**")
            if files:
                cl_lines.append("- 우선 확인 문서:")
                for file_name in files:
                    cl_lines.append(f"  * {file_name}")
            cl_lines.append("")
    else:
        for row in priority_rows[:15]:          # 최대 15개 프로젝트 표시
            imp_score = row.get("importance_score", 0)
            bd        = row.get("importance_breakdown", {})
            bd_str    = " / ".join(f"{k} {v}점" for k, v in bd.items()) if bd else ""

            cl_lines.append(f"#### {row['name']}  `[{row['category']}]`")
            cl_lines.append(f"- **중요도 {imp_score}점**   *(수정일:{bd.get('수정일',0)} 문서:{bd.get('문서수',0)} 산출물:{bd.get('산출물수',0)} 규모:{bd.get('규모',0)} 진행:{bd.get('진행중',0)})*")
            cl_lines.append(f"- 최근 수정일: **{row['last_modified']}**")

            if row["deliverables"]:
                cl_lines.append("- 주요 산출물:")
                for d in row["deliverables"]:
                    cl_lines.append(f"  * {d}")

            if row["successor_notes"]:
                cl_lines.append("- 후임자 확인사항:")
                for note_line in row["successor_notes"].split("\n")[:3]:
                    note_line = note_line.strip()
                    if note_line:
                        cl_lines.append(f"  * {note_line}")

            cl_lines.append("")

    if not cl_lines:
        cl_lines = ["(최근 수정된 프로젝트 없음)"]

    cl_lines.append(_GPT_PLACEHOLDER_BLOCK)
    checklist = "\n".join(cl_lines)

    # ── 섹션 11: 파일 맵 ─────────────────────────────────────────────────
    file_map  = "| 파일/폴더 경로 | 업무 카테고리 | 비고 |\n|--------------|------------|------|\n"
    shown_fm: set[str] = set()
    for cat_name, data in cat_items:
        for fname in _cat_item_files(data)[:3]:
            display_fname = _report_file_label(fname)
            if display_fname in shown_fm:
                continue
            is_n, _ = is_noise_path(fname)
            if is_n:
                continue
            shown_fm.add(display_fname)
            file_map += f"| {display_fname} | {cat_name} | — |\n"

    # ── 섹션 12: 후임자 행동계획 ───────────────────────────────────────────
    action_engine = ActionPlanEngine()
    action_blocks: list[str] = []
    if reconstructed_units:
        for cat_name, data in cat_items:
            plans = []
            for ps in data["projects"]:
                plan = getattr(ps, "action_plan", None)
                if plan is None:
                    plan = action_engine.build_action_plan(
                        ps,
                        getattr(ps, "representative_docs", []),
                        getattr(ps, "supporting_docs", []),
                    )
                plans.append(plan)
            block: list[str] = [f"### {cat_name}", "", "**우선 업무**"]
            _append_report_bullets(block, _merge_plan_items(plans, "priority_tasks"))
            block.extend(["", "**필수 문서**"])
            _append_report_bullets(
                block,
                _merge_plan_items(plans, "required_documents") or _tm_extract_filenames(_cat_item_files(data), 5),
            )
            block.extend(["", "**주의 리스크**"])
            _append_report_bullets(block, _merge_plan_items(plans, "risks"))
            block.extend(["", "**첫 주 행동계획**"])
            _append_report_bullets(block, _merge_plan_items(plans, "first_week_actions"))
            action_blocks.append("\n".join(block))
    else:
        for ps in project_summaries:
            plan = getattr(ps, "action_plan", None)
            if plan is None:
                plan = action_engine.build_action_plan(
                    ps,
                    getattr(ps, "representative_docs", []),
                    getattr(ps, "supporting_docs", []),
                )

            block: list[str] = [f"### {ps.project_key}", "", "**우선 업무**"]
            _append_report_bullets(block, getattr(plan, "priority_tasks", []))
            block.extend(["", "**필수 문서**"])
            _append_report_bullets(block, getattr(plan, "required_documents", []))
            block.extend(["", "**주의 리스크**"])
            _append_report_bullets(block, getattr(plan, "risks", []))
            block.extend(["", "**첫 주 행동계획**"])
            _append_report_bullets(block, getattr(plan, "first_week_actions", []))
            action_blocks.append("\n".join(block))

    action_plan_section = "\n\n".join(action_blocks) or "(데이터 없음)"

    # ── 품질 점수 섹션 ────────────────────────────────────────────────────
    quality_section = (
        f"| 항목 | 점수 |\n|------|------|\n"
        f"| 분류 정확도 | {quality['분류_정확도']}점 |\n"
        f"| 프로젝트 매핑 | {quality['프로젝트_매핑']}점 |\n"
        f"| 문서 활용도 | {quality['문서_활용도']}점 |\n"
        f"| 기타 비율 | {quality['기타_비율']}% |\n\n"
        "> **분류 정확도** = 정상 분류 프로젝트 / 전체 프로젝트 × 100  \n"
        "> **프로젝트 매핑** = 평균 카테고리 매칭 점수 (정규화, 50점 기준 100%)  \n"
        "> **문서 활용도** = 요약 추출된 문서 / 전체 문서 × 100  \n"
        "> **기타 비율** = '기타' 분류 프로젝트 비율 — 낮을수록 분류 정확도 높음"
    )

    # ── 카테고리 통계 ─────────────────────────────────────────────────────
    stats_lines = ["| 업무 | 프로젝트 수 | 문서 수 |",
                   "|------|---------|------|"]
    for cat_name, data in cat_items:
        stats_lines.append(
            f"| {cat_name} | {len(data['projects'])}개 | {data['doc_count']}개 |"
        )
    stats_table = "\n".join(stats_lines)

    return f"""\
# 업무 수행 체계 복원 보고서

> **[테스트 모드 — 규칙 기반 업무 복원]** GPT 미사용 / 비용 $0 / 생성일시: {ts}

{_GPT_NOTICE_BLOCK}

---

## 1. 담당업무 개요

{overview}

---

## 2. 업무 체계 맵

{work_map}

---

## 3. 현재 진행 중 업무

{progress_section}

---

## 4. 업무 카테고리별 상세 설명

{cat_detail}

---

## 5. 업무별 수행 절차

{procedures_section}

---

## 6. 업무별 주요 산출물

{outputs_section}

---

## 7. 최근 30일 최다 키워드

> 최근 30일 동안 생성·수정된 파일명·폴더명 기준으로 가장 많이 등장한 키워드입니다.  
> 고객사명이 포함될 수 있으며, 실제 업무 집중 영역을 나타냅니다.

{kw_section}

---

## 8. 업무별 주의사항

{cautions_section}

---

## 9. 업무별 핵심 노하우

{knowhow_section}

---

## 10. 후임자 우선 확인사항

{checklist}

---

## 11. 주요 파일 맵

{file_map}

---

## 12. 후임자 행동계획

{action_plan_section}

---

## [테스트 모드 품질 점수]

{quality_section}

---

## [카테고리 통계]

{stats_table}

업무 카테고리 추론 기준: 업무분장 입력값 + 폴더명·파일명 분류  
카테고리 매핑 방식: 폴더명 우선 가중 키워드 스코어링 (GPT 미사용)
"""


def _build_draft_report(
    customer_summaries: list,
    project_summaries: list,
    doc_summaries: list,
    job_desc: str = "",
    report_reconstruction=None,
) -> str:
    """규칙기반 초안 보고서 마크다운을 생성한다.

    테스트 모드와 동일한 규칙 기반 로직을 100% 재사용하며,
    GPT 보강 단계의 입력 자료로 활용된다. (중복 구현 없음)
    """
    import datetime
    report = _build_test_report_md(
        customer_summaries,
        project_summaries,
        doc_summaries,
        job_desc=job_desc,
        report_reconstruction=report_reconstruction,
    )
    # 테스트 모드 헤더를 초안 헤더로 교체
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    report = report.replace(
        "> **[테스트 모드 — 규칙 기반 업무 복원]** GPT 미사용 / 비용 $0",
        f"> **[규칙기반 초안]** 파일명·폴더명·메타데이터 기반 자동 생성 / {ts}",
    )
    return report


def _save_draft_report_md(draft_md: str) -> None:
    """output/draft_report.md 에 규칙기반 초안을 저장한다."""
    from pathlib import Path
    output_dir = Path(__file__).resolve().parents[3] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "draft_report.md"
    path.write_text(draft_md, encoding="utf-8")
    print(f"[Step 8.8] output/draft_report.md 저장 완료  ({len(draft_md):,}자)")


def _save_test_report_docx(markdown: str) -> str:
    """테스트 보고서를 output/test_report.docx 로 저장하고 경로를 반환한다."""
    from pathlib import Path
    from src.core.document_writer import DocumentWriter

    output_dir = Path(__file__).resolve().parents[3] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = str(output_dir / "test_report.docx")

    writer = DocumentWriter()
    doc = writer.create(markdown)
    writer.save(doc, path)
    print(f"[테스트 모드] test_report.docx 저장 완료: {path}")
    return path


# ── 보고서 고객사 검증 헬퍼 ────────────────────────────────────────────
def _validate_report_customers(
    result_md: str,
    customer_summaries: "list",
    all_project_summaries: "list",
) -> None:
    """
    최종 보고서를 검증하고 output/report_validation.txt 를 저장한다.

    검증 항목:
      1. 업무 카테고리 수
      2. 카테고리별 고객사 사례 수
      3. 사용된 고객사 목록 (허용 목록 내)
      4. 허용되지 않은 고객사 존재 여부 (WARNING)
    """
    import datetime
    import re
    from pathlib import Path
    from src.core.customer_summarizer import _extract_customer_name

    # ── 허용 목록 구성 ─────────────────────────────────────────────────
    allowed_names: set[str] = {cs.customer_name for cs in customer_summaries}

    # project_summaries 전체에서 허용 외 후보 추출
    all_possible: set[str] = set()
    for ps in all_project_summaries:
        name = _extract_customer_name(ps)
        if name and name not in allowed_names and len(name) >= 2:
            all_possible.add(name)

    # ── 섹션 헤딩 추출 (## 로 시작하는 라인) ──────────────────────────
    section_headings: list[str] = re.findall(
        r"^##\s+.+", result_md, re.MULTILINE
    )

    # ── 업무 카테고리 추출 (### 로 시작하는 라인) ──────────────────────
    category_headings: list[str] = re.findall(
        r"^###\s+(.+)", result_md, re.MULTILINE
    )
    # 카테고리명 정리 (수행 절차, 산출물 등 접미어 포함된 경우 원본 유지)
    unique_categories = list(dict.fromkeys(h.strip() for h in category_headings))

    # ── 허용 고객사 등장 여부 ──────────────────────────────────────────
    customers_in_report: list[str] = [
        name for name in sorted(allowed_names) if name in result_md
    ]
    customers_missing_in_report: list[str] = [
        name for name in sorted(allowed_names) if name not in result_md
    ]

    # ── 허용 외 고객사 검사 ───────────────────────────────────────────
    unexpected_found: list[str] = sorted(
        candidate for candidate in all_possible if candidate in result_md
    )

    # ── 콘솔 출력 ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("[보고서 검증]")
    print(f"  섹션 수              : {len(section_headings)}개")
    print(f"  업무 카테고리 수      : {len(unique_categories)}개  →  {', '.join(unique_categories) or '없음'}")
    print(f"  허용된 고객사 수      : {len(allowed_names)}개  →  {', '.join(sorted(allowed_names))}")
    print(f"  보고서 내 사용 고객사 : {len(customers_in_report)}개  →  {', '.join(customers_in_report) or '없음'}")
    if customers_missing_in_report:
        print(f"  보고서 미등장 고객사  : {', '.join(customers_missing_in_report)}")

    if unexpected_found:
        for name in unexpected_found:
            print(f"\n  WARNING: Unexpected customer detected: {name}")
            print(f"           → customer_summaries에 없으나 보고서에 등장합니다.")
            print(f"           → output/final_eval_input.txt 와 보고서를 확인하세요.")
        hallucination_ok = False
    else:
        print("  Hallucination 검증   : 이상 없음 (허용 외 고객사 없음)")
        hallucination_ok = True
    print("=" * 60 + "\n")

    # ── report_validation.txt 저장 ────────────────────────────────────
    try:
        output_dir = Path(__file__).resolve().parents[3] / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"[보고서 검증 결과]  {ts}",
            "=" * 60,
            "",
            f"[1] 업무 카테고리 수: {len(unique_categories)}개",
        ]
        for i, cat in enumerate(unique_categories, 1):
            lines.append(f"    {i}. {cat}")

        lines += [
            "",
            f"[2] 섹션 구조: {len(section_headings)}개 섹션",
        ]
        for h in section_headings:
            lines.append(f"    {h}")

        lines += [
            "",
            f"[3] 허용 고객사 목록: {len(allowed_names)}개",
        ]
        for name in sorted(allowed_names):
            in_report = "O (보고서 등장)" if name in result_md else "X (미등장)"
            lines.append(f"    - {name}  [{in_report}]")

        lines += [
            "",
            f"[4] 허용 외 고객사 (Hallucination) 검사:",
            f"    허용 외 후보: {len(all_possible)}개",
        ]
        if unexpected_found:
            for name in unexpected_found:
                lines.append(f"    WARNING: {name}  ← 보고서에 등장 (허용되지 않음)")
        else:
            lines.append("    결과: 이상 없음 — 허용되지 않은 고객사 없음")

        lines += [
            "",
            "=" * 60,
            f"종합 판정: {'PASS' if hallucination_ok else 'FAIL — Hallucination 감지'}",
        ]

        path = output_dir / "report_validation.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[로그] output/report_validation.txt 저장 완료")
    except Exception as e:
        print(f"[경고] report_validation.txt 저장 실패: {e}")
