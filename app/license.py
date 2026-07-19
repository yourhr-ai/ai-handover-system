import base64
import calendar
import hashlib
import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
import winreg
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SECRET_KEY = "7a9df181d69b561f7486ac07cf51d3613bc72db5a91e202d3f26ca8c2e6c4fd8"

LICENSE_FILE_PATH = Path("config") / "license.json"
LAST_SEEN_DATE_FILE_PATH = Path("config") / "last_seen_date.dat"
CLOCK_SKEW_WARNING_DAYS = 1
TRUSTED_TIME_CHECK_URL = "https://www.google.com"
TRUSTED_TIME_CHECK_TIMEOUT_SECONDS = 3
KST = timezone(timedelta(hours=9))

# TODO: 실제 운영 중인 hr-ai-review 서버 주소가 정해지면 아래 값을 교체할 것.
LICENSE_SERVER_BASE_URL = os.environ.get(
    "HANDOVER_LICENSE_SERVER_BASE_URL", "https://review.yourhr.co.kr"
).rstrip("/")
LICENSE_ACTIVATE_URL = f"{LICENSE_SERVER_BASE_URL}/api/license/activate"
LICENSE_SERVER_TIMEOUT_SECONDS = 10
# Portal page where a license key holder charges credits / data-processing
# quota. Linked from the credit/quota-insufficient notices across the app.
HANDOVER_PORTAL_URL = f"{LICENSE_SERVER_BASE_URL}/handover/portal"


def _parse_validity_code(validity_code: str) -> tuple[str, int] | None:
    if len(validity_code) < 2:
        return None

    unit, amount_str = validity_code[0].upper(), validity_code[1:]
    if unit not in ("Y", "M"):
        return None

    try:
        amount = int(amount_str)
    except ValueError:
        return None

    return unit, amount


def validate_license(license_code: str) -> bool:
    parts = license_code.strip().rsplit("-", 2)
    if len(parts) != 3:
        return False

    company_code, validity_code, checksum = parts
    if not company_code or len(checksum) != 8:
        return False
    if _parse_validity_code(validity_code) is None:
        return False

    expected_checksum = (
        hashlib.sha256(f"{company_code}{validity_code}{SECRET_KEY}".encode("utf-8"))
        .hexdigest()[:8]
        .upper()
    )
    return checksum.upper() == expected_checksum


def _get_uuid_via_wmic() -> str | None:
    try:
        result = subprocess.run(
            ["wmic", "csproduct", "get", "uuid"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    candidates = [line for line in lines if line.upper() != "UUID"]
    if not candidates:
        return None

    uuid_value = candidates[0].upper()
    if not uuid_value or set(uuid_value.replace("-", "")) == {"0"}:
        return None
    return uuid_value


def _get_uuid_via_powershell() -> str | None:
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    uuid_value = result.stdout.strip().upper()
    if not uuid_value or set(uuid_value.replace("-", "")) == {"0"}:
        return None
    return uuid_value


def _get_uuid_via_machine_guid() -> str | None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
    except OSError:
        return None

    value = value.strip().upper()
    if not value:
        return None
    return value


def get_device_id() -> str | None:
    """이 PC를 식별하는 값을 반환한다. 마더보드 UUID를 우선 사용하고,
    조회할 수 없으면 레지스트리의 MachineGuid로 대체한다. 둘 다 실패하면 None."""
    for probe in (_get_uuid_via_wmic, _get_uuid_via_powershell, _get_uuid_via_machine_guid):
        device_id = probe()
        if device_id:
            return device_id

    logger.error("PC 식별에 실패했습니다: UUID와 MachineGuid 조회가 모두 실패했습니다.")
    return None


def check_server_reachable() -> bool:
    """hr-ai-review 서버에 연결 가능한지 확인한다 (인터넷 연결 필수 체크용)."""
    try:
        request = urllib.request.Request(LICENSE_SERVER_BASE_URL, method="HEAD")
        with urllib.request.urlopen(request, timeout=LICENSE_SERVER_TIMEOUT_SECONDS):
            return True
    except Exception:
        return False


def verify_license_with_server(license_code: str, device_id: str) -> tuple[str, str | None]:
    """hr-ai-review 서버에 라이선스 활성화/재확인을 요청한다.

    반환값은 (status, server_message) 이며 status는 다음 중 하나:
    - "activated": 이 기기에서 최초 활성화됨
    - "already_activated_same_device": 이미 이 기기에서 활성화되어 있음
    - "activated_on_other_device": 다른 기기에서 이미 활성화됨
    - "license_terminated": 관리자가 지정한 종료일이 지남
    - "not_found": 서버에 존재하지 않는 라이선스 코드
    - "network_error": 서버 요청 자체가 실패함 (타임아웃/네트워크 오류/이상 응답)
    """
    payload = json.dumps({"license_code": license_code, "device_id": device_id}).encode("utf-8")
    request = urllib.request.Request(
        LICENSE_ACTIVATE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=LICENSE_SERVER_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError, OSError):
            error_body = {}

        if not isinstance(error_body, dict):
            error_body = {}

        known_status = error_body.get("status")
        if known_status in (
            "activated",
            "already_activated_same_device",
            "activated_on_other_device",
            "license_terminated",
            "not_found",
        ):
            return known_status, error_body.get("message") or error_body.get("reason")

        logger.warning("라이선스 서버가 HTTP %s를 반환했습니다: %s", exc.code, error_body)
        return "network_error", None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning("라이선스 서버 요청에 실패했습니다: %s", exc)
        return "network_error", None

    if not isinstance(body, dict):
        logger.warning("라이선스 서버가 JSON 객체가 아닌 응답을 반환했습니다: %s", body)
        return "network_error", None

    status = body.get("status")
    if status not in (
        "activated",
        "already_activated_same_device",
        "activated_on_other_device",
        "license_terminated",
        "not_found",
    ):
        logger.warning("라이선스 서버로부터 알 수 없는 응답을 받았습니다: %s", body)
        return "network_error", None

    return status, body.get("reason") or body.get("message")


def _load_license_data() -> dict:
    if not LICENSE_FILE_PATH.exists():
        return {}
    try:
        return json.loads(LICENSE_FILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_license_data(data: dict) -> None:
    LICENSE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LICENSE_FILE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_license(license_code: str) -> None:
    data = _load_license_data()
    data["license_code"] = license_code.strip()
    _save_license_data(data)


def load_saved_license_code() -> str | None:
    return _load_license_data().get("license_code")


def _first_use_record_checksum(license_code: str, first_used_at: str, expiry_at: str) -> str:
    return hashlib.sha256(
        f"{license_code}{first_used_at}{expiry_at}{SECRET_KEY}".encode("utf-8")
    ).hexdigest()[:8]


def _calculate_expiry_datetime(first_used_at: datetime, unit: str, amount: int) -> datetime:
    if unit == "Y":
        try:
            return first_used_at.replace(year=first_used_at.year + amount)
        except ValueError:
            return first_used_at.replace(year=first_used_at.year + amount, day=28)
    target_month_index = first_used_at.month - 1 + amount
    target_year = first_used_at.year + target_month_index // 12
    target_month = target_month_index % 12 + 1
    target_day = min(first_used_at.day, calendar.monthrange(target_year, target_month)[1])
    return first_used_at.replace(year=target_year, month=target_month, day=target_day)


def _resolve_expiry_datetime(license_code: str, unit: str, amount: int) -> datetime | None:
    data = _load_license_data()
    records = data.get("first_use_records", {})
    record = records.get(license_code)

    if record is not None:
        first_used_at = record.get("first_used_at")
        expiry_at = record.get("expiry_at")
        checksum = record.get("checksum")
        if not (first_used_at and expiry_at and checksum):
            logger.warning("최초 사용일 기록이 손상되었습니다: %s", license_code)
            return None
        if checksum != _first_use_record_checksum(license_code, first_used_at, expiry_at):
            logger.warning("최초 사용일 기록이 변조된 것으로 보입니다: %s", license_code)
            return None
        return datetime.fromisoformat(expiry_at)

    # 이 코드로는 처음 실행됨 - 지금 이 순간을 최초 사용일로 고정해 기록한다.
    first_used_at_dt = datetime.now()
    expiry_at_dt = _calculate_expiry_datetime(first_used_at_dt, unit, amount)
    first_used_at_str = first_used_at_dt.isoformat(timespec="seconds")
    expiry_at_str = expiry_at_dt.isoformat(timespec="seconds")

    records[license_code] = {
        "first_used_at": first_used_at_str,
        "expiry_at": expiry_at_str,
        "checksum": _first_use_record_checksum(license_code, first_used_at_str, expiry_at_str),
    }
    data["first_use_records"] = records
    _save_license_data(data)
    return expiry_at_dt


def _encode_last_seen_date(seen_date: date) -> str:
    date_str = seen_date.isoformat()
    checksum = hashlib.sha256(f"{date_str}{SECRET_KEY}".encode("utf-8")).hexdigest()[:8]
    payload = f"{date_str}:{checksum}"
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_last_seen_date(encoded: str) -> date | None:
    try:
        payload = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
        date_str, checksum = payload.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return None

    expected_checksum = hashlib.sha256(f"{date_str}{SECRET_KEY}".encode("utf-8")).hexdigest()[:8]
    if checksum != expected_checksum:
        return None

    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def _read_last_seen_date() -> date | None:
    if not LAST_SEEN_DATE_FILE_PATH.exists():
        return None
    try:
        encoded = LAST_SEEN_DATE_FILE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return _decode_last_seen_date(encoded)


def _write_last_seen_date(seen_date: date) -> None:
    LAST_SEEN_DATE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_SEEN_DATE_FILE_PATH.write_text(_encode_last_seen_date(seen_date), encoding="utf-8")


def _check_system_clock_rollback(today: date) -> bool:
    last_seen = _read_last_seen_date()
    if last_seen is not None and today < last_seen:
        logger.warning(
            "시스템 시간이 올바르지 않습니다. 날짜를 확인해주세요. (마지막 확인: %s, 현재: %s)",
            last_seen.isoformat(),
            today.isoformat(),
        )
        return False

    if last_seen is None or today > last_seen:
        _write_last_seen_date(today)
    return True


def _check_trusted_time_skew(today: date) -> None:
    try:
        request = urllib.request.Request(TRUSTED_TIME_CHECK_URL, method="HEAD")
        with urllib.request.urlopen(request, timeout=TRUSTED_TIME_CHECK_TIMEOUT_SECONDS) as response:
            server_date_header = response.headers.get("Date")
        if not server_date_header:
            return
        trusted_datetime = parsedate_to_datetime(server_date_header)
        if trusted_datetime.tzinfo is None:
            trusted_datetime = trusted_datetime.replace(tzinfo=timezone.utc)
        trusted_date = trusted_datetime.astimezone(KST).date()
    except Exception:
        # 인터넷 연결이 없거나 서버 응답을 해석할 수 없는 경우 - 이 검증은 건너뛴다.
        return

    if abs((trusted_date - today).days) >= CLOCK_SKEW_WARNING_DAYS:
        logger.warning(
            "로컬 시스템 날짜(%s)가 신뢰할 수 있는 서버 날짜(%s)와 차이가 큽니다.",
            today.isoformat(),
            trusted_date.isoformat(),
        )


def is_license_active() -> bool:
    license_code = load_saved_license_code()
    if not license_code or not validate_license(license_code):
        return False

    today = datetime.now().date()
    if not _check_system_clock_rollback(today):
        return False
    _check_trusted_time_skew(today)

    _, validity_code, _ = license_code.strip().rsplit("-", 2)
    unit, amount = _parse_validity_code(validity_code)

    expiry_at = _resolve_expiry_datetime(license_code.strip(), unit, amount)
    if expiry_at is None:
        return False

    return datetime.now() <= expiry_at
