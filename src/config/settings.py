from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


@dataclass
class Settings:
    api_key: str = ""
    model: str = "gpt-5.5"

    @classmethod
    def load(cls) -> "Settings":
        """Load settings from .env file and environment variables."""
        load_dotenv(dotenv_path=_ENV_FILE, override=False)
        return cls(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        )

    def is_valid(self) -> bool:
        """Return True if the API key is present."""
        return bool(self.api_key and self.api_key.strip())

    def apply_key(self, key: str) -> None:
        """Override the API key at runtime (e.g. from the UI dialog)."""
        self.api_key = key.strip()
