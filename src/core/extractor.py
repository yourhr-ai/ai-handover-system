from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

class FileExtractor:
    """
    pdf, docx, xlsx, txt, zip, hwp, hwpx 파일에서 텍스트를 추출한다.

    PDF: 일반 텍스트 추출 → 텍스트 없으면 OCR 자동 수행 (Tesseract+Poppler 설치 시)
    HWP/HWPX: olefile / ZIP+XML 기반 추출
    ZIP: 내부 지원 파일 재귀 추출
    """

    SUPPORTED = {".pdf", ".docx", ".xlsx", ".txt", ".hwp", ".hwpx"}

    def extract(self, file_path: str) -> str:
        path = Path(file_path)
        suffix = path.suffix.lower()

        if suffix == ".zip":
            return self._extract_zip(path)
        if suffix not in self.SUPPORTED:
            raise ValueError(f"지원하지 않는 파일 형식입니다: {suffix}")

        dispatch = {
            ".pdf":  self._extract_pdf,
            ".docx": self._extract_docx,
            ".xlsx": self._extract_xlsx,
            ".txt":  self._extract_txt,
            ".hwp":  self._extract_hwp,
            ".hwpx": self._extract_hwp,
        }
        return dispatch[suffix](path)

    # ── PDF ───────────────────────────────────────────────────────
    def _extract_pdf(self, path: Path) -> str:
        """PDF에서 일반 텍스트만 추출한다. OCR은 워커가 별도로 제어한다."""
        import pdfplumber

        texts: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    texts.append(text)

        return "\n".join(texts)

    # ── DOCX ──────────────────────────────────────────────────────
    def _extract_docx(self, path: Path) -> str:
        from docx import Document

        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    # ── XLSX ──────────────────────────────────────────────────────
    def _extract_xlsx(self, path: Path) -> str:
        import openpyxl

        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        lines: list[str] = []
        for sheet in wb.worksheets:
            lines.append(f"[시트: {sheet.title}]")
            for row in sheet.iter_rows(values_only=True):
                row_text = "\t".join(str(c) for c in row if c is not None)
                if row_text.strip():
                    lines.append(row_text)
        wb.close()
        return "\n".join(lines)

    # ── TXT ───────────────────────────────────────────────────────
    def _extract_txt(self, path: Path) -> str:
        for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
            try:
                return path.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return path.read_text(encoding="utf-8", errors="replace")

    # ── HWP / HWPX ────────────────────────────────────────────────
    def _extract_hwp(self, path: Path) -> str:
        from src.core.hwp_extractor import HwpExtractor

        return HwpExtractor().extract(path)

    # ── ZIP ───────────────────────────────────────────────────────
    def _extract_zip(self, path: Path) -> str:
        tmp_dir = Path(tempfile.mkdtemp(prefix="handover_zip_"))
        try:
            with zipfile.ZipFile(str(path), "r") as zf:
                zf.extractall(tmp_dir)

            parts: list[str] = []
            for inner in sorted(tmp_dir.rglob("*")):
                if inner.is_file() and inner.suffix.lower() in self.SUPPORTED:
                    try:
                        text = self.extract(str(inner))
                        if text.strip():
                            parts.append(
                                f"[파일: {inner.relative_to(tmp_dir)}]\n{text}"
                            )
                    except Exception:
                        pass

            if not parts:
                raise ValueError(
                    "ZIP 파일 내에 지원하는 파일(pdf/docx/xlsx/txt/hwp/hwpx)이 없습니다."
                )
            return "\n\n".join(parts)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
