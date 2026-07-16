import re

from app.services.parallel_file_runner import run_process_items_with_timeout

KAKAO_FILE_TIMEOUT_SECONDS = 10

_DATE_PATTERN = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일")
_MESSAGE_PATTERN = re.compile(
    r"^\[(?P<sender>.+?)\]\s*\[(?P<ampm>오전|오후)\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})\]"
    r"\s*(?P<message>.*)$"
)


def process_kakao_files(
    file_paths: list[str],
    *,
    timeout_seconds: float = KAKAO_FILE_TIMEOUT_SECONDS,
    max_workers: int | None = None,
) -> tuple[list[dict], int]:
    parsed_messages: list[dict] = []
    failed_count = 0

    outcomes = run_process_items_with_timeout(
        file_paths,
        _parse_kakao_file,
        timeout_seconds=timeout_seconds,
        max_workers=max_workers,
    )
    for status, messages in outcomes:
        if status != "ok" or messages is None:
            failed_count += 1
        else:
            parsed_messages.extend(messages)

    return parsed_messages, failed_count


def _parse_kakao_file(file_path: str) -> list[dict] | None:
    content = _read_text_file(file_path)
    if content is None:
        return None

    messages: list[dict] = []
    current_date = ""

    for line in content.splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            continue

        if "-" in stripped_line:
            date_match = _DATE_PATTERN.search(stripped_line)
            if date_match and "---" in stripped_line:
                year, month, day = date_match.groups()
                current_date = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
                continue

        message_match = _MESSAGE_PATTERN.match(stripped_line)
        if message_match is None:
            continue

        messages.append(
            {
                "date": current_date,
                "time": (
                    f"{message_match.group('ampm')} "
                    f"{message_match.group('hour')}:{message_match.group('minute')}"
                ),
                "sender": message_match.group("sender"),
                "message": message_match.group("message"),
                "source_file": file_path,
            }
        )

    if not messages:
        return None

    return messages


def _read_text_file(file_path: str) -> str | None:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            with open(file_path, "r", encoding=encoding) as text_file:
                return text_file.read()
        except (UnicodeDecodeError, LookupError):
            continue
        except OSError:
            return None

    return None
