from app.database import Database
import sqlite3


def profile(user_id, name):
    return {
        "telegram_user_id": user_id, "name": name, "sex": "male", "age": 20,
        "height_cm": 180, "weight_kg": 70, "target_weight_kg": 78,
        "activity": "medium", "meals_per_day": 4, "allergies": "",
        "dislikes": "", "city": "Ярославль", "stores": "", "weekly_budget": 4500,
    }


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
    backup_path = tmp_path / "backup.db"
    db.backup_to(str(backup_path))
    with sqlite3.connect(backup_path) as snapshot:
        assert snapshot.execute("SELECT name FROM profiles WHERE telegram_user_id=10").fetchone()[0] == "Kirill"
        assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_restore_validates_and_replaces_database(tmp_path):
    live = Database(str(tmp_path / "live.db"))
    live.initialize()
    live.upsert_profile(profile(1, "Old"))
    source = Database(str(tmp_path / "source.db"))
    source.initialize()
    source.upsert_profile(profile(2, "Restored"))

    live.restore_from(source.path)
    assert live.get_profile(1) is None
    assert live.get_profile(2)["name"] == "Restored"

    invalid = tmp_path / "invalid.db"
    with sqlite3.connect(invalid) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    try:
        live.restore_from(str(invalid))
    except ValueError:
        pass
    else:
        raise AssertionError("invalid database must be rejected")
    assert live.get_profile(2)["name"] == "Restored"


def test_admin_events_and_user_aggregates(tmp_path):
    db = Database(str(tmp_path / "admin.db"))
    db.initialize()
    db.upsert_profile(profile(10, "Kirill"))
    db.record_event(10, "ai_photo")
    db.record_event(10, "ai_plan", "error", "TimeoutError")
    db.record_event(20, "bot_start")

    overview = db.admin_overview()
    assert overview["metrics"]["total_users"] == 2
    assert overview["metrics"]["profiles"] == 1
    assert overview["metrics"]["ai_requests_7d"] == 2
    assert overview["metrics"]["ai_errors_7d"] == 1
    assert len(overview["trend"]) == 14
    assert overview["recent_events"][0]["detail"] == "TimeoutError" or overview["recent_events"][1]["detail"] == "TimeoutError"

    users = db.admin_users()
    assert users["total"] == 2
    by_id = {item["telegram_user_id"]: item for item in users["items"]}
    assert by_id[10]["ai_requests"] == 2
    assert by_id[20]["name"] == "Без профиля"
    assert db.admin_users(search="Kir")["total"] == 1
