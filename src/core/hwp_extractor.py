from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

# HWP 5.x 레코드 태그 — 텍스트 단락
_HWPTAG_PARA_TEXT = 67


def _parse_hwp5_body(data: bytes) -> str:
    """HWP 5.x BodyText 스트림에서 텍스트 레코드를 파싱한다."""
    texts: list[str] = []
    i = 0
    while i + 4 <= len(data):
        header = struct.unpack_from("<I", data, i)[0]
        tag_id = header & 0x3FF
        size = (header >> 20) & 0xFFF

        if size == 0xFFF:
            # 확장 크기 필드
            if i + 8 > len(data):
                break
            size = struct.unpack_from("<I", data, i + 4)[0]
            i += 8
        else:
            i += 4

        if i + size > len(data):
            break

        if tag_id == _HWPTAG_PARA_TEXT:
            try:
                text = data[i : i + size].decode("utf-16-le", errors="ignore")
                clean = text.replace("\x00", "").strip()
                if clean:
                    texts.append(clean)
            except Exception:
                pass

        i += size

    return "\n".join(texts)


class HwpExtractor:
    """
    HWP 5.x (.hwp) 및 HWPX (.hwpx) 텍스트 추출기.

    .hwp  → OLE 컨테이너 파싱 (olefile 필요)
    .hwpx → ZIP + XML 파싱 (표준 라이브러리로 처리)
    """

    def extract(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".hwp":
            return self._extract_hwp(path)
        if suffix == ".hwpx":
            return self._extract_hwpx(path)
        raise ValueError(f"지원하지 않는 HWP 형식: {suffix}")

    # ------------------------------------------------------------------
    def _extract_hwp(self, path: Path) -> str:
        try:
            import olefile
        except ImportError:
            raise RuntimeError("olefile 패키지가 필요합니다: pip install olefile")

        if not olefile.isOleFile(str(path)):
            raise ValueError("유효한 HWP OLE 파일이 아닙니다.")

        ole = olefile.OleFileIO(str(path))
        texts: list[str] = []

        section_idx = 0
        while True:
            stream_name = f"BodyText/Section{section_idx}"
            if not ole.exists(stream_name):
                break
            raw = ole.openstream(stream_name).read()

            # HWP는 raw deflate(wbits=-15) 또는 비압축 저장
            try:
                decompressed = zlib.decompress(raw, -15)
            except zlib.error:
                decompressed = raw

            text = _parse_hwp5_body(decompressed)
            if text:
                texts.append(text)
            section_idx += 1

        ole.close()

        if not texts:
            raise ValueError("HWP에서 텍스트를 추출하지 못했습니다 (스캔 이미지이거나 손상된 파일).")

        return "\n\n".join(texts)

    def _extract_hwpx(self, path: Path) -> str:
        """
        HWPX는 ZIP 컨테이너 내부에 XML로 저장된다.
        Contents/section*.xml 파일에서 텍스트 노드를 추출한다.
        """
        if not zipfile.is_zipfile(str(path)):
            raise ValueError("유효한 HWPX ZIP 파일이 아닙니다.")

        texts: list[str] = []
        ns_strip = lambda tag: tag.split("}")[-1] if "}" in tag else tag

        with zipfile.ZipFile(str(path), "r") as zf:
            section_files = sorted(
                name for name in zf.namelist()
                if "section" in name.lower() and name.endswith(".xml")
            )
            for name in section_files:
                with zf.open(name) as f:
                    try:
                        root = ET.parse(f).getroot()
                        for elem in root.iter():
                            local = ns_strip(elem.tag)
                            if local in ("t", "run", "para", "text"):
                                if elem.text and elem.text.strip():
                                    texts.append(elem.text.strip())
                    except ET.ParseError:
                        pass

        if not texts:
            raise ValueError("HWPX에서 텍스트를 추출하지 못했습니다.")

        return "\n".join(texts)
