"""Configuration loaded from environment variables; secrets are never logged."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    email: str
    app_password: str = field(repr=False)
    imap_host: str = "imap.yandex.com"
    imap_port: int = 993
    allow_write: bool = False
    promotions_folder: str = "AI-Promotions"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        email = os.getenv("YANDEX_EMAIL", "").strip()
        password = os.getenv("YANDEX_APP_PASSWORD", "").strip()
        if not email or not password:
            raise RuntimeError("Set YANDEX_EMAIL and YANDEX_APP_PASSWORD in the environment (see .env.example)")
        return cls(
            email=email,
            app_password=password,
            imap_host=os.getenv("YANDEX_IMAP_HOST", "imap.yandex.com"),
            imap_port=int(os.getenv("YANDEX_IMAP_PORT", "993")),
            allow_write=env_bool("YANDEX_ALLOW_WRITE"),
            promotions_folder=os.getenv("YANDEX_PROMOTIONS_FOLDER", "AI-Promotions"),
        )
