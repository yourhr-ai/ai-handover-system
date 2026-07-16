import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.license import LICENSE_SERVER_BASE_URL, LICENSE_SERVER_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

_BALANCE_URL = f"{LICENSE_SERVER_BASE_URL}/api/license/token/balance"
_PRECHECK_URL = f"{LICENSE_SERVER_BASE_URL}/api/license/token/precheck"
_CONSUME_URL = f"{LICENSE_SERVER_BASE_URL}/api/license/token/consume"
_RESERVE_URL = f"{LICENSE_SERVER_BASE_URL}/api/license/token/reserve"
_FINALIZE_URL = f"{LICENSE_SERVER_BASE_URL}/api/license/token/finalize"
_TOKEN_UNIT_COST_URL = f"{LICENSE_SERVER_BASE_URL}/api/handover/token-unit-cost"
_PACKAGE_BANNERS_URL = f"{LICENSE_SERVER_BASE_URL}/api/handover/package-banners"
_CHAT_FEEDBACK_URL = f"{LICENSE_SERVER_BASE_URL}/api/handover/chat-feedback"
_PENDING_PATH = Path("config") / "pending_credit_consumptions.json"
_QUEUE_LOCK = threading.Lock()
_CONSUME_RETRIES = 3
_PRECHECK_CACHE_TTL_SECONDS = 8.0
_PRECHECK_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_PRECHECK_CACHE_LOCK = threading.Lock()
_PENDING_WARN_AFTER_SECONDS = 7 * 24 * 60 * 60


def _request_json(url: str, *, payload: dict | None = None) -> dict | None:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=LICENSE_SERVER_TIMEOUT_SECONDS
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.warning("크레딧 서버가 HTTP %s를 반환했습니다: %s", exc.code, url)
        return None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning("크레딧 서버 요청에 실패했습니다: %s", exc)
        return None
    if not isinstance(body, dict):
        logger.warning("크레딧 서버가 JSON 객체가 아닌 응답을 반환했습니다: %s", body)
        return None
    return body


def check_balance(license_code: str) -> dict | None:
    if not license_code:
        return None
    query = urllib.parse.urlencode({"license_code": license_code.strip()})
    return _request_json(f"{_BALANCE_URL}?{query}")


def get_embedding_unit_cost() -> float | None:
    """Return the server's current KRW/credit cost per 1,000 embedding tokens."""
    result = _request_json(_TOKEN_UNIT_COST_URL)
    if result is None:
        return None
    try:
        unit_cost = float(result["embedding_krw_per_1k_tokens"])
    except (KeyError, TypeError, ValueError):
        return None
    return unit_cost if unit_cost > 0 else None


def fetch_package_banners() -> dict | None:
    """Return optional package-progress banners without blocking package creation."""
    return _request_json(_PACKAGE_BANNERS_URL)


def submit_chat_feedback(
    license_code: str,
    question: str,
    answer_preview: str,
    rating: str,
) -> dict | None:
    """Submit optional answer feedback; callers intentionally ignore failures."""
    if not license_code or not question or rating not in {"up", "down"}:
        return None
    return _request_json(
        _CHAT_FEEDBACK_URL,
        payload={
            "licenseCode": license_code.strip(),
            "question": question,
            "answerPreview": answer_preview[:200],
            "rating": rating,
        },
    )


def precheck_action(license_code: str, action_type: str) -> dict | None:
    if not license_code:
        return None
    key = (license_code.strip(), action_type)
    now = time.monotonic()
    with _PRECHECK_CACHE_LOCK:
        cached = _PRECHECK_CACHE.get(key)
        if cached is not None and now - cached[0] < _PRECHECK_CACHE_TTL_SECONDS:
            return dict(cached[1])
    result = _request_json(
        _PRECHECK_URL,
        payload={"license_code": key[0], "action_type": action_type},
    )
    if result is not None:
        with _PRECHECK_CACHE_LOCK:
            _PRECHECK_CACHE[key] = (time.monotonic(), dict(result))
    return result


def reserve_credits(
    license_code: str, action_type: str, cost: int
) -> dict | None:
    """Reserve the full estimated action cost before starting expensive work."""
    if not license_code:
        return None
    return _request_json(
        _RESERVE_URL,
        payload={
            "license_code": license_code.strip(),
            "action_type": action_type,
            "cost": max(0, int(cost)),
        },
    )


def finalize_credit_reservation(
    license_code: str,
    reservation_id: str,
    action_type: str,
    *,
    embedding_tokens: int = 0,
    cancel: bool = False,
) -> dict | None:
    """Settle a reservation with actual usage, or release it on cancel/failure."""
    if not license_code or not reservation_id:
        return None
    payload = {
        "license_code": license_code.strip(),
        "reservation_id": reservation_id,
        "action_type": action_type,
        "embedding_tokens": max(0, int(embedding_tokens)),
        "cancel": bool(cancel),
    }
    for attempt in range(_CONSUME_RETRIES):
        result = _request_json(_FINALIZE_URL, payload=payload)
        if result is not None and "balance_after" in result:
            return result
        if attempt + 1 < _CONSUME_RETRIES:
            time.sleep(0.25 * (2**attempt))
    return None


def _load_pending() -> list[dict]:
    try:
        body = json.loads(_PENDING_PATH.read_text(encoding="utf-8"))
        return body if isinstance(body, list) else []
    except (OSError, ValueError):
        return []


def _save_pending(items: list[dict]) -> None:
    _PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PENDING_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _send_consume(payload: dict) -> dict | None:
    request_payload = {key: value for key, value in payload.items() if key != "queued_at"}
    for attempt in range(_CONSUME_RETRIES):
        result = _request_json(_CONSUME_URL, payload=request_payload)
        if result is not None and "balance_after" in result:
            return result
        if attempt + 1 < _CONSUME_RETRIES:
            time.sleep(0.25 * (2**attempt))
    return None


def flush_pending_consumptions() -> int:
    with _QUEUE_LOCK:
        pending = _load_pending()
        now = time.time()
        try:
            queue_mtime = _PENDING_PATH.stat().st_mtime
        except OSError:
            queue_mtime = now
        for payload in pending:
            queued_at = payload.get("queued_at")
            age_base = queued_at if isinstance(queued_at, (int, float)) else queue_mtime
            if now - age_base >= _PENDING_WARN_AFTER_SECONDS:
                logger.warning("오래된 크레딧 소비 큐가 남아 있습니다: queued_at=%s action=%s", queued_at, payload.get("action_type"))
        unsent: list[dict] = []
        sent = 0
        for payload in pending:
            if _send_consume(payload) is None:
                unsent.append(payload)
            else:
                sent += 1
        if pending or _PENDING_PATH.exists():
            _save_pending(unsent)
        return sent


def consume_credits(
    license_code: str,
    action_type: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    embedding_tokens: int = 0,
) -> dict | None:
    payload = {
        "license_code": license_code.strip(),
        "action_type": action_type,
        "prompt_tokens": max(0, int(prompt_tokens)),
        "completion_tokens": max(0, int(completion_tokens)),
        "embedding_tokens": max(0, int(embedding_tokens)),
        "queued_at": time.time(),
    }
    with _QUEUE_LOCK:
        pending = _load_pending()
        unsent = [item for item in pending if _send_consume(item) is None]
        result = _send_consume(payload)
        if result is None:
            unsent.append(payload)
        _save_pending(unsent)
        return result
