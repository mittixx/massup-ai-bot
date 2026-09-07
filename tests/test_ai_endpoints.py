from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import DayPlan, FoodItem, GroceryItem, MealPlanItem, PhotoAnalysis, WeekPlan


def ready_client(tmp_path):
    app = create_app(Settings(database_path=str(tmp_path / "ai.db"), dev_mode=True, run_bot=False))
    client = TestClient(app)
    return app, client


def profile_payload():
    return {
        "telegram_user_id": 1, "name": "Kirill", "sex": "male", "age": 20,
        "height_cm": 180, "weight_kg": 70, "target_weight_kg": 78,
        "activity": "medium", "meals_per_day": 4, "weekly_budget": 4500,
    }


def test_photo_analysis_is_saved(tmp_path):
    app, client = ready_client(tmp_path)
    headers = {"X-Debug-User-ID": "1"}
    with client:
        client.post("/api/profile", headers=headers, json=profile_payload())

        async def fake_analysis(*_args, **_kwargs):
            return PhotoAnalysis(
                dish_name="Рис с курицей",
                items=[FoodItem(name="Рис", estimated_grams=200, kcal=260, protein=5, fat=1, carbs=57)],
                total_grams=350, total_kcal=510, total_protein=38, total_fat=13,
                total_carbs=60, confidence=0.78, assumptions=["Учтена чайная ложка масла"],
            )

        app.state.ai.analyze_photo = fake_analysis
        response = client.post(
            "/api/meals/photo", headers=headers,
            data={"telegram_user_id": "1", "meal_type": "Обед"},
            files={"image": ("food.jpg", b"fake-jpeg", "image/jpeg")},
        )
        assert response.status_code == 200
        assert response.json()["analysis"]["total_kcal"] == 510
        assert client.get("/api/dashboard", headers=headers).json()["totals"]["kcal"] == 510


def test_week_plan_is_saved(tmp_path):
    app, client = ready_client(tmp_path)
    headers = {"X-Debug-User-ID": "1"}
    with client:
        client.post("/api/profile", headers=headers, json=profile_payload())
        meal = MealPlanItem(
            meal_type="Завтрак", dish="Овсянка", ingredients=["Овсянка 100 г"],
            kcal=500, protein=20, fat=15, carbs=70, recipe="Сварить крупу.",
        )
        plan = WeekPlan(
            title="Экономный набор", budget=4500, estimated_total=4200,
            days=[DayPlan(day=f"День {i}", meals=[meal], total_kcal=500,
                          total_protein=20, total_fat=15, total_carbs=70) for i in range(1, 8)],
            groceries=[GroceryItem(category="Крупы", name="Овсянка", quantity="1 кг", estimated_price=150)],
            prep_plan=["Сварить овсянку"], substitutions=[], notes=[],
        )

        async def fake_plan(*_args, **_kwargs):
            return plan

        app.state.ai.make_week_plan = fake_plan
        response = client.post(
            "/api/plan", headers=headers,
            json={"telegram_user_id": 1, "budget": 4500, "pantry": "", "wishes": ""},
        )
        assert response.status_code == 200
        assert len(response.json()["days"]) == 7
        assert client.get("/api/dashboard", headers=headers).json()["latest_plan"]["estimated_total"] == 4200
