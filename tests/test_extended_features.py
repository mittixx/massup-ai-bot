from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.config import Settings
from app.insights import build_insights
from app.main import create_app
from app.rate_limit import RateLimiter
from app.schemas import PhotoAnalysis


def profile_payload(user_id: int = 1, name: str = "User") -> dict:
    return {
        "telegram_user_id": user_id,
        "name": name,
        "sex": "male",
        "age": 25,
        "height_cm": 180,
        "weight_kg": 70,
        "target_weight_kg": 78,
        "activity": "medium",
        "meals_per_day": 4,
        "weekly_budget": 4500,
    }


def ready_app(tmp_path):
    return create_app(Settings(
        database_path=str(tmp_path / "extended.db"),
        dev_mode=True,
        run_bot=False,
        owner_telegram_id=1,
        public_access=True,
        openai_api_key="test",
        port=3000,
    ))


def test_photo_requires_confirmation_when_save_is_false(tmp_path):
    app = ready_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/profile", json=profile_payload())

        async def fake_photo(*_args):
            return PhotoAnalysis(
                dish_name="Паста", items=[], total_grams=300, total_kcal=500,
                total_protein=20, total_fat=12, total_carbs=70,
                confidence=0.8, assumptions=["Порция оценена по фото"],
            )

        app.state.ai.analyze_photo = fake_photo
        analyzed = client.post(
            "/api/meals/photo",
            data={"telegram_user_id": "1", "meal_type": "Обед", "save": "false"},
            files={"image": ("food.jpg", b"photo", "image/jpeg")},
        )
        assert analyzed.status_code == 200
        assert analyzed.json()["meal"] is None
        assert client.get("/api/dashboard").json()["meals"] == []

        confirmed = client.post("/api/meals/photo/confirm", json={
            "telegram_user_id": 1, "name": "Паста с курицей", "meal_type": "Обед",
            "grams": 320, "kcal": 530, "protein": 35, "fat": 13, "carbs": 68,
            "confidence": 0.8,
        })
        assert confirmed.status_code == 200
        assert confirmed.json()["source"] == "photo_confirmed"
        assert client.get("/api/dashboard").json()["meals"][0]["name"] == "Паста с курицей"


def test_favorites_water_workouts_and_measurements(tmp_path):
    app = ready_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/profile", json=profile_payload())
        favorite = client.post("/api/favorites", json={
            "telegram_user_id": 1, "name": "Коктейль", "meal_type": "Перекус",
            "grams": 400, "kcal": 550, "protein": 35, "fat": 12, "carbs": 75,
        }).json()
        assert client.get("/api/favorites").json()["items"][0]["name"] == "Коктейль"
        assert client.post(f"/api/favorites/{favorite['id']}/use", json={"telegram_user_id": 1}).status_code == 200

        assert client.post("/api/water", json={"telegram_user_id": 1, "ml": 250}).status_code == 200
        water = client.get("/api/water").json()
        assert water["total_ml"] == 250
        assert water["target_ml"] == 2450

        workout = client.post("/api/workouts", json={
            "telegram_user_id": 1, "name": "Силовая", "duration_minutes": 60,
            "notes": "Присед 4×8",
        })
        assert workout.status_code == 200
        assert client.get("/api/workouts").json()["items"][0]["duration_minutes"] == 60

        measurement = client.post("/api/measurements", json={
            "telegram_user_id": 1, "chest_cm": 100, "waist_cm": 80,
        })
        assert measurement.status_code == 200
        assert client.get("/api/measurements").json()["items"][0]["chest_cm"] == 100


def test_progress_photos_are_private_and_deletable(tmp_path):
    app = ready_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/profile", json=profile_payload())
        client.post(
            "/api/profile",
            headers={"X-Debug-User-ID": "2"},
            json=profile_payload(2, "Other"),
        )
        created = client.post(
            "/api/progress-photos",
            data={"telegram_user_id": "1"},
            files={"image": ("progress.jpg", b"private-image", "image/jpeg")},
        )
        assert created.status_code == 200
        photo_id = created.json()["id"]
        assert client.get(f"/api/progress-photos/{photo_id}").content == b"private-image"
        assert client.get(
            f"/api/progress-photos/{photo_id}", headers={"X-Debug-User-ID": "2"}
        ).status_code == 404
        assert client.delete(f"/api/progress-photos/{photo_id}").status_code == 200


def test_shopping_list_is_merged_and_persistent(tmp_path):
    app = ready_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/profile", json=profile_payload())
        app.state.db.save_plan(1, 4500, {
            "groceries": [
                {"name": "Рис", "quantity": "1 кг", "estimated_price": 100},
                {"name": "рис", "quantity": "500 г", "estimated_price": 60},
                {"name": "Курица", "quantity": "2 кг", "estimated_price": 700},
            ]
        })
        shopping = client.get("/api/shopping").json()
        assert len(shopping["items"]) == 2
        rice = next(item for item in shopping["items"] if item["name"].lower() == "рис")
        assert rice["estimated_price"] == 160
        toggled = client.patch(
            f"/api/shopping/{rice['id']}",
            json={"telegram_user_id": 1, "checked": True},
        ).json()
        assert toggled["checked_total"] == 160


def test_feedback_blocking_broadcast_and_account_deletion(tmp_path):
    app = ready_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/profile", json=profile_payload())
        client.post(
            "/api/profile", headers={"X-Debug-User-ID": "2"}, json=profile_payload(2, "Visitor")
        )
        feedback = client.post(
            "/api/feedback",
            headers={"X-Debug-User-ID": "2"},
            data={"message": "Не открывается раздел"},
            files={"screenshot": ("screen.png", b"screen", "image/png")},
        )
        assert feedback.status_code == 200
        feedback_id = feedback.json()["id"]
        admin_feedback = client.get("/api/admin/feedback").json()["items"]
        assert admin_feedback[0]["has_screenshot"] is True
        assert client.get(f"/api/admin/feedback/{feedback_id}/screenshot").content == b"screen"

        blocked = client.put(
            "/api/admin/users/2/control", json={"blocked": True, "note": "spam"}
        )
        assert blocked.status_code == 200
        assert client.get("/api/profile", headers={"X-Debug-User-ID": "2"}).status_code == 403
        client.put("/api/admin/users/2/control", json={"blocked": False, "note": ""})

        bot = SimpleNamespace(send_message=AsyncMock())
        app.state.bot = bot
        broadcast = client.post("/api/admin/broadcast", json={
            "message": "Новая версия MassUp AI", "audience": "all", "confirmed": True,
        })
        assert broadcast.status_code == 200
        assert broadcast.json()["sent"] == 2
        assert bot.send_message.await_count == 2

        deleted = client.request(
            "DELETE", "/api/account", headers={"X-Debug-User-ID": "2"},
            json={"confirmation": "УДАЛИТЬ"},
        )
        assert deleted.status_code == 200
        assert app.state.db.get_profile(2) is None


def test_barcode_cache_and_burst_limiter(tmp_path):
    app = ready_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/profile", json=profile_payload())
        cached = {
            "barcode": "4601234567890", "name": "Творог", "brand": "Test",
            "quantity": "200 г", "image_url": None, "ingredients": "молоко",
            "allergens": ["milk"], "kcal_per_100g": 120,
            "protein_per_100g": 18, "fat_per_100g": 5, "carbs_per_100g": 3,
            "source": "Open Food Facts",
        }
        app.state.db.cache_barcode(cached["barcode"], cached)
        response = client.get(f"/api/barcode/{cached['barcode']}")
        assert response.status_code == 200
        assert response.json()["cached"] is True
        assert response.json()["name"] == "Творог"
        assert client.get("/api/barcode/not-a-code").status_code == 422

    limiter = RateLimiter()
    assert limiter.allow(2, "ai", 2, 60) is True
    assert limiter.allow(2, "ai", 2, 60) is True
    assert limiter.allow(2, "ai", 2, 60) is False
    assert limiter.allow(3, "ai", 2, 60) is True


def test_gamification_level_and_rewards():
    today = date.today()
    meals = [
        {"eaten_on": str(today), "kcal": 2500, "protein": 150, "fat": 80, "carbs": 300}
        for _ in range(10)
    ]
    insights = build_insights(
        {"target_weight_kg": 80},
        {"calories": 25000, "protein": 1500, "fat": 800, "carbs": 3000},
        meals,
        [{"measured_on": str(today), "weight_kg": 70}],
        today,
    )
    assert insights["gamification"]["points"] >= 60
    assert any(item["code"] == "meals_10" for item in insights["achievements"])


def test_upgrade_keeps_existing_profile_and_meals(tmp_path):
    database_path = tmp_path / "upgrade.db"
    first_app = create_app(Settings(
        database_path=str(database_path), dev_mode=True, run_bot=False,
        owner_telegram_id=1, public_access=True, port=3000,
    ))
    with TestClient(first_app) as client:
        client.post("/api/profile", json=profile_payload())
        client.post("/api/meals/manual", json={
            "telegram_user_id": 1, "name": "Гречка", "meal_type": "Обед",
            "grams": 250, "kcal": 320, "protein": 12, "fat": 5, "carbs": 58,
        })

    upgraded_app = create_app(Settings(
        database_path=str(database_path), dev_mode=True, run_bot=False,
        owner_telegram_id=1, public_access=True, port=3000,
    ))
    with TestClient(upgraded_app) as client:
        assert client.get("/api/profile").json()["name"] == "User"
        assert client.get("/api/dashboard").json()["meals"][0]["name"] == "Гречка"
        assert client.get("/api/favorites").json()["items"] == []
