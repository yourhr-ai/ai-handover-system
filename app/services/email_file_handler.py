import email
import os
import shutil
import tempfile
import zipfile
from email.header import decode_header
from email.message import Message
from pathlib import Path

from app.services.parallel_file_runner import run_process_items_with_timeout

_EMAIL_EXTENSIONS = {".eml", ".msg"}
EMAIL_FILE_TIMEOUT_SECONDS = 15


def process_email_files(
    file_paths: list[str],
    *,
    timeout_seconds: float = EMAIL_FILE_TIMEOUT_SECONDS,
    max_workers: int | None = None,
) -> tuple[list[dict], int]:
    parsed_emails: list[dict] = []
    failed_count = 0

    outcomes = run_process_items_with_timeout(
        file_paths,
        _process_email_file,
        timeout_seconds=timeout_seconds,
        max_workers=max_workers,
    )
    for status, result in outcomes:
        if status != "ok" or result is None:
            failed_count += 1
            continue
        emails, item_failed_count = result
        parsed_emails.extend(emails)
        failed_count += item_failed_count

    return parsed_emails, failed_count


def _process_email_file(file_path: str) -> tuple[list[dict], int]:
    extension = Path(file_path).suffix.lower()
    if extension == ".eml":
        parsed = _parse_eml_file(file_path)
        return ([parsed], 0) if parsed is not None else ([], 1)
    if extension == ".msg":
        parsed = _parse_msg_file(file_path)
        return ([parsed], 0) if parsed is not None else ([], 1)
    if extension == ".zip":
        return _process_zip_file(file_path)
    return [], 1


def _parse_eml_file(file_path: str) -> dict | None:
    try:
        with open(file_path, "rb") as eml_file:
            message = email.message_from_binary_file(eml_file)
        return _build_email_dict_from_message(message, source_file=file_path)
    except Exception:
        return None


def _parse_msg_file(file_path: str) -> dict | None:
    try:
        import extract_msg
    except ImportError:
        return None

    try:
        msg = extract_msg.Message(file_path)
        try:
            attachments = _extract_msg_attachments(msg)
            return {
                "sender": msg.sender or "",
                "recipient": msg.to or "",
                "subject": msg.subject or "",
                "date": str(msg.date) if msg.date else "",
                "body": msg.body or "",
                "source_file": file_path,
                "attachments": attachments,
            }
        finally:
            msg.close()
    except Exception:
        return None


def _process_zip_file(file_path: str) -> tuple[list[dict], int]:
    parsed_emails: list[dict] = []
    failed_count = 0

    temp_dir = tempfile.mkdtemp(prefix="email_zip_")
    try:
        try:
            with zipfile.ZipFile(file_path) as zip_file:
                zip_file.extractall(temp_dir)
        except Exception:
            return [], 1

        for root, _dirs, names in os.walk(temp_dir):
            for name in names:
                extension = Path(name).suffix.lower()
                if extension not in _EMAIL_EXTENSIONS:
                    continue

                extracted_path = os.path.join(root, name)
                parsed = (
                    _parse_eml_file(extracted_path)
                    if extension == ".eml"
                    else _parse_msg_file(extracted_path)
                )
                if parsed is None:
                    failed_count += 1
                    continue

                relative_path = os.path.relpath(extracted_path, temp_dir).replace(
                    os.sep, "/"
                )
                parsed["source_file"] = f"{file_path}::{relative_path}"
                parsed_emails.append(parsed)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return parsed_emails, failed_count


def _build_email_dict_from_message(message: Message, source_file: str) -> dict:
    return {
        "sender": _decode_header_value(message.get("From", "")),
        "recipient": _decode_header_value(message.get("To", "")),
        "subject": _decode_header_value(message.get("Subject", "")),
        "date": message.get("Date", ""),
        "body": _extract_body(message),
        "source_file": source_file,
        "attachments": _extract_eml_attachments(message),
    }


def _decode_header_value(value: str) -> str:
    if not value:
        return ""

    decoded_parts = decode_header(value)
    return "".join(
        part.decode(encoding or "utf-8", errors="ignore")
        if isinstance(part, bytes)
        else part
        for part, encoding in decoded_parts
    )


def _extract_body(message: Message) -> str:
    if not message.is_multipart():
        return _decode_payload(message)

    plain_text_part = _find_body_part(message, "text/plain")
    if plain_text_part is not None:
        return _decode_payload(plain_text_part)

    html_part = _find_body_part(message, "text/html")
    if html_part is not None:
        return _decode_payload(html_part)

    return ""


def _find_body_part(message: Message, content_type: str) -> Message | None:
    for part in message.walk():
        if part.get_content_type() != content_type:
            continue
        if "attachment" in str(part.get("Content-Disposition", "")):
            continue
        return part

    return None


def _decode_payload(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="ignore")
    except Exception:
        return ""


def _extract_eml_attachments(message: Message) -> list[dict]:
    attachments: list[dict] = []
    if not message.is_multipart():
        return attachments

    for part in message.walk():
        filename = part.get_filename()
        disposition = str(part.get("Content-Disposition", "")).lower()
        if not filename and "attachment" not in disposition:
            continue
        if not filename:
            continue

        try:
            content_bytes = part.get_payload(decode=True) or b""
        except Exception:
            continue

        decoded_filename = _decode_header_value(filename)
        attachments.append(
            {
                "filename": decoded_filename,
                "content_bytes": content_bytes,
                "size": len(content_bytes),
            }
        )

    return attachments


def _extract_msg_attachments(msg: object) -> list[dict]:
    attachments: list[dict] = []
    for attachment in getattr(msg, "attachments", []) or []:
        try:
            filename = (
                getattr(attachment, "longFilename", None)
                or getattr(attachment, "shortFilename", None)
                or getattr(attachment, "filename", None)
                or "attachment"
            )
            content = getattr(attachment, "data", b"")
            if callable(content):
                content = content()
            if isinstance(content, str):
                content = content.encode("utf-8", errors="ignore")
            if not isinstance(content, bytes):
                continue
            attachments.append(
                {
                    "filename": str(filename),
                    "content_bytes": content,
                    "size": len(content),
                }
            )
        except Exception:
            continue

    return attachments
