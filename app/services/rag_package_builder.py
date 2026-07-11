import hashlib
import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
import json
import logging
import os
import re
import shutil
import tempfile
import time
import warnings
import zipfile
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Callable

from app.services.analysis_result import AnalysisResult, AnalyzedFile


EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_BATCH_SIZE = 200
EMBEDDING_BATCH_MAX_CHARS = 200_000
FILE_EXTRACTION_MAX_WORKERS = 8
EMBEDDING_MAX_WORKERS = 2


class RagPackageCancelled(Exception):
    pass


def _raise_if_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise RagPackageCancelled()
EMBEDDING_RETRY_ATTEMPTS = 5
EMBEDDING_REQUEST_DELAY_SECONDS = 0.2
PDF_EXTRACT_TIMEOUT_SECONDS = 12
RAG_TEXT_EXTRACTION_EXTENSIONS = {".docx", ".pptx", ".ppt", ".txt", ".md"}
SUPPORTED_EMAIL_ATTACHMENT_EXTENSIONS = RAG_TEXT_EXTRACTION_EXTENSIONS
SYSTEM_FILE_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
COMMON_REMOVAL_KEYWORDS = (
    "confidential",
    "all rights reserved",
    "copyright",
    "©",
    "무단전재",
    "저작권",
    "수신거부",
    "수신 거부",
    "unsubscribe",
    "발송되었습니다",
    "클릭하세요",
    "클릭해주세요",
    "if you don't want to receive",
)
EMAIL_SIGNATURE_KEYWORDS = (
    "회사",
    "직책",
    "부서",
    "전화",
    "연락처",
    "mobile",
    "tel",
    "fax",
    "email",
    "e-mail",
)
_EXTRACTED_TEXT_CACHE: dict[str, str] = {}
_ATTACHMENT_TEXT_CACHE: dict[str, str] = {}
_PDF_TEXT_TIMEOUT_PATHS: set[str] = set()


def extract_text_for_rag(file_path: str) -> str:
    extension = Path(file_path).suffix.lower()
    if extension in {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml"}:
        return _extract_plain_text_file(file_path)
    if extension == ".docx":
        return _extract_docx_text(file_path)
    if extension == ".pdf":
        return _extract_pdf_text(file_path)
    if extension == ".xlsx":
        return _extract_xlsx_text(file_path)
    if extension == ".pptx":
        return _extract_pptx_text(file_path)
    return ""


def _build_unsupported_placeholder_text(
    file_name: str,
    source_path: str,
    modified_at: str,
) -> str:
    return (
        f"[파일명: {file_name}] 이 파일은 자동으로 내용을 읽을 수 없는 형식"
        f"(예: hwp)입니다. 제목을 참고하시고, 세부 내용은 원본 파일을 직접 "
        f"확인하세요. 원본 경로: {source_path}, 수정일시: {modified_at}"
    )


def _build_unsupported_placeholder_chunk_record(
    source_file: str,
    file_name: str,
    source_path: str,
    modified_at: str,
) -> dict:
    return {
        "chunk_text": _build_unsupported_placeholder_text(
            file_name,
            source_path,
            modified_at,
        ),
        "source_file": source_file,
        "chunk_index": 0,
        "metadata": {
            "source_path": source_path,
            "file_name": file_name,
            "modified_at": modified_at,
            "extraction_status": "unsupported_format",
        },
    }


def _build_not_in_whitelist_placeholder_text(
    file_name: str,
    source_path: str,
    modified_at: str,
) -> str:
    return (
        f"[파일명: {file_name}] 이 파일은 지원 대상이 아니라 내용을 자동으로 "
        f"읽지 않았습니다. 제목을 참고하시고, 세부 내용은 원본 파일을 직접 "
        f"확인하세요. 원본 경로: {source_path}, 수정일시: {modified_at}"
    )


def _build_not_in_whitelist_placeholder_chunk_record(
    source_file: str,
    file_name: str,
    source_path: str,
    modified_at: str,
) -> dict:
    return {
        "chunk_text": _build_not_in_whitelist_placeholder_text(
            file_name,
            source_path,
            modified_at,
        ),
        "source_file": source_file,
        "chunk_index": 0,
        "metadata": {
            "source_path": source_path,
            "file_name": file_name,
            "modified_at": modified_at,
            "extraction_status": "not_in_whitelist",
        },
    }


def _extract_plain_text_file(file_path: str) -> str:
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return Path(file_path).read_text(encoding=encoding, errors="ignore")
        except OSError:
            return ""
        except UnicodeError:
            continue
    return ""


def _extract_docx_text(file_path: str) -> str:
    try:
        from docx import Document

        document = Document(file_path)
        parts = [paragraph.text.strip() for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(part for part in parts if part)
    except Exception:
        return ""


def _extract_pdf_text(file_path: str) -> str:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_extract_pdf_text_without_timeout, file_path)
    try:
        return future.result(timeout=PDF_EXTRACT_TIMEOUT_SECONDS)
    except TimeoutError:
        _PDF_TEXT_TIMEOUT_PATHS.add(str(Path(file_path)))
        future.cancel()
        return ""
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _extract_pdf_text_without_timeout(file_path: str) -> str:
    try:
        logging.getLogger("pdfminer").setLevel(logging.ERROR)
        logging.getLogger("pdfplumber").setLevel(logging.ERROR)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*FontBBox.*")
            import pdfplumber

            parts = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    if text.strip():
                        parts.append(text.strip())
            return "\n\n".join(parts)
    except Exception:
        return ""


def _extract_xlsx_text(file_path: str) -> str:
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(file_path, read_only=True, data_only=True)
        parts = []
        try:
            for worksheet in workbook.worksheets:
                parts.append(f"[시트: {worksheet.title}]")
                for row in worksheet.iter_rows(values_only=True):
                    values = [str(value).strip() for value in row if value not in (None, "")]
                    if values:
                        parts.append(" | ".join(values))
        finally:
            workbook.close()
        return "\n".join(parts)
    except Exception:
        return ""


def _extract_pptx_text(file_path: str) -> str:
    try:
        from pptx import Presentation

        presentation = Presentation(file_path)
        parts = []
        for slide_index, slide in enumerate(presentation.slides, start=1):
            slide_parts = []
            for shape in slide.shapes:
                text = getattr(shape, "text", "")
                if text.strip():
                    slide_parts.append(text.strip())
            if slide_parts:
                parts.append(f"[슬라이드 {slide_index}]\n" + "\n".join(slide_parts))
        return "\n\n".join(parts)
    except Exception:
        return ""


def preprocess_text_for_rag(text: str, source_type: str) -> str:
    if source_type == "kakao":
        return text
    if source_type == "email":
        if _looks_like_html(text):
            text = _strip_html_to_text(text)
        text = _remove_lines_with_keywords(
            text,
            (*COMMON_REMOVAL_KEYWORDS, *EMAIL_SIGNATURE_KEYWORDS),
        )
        return _remove_email_signature(_remove_email_reply_chain(text)).strip()
    if source_type == "document":
        return _remove_lines_with_keywords(text, COMMON_REMOVAL_KEYWORDS).strip()
    return text


def split_into_chunks(
    text: str,
    chunk_size: int = 700,
    overlap: int = 100,
) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []

    chunks: list[str] = []
    current = ""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", cleaned) if p.strip()]
    for paragraph in paragraphs:
        paragraph_parts = _split_long_text(paragraph, chunk_size, overlap)
        for part in paragraph_parts:
            if not current:
                current = part
                continue
            candidate = f"{current}\n\n{part}"
            if len(candidate) <= chunk_size:
                current = candidate
            else:
                chunks.append(current)
                prefix = _tail_overlap(current, overlap)
                current = f"{prefix}\n\n{part}" if prefix else part

    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


def embed_chunks(
    chunks: list[str],
    api_key: str,
    progress_callback: Callable[[str, int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict]:
    embedded, _failures = _embed_chunks_with_failures(
        chunks,
        api_key,
        progress_callback,
        cancel_check,
    )
    return embedded


def _embed_chunks_with_failures(
    chunks: list[str],
    api_key: str,
    progress_callback: Callable[[str, int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[list[dict], list[dict]]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    batches = _build_embedding_batches(chunks)
    total_batches = len(batches)
    if total_batches == 0:
        return [], []
    _raise_if_cancelled(cancel_check)

    def embed_batch(start: int, batch: list[str]) -> list[dict]:
        _raise_if_cancelled(cancel_check)
        if EMBEDDING_REQUEST_DELAY_SECONDS > 0:
            time.sleep(EMBEDDING_REQUEST_DELAY_SECONDS)
        _raise_if_cancelled(cancel_check)
        response = _create_embedding_with_retry(client, batch)
        _raise_if_cancelled(cancel_check)
        vectors = [item.embedding for item in response.data]
        batch_embedded: list[dict] = []
        for offset, (chunk_text, vector) in enumerate(zip(batch, vectors)):
            batch_embedded.append(
                {
                    "chunk_text": chunk_text,
                    "embedding_vector": vector,
                    "source_file": "",
                    "chunk_index": start + offset,
                }
            )
        return batch_embedded

    results: list[list[dict] | None] = [None] * total_batches
    failures: list[dict] = []
    completed_batches = 0
    max_workers = min(EMBEDDING_MAX_WORKERS, total_batches)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(embed_batch, start, batch): batch_index
            for batch_index, (start, batch) in enumerate(batches)
        }
        for future in as_completed(futures):
            if cancel_check is not None and cancel_check():
                for pending_future in futures:
                    pending_future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                raise RagPackageCancelled()
            batch_index = futures[future]
            start, batch = batches[batch_index]
            try:
                results[batch_index] = future.result()
            except Exception as exc:
                error_message = f"{type(exc).__name__}: {exc}"
                for offset, chunk_text in enumerate(batch):
                    failures.append(
                        {
                            "chunk_index": start + offset,
                            "batch_index": batch_index,
                            "chunk_text_preview": chunk_text[:500],
                            "error": error_message,
                        }
                    )
            completed_batches += 1
            if progress_callback is not None:
                progress_callback("embedding", completed_batches, total_batches)
            _raise_if_cancelled(cancel_check)

    embedded: list[dict] = []
    for batch_result in results:
        if batch_result:
            embedded.extend(batch_result)
    return embedded, failures


def _build_embedding_batches(chunks: list[str]) -> list[tuple[int, list[str]]]:
    batches: list[tuple[int, list[str]]] = []
    current_start = 0
    current_batch: list[str] = []
    current_chars = 0
    for index, chunk in enumerate(chunks):
        chunk_chars = len(chunk)
        should_flush = (
            current_batch
            and (
                len(current_batch) >= EMBEDDING_BATCH_SIZE
                or current_chars + chunk_chars > EMBEDDING_BATCH_MAX_CHARS
            )
        )
        if should_flush:
            batches.append((current_start, current_batch))
            current_start = index
            current_batch = []
            current_chars = 0
        current_batch.append(chunk)
        current_chars += chunk_chars
    if current_batch:
        batches.append((current_start, current_batch))
    return batches


def save_rag_package(
    chunks_with_embeddings: list[dict],
    analysis_result: AnalysisResult,
    output_path: str,
    api_key: str,
    source_map: dict[str, dict[str, Any]] | None = None,
    excluded_files: list[dict] | None = None,
    embedding_failures: list[dict] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    zip_path = _get_rag_package_zip_path(Path(output_path))
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    source_map = source_map or {}
    excluded_files = excluded_files or []
    embedding_failures = embedding_failures or []

    with tempfile.TemporaryDirectory(prefix="handover_rag_package_") as temp_dir:
        _raise_if_cancelled(cancel_check)
        package_path = Path(temp_dir) / zip_path.stem
        package_path.mkdir(parents=True, exist_ok=True)

        manifest = {
            "package_name": package_path.name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "analysis_id": _build_analysis_id(analysis_result),
            "document_count": len(
                {
                    item.get("source_file", "")
                    for item in chunks_with_embeddings
                    if item.get("source_file", "")
                }
            ),
            "chunk_count": len(chunks_with_embeddings),
            "embedding_failed_chunk_count": len(embedding_failures),
            "embedding_model": EMBEDDING_MODEL,
        }
        (package_path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with (package_path / "chunks.jsonl").open("w", encoding="utf-8") as file:
            for index, item in enumerate(chunks_with_embeddings):
                _raise_if_cancelled(cancel_check)
                chunk_id = item.get("chunk_id") or f"chunk-{index:06d}"
                file.write(
                    json.dumps(
                        {
                            "chunk_id": chunk_id,
                            "source_file": item.get("source_file", ""),
                            "chunk_text": item.get("chunk_text", ""),
                            "embedding": item.get("embedding_vector", []),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        (package_path / "source_map.json").write_text(
            json.dumps(source_map, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (package_path / "excluded_files.json").write_text(
            json.dumps(excluded_files, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (package_path / "embedding_failures.json").write_text(
            json.dumps(embedding_failures, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (package_path / "api_key.dat").write_bytes(base64.b64encode(api_key.encode("utf-8")))
        _raise_if_cancelled(cancel_check)
        _zip_package_folder(package_path, zip_path, cancel_check)
        _raise_if_cancelled(cancel_check)

    return str(zip_path)


def estimate_rag_package_cost(
    analysis_result: AnalysisResult,
    folder_paths: list[str] | None = None,
    parsed_emails: list[dict] | None = None,
    kakao_file_paths: list[str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, int | float]:
    _raise_if_cancelled(cancel_check)
    root_map = _build_root_map(folder_paths or _split_root_folder_paths(analysis_result))
    embeddable: list[tuple[AnalyzedFile, Path]] = []
    for file in analysis_result.all_files:
        _raise_if_cancelled(cancel_check)
        if _should_exclude_file(file):
            continue
        absolute_path = _resolve_absolute_path(file, root_map)
        if absolute_path is None or not absolute_path.is_file():
            continue
        embeddable.append((file, absolute_path))

    selected_files, _duplicate_exclusions = _select_latest_unique_files(embeddable)
    estimated_chars = 0
    for file, absolute_path in selected_files:
        _raise_if_cancelled(cancel_check)
        if not _is_rag_text_extraction_file(file.file_name):
            estimated_chars += len(
                _build_not_in_whitelist_placeholder_text(
                    file.file_name,
                    file.relative_path,
                    file.modified_at,
                )
            )
        else:
            estimated_chars += len(_get_preprocessed_text_for_file(file, absolute_path))
    for memo in analysis_result.memos:
        _raise_if_cancelled(cancel_check)
        estimated_chars += len(f"{memo.title}\n{memo.content}".strip())
    for record in _get_email_text_records(parsed_emails):
        _raise_if_cancelled(cancel_check)
        estimated_chars += len(preprocess_text_for_rag(record["text"], "email"))
    for record in _get_email_attachment_text_records(parsed_emails):
        _raise_if_cancelled(cancel_check)
        estimated_chars += len(preprocess_text_for_rag(record["text"], "document"))
    for path in _get_kakao_text_paths(kakao_file_paths):
        _raise_if_cancelled(cancel_check)
        estimated_chars += len(preprocess_text_for_rag(_get_cached_text_for_path(path), "kakao"))
    estimated_tokens = max(1, estimated_chars // 4) if estimated_chars else 0
    estimated_cost_krw = int((estimated_tokens / 1000) * 0.03)
    return {
        "file_count": len(selected_files),
        "estimated_tokens": estimated_tokens,
        "estimated_cost_krw": estimated_cost_krw,
    }


def _get_rag_package_zip_path(output_path: Path) -> Path:
    if output_path.suffix.casefold() == ".zip":
        return output_path
    return output_path.with_suffix(".zip")


def _zip_package_folder(
    package_path: Path,
    zip_path: Path,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(path for path in package_path.rglob("*") if path.is_file()):
            _raise_if_cancelled(cancel_check)
            archive.write(file_path, file_path.relative_to(package_path))


def build_and_save_rag_package(
    analysis_result: AnalysisResult,
    folder_paths: list[str],
    api_key: str,
    output_path: str,
    parsed_emails: list[dict] | None = None,
    kakao_file_paths: list[str] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, int | str]:
    _raise_if_cancelled(cancel_check)
    root_map = _build_root_map(folder_paths)
    excluded_files: list[dict] = []
    embeddable: list[tuple[AnalyzedFile, Path]] = []

    for analyzed_file in analysis_result.all_files:
        _raise_if_cancelled(cancel_check)
        absolute_path = _resolve_absolute_path(analyzed_file, root_map)
        if _should_exclude_file(analyzed_file):
            excluded_files.append(_build_excluded_file_record(analyzed_file, "excluded_extension_or_system"))
            continue
        if absolute_path is None or not absolute_path.is_file():
            excluded_files.append(_build_excluded_file_record(analyzed_file, "missing_file"))
            continue
        embeddable.append((analyzed_file, absolute_path))

    selected_files, duplicate_exclusions = _select_latest_unique_files(embeddable)
    excluded_files.extend(duplicate_exclusions)

    chunk_records: list[dict] = []
    source_map: dict[str, dict[str, Any]] = {}
    total_files = len(selected_files)
    file_results: list[tuple[list[dict], list[dict]] | None] = [None] * total_files
    completed_files = 0
    if total_files:
        max_workers = min(FILE_EXTRACTION_MAX_WORKERS, total_files)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_build_file_chunk_records, analyzed_file, absolute_path): index
                for index, (analyzed_file, absolute_path) in enumerate(selected_files)
            }
            for future in as_completed(futures):
                if cancel_check is not None and cancel_check():
                    for pending_future in futures:
                        pending_future.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise RagPackageCancelled()
                index = futures[future]
                file_results[index] = future.result()
                completed_files += 1
                if progress_callback is not None:
                    progress_callback("files", completed_files, total_files)
                _raise_if_cancelled(cancel_check)

    for result_records in file_results:
        _raise_if_cancelled(cancel_check)
        if result_records is None:
            continue
        records, exclusions = result_records
        chunk_records.extend(records)
        excluded_files.extend(exclusions)

    for memo_index, memo in enumerate(analysis_result.memos):
        _raise_if_cancelled(cancel_check)
        memo_text = f"{memo.title}\n{memo.content}".strip()
        for chunk_index, chunk in enumerate(split_into_chunks(memo_text)):
            source_file = f"memo:{memo_index}"
            chunk_records.append(
                {
                    "chunk_text": chunk,
                    "source_file": source_file,
                    "chunk_index": chunk_index,
                    "metadata": {
                        "source_path": source_file,
                        "file_name": f"메모: {memo.title}",
                        "modified_at": memo.updatedat,
                    },
                }
            )

    for record in _get_email_text_records(parsed_emails):
        _raise_if_cancelled(cancel_check)
        text = preprocess_text_for_rag(record["text"], "email")
        for chunk_index, chunk in enumerate(split_into_chunks(text)):
            source_file = record["source_file"]
            chunk_records.append(
                {
                    "chunk_text": chunk,
                    "source_file": source_file,
                    "chunk_index": chunk_index,
                    "metadata": {
                        "source_path": source_file,
                        "file_name": f"메일: {record['subject']}",
                        "modified_at": record["date"],
                    },
                }
            )

    for record in _get_email_attachment_records(parsed_emails):
        _raise_if_cancelled(cancel_check)
        source_file = f"{record['source_file']}::attachment::{record['filename']}"
        attachment_display_name = (
            f"{record['email_subject']}의 첨부파일: {record['filename']}"
        )
        if not _is_supported_email_attachment(record):
            chunk_records.append(
                _build_not_in_whitelist_placeholder_chunk_record(
                    source_file,
                    attachment_display_name,
                    source_file,
                    record["date"],
                )
            )
            continue

        text = preprocess_text_for_rag(
            _extract_attachment_text(record),
            "document",
        )
        if not text:
            excluded_files.append(
                {
                    "file_name": record["filename"],
                    "relative_path": (
                        f"출처: {record['email_subject']}의 첨부파일 "
                        f"{record['filename']}"
                    ),
                    "modified_at": record["date"],
                    "reason": "email_attachment_extract_failed",
                }
            )
            chunk_records.append(
                _build_unsupported_placeholder_chunk_record(
                    source_file,
                    attachment_display_name,
                    source_file,
                    record["date"],
                )
            )
            continue

        for chunk_index, chunk in enumerate(split_into_chunks(text)):
            chunk_records.append(
                {
                    "chunk_text": chunk,
                    "source_file": source_file,
                    "chunk_index": chunk_index,
                    "metadata": {
                        "source_path": source_file,
                        "file_name": (
                            f"{record['email_subject']} - 첨부파일: "
                            f"{record['filename']}"
                        ),
                        "modified_at": record["date"],
                    },
                }
            )

    for external_path in _get_kakao_text_paths(kakao_file_paths):
        _raise_if_cancelled(cancel_check)
        text = preprocess_text_for_rag(_get_cached_text_for_path(external_path), "kakao")
        for chunk_index, chunk in enumerate(split_into_chunks(text)):
            chunk_records.append(
                {
                    "chunk_text": chunk,
                    "source_file": external_path,
                    "chunk_index": chunk_index,
                    "metadata": {
                        "source_path": external_path,
                        "file_name": Path(external_path).name,
                        "modified_at": "",
                    },
                }
            )

    embedded, embedding_failures = _embed_chunk_records(
        chunk_records,
        api_key,
        progress_callback,
        cancel_check,
    )
    for index, item in enumerate(embedded):
        _raise_if_cancelled(cancel_check)
        chunk_id = f"chunk-{index:06d}"
        item["chunk_id"] = chunk_id
        source_map[chunk_id] = item.pop("metadata", {})

    saved_path = save_rag_package(
        embedded,
        analysis_result,
        output_path,
        api_key,
        source_map=source_map,
        excluded_files=excluded_files,
        embedding_failures=embedding_failures,
        cancel_check=cancel_check,
    )
    return {
        "embedding_failed_chunk_count": len(embedding_failures),
        "saved_path": saved_path,
    }


def _build_file_chunk_records(
    analyzed_file: AnalyzedFile,
    absolute_path: Path,
) -> tuple[list[dict], list[dict]]:
    exclusions: list[dict] = []
    records: list[dict] = []
    if not _is_rag_text_extraction_file(analyzed_file.file_name):
        records.append(
            _build_not_in_whitelist_placeholder_chunk_record(
                analyzed_file.relative_path,
                analyzed_file.file_name,
                analyzed_file.relative_path,
                analyzed_file.modified_at,
            )
        )
        return records, exclusions

    text = _get_preprocessed_text_for_file(analyzed_file, absolute_path)
    if _is_pdf_extract_timeout(absolute_path):
        exclusions.append(_build_excluded_file_record(analyzed_file, "pdf_extract_timeout"))
        return records, exclusions
    if not text:
        exclusions.append(_build_excluded_file_record(analyzed_file, "unsupported_format"))
        records.append(
            _build_unsupported_placeholder_chunk_record(
                analyzed_file.relative_path,
                analyzed_file.file_name,
                analyzed_file.relative_path,
                analyzed_file.modified_at,
            )
        )
        return records, exclusions

    for chunk_index, chunk in enumerate(split_into_chunks(text)):
        records.append(
            {
                "chunk_text": chunk,
                "source_file": analyzed_file.relative_path,
                "chunk_index": chunk_index,
                "metadata": {
                    "source_path": analyzed_file.relative_path,
                    "file_name": analyzed_file.file_name,
                    "modified_at": analyzed_file.modified_at,
                },
            }
        )
    return records, exclusions


def _remove_email_reply_chain(text: str) -> str:
    patterns = [
        r"(?im)^-----Original Message-----.*\Z",
        r"(?im)^>.*(?:\n>.*)*",
        r"(?im)^On .+ wrote:\s*.*\Z",
    ]
    result = text
    for pattern in patterns:
        result = re.sub(pattern, "", result, flags=re.DOTALL)
    return result


def _remove_lines_with_keywords(text: str, keywords: tuple[str, ...]) -> str:
    return "\n".join(
        line
        for line in text.splitlines()
        if not any(keyword in line.casefold() for keyword in keywords)
    )


def _looks_like_html(text: str) -> bool:
    lowered = text[:2000].casefold()
    return (
        "<html" in lowered
        or "<!doctype html" in lowered
        or bool(re.search(r"<(?:body|head|div|span|p|table|br)\b", lowered))
    )


def _strip_html_to_text(text: str) -> str:
    without_scripts = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1>",
        " ",
        text,
    )
    with_line_breaks = re.sub(
        r"(?i)<\s*(br|/p|/div|/tr|/li|/h[1-6])\s*/?\s*>",
        "\n",
        without_scripts,
    )
    without_tags = re.sub(r"(?s)<[^>]+>", " ", with_line_breaks)
    decoded = unescape(without_tags)
    lines = [
        re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        for line in decoded.splitlines()
    ]
    compact_lines = [line for line in lines if line]
    return "\n".join(compact_lines)


def _remove_email_signature(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.fullmatch(r"[-_=]{2,}", stripped):
            signature = "\n".join(lines[index + 1 : index + 8]).casefold()
            hits = sum(1 for keyword in EMAIL_SIGNATURE_KEYWORDS if keyword in signature)
            if hits >= 2:
                return "\n".join(lines[:index]).strip()
    return text


def _split_long_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    parts = []
    start = 0
    step = max(chunk_size - overlap, 1)
    while start < len(text):
        parts.append(text[start : start + chunk_size].strip())
        start += step
    return [part for part in parts if part]


def _tail_overlap(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return text if len(text) <= overlap else ""
    return text[-overlap:].strip()


def _create_embedding_with_retry(client: Any, batch: list[str]) -> Any:
    last_exception: Exception | None = None
    for attempt in range(EMBEDDING_RETRY_ATTEMPTS):
        try:
            return client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        except Exception as exc:
            last_exception = exc
            if attempt == EMBEDDING_RETRY_ATTEMPTS - 1:
                break
            time.sleep(2 ** (attempt + 1))
    detail = (
        f"{type(last_exception).__name__}: {last_exception}"
        if last_exception is not None
        else "unknown error"
    )
    raise RuntimeError(f"임베딩 API 호출에 실패했습니다: {detail}") from last_exception


def _embed_chunk_records(
    chunk_records: list[dict],
    api_key: str,
    progress_callback: Callable[[str, int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[list[dict], list[dict]]:
    texts = [record["chunk_text"] for record in chunk_records]
    embedded, failures = _embed_chunks_with_failures(
        texts,
        api_key,
        progress_callback,
        cancel_check,
    )
    for item in embedded:
        original_index = int(item["chunk_index"])
        record = chunk_records[original_index]
        item["source_file"] = record["source_file"]
        item["chunk_index"] = record["chunk_index"]
        item["metadata"] = record["metadata"]

    failure_records: list[dict] = []
    for failure in failures:
        original_index = int(failure["chunk_index"])
        record = chunk_records[original_index]
        failure_records.append(
            {
                "source_file": record["source_file"],
                "chunk_index": record["chunk_index"],
                "metadata": record["metadata"],
                "chunk_text_preview": failure["chunk_text_preview"],
                "batch_index": failure["batch_index"],
                "error": failure["error"],
            }
        )
    return embedded, failure_records


def _get_preprocessed_text_for_file(file: AnalyzedFile, absolute_path: Path) -> str:
    text = _get_cached_text_for_path(str(absolute_path))
    return preprocess_text_for_rag(text, _infer_source_type(file))


def _get_cached_text_for_path(path: str) -> str:
    cache_key = str(Path(path))
    if cache_key not in _EXTRACTED_TEXT_CACHE:
        _EXTRACTED_TEXT_CACHE[cache_key] = extract_text_for_rag(cache_key)
    return _EXTRACTED_TEXT_CACHE[cache_key]


def _is_rag_text_extraction_file(file_name: str) -> bool:
    return Path(file_name).suffix.lower() in RAG_TEXT_EXTRACTION_EXTENSIONS


def _is_pdf_extract_timeout(path: Path) -> bool:
    return str(path) in _PDF_TEXT_TIMEOUT_PATHS


def _get_email_text_records(
    parsed_emails: list[dict] | None,
) -> list[dict]:
    if not parsed_emails:
        return []

    records: list[dict] = []
    seen: set[str] = set()
    for email in parsed_emails:
        try:
            source_file = str(email.get("source_file", ""))
            if source_file in seen:
                continue
            seen.add(source_file)
            subject = str(email.get("subject") or "(제목 없음)")
            sender = str(email.get("sender") or "")
            date = str(email.get("date") or "")
            body = str(email.get("body") or "")
            records.append(
                {
                    "source_file": source_file,
                    "subject": subject,
                    "date": date,
                    "text": f"발신자: {sender}\n발송일시: {date}\n제목: {subject}\n\n{body}".strip(),
                }
            )
        except Exception:
            continue
    return records


def _get_email_attachment_text_records(
    parsed_emails: list[dict] | None,
) -> list[dict]:
    records: list[dict] = []
    for record in _get_email_attachment_records(parsed_emails):
        if not _is_supported_email_attachment(record):
            attachment_display_name = (
                f"{record['email_subject']}의 첨부파일: {record['filename']}"
            )
            source_file = f"{record['source_file']}::attachment::{record['filename']}"
            records.append(
                {
                    **record,
                    "text": _build_not_in_whitelist_placeholder_text(
                        attachment_display_name,
                        source_file,
                        record["date"],
                    ),
                }
            )
            continue
        text = _extract_attachment_text(record)
        if not text:
            continue
        records.append({**record, "text": text})
    return records


def _get_email_attachment_records(
    parsed_emails: list[dict] | None,
) -> list[dict]:
    if not parsed_emails:
        return []

    records: list[dict] = []
    seen: set[str] = set()
    for email in parsed_emails:
        try:
            source_file = str(email.get("source_file", ""))
            email_subject = str(email.get("subject") or "(제목 없음)")
            date = str(email.get("date") or "")
            for attachment in email.get("attachments", []) or []:
                filename = str(attachment.get("filename") or "attachment")
                content_bytes = attachment.get("content_bytes") or b""
                if not isinstance(content_bytes, bytes):
                    continue
                key = f"{source_file}\0{filename}\0{hashlib.sha256(content_bytes).hexdigest()}"
                if key in seen:
                    continue
                seen.add(key)
                records.append(
                    {
                        "source_file": source_file,
                        "email_subject": email_subject,
                        "date": date,
                        "filename": filename,
                        "content_bytes": content_bytes,
                        "size": int(attachment.get("size") or len(content_bytes)),
                    }
                )
        except Exception:
            continue
    return records


def _is_supported_email_attachment(record: dict) -> bool:
    return Path(record.get("filename", "")).suffix.lower() in SUPPORTED_EMAIL_ATTACHMENT_EXTENSIONS


def _extract_attachment_text(record: dict) -> str:
    content_bytes = record.get("content_bytes") or b""
    if not isinstance(content_bytes, bytes) or not content_bytes:
        return ""

    extension = Path(record.get("filename", "")).suffix.lower()
    cache_key = hashlib.sha256(
        extension.encode("utf-8") + b"\0" + content_bytes
    ).hexdigest()
    if cache_key in _ATTACHMENT_TEXT_CACHE:
        return _ATTACHMENT_TEXT_CACHE[cache_key]

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
            temp_file.write(content_bytes)
            temp_path = temp_file.name
        text = extract_text_for_rag(temp_path)
    except Exception:
        text = ""
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    _ATTACHMENT_TEXT_CACHE[cache_key] = text
    return text


def _get_kakao_text_paths(kakao_file_paths: list[str] | None) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for path in kakao_file_paths or []:
        if path in seen or not Path(path).is_file():
            continue
        seen.add(path)
        paths.append(path)
    return paths


def _should_exclude_file(file: AnalyzedFile) -> bool:
    name = file.file_name.casefold()
    return name in SYSTEM_FILE_NAMES or file.is_hidden_or_system


def _select_latest_unique_files(
    files: list[tuple[AnalyzedFile, Path]],
) -> tuple[list[tuple[AnalyzedFile, Path]], list[dict]]:
    by_hash: dict[str, list[tuple[AnalyzedFile, Path]]] = {}
    excluded: list[dict] = []
    for item in files:
        file_hash = _hash_file(item[1])
        if file_hash is None:
            excluded.append(_build_excluded_file_record(item[0], "read_error"))
            continue
        by_hash.setdefault(file_hash, []).append(item)

    selected: list[tuple[AnalyzedFile, Path]] = []
    for duplicates in by_hash.values():
        latest = max(duplicates, key=lambda item: item[0].modified_timestamp)
        selected.append(latest)
        for duplicate in duplicates:
            if duplicate is latest:
                continue
            excluded.append(_build_excluded_file_record(duplicate[0], "duplicate_content"))
    return selected, excluded


def _hash_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _build_root_map(folder_paths: list[str]) -> dict[str, Path]:
    roots = [Path(path) for path in folder_paths]
    if len(roots) == 1:
        root = roots[0]
        return {root.name or str(root): root}

    root_map: dict[str, Path] = {}
    used: set[str] = set()
    for root in roots:
        base = root.name or str(root)
        namespace = base
        suffix = 2
        while namespace in used:
            namespace = f"{base} ({suffix})"
            suffix += 1
        used.add(namespace)
        root_map[namespace] = root
    return root_map


def _split_root_folder_paths(analysis_result: AnalysisResult) -> list[str]:
    return [
        path.strip()
        for path in analysis_result.root_folder_path.split("; ")
        if path.strip()
    ]


def _resolve_absolute_path(
    file: AnalyzedFile,
    root_map: dict[str, Path],
) -> Path | None:
    namespace, _, remainder = file.relative_path.partition("/")
    root = root_map.get(namespace)
    if root is None:
        return None
    return root / remainder if remainder else root / file.file_name


def _infer_source_type(file: AnalyzedFile) -> str:
    extension = Path(file.file_name).suffix.lower()
    if extension in {".eml", ".msg"}:
        return "email"
    if extension == ".txt" and "kakao" in file.file_name.casefold():
        return "kakao"
    return "document"


def _build_excluded_file_record(file: AnalyzedFile, reason: str) -> dict:
    return {
        "file_name": file.file_name,
        "relative_path": file.relative_path,
        "modified_at": file.modified_at,
        "reason": reason,
    }


def _build_analysis_id(analysis_result: AnalysisResult) -> str:
    raw = "|".join(
        [
            analysis_result.root_folder_path,
            str(analysis_result.total_file_count),
            str(analysis_result.total_size_bytes),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
