from app.database import Database


def test_profile_meal_and_weight_roundtrip(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    profile = {
        "telegram_user_id": 10, "name": "Kirill", "sex": "male", "age": 20,
        "height_cm": 180, "weight_kg": 70, "target_weight_kg": 78,
        "activity": "medium", "meals_per_day": 4, "allergies": "",
        "dislikes": "", "city": "Ярославль", "stores": "", "weekly_budget": 4500,
    }
    db.upsert_profile(profile)
    assert db.get_profile(10)["name"] == "Kirill"
    meal = db.add_meal({
        "telegram_user_id": 10, "eaten_on": "2026-09-07", "meal_type": "Обед",
        "name": "Рис с курицей", "grams": 400, "kcal": 600,
        "protein": 40, "fat": 15, "carbs": 75, "source": "manual",
    })
    assert meal["id"] > 0
    assert len(db.meals_for_day(10, "2026-09-07")) == 1
    db.save_weight(10, "2026-09-07", 70.5)
    assert db.weights(10)[0]["weight_kg"] == 70.5

