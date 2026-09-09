from datetime import UTC, date, datetime, timedelta

from app.database import Database
from app.insights import build_insights, due_reminder_kinds


def profile(user_id=1):
    return {
        "telegram_user_id": user_id, "name": "Test", "sex": "male", "age": 25,
        "height_cm": 180, "weight_kg": 72, "target_weight_kg": 78,
        "activity": "medium", "meals_per_day": 4, "allergies": "",
        "dislikes": "", "city": "", "stores": "", "weekly_budget": 4500,
    }


def test_insights_streak_achievements_and_forecast():
    today = date(2026, 9, 10)
    meals = [
        {
            "eaten_on": str(today - timedelta(days=offset)),
            "kcal": 2700,
            "protein": 130,
            "fat": 70,
            "carbs": 350,
        }
        for offset in range(4)
    ]
    weights = [
        {"measured_on": "2026-08-27", "weight_kg": 70.0},
        {"measured_on": "2026-09-10", "weight_kg": 71.0},
    ]
    result = build_insights(
        profile(),
        {"calories": 2700, "protein": 130, "fat": 70, "carbs": 350},
        meals,
        weights,
        today,
    )
    assert result["streak_days"] == 4
    assert result["week"]["days_logged"] == 4
    assert result["week"]["days_on_target"] == 4
    assert result["forecast"]["status"] == "on_track"
    assert result["forecast"]["weekly_rate"] == 0.5
    assert {item["code"] for item in result["achievements"]} >= {"first_meal", "streak_3"}


def test_reminder_settings_migrate_and_claim_once(tmp_path):
    db = Database(str(tmp_path / "features.db"))
    db.initialize()
    db.upsert_profile(profile())
    defaults = db.get_reminders(1)
    assert defaults["enabled"] is False
    defaults.update(enabled=True, timezone="UTC", breakfast_time="08:00")
    saved = db.save_reminders(defaults)
    assert saved["enabled"] is True
    assert db.enabled_reminders()[0]["breakfast_time"] == "08:00"
    assert db.claim_reminder(1, "breakfast", "2026-09-10") is True
    assert db.claim_reminder(1, "breakfast", "2026-09-10") is False


def test_existing_database_is_migrated_without_losing_profile(tmp_path):
    db = Database(str(tmp_path / "old-release.db"))
    db.initialize()
    db.upsert_profile(profile())
    with db.connect() as connection:
        connection.execute("DROP TABLE reminder_log")
        connection.execute("DROP TABLE reminder_settings")

    db.initialize()
    assert db.get_profile(1)["name"] == "Test"
    assert db.get_reminders(1)["enabled"] is False


def test_due_reminders_use_timezone_and_window():
    settings = Database.default_reminders(1)
    settings.update(
        enabled=True,
        timezone="UTC",
        breakfast_time="09:00",
        lunch_time="14:00",
        evening_time="20:30",
        weigh_weekday=3,
        weigh_time="09:00",
        weekly_time="19:00",
    )
    local_day, kinds = due_reminder_kinds(
        settings, datetime(2026, 9, 10, 9, 4, tzinfo=UTC)
    )
    assert local_day == date(2026, 9, 10)
    assert kinds == ["breakfast", "weigh"]
    assert due_reminder_kinds(settings, datetime(2026, 9, 10, 9, 15, tzinfo=UTC))[1] == []
