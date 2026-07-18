"""HTTP client for the hr-ai-review AI proxy.

exe는 더 이상 OpenAI를 직접 호출하지 않는다. 인수인계서 생성/챗봇 질문은
POST {LICENSE_SERVER_BASE_URL}/api/handover/ai/chat 을, 임베딩(패키지 생성,
챗봇 질문 임베딩)은 POST {LICENSE_SERVER_BASE_URL}/api/handover/ai/embeddings
를 경유한다. 서버가 라이선스 코드로 상품을 찾아 그 상품에 등록된 GPT 키로
실제 OpenAI를 호출하고, chat 엔드포인트는 크레딧 reserve/finalize도 내부적으로
처리한다.
"""

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Callable

import numpy as np

from app.license import LICENSE_SERVER_BASE_URL

CHAT_URL = f"{LICENSE_SERVER_BASE_URL}/api/handover/ai/chat"
EMBEDDINGS_URL = f"{LICENSE_SERVER_BASE_URL}/api/handover/ai/embeddings"

SAFE_SERVICE_ERROR_MESSAGE = "일시적으로 서비스 이용이 어렵습니다. 잠시 후 다시 시도해주세요."
INSUFFICIENT_CREDITS_MESSAGE = "크레딧이 부족합니다. 설명서 페이지에서 사용량을 구매해 주세요."

# Matches the server's own `export const maxDuration = 60` route config - if the
# server is going to give up at 60s, waiting much longer here only delays the
# user seeing an error.
CHAT_TIMEOUT_SECONDS = 70
EMBEDDINGS_TIMEOUT_SECONDS = 70


class AiProxyError(Exception):
    """Base class for every error this client raises. `message` is always
    safe to show the user as-is - server-side error detail never reaches here."""

    def __init__(self, message: str = SAFE_SERVICE_ERROR_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


class InsufficientCreditsError(AiProxyError):
    def __init__(self, balance: float = 0, required: float = 0) -> None:
        super().__init__(INSUFFICIENT_CREDITS_MESSAGE)
        self.balance = balance
        self.required = required


class ServiceUnavailableError(AiProxyError):
    pass


class PayloadTooLargeError(AiProxyError):
    pass


class ProxyTimeoutError(AiProxyError):
    pass


def _raise_for_status(status: int, body: dict) -> None:
    message = str(body.get("message") or SAFE_SERVICE_ERROR_MESSAGE)
    if status == 402:
        raise InsufficientCreditsError(
            balance=body.get("balance", 0),
            required=body.get("required_credits", 0),
        )
    if status == 413:
        raise PayloadTooLargeError(message)
    if status == 504:
        raise ProxyTimeoutError(message)
    raise ServiceUnavailableError(message)


def _post_json(url: str, payload: dict, timeout: float) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
            return response.status, body if isinstance(body, dict) else {}
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError, OSError):
            body = {}
        return exc.code, body if isinstance(body, dict) else {}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise ServiceUnavailableError() from exc


def call_chat(
    license_code: str,
    action_type: str,
    model: str,
    messages: list[dict],
    *,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Non-streaming POST /api/handover/ai/chat.

    Returns {"content": str, "usage": {"prompt_tokens", "completion_tokens"},
    "credits_charged": int, "balance_after": int}.
    Raises InsufficientCreditsError / PayloadTooLargeError / ProxyTimeoutError /
    ServiceUnavailableError.
    """
    payload: dict[str, Any] = {
        "licenseCode": license_code,
        "actionType": action_type,
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if response_format is not None:
        payload["responseFormat"] = response_format
    if max_tokens is not None:
        payload["maxTokens"] = max_tokens
    if reasoning_effort is not None:
        payload["reasoningEffort"] = reasoning_effort

    status, body = _post_json(CHAT_URL, payload, CHAT_TIMEOUT_SECONDS)
    if status != 200 or body.get("status") != "ok":
        _raise_for_status(status, body)
    return body


def call_chat_stream(
    license_code: str,
    action_type: str,
    model: str,
    messages: list[dict],
    *,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    on_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Streaming POST /api/handover/ai/chat (stream: true).

    Reads the server's SSE response ("data: {...}\\n\\n" lines) as it arrives,
    calling on_delta(text) for every {"type": "delta", ...} event. Returns the
    same shape as call_chat once the stream ends with {"type": "usage", ...}
    and "[DONE]".
    """
    payload: dict[str, Any] = {
        "licenseCode": license_code,
        "actionType": action_type,
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if max_tokens is not None:
        payload["maxTokens"] = max_tokens
    if reasoning_effort is not None:
        payload["reasoningEffort"] = reasoning_effort

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        CHAT_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        response = urllib.request.urlopen(request, timeout=CHAT_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError, OSError):
            body = {}
        _raise_for_status(exc.code, body if isinstance(body, dict) else {})
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise ServiceUnavailableError() from exc

    content_parts: list[str] = []
    usage_event: dict[str, Any] = {}
    try:
        with response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:") :].strip()
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except ValueError:
                    continue
                event_type = event.get("type")
                if event_type == "delta":
                    delta = event.get("content") or ""
                    if delta:
                        content_parts.append(delta)
                        if on_delta is not None:
                            on_delta(delta)
                elif event_type == "usage":
                    usage_event = event
                elif event_type == "error":
                    raise ServiceUnavailableError(
                        str(event.get("message") or SAFE_SERVICE_ERROR_MESSAGE)
                    )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ServiceUnavailableError() from exc

    return {
        "content": "".join(content_parts),
        "usage": usage_event.get("usage", {}),
        "credits_charged": usage_event.get("credits_charged", 0),
        "balance_after": usage_event.get("balance_after"),
    }


def call_embeddings(license_code: str, texts: list[str]) -> dict[str, Any]:
    """POST /api/handover/ai/embeddings.

    The server responds with each embedding as a base64-encoded little-endian
    float32 buffer (encoding_format="base64"), not a JSON float array - decode
    with numpy.frombuffer(..., dtype="<f4") and verify the length matches
    embeddingDimensions before trusting it.

    Returns {"embeddings": list[list[float]], "usage": {...}}. No credit
    deduction happens here - embeddings are billed via the separate GB-based
    data-processing quota that the caller tracks itself.
    """
    payload = {"licenseCode": license_code, "texts": texts}
    status, body = _post_json(EMBEDDINGS_URL, payload, EMBEDDINGS_TIMEOUT_SECONDS)
    if status != 200 or body.get("status") != "ok":
        _raise_for_status(status, body)

    dimensions = int(body.get("embeddingDimensions", 0) or 0)
    vectors: list[list[float]] = []
    for encoded in body.get("embeddings", []):
        raw_bytes = base64.b64decode(encoded)
        vector = np.frombuffer(raw_bytes, dtype="<f4")
        if dimensions and len(vector) != dimensions:
            raise ServiceUnavailableError(
                f"임베딩 벡터 길이가 예상과 다릅니다 (기대: {dimensions}, 실제: {len(vector)})."
            )
        vectors.append(vector.tolist())

    return {"embeddings": vectors, "usage": body.get("usage", {})}
