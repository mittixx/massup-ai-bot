from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "api.db"),
        dev_mode=True,
        run_bot=False,
    )
    return TestClient(create_app(settings))


def test_health_and_profile_flow(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/health").json()["status"] == "ok"
        headers = {"X-Debug-User-ID": "1"}
        assert client.get("/api/profile", headers=headers).status_code == 404
        payload = {
            "telegram_user_id": 1, "name": "Kirill", "sex": "male", "age": 20,
            "height_cm": 180, "weight_kg": 70, "target_weight_kg": 78,
            "activity": "medium", "meals_per_day": 4, "weekly_budget": 4500,
        }
        response = client.post("/api/profile", headers=headers, json=payload)
        assert response.status_code == 200
        assert response.json()["targets"]["surplus"] == 300
        meal = {"telegram_user_id": 1, "name": "Овсянка", "kcal": 500, "protein": 20, "fat": 15, "carbs": 70}
        assert client.post("/api/meals/manual", headers=headers, json=meal).status_code == 200
        dashboard = client.get("/api/dashboard", headers=headers).json()
        assert dashboard["totals"]["kcal"] == 500
        assert len(dashboard["meals"]) == 1
        meal_id = dashboard["meals"][0]["id"]
        correction = {
            "telegram_user_id": 1, "name": "Овсянка с бананом", "grams": 350,
            "kcal": 550, "protein": 22, "fat": 16, "carbs": 78,
        }
        assert client.patch(f"/api/meals/{meal_id}", headers=headers, json=correction).status_code == 200
        assert client.get("/api/dashboard", headers=headers).json()["totals"]["kcal"] == 550
        assert client.delete(f"/api/meals/{meal_id}", headers=headers).status_code == 200
        assert client.get("/api/dashboard", headers=headers).json()["meals"] == []

        weight_response = client.post(
            "/api/weight",
            headers=headers,
            json={"telegram_user_id": 1, "weight_kg": 71.2},
        )
        assert weight_response.status_code == 200
        assert weight_response.json()["weight"]["weight_kg"] == 71.2
        progress = client.get("/api/progress", headers=headers).json()
        assert progress["weights"][-1]["weight_kg"] == 71.2


def test_cannot_write_other_user(tmp_path):
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/profile",
            headers={"X-Debug-User-ID": "1"},
            json={
                "telegram_user_id": 2, "name": "Other", "sex": "male", "age": 20,
                "height_cm": 180, "weight_kg": 70, "target_weight_kg": 78,
                "activity": "medium", "meals_per_day": 4, "weekly_budget": 4500,
            },
        )
        assert response.status_code == 403


def test_admin_panel_is_owner_only_and_returns_metrics(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "admin.db"),
        dev_mode=True,
        run_bot=False,
        owner_telegram_id=1,
        public_access=True,
        openai_api_key="configured-for-test",
        port=3000,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        for user_id, name in ((1, "Owner"), (2, "Visitor")):
            response = client.post(
                "/api/profile",
                headers={"X-Debug-User-ID": str(user_id)},
                json={
                    "telegram_user_id": user_id, "name": name, "sex": "male", "age": 20,
                    "height_cm": 180, "weight_kg": 70, "target_weight_kg": 78,
                    "activity": "medium", "meals_per_day": 4, "weekly_budget": 4500,
                },
            )
            assert response.status_code == 200

        visitor_headers = {"X-Debug-User-ID": "2"}
        assert client.get("/api/admin/session", headers=visitor_headers).status_code == 403
        assert client.get("/api/admin/overview", headers=visitor_headers).status_code == 403
        assert client.get("/api/admin/users", headers=visitor_headers).status_code == 403

        owner_headers = {"X-Debug-User-ID": "1"}
        assert client.get("/api/admin/session", headers=owner_headers).json()["is_owner"] is True
        overview = client.get("/api/admin/overview", headers=owner_headers).json()
        assert overview["metrics"]["total_users"] == 2
        assert overview["metrics"]["profiles"] == 2
        assert overview["runtime"] == {
            "version": app.state.version,
            "bot_polling": "disabled",
            "ai_configured": True,
            "public_access": True,
            "port": 3000,
        }
        users = client.get("/api/admin/users?search=Visitor", headers=owner_headers).json()
        assert users["total"] == 1
        assert users["items"][0]["telegram_user_id"] == 2
        assert "allergies" not in users["items"][0]
