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
_PENDING_PATH = Path("config") / "pending_credit_consumptions.json"
_QUEUE_LOCK = threading.Lock()
_CONSUME_RETRIES = 3


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


def precheck_action(license_code: str, action_type: str) -> dict | None:
    if not license_code:
        return None
    return _request_json(
        _PRECHECK_URL,
        payload={"license_code": license_code.strip(), "action_type": action_type},
    )


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
    for attempt in range(_CONSUME_RETRIES):
        result = _request_json(_CONSUME_URL, payload=payload)
        if result is not None and "balance_after" in result:
            return result
        if attempt + 1 < _CONSUME_RETRIES:
            time.sleep(0.25 * (2**attempt))
    return None


def flush_pending_consumptions() -> int:
    with _QUEUE_LOCK:
        pending = _load_pending()
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
    }
    with _QUEUE_LOCK:
        pending = _load_pending()
        unsent = [item for item in pending if _send_consume(item) is None]
        result = _send_consume(payload)
        if result is None:
            unsent.append(payload)
        _save_pending(unsent)
        return result
