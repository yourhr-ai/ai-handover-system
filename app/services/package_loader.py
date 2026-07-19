import json
import logging
import re
import tempfile
import time
import zipfile
from datetime import datetime
from io import FileIO
from pathlib import Path
from collections.abc import Callable
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from app.gdrive_config import GDRIVE_API_KEY

logger = logging.getLogger(__name__)

_GDRIVE_API_KEY_PLACEHOLDER = "여기에 실제 발급받은 키를 넣을 자리"
_GDRIVE_VIEWER_PERMISSION_MESSAGE = "공유 설정을 확인해주세요 (링크가 있는 모든 사용자: 뷰어)"
_GDRIVE_FOLDER_NOT_FOUND_MESSAGE = "폴더를 찾을 수 없습니다. 링크를 다시 확인해주세요"


class PackageLoadCancelled(Exception):
    """Raised when a caller cooperatively cancels a long package load."""


def _check_cancel(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise PackageLoadCancelled()


def _report_progress(
    progress_callback: Callable[[str, int], None] | None,
    stage: str,
    current: int = 0,
) -> None:
    if progress_callback is not None:
        progress_callback(stage, current)


def load_packages_from_folder(
    folder_path: str,
    *,
    progress_callback: Callable[[str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict]:
    packages: list[dict] = []
    package_folder = Path(folder_path)
    if not package_folder.is_dir():
        logger.warning("Package folder does not exist: %s", folder_path)
        return packages

    zip_count = 0
    folder_count = 0
    for zip_path in sorted(package_folder.glob("*.zip")):
        _check_cancel(cancel_check)
        try:
            package = load_package_from_zip(
                zip_path,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
        except PackageLoadCancelled:
            raise
        except Exception as exc:
            logger.warning("Skipping invalid package %s: %s", zip_path, exc)
            continue
        if package is not None:
            packages.append(package)
            zip_count += 1

    for child in sorted(package_folder.iterdir()):
        _check_cancel(cancel_check)
        if not child.is_dir() or not _is_package_root(child):
            continue
        try:
            package = _load_package_folder(
                child,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
        except PackageLoadCancelled:
            raise
        except Exception as exc:
            logger.warning("Skipping invalid package folder %s: %s", child, exc)
            continue
        if package is not None:
            packages.append(package)
            folder_count += 1

    print(f"총 {len(packages)}개 패키지 로드 (zip: {zip_count}개, 폴더: {folder_count}개)")
    if not packages:
        print("경고: 지정된 폴더에서 패키지를 찾지 못했습니다. .zip 파일이나 manifest.json이 포함된 패키지 폴더가 있는지 확인하세요.")

    return packages


def load_packages_from_gdrive_link(share_url: str) -> list[dict]:
    folder_id = _extract_gdrive_folder_id(share_url)
    if not folder_id:
        raise ValueError("구글드라이브 공유 폴더 링크에서 폴더 ID를 찾지 못했습니다.")

    if not GDRIVE_API_KEY or GDRIVE_API_KEY == _GDRIVE_API_KEY_PLACEHOLDER:
        raise RuntimeError("구글드라이브 API 키가 설정되어 있지 않습니다. 배포자 설정을 확인해주세요.")

    with tempfile.TemporaryDirectory(prefix="handover_gdrive_packages_") as temp_dir:
        temp_path = Path(temp_dir)
        try:
            service = build("drive", "v3", developerKey=GDRIVE_API_KEY, cache_discovery=False)
            zip_files = _list_gdrive_zip_files(service, folder_id)
            for file_info in zip_files:
                _download_gdrive_file(service, file_info, temp_path)
        except HttpError as exc:
            raise RuntimeError(_get_gdrive_error_message(exc)) from exc

        return load_packages_from_folder(str(temp_path))


def _extract_gdrive_folder_id(share_url: str) -> str | None:
    text = share_url.strip()
    patterns = [r"/folders/([^/?#]+)", r"[?&]id=([a-zA-Z0-9_-]+)"]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]+", text):
        return text
    return None


def _list_gdrive_zip_files(service: Any, folder_id: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed=false"

    while True:
        response = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name)",
                pageToken=page_token,
                pageSize=1000,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files.extend(
            file_info
            for file_info in response.get("files", [])
            if str(file_info.get("name", "")).casefold().endswith(".zip")
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            return files


def _download_gdrive_file(service: Any, file_info: dict[str, str], output_folder: Path) -> None:
    file_id = file_info["id"]
    file_name = _sanitize_download_filename(file_info.get("name") or f"{file_id}.zip")
    output_path = _unique_download_path(output_folder / file_name)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

    with FileIO(output_path, "wb") as file:
        downloader = MediaIoBaseDownload(file, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def _sanitize_download_filename(file_name: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", file_name).strip()
    return sanitized or "package.zip"


def _unique_download_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"다운로드 파일명을 만들 수 없습니다: {path.name}")


def _get_gdrive_error_message(exc: HttpError) -> str:
    status = getattr(exc.resp, "status", None)
    if status == 403:
        return _GDRIVE_VIEWER_PERMISSION_MESSAGE
    if status == 404:
        return _GDRIVE_FOLDER_NOT_FOUND_MESSAGE
    return f"구글드라이브 API 요청에 실패했습니다. 상태 코드: {status or '알 수 없음'}"


def merge_and_deduplicate_chunks(
    packages: list[dict],
    *,
    progress_callback: Callable[[str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    all_chunks: list[dict] = []
    candidates_by_source: dict[str, dict[tuple[int, str], dict]] = {}
    memo_chunks: list[dict] = []

    for package_index, package in enumerate(packages):
        package_name = package.get("package_name", "")
        package_created_at = _parse_datetime(package.get("created_at", ""))
        for chunk_index, chunk in enumerate(package.get("chunks", []), start=1):
            if chunk_index % 500 == 0:
                _check_cancel(cancel_check)
                _report_progress(progress_callback, "deduplicate", len(all_chunks))
                time.sleep(0.001)
            enriched = dict(chunk)
            enriched["package_name"] = package_name
            all_chunks.append(enriched)

            source_path = _get_chunk_source_path(enriched)
            if not source_path:
                memo_chunks.append(enriched)
                continue
            if source_path.startswith("memo:"):
                memo_chunks.append(enriched)
                continue

            modified_at = _parse_datetime(enriched.get("source_metadata", {}).get("modified_at", ""))
            package_key = (package_index, package_name)
            source_candidates = candidates_by_source.setdefault(source_path, {})
            current = source_candidates.get(package_key)
            if current is None:
                source_candidates[package_key] = {
                    "modified_at": modified_at,
                    "package_created_at": package_created_at,
                    "package_index": package_index,
                    "chunks": [enriched],
                }
                continue

            if modified_at > current["modified_at"]:
                source_candidates[package_key] = {
                    "modified_at": modified_at,
                    "package_created_at": package_created_at,
                    "package_index": package_index,
                    "chunks": [enriched],
                }
            elif modified_at == current["modified_at"]:
                current["chunks"].append(enriched)

    deduplicated_chunks = []
    for source_index, source_candidates in enumerate(
        candidates_by_source.values(), start=1
    ):
        if source_index % 500 == 0:
            _check_cancel(cancel_check)
            time.sleep(0.001)
        winner = max(
            source_candidates.values(),
            key=lambda group: (
                group["modified_at"],
                group["package_created_at"],
                group["package_index"],
            ),
        )
        deduplicated_chunks.extend(winner["chunks"])
    deduplicated_chunks.extend(memo_chunks)

    chunk_package_map = {
        chunk.get("chunk_id", f"chunk-{index:06d}"): chunk.get("package_name", "")
        for index, chunk in enumerate(deduplicated_chunks)
    }
    return {
        "chunks": deduplicated_chunks,
        "chunk_package_map": chunk_package_map,
        "before_count": len(all_chunks),
        "after_count": len(deduplicated_chunks),
    }


def load_package_from_zip(
    zip_path: Path,
    *,
    progress_callback: Callable[[str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict | None:
    """Load a single package directly from a .zip file path.

    Used both by ``load_packages_from_folder``'s per-zip scan loop and by
    the chatbot's "폴더 또는 zip 파일 선택" dialog when the user picks a
    single .zip file instead of a folder. Raises ``ValueError`` (with a
    Korean message suitable for showing directly to the user) if the file
    isn't a readable zip or doesn't contain a valid package.
    """
    with tempfile.TemporaryDirectory(prefix="handover_package_") as temp_dir:
        temp_path = Path(temp_dir)
        try:
            with zipfile.ZipFile(zip_path) as archive:
                _extract_archive_responsively(
                    archive,
                    temp_path,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                )
        except zipfile.BadZipFile as exc:
            raise ValueError("올바른 zip 파일이 아닙니다.") from exc

        root = _find_package_root(temp_path)
        return _load_package_root(
            root,
            fallback_name=zip_path.stem,
            zip_path=zip_path,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )


def _load_package_folder(
    folder_path: Path,
    *,
    progress_callback: Callable[[str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict | None:
    return _load_package_root(
        folder_path,
        fallback_name=folder_path.name,
        folder_path=folder_path,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )


def _load_package_root(
    root: Path,
    *,
    fallback_name: str,
    zip_path: Path | None = None,
    folder_path: Path | None = None,
    progress_callback: Callable[[str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    _check_cancel(cancel_check)
    manifest = _read_json(root / "manifest.json")
    source_map = _read_json(root / "source_map.json")
    chunks = _read_chunks_jsonl(
        root / "chunks.jsonl",
        source_map,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )

    package = {
        "package_name": manifest.get("package_name") or fallback_name,
        "created_at": manifest.get("created_at", ""),
        "manifest": manifest,
        "chunks": chunks,
        "source_map": source_map,
    }
    if zip_path is not None:
        package["zip_path"] = str(zip_path)
    if folder_path is not None:
        package["folder_path"] = str(folder_path)
    return package


def _find_package_root(temp_path: Path) -> Path:
    candidates = [temp_path, *[path for path in temp_path.rglob("*") if path.is_dir()]]
    for candidate in candidates:
        if _is_package_root(candidate):
            return candidate
    raise ValueError(
        "올바른 인수인계패키지 파일이 아닙니다. "
        "(manifest.json, chunks.jsonl, source_map.json이 포함되어 있어야 합니다.)"
    )


def _is_package_root(path: Path) -> bool:
    required = {"manifest.json", "chunks.jsonl", "source_map.json"}
    try:
        names = {child.name for child in path.iterdir() if child.is_file()}
    except OSError:
        return False
    return required.issubset(names)


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"missing file: {path.name}")
    with path.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"invalid json object: {path.name}")
    return data


def _read_chunks_jsonl(
    path: Path,
    source_map: dict[str, Any],
    *,
    progress_callback: Callable[[str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict]:
    if not path.is_file():
        raise ValueError("missing file: chunks.jsonl")

    chunks: list[dict] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if line_number % 250 == 0:
                _check_cancel(cancel_check)
                _report_progress(progress_callback, "parse", line_number)
                time.sleep(0.001)
            stripped = line.strip()
            if not stripped:
                continue
            try:
                chunk = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid chunks.jsonl line {line_number}") from exc
            if not isinstance(chunk, dict):
                continue
            chunk_id = chunk.get("chunk_id", "")
            metadata = source_map.get(chunk_id, {}) if isinstance(source_map, dict) else {}
            chunk["source_metadata"] = metadata if isinstance(metadata, dict) else {}
            chunks.append(chunk)
    return chunks


def _extract_archive_responsively(
    archive: zipfile.ZipFile,
    destination: Path,
    *,
    progress_callback: Callable[[str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    destination_root = destination.resolve()
    copied_bytes = 0
    for member in archive.infolist():
        _check_cancel(cancel_check)
        target = (destination / member.filename).resolve()
        try:
            target.relative_to(destination_root)
        except ValueError as exc:
            raise ValueError("unsafe zip member path") from exc
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                copied_bytes += len(block)
                _check_cancel(cancel_check)
                _report_progress(progress_callback, "extract", copied_bytes)
                time.sleep(0.001)


def _get_chunk_source_path(chunk: dict) -> str:
    metadata = chunk.get("source_metadata", {})
    if isinstance(metadata, dict):
        source_path = metadata.get("source_path")
        if source_path:
            return str(source_path)
    source_file = chunk.get("source_file")
    return str(source_file) if source_file else ""


def _parse_datetime(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min
    candidates = [text, text.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return datetime.min
