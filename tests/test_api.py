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
