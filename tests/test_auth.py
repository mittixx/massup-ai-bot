import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.auth import validate_telegram_init_data


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
