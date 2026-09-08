import asyncio
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AuthenticationError

from app.config import Settings
from app.main import VERSION, create_app
from tests.test_ai_endpoints import profile_payload


def make_app(tmp_path, **kwargs):
    return create_app(Settings(database_path=str(tmp_path / "regression.db"), run_bot=False, dev_mode=True, **kwargs))


def test_weight_survives_restart_and_historical_entry(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        client.post("/api/profile", json=profile_payload()).raise_for_status()
        for weight in (71.1, 71.2):
            client.post("/api/weight", json={"telegram_user_id": 1, "weight_kg": weight}).raise_for_status()
        assert len(client.get("/api/progress").json()["weights"]) == 1
        client.post("/api/weight", json={"telegram_user_id": 1, "weight_kg": 68,
                    "measured_on": str(date.today() - timedelta(days=1))}).raise_for_status()
    with TestClient(make_app(tmp_path)) as restarted:
        history = restarted.get("/api/progress").json()["weights"]
        assert [item["weight_kg"] for item in history] == [68, 71.2]
        assert history[-1]["measured_on"] == str(date.today())
        assert restarted.get("/api/profile").json()["weight_kg"] == 71.2


def test_weight_requires_profile_and_valid_date(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        body = {"telegram_user_id": 1, "weight_kg": 71.2}
        assert client.post("/api/weight", json=body).status_code == 409
        client.post("/api/profile", json=profile_payload())
        body["measured_on"] = str(date.today() + timedelta(days=1))
        assert client.post("/api/weight", json=body).status_code == 422


def test_dashboard_date_rolls_over_without_restart(tmp_path, monkeypatch):
    class Clock(date):
        current = date(2030, 1, 1)

        @classmethod
        def today(cls):
            return cls.current

    with TestClient(make_app(tmp_path)) as client:
        client.post("/api/profile", json=profile_payload())
        monkeypatch.setattr("app.api.date", Clock)
        assert client.get("/api/dashboard").json()["date"] == "2030-01-01"
        Clock.current = date(2030, 1, 2)
        assert client.get("/api/dashboard").json()["date"] == "2030-01-02"
        assert client.get("/api/dashboard?day=2029-12-31").json()["date"] == "2029-12-31"


def test_release_cache_and_russian_encoding(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["version"] == VERSION
        assert health.json()["database"] == "ok"
        assert health.json()["bot_polling"] == "disabled"
        page = client.get("/")
        assert page.headers["cache-control"] == "no-store"
        assert "Мой профиль" in page.text
        assert "Рџ" not in page.text
        for asset in ("style.css", "weight.css", "app.js"):
            path = f"/static/{asset}?v={VERSION}"
            assert path in page.text
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("endpoint", ["plan", "photo"])
@pytest.mark.parametrize("kind", ["authentication", "unexpected"])
def test_ai_errors_do_not_leak_in_response_or_logs(tmp_path, monkeypatch, caplog, endpoint, kind):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/profile", json=profile_payload())
        def fail(**kwargs):
            secret = "sk-test-DO-NOT-LEAK"
            if kind == "authentication":
                raise AuthenticationError("Invalid API key: " + secret,
                    response=httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
                    body={"error": {"message": secret}})
            raise RuntimeError(secret)
        monkeypatch.setattr(app.state.ai, "_parse", fail)
        if endpoint == "plan":
            response = client.post("/api/plan", json={"telegram_user_id": 1, "budget": 4000})
        else:
            response = client.post("/api/meals/photo", data={"telegram_user_id": "1"},
                                   files={"image": ("test.jpg", b"fake", "image/jpeg")})
        assert response.status_code == (503 if kind == "authentication" else 502)
        assert "sk-test" not in response.text + caplog.text
        assert "Invalid API key" not in response.text + caplog.text
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["meals"] == [] and dashboard["latest_plan"] is None


def test_polling_does_not_own_signals_and_session_closes(tmp_path, monkeypatch):
    observed = {}
    async def polling(bot, **kwargs):
        observed.update(kwargs)
        await asyncio.Event().wait()
    session = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr("app.main.create_dispatcher", lambda *_: (
        SimpleNamespace(session=session), SimpleNamespace(start_polling=polling)))
    app = create_app(Settings(database_path=str(tmp_path / "poll.db"), bot_token="123:fake", run_bot=True))
    with TestClient(app) as client:
        assert client.get("/health").json()["bot_polling"] == "active"
    assert observed == {"handle_signals": False, "close_bot_session": False}
    session.close.assert_awaited_once()


def test_run_uses_configured_host_and_port(monkeypatch):
    import runpy
    seen = {}
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(host="0.0.0.0", port=8123))
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(app=app, **kw))
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "run.py"), run_name="__main__")
    assert seen == {"app": "app.main:app", "host": "0.0.0.0", "port": 8123, "reload": False}
