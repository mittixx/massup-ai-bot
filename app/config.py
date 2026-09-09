from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


@dataclass(slots=True)
class Settings:
    bot_token: str = ""
    owner_telegram_id: int = 0
    public_access: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    webapp_url: str = "http://localhost:8000"
    database_path: str = "data/nutrition.db"
    host: str = "0.0.0.0"
    port: int = 8000
    run_bot: bool = True
    dev_mode: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            bot_token=os.getenv("BOT_TOKEN", "").strip(),
            owner_telegram_id=int(os.getenv("OWNER_TELEGRAM_ID", "0") or 0),
            public_access=_as_bool(os.getenv("PUBLIC_ACCESS"), False),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna").strip(),
            webapp_url=os.getenv("WEBAPP_URL", "http://localhost:8000").strip(),
            database_path=os.getenv("DATABASE_PATH", "data/nutrition.db").strip(),
            host=os.getenv("HOST", "0.0.0.0").strip(),
            port=int(os.getenv("PORT", "8000")),
            run_bot=_as_bool(os.getenv("RUN_BOT"), True),
            dev_mode=_as_bool(os.getenv("DEV_MODE"), False),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
