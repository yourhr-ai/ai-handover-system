from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

# ── 임계값 ────────────────────────────────────────────────────────────
THRESHOLD_MUST = 80        # 반드시 분석
THRESHOLD_CANDIDATE = 50   # 분석 후보
CURRENT_WORK_DAYS = 30     # 최근 30일 이내 = 현재 진행 업무 후보
RECENT_DONE_DAYS = 90      # 최근 90일 이내 = 최근 완료 업무


def score_label(score: int) -> str:
    if score >= THRESHOLD_MUST:
        return "높음"
    if score >= THRESHOLD_CANDIDATE:
        return "중간"
    return "제외"


# ── 패턴 정의 ──────────────────────────────────────────────────────────
def _c(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_FN_EXCLUDE = _c([
    r'사업자.{0,2}등록증', r'법인.{0,2}등기', r'등기부.{0,2}등본',
    r'통장.{0,2}사본', r'인감.{0,2}증명', r'원천.{0,2}징수',
    r'급여.{0,2}명세', r'주민.{0,2}등록', r'신분증',
    r'여권.{0,2}사본', r'운전.{0,2}면허', r'4대.{0,2}보험',
    r'건강.{0,2}보험.{0,4}(확인|납부|증명)',
    r'국민.{0,2}연금.{0,4}(확인|납부|증명)',
    r'가족.{0,2}관계.{0,2}증명', r'(병역|전역).{0,2}증명',
    r'기본.{0,2}증명서',
])

_FN_HIGH = _c([
    r'(제안|견적).{0,2}(서)?', r'(계약|MOU|MOA|협약).{0,2}(서)?',
    r'(회의|미팅|MTG).{0,4}(록|메모|결과|내용)',
    r'(인터뷰|interview)',
    r'업무.{0,4}(계획|보고|일지|분장|정리|흐름|매뉴얼)',
    r'(성과|평가|실적).{0,4}(보고|자료|결과|제도)',
    r'(기획|산출|결과).{0,4}(서|물|보고)',
    r'(프로젝트|project|PJT)',
    r'컨설팅|consulting',
    r'(고객|클라이언트|client)',
    r'(수행|운영|진행).{0,4}(계획|결과|현황)',
    r'(분기|연간|월간|주간).{0,4}(보고|계획|실적)',
    r'(가이드|guide|manual|매뉴얼)',
    r'(KPI|OKR|목표.{0,4}(설정|관리))',
    r'생활.{0,2}(가이드|안내)', r'직급.{0,2}체계',
    r'(인수|인계|handover)',
    r'제도.{0,4}(설계|개선|운영)',
    r'(채용|온보딩)',
    r'(예산|비용).{0,4}(계획|집행|분석)',
    r'카카오.{0,2}(채널|백업)',
    r'메일.{0,2}(백업|아카이브)',
])

_FN_MEDIUM = _c([
    r'(교육|학습|training).{0,4}(자료|교재)',
    r'(정책|policy|규정|지침)',
    r'(공지|안내|notice)',
    r'(표준|standard|template|양식)',
    r'(조직도|org)', r'(참고|reference)', r'(법령|법규|규정집)',
])

_DIR_HIGH = _c([
    r'(고객|클라이언트|client)', r'(프로젝트|project|PJT)',
    r'(컨설팅|consulting)', r'(제안|견적)',
    r'(회의|미팅)', r'(산출|결과|output)',
    r'(평가|성과|실적)', r'(업무|work)',
    r'(계획|plan)', r'(개발|dev)', r'(마케팅|marketing)',
])

_DIR_EXCLUDE = _c([
    r'(증빙|행정|세무)', r'법인.{0,2}(서류|서)',
    r'(사업자|인감|통장)', r'(개인|personal)',
])

_CONTENT_KEYWORDS = [
    '프로젝트', '고객', '회의', '제안', '계획', '성과', '업무', '수행',
    '컨설팅', '목표', '결과', '보고', '협의', '계약', '인터뷰', 'KPI',
]


# ── 날짜 점수 ──────────────────────────────────────────────────────────
def _modified_score(mtime: float) -> int:
    """최근 수정일 점수: -5 ~ +20"""
    days = (time.time() - mtime) / 86400
    if days <= 7:    return 20
    if days <= 30:   return 15
    if days <= 90:   return 10
    if days <= 180:  return 5
    if days <= 365:  return 0
    if days <= 730:  return 0
    return -5   # 2년 이상


def _created_score(ctime: float) -> int:
    """생성일 점수: 0 ~ +10"""
    days = (time.time() - ctime) / 86400
    if days <= 90:   return 10
    if days <= 365:  return 5
    return 0


def _work_status(mtime: float) -> str:
    """수정일 기반 업무 진행 상태 추론"""
    days = (time.time() - mtime) / 86400
    if days <= CURRENT_WORK_DAYS:
        return "현재 진행 업무 (최근 30일)"
    if days <= RECENT_DONE_DAYS:
        return "최근 완료 업무 (최근 90일)"
    if days <= 180:
        return "최근 6개월 업무"
    if days <= 365:
        return "1년 이내 업무"
    return "1년 이상 지난 업무"


# ── 결과 데이터클래스 ──────────────────────────────────────────────────
@dataclass
class ScoreResult:
    final_score: int       # 0-100 최종 가중치 점수
    relevance_score: int   # 0-100 업무 관련성 점수 (날짜 제외)
    label: str             # 높음 / 중간 / 제외
    filename_score: int
    folder_score: int
    content_bonus: int
    modified_score: int    # -5 ~ 20
    created_score: int     # 0 ~ 10
    reason: str
    work_status: str       # 업무 진행 상태 추론
    is_current_work: bool  # 최근 30일 이내 수정 여부
    days_since_modified: float


class FileClassifier:
    """
    파일명·폴더명·내용·날짜를 결합한 0-100 업무 관련성 점수를 산출한다.

    최종 점수 = 업무관련성(70%) + 수정일(20%) + 생성일(10%)

    점수 기준:
      80-100 : 반드시 분석 (높음)
      50-79  : 분석 후보 (중간)
      0-49   : 제외
    """

    _BASE = 45

    def score(
        self,
        abs_path: str,
        display_name: str,
        extracted_text: str = "",
        mtime: float | None = None,
        ctime: float | None = None,
    ) -> ScoreResult:
        """
        업무 관련성 + 날짜 가중치를 합산한 최종 점수를 반환한다.
        mtime/ctime 미제공 시 파일 시스템에서 자동 읽기를 시도한다.
        """
        path = Path(abs_path)

        # ── 날짜 정보 수집 ──────────────────────────────────────
        try:
            stat = path.stat()
            mtime = mtime or stat.st_mtime
            ctime = ctime or stat.st_ctime
        except OSError:
            mtime = mtime or time.time()
            ctime = ctime or time.time()

        days_since_mod = (time.time() - mtime) / 86400

        # ── 파일명 점수 ──────────────────────────────────────────
        stem = path.stem
        full = f"{display_name} {stem}".replace("\\", "/")

        fn_score = self._BASE
        fn_reason = "기본"

        for pat in _FN_EXCLUDE:
            if pat.search(stem) or pat.search(display_name):
                fn_score = max(3, fn_score - 45)
                fn_reason = f"제외 파일명 ({pat.pattern})"
                break
        else:
            for pat in _FN_HIGH:
                if pat.search(full):
                    fn_score = min(90, fn_score + 45)
                    fn_reason = f"업무 핵심 파일명 ({pat.pattern})"
                    break
            else:
                for pat in _FN_MEDIUM:
                    if pat.search(full):
                        fn_score = min(65, fn_score + 15)
                        fn_reason = f"참고 자료 ({pat.pattern})"
                        break

        # ── 폴더명 점수 ──────────────────────────────────────────
        folder_parts = display_name.replace("\\", "/").split("/")[:-1]
        folder_str = "/".join(folder_parts)
        dir_score = 0
        dir_reason = ""

        for pat in _DIR_EXCLUDE:
            if pat.search(folder_str):
                dir_score = -15
                dir_reason = f"행정/개인 폴더 ({pat.pattern})"
                break
        else:
            for pat in _DIR_HIGH:
                if pat.search(folder_str):
                    dir_score = 25
                    dir_reason = f"업무 폴더 ({pat.pattern})"
                    break

        # ── 내용 키워드 보너스 ────────────────────────────────────
        content_bonus = 0
        if extracted_text:
            hits = sum(1 for kw in _CONTENT_KEYWORDS if kw in extracted_text)
            content_bonus = min(10, hits * 2)

        # ── 업무 관련성 점수 (0-100) ──────────────────────────────
        relevance = max(0, min(100, fn_score + dir_score + content_bonus))

        # ── 날짜 점수 ────────────────────────────────────────────
        mod_score = _modified_score(mtime)
        cre_score = _created_score(ctime)

        # ── 최종 가중치 점수 ──────────────────────────────────────
        # 최종 = 관련성(70%) + 수정일(20%) + 생성일(10%)
        final = max(0, min(100,
            int(relevance * 0.7) + mod_score + cre_score
        ))

        reason_parts = [fn_reason]
        if dir_reason:
            reason_parts.append(dir_reason)
        if content_bonus:
            reason_parts.append(f"내용 키워드 +{content_bonus}")
        reason_parts.append(
            f"수정일 {int(days_since_mod)}일전 →{'+' if mod_score >= 0 else ''}{mod_score}점"
        )

        return ScoreResult(
            final_score=final,
            relevance_score=relevance,
            label=score_label(final),
            filename_score=max(0, fn_score),
            folder_score=max(0, dir_score),
            content_bonus=content_bonus,
            modified_score=mod_score,
            created_score=cre_score,
            reason=" | ".join(reason_parts),
            work_status=_work_status(mtime),
            is_current_work=(days_since_mod <= CURRENT_WORK_DAYS),
            days_since_modified=days_since_mod,
        )
