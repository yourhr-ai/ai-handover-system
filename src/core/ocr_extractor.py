from __future__ import annotations

from pathlib import Path


def _tesseract_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _poppler_available() -> bool:
    try:
        from pdf2image import convert_from_path
        from pdf2image.exceptions import PDFInfoNotInstalledError
        return True
    except Exception:
        return False


class OcrExtractor:
    """
    OCR 폴백 처리기.
    Tesseract OCR + pdf2image(Poppler) 설치 시에만 동작한다.

    시스템 의존성:
      - Tesseract OCR: https://github.com/tesseract-ocr/tesseract
        Windows: winget install UB-Mannheim.TesseractOCR
        kor.traineddata 설치 필요 (한국어 OCR)
      - Poppler: https://github.com/oschwartz10612/poppler-windows
        PATH에 poppler/bin 추가 필요
    """

    def __init__(self) -> None:
        self._tesseract_ok = _tesseract_available()
        self._poppler_ok = _poppler_available()

    def is_available(self) -> bool:
        return self._tesseract_ok and self._poppler_ok

    def unavailable_reason(self) -> str:
        reasons = []
        if not self._tesseract_ok:
            reasons.append("Tesseract OCR 미설치 (winget install UB-Mannheim.TesseractOCR)")
        if not self._poppler_ok:
            reasons.append("Poppler 미설치 (PATH에 poppler/bin 추가 필요)")
        return " / ".join(reasons)

    def extract(self, path: Path, lang: str = "kor+eng") -> tuple[str, bool]:
        """
        PDF에서 OCR로 텍스트를 추출한다.
        Returns (text, ocr_used).
        사용 불가 시 RuntimeError를 발생시킨다.
        """
        if not self.is_available():
            raise RuntimeError(f"OCR 불가: {self.unavailable_reason()}")

        import pytesseract
        from pdf2image import convert_from_path
        from pdf2image.exceptions import PDFInfoNotInstalledError

        try:
            images = convert_from_path(str(path), dpi=200)
        except PDFInfoNotInstalledError:
            raise RuntimeError("Poppler 미설치 — PATH에 poppler/bin 추가 필요")
        except Exception as exc:
            raise RuntimeError(f"PDF 이미지 변환 실패: {exc}")

        texts: list[str] = []
        for img in images:
            try:
                text = pytesseract.image_to_string(img, lang=lang)
                if text.strip():
                    texts.append(text.strip())
            except pytesseract.pytesseract.TesseractError as exc:
                # 한국어 언어 데이터 없는 경우 영어로 재시도
                if "kor" in lang:
                    try:
                        text = pytesseract.image_to_string(img, lang="eng")
                        if text.strip():
                            texts.append(text.strip())
                    except Exception:
                        pass
                else:
                    raise RuntimeError(f"Tesseract 오류: {exc}")

        return "\n\n".join(texts), True
