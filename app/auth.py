from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request

from app.config import Settings


def validate_telegram_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict:
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        raise ValueError("Отсутствует подпись Telegram")

    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("Неверная подпись Telegram")

    auth_date = int(pairs.get("auth_date", "0"))
    if auth_date and time.time() - auth_date > max_age:
        raise ValueError("Сессия Telegram устарела")
    return pairs


def current_user_id(request: Request, settings: Settings) -> int:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data and settings.bot_token:
        try:
            payload = validate_telegram_init_data(init_data, settings.bot_token)
            user = json.loads(payload.get("user", "{}"))
            user_id = int(user["id"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    elif settings.dev_mode:
        user_id = int(request.headers.get("X-Debug-User-ID", "1"))
    else:
        raise HTTPException(status_code=401, detail="Откройте приложение внутри Telegram")

    if (
        not settings.public_access
        and settings.owner_telegram_id
        and user_id != settings.owner_telegram_id
    ):
        raise HTTPException(status_code=403, detail="Это персональный бот")
    return user_id


def current_owner_id(request: Request, settings: Settings) -> int:
    """Return the signed-in owner or reject access to administrative data."""
    user_id = current_user_id(request, settings)
    if not settings.owner_telegram_id or user_id != settings.owner_telegram_id:
        raise HTTPException(status_code=403, detail="Доступно только владельцу бота")
    return user_id
