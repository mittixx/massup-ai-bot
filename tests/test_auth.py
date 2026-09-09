import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

from app.auth import current_user_id, validate_telegram_init_data
from app.config import Settings


def signed_init_data(token: str, user_id: int) -> str:
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "test-query",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def test_valid_telegram_signature():
    result = validate_telegram_init_data(signed_init_data("123:ABC", 42), "123:ABC")
    assert json.loads(result["user"])["id"] == 42


def test_invalid_telegram_signature():
    with pytest.raises(ValueError, match="Неверная подпись"):
        validate_telegram_init_data(signed_init_data("123:ABC", 42), "wrong-token")


def test_public_access_accepts_signed_users_and_private_mode_rejects_them():
    request = type("Request", (), {
        "headers": {"X-Telegram-Init-Data": signed_init_data("123:ABC", 43)}
    })()
    public = Settings(
        bot_token="123:ABC", owner_telegram_id=42, public_access=True, dev_mode=False
    )
    assert current_user_id(request, public) == 43

    private = Settings(
        bot_token="123:ABC", owner_telegram_id=42, public_access=False, dev_mode=False
    )
    with pytest.raises(HTTPException) as error:
        current_user_id(request, private)
    assert error.value.status_code == 403
