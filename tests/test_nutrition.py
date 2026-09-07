from app.nutrition import calculate_targets, percent
from app.schemas import ProfileInput


def profile(**overrides):
    data = {
        "telegram_user_id": 1,
        "name": "Test",
        "sex": "male",
        "age": 25,
        "height_cm": 175,
        "weight_kg": 65,
        "target_weight_kg": 72,
        "activity": "medium",
        "meals_per_day": 4,
        "weekly_budget": 4000,
    }
    data.update(overrides)
    return ProfileInput(**data)


def test_targets_for_weight_gain():
    result = calculate_targets(profile())
    assert result.bmr == 1624
    assert result.maintenance_kcal == 2517
    assert result.calories == 2817
    assert result.protein == 117
    assert result.fat == 58
    assert result.carbs == 456
    assert result.surplus == 300


def test_no_surplus_when_target_reached():
    result = calculate_targets(profile(target_weight_kg=60))
    assert result.surplus == 0
    assert result.calories == result.maintenance_kcal


def test_percent_is_clamped():
    assert percent(50, 100) == 50
    assert percent(120, 100) == 100
    assert percent(-10, 100) == 0
