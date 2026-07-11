import base64
import binascii
from pathlib import Path


API_KEY_FILE_PATH = Path("config") / "api_key.dat"
PACKAGE_API_KEY_FILE_PATH = Path(__file__).resolve().parents[1] / "config" / "api_key.dat"


def save_api_key(key: str) -> None:
    API_KEY_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    encoded = base64.b64encode(key.encode("utf-8"))
    API_KEY_FILE_PATH.write_bytes(encoded)


def load_api_key() -> str | None:
    if not API_KEY_FILE_PATH.is_file():
        return load_packaged_api_key()
    try:
        encoded = API_KEY_FILE_PATH.read_bytes()
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, OSError):
        return load_packaged_api_key()


def load_packaged_api_key() -> str | None:
    if not PACKAGE_API_KEY_FILE_PATH.is_file():
        return None
    try:
        api_key = PACKAGE_API_KEY_FILE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return api_key or None
