from __future__ import annotations

from app.schemas import NutritionTargets, ProfileInput


ACTIVITY_FACTORS = {
    "low": 1.2,
    "light": 1.375,
    "medium": 1.55,
    "high": 1.725,
    "very_high": 1.9,
}


def calculate_targets(profile: ProfileInput) -> NutritionTargets:
    """Mifflin–St Jeor + умеренный профицит для плавного набора веса."""
    sex_offset = 5 if profile.sex == "male" else -161
    bmr = 10 * profile.weight_kg + 6.25 * profile.height_cm - 5 * profile.age + sex_offset
    maintenance = bmr * ACTIVITY_FACTORS[profile.activity]

    # Для уже достигнутой/превышенной цели не добавляем профицит автоматически.
    surplus = 300 if profile.target_weight_kg > profile.weight_kg else 0
    calories = maintenance + surplus
    protein = profile.weight_kg * 1.8
    fat = profile.weight_kg * 0.9
    carbs = max(0, (calories - protein * 4 - fat * 9) / 4)

    return NutritionTargets(
        bmr=round(bmr),
        maintenance_kcal=round(maintenance),
        calories=round(calories),
        protein=round(protein),
        fat=round(fat),
        carbs=round(carbs),
        surplus=surplus,
    )


def percent(value: float, target: float) -> int:
    if target <= 0:
        return 0
    return max(0, min(100, round(value / target * 100)))

