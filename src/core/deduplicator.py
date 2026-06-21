from __future__ import annotations

"""
중복 문서 감지 및 제거.

같은 폴더 내에서 이름이 유사한 파일들을 그룹화하고
수정일이 가장 최근인 파일만 남긴다.

중복 판정 기준:
  - 같은 상위 폴더
  - 버전/날짜 접미사를 제거한 정규화 파일명이 동일
"""

import re
from dataclasses import dataclass
from pathlib import Path

# ── 버전/날짜 제거 패턴 ──────────────────────────────────────────────
# 파일명 끝 또는 확장자 직전에 붙는 버전 표시를 제거
_VER = re.compile(
    r'(_v\d+)'             # _v1, _v2, _v10
    r'|(_ver\.?\d+)'       # _ver1, _ver.2
    r'|(_최종\d*)'         # _최종, _최종2
    r'|(_final\d*)'        # _final, _final2
    r'|(_수정\d*)'         # _수정, _수정2
    r'|(_revised\d*)'      # _revised
    r'|(_draft\d*)'        # _draft
    r'|(\(\d+\))'          # (1), (2) — 복사본
    r'|(_copy\d*)'         # _copy, _copy2
    r'|(_\d{8})'           # _20260101 — 날짜
    r'|(_\d{6})',          # _202601   — 연월
    re.IGNORECASE,
)


@dataclass
class DedupResult:
    """중복 감지 결과"""
    keep: list[str]                   # 유지할 abs_path 목록
    duplicates: list[tuple[str, str]] # (중복 abs_path, 유지되는 abs_path)


def deduplicate(
    items: list[tuple[str, str, float]],  # (abs_path, display_name, mtime)
) -> DedupResult:
    """
    파일 목록에서 중복 문서를 감지하고 최신본만 남긴다.

    같은 상위 폴더 내에서 정규화된 줄기 이름이 같은 파일들을 그룹화한다.
    그룹 내에서 mtime이 가장 최근인 파일을 유지하고 나머지는 구버전으로 표시한다.
    """
    # (parent_dir, normalized_stem) → [(abs_path, display_name, mtime)]
    groups: dict[tuple[str, str], list[tuple[str, str, float]]] = {}

    for abs_path, display_name, mtime in items:
        path = Path(abs_path)
        parent = str(path.parent)
        normalized = _normalize(path.stem).lower()
        key = (parent, normalized)
        groups.setdefault(key, []).append((abs_path, display_name, mtime))

    keep: list[str] = []
    duplicates: list[tuple[str, str]] = []

    for group in groups.values():
        if len(group) == 1:
            keep.append(group[0][0])
        else:
            # 수정일 기준 최신본 선택
            sorted_group = sorted(group, key=lambda x: x[2], reverse=True)
            winner_path = sorted_group[0][0]
            keep.append(winner_path)
            for abs_path, _dn, _mt in sorted_group[1:]:
                duplicates.append((abs_path, winner_path))
            print(
                f"  [중복 감지] 그룹 {len(sorted_group)}개 → "
                f"유지: {Path(winner_path).name}  "
                f"제외: {[Path(x[0]).name for x in sorted_group[1:]]}"
            )

    return DedupResult(keep=keep, duplicates=duplicates)


def get_winner_name(winner_path: str, display_map: dict[str, str]) -> str:
    return display_map.get(winner_path, Path(winner_path).name)


def _normalize(stem: str) -> str:
    """버전/날짜 접미사를 제거하고 정규화된 줄기를 반환한다."""
    result = _VER.sub("", stem)
    # 끝에 남은 구분자(공백, _, -) 정리
    result = re.sub(r"[\s_\-]+$", "", result).strip()
    return result if result else stem
