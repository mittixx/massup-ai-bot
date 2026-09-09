from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MACROS = ("kcal", "protein", "fat", "carbs")


def meal_totals(meals: list[dict[str, Any]]) -> dict[str, float]:
    return {
        key: round(sum(float(meal.get(key, 0)) for meal in meals), 1)
        for key in MACROS
    }


def current_streak(meals: list[dict[str, Any]], today: date) -> int:
    logged = {date.fromisoformat(str(meal["eaten_on"])) for meal in meals}
    cursor = today if today in logged else today - timedelta(days=1)
    streak = 0
    while cursor in logged:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def build_insights(
    profile: dict[str, Any],
    targets: dict[str, Any],
    meals: list[dict[str, Any]],
    weights: list[dict[str, Any]],
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    week_start = today - timedelta(days=6)
    weekly_meals = [
        meal for meal in meals
        if week_start <= date.fromisoformat(str(meal["eaten_on"])) <= today
    ]
    logged_days = sorted({str(meal["eaten_on"]) for meal in weekly_meals})
    per_day: dict[str, list[dict[str, Any]]] = {}
    for meal in weekly_meals:
        per_day.setdefault(str(meal["eaten_on"]), []).append(meal)
    daily = [meal_totals(items) for items in per_day.values()]
    averages = {
        key: round(sum(item[key] for item in daily) / len(daily), 1) if daily else 0
        for key in MACROS
    }
    target_days = sum(
        1 for item in daily
        if targets["calories"] * 0.85 <= item["kcal"] <= targets["calories"] * 1.15
    )

    ordered_weights = sorted(weights, key=lambda item: str(item["measured_on"]))
    weekly_weights = [
        item for item in ordered_weights
        if date.fromisoformat(str(item["measured_on"])) >= week_start
    ]
    weight_change = 0.0
    if len(weekly_weights) >= 2:
        weight_change = round(
            float(weekly_weights[-1]["weight_kg"]) - float(weekly_weights[0]["weight_kg"]), 2
        )

    streak = current_streak(meals, today)
    achievements = []
    if meals:
        achievements.append({"code": "first_meal", "title": "Первый шаг", "text": "Первый приём пищи записан"})
    for days in (3, 7, 30):
        if streak >= days:
            achievements.append({"code": f"streak_{days}", "title": f"Серия {days} дней", "text": "Дневник ведётся без пропусков"})
    if len(ordered_weights) >= 5:
        achievements.append({"code": "weights_5", "title": "Контроль прогресса", "text": "Записано 5 взвешиваний"})
    if len(ordered_weights) >= 2 and float(ordered_weights[-1]["weight_kg"]) - float(ordered_weights[0]["weight_kg"]) >= 1:
        achievements.append({"code": "gain_1kg", "title": "+1 килограмм", "text": "Первый килограмм к цели набран"})

    forecast = {
        "status": "insufficient_data",
        "weekly_rate": 0.0,
        "estimated_date": None,
        "message": "Нужно минимум два взвешивания в разные дни.",
    }
    if len(ordered_weights) >= 2:
        first, last = ordered_weights[0], ordered_weights[-1]
        first_day = date.fromisoformat(str(first["measured_on"]))
        last_day = date.fromisoformat(str(last["measured_on"]))
        elapsed = (last_day - first_day).days
        current = float(last["weight_kg"])
        target = float(profile["target_weight_kg"])
        if (target - current) <= 0:
            forecast.update(status="achieved", message="Цель по весу уже достигнута.")
        elif elapsed > 0:
            weekly_rate = round((current - float(first["weight_kg"])) / elapsed * 7, 2)
            forecast["weekly_rate"] = weekly_rate
            if weekly_rate > 0.03:
                days_left = round((target - current) / (weekly_rate / 7))
                if 0 < days_left <= 730:
                    estimated = last_day + timedelta(days=days_left)
                    forecast.update(
                        status="on_track",
                        estimated_date=str(estimated),
                        message=f"При текущем темпе цель ориентировочно будет достигнута {estimated.strftime('%d.%m.%Y')}.",
                    )
                else:
                    forecast.update(status="slow", message="Текущий темп слишком мал для надёжного прогноза.")
            else:
                forecast.update(status="not_growing", message="Устойчивый рост веса пока не определяется.")

    return {
        "streak_days": streak,
        "achievements": achievements,
        "week": {
            "start": str(week_start),
            "end": str(today),
            "days_logged": len(logged_days),
            "days_on_target": target_days,
            "averages": averages,
            "weight_change_kg": weight_change,
        },
        "forecast": forecast,
    }


def due_reminder_kinds(settings: dict[str, Any], now: datetime | None = None) -> tuple[date, list[str]]:
    now = now or datetime.now(UTC)
    try:
        local = now.astimezone(ZoneInfo(settings.get("timezone") or "Europe/Moscow"))
    except ZoneInfoNotFoundError:
        local = now.astimezone(UTC)
    current_minutes = local.hour * 60 + local.minute

    def due(value: str, window: int = 10) -> bool:
        hour, minute = (int(part) for part in value.split(":"))
        target = hour * 60 + minute
        return target <= current_minutes < target + window

    kinds: list[str] = []
    if settings.get("enabled"):
        if settings.get("meal_reminders"):
            for kind, field in (
                ("breakfast", "breakfast_time"),
                ("lunch", "lunch_time"),
                ("evening", "evening_time"),
            ):
                if due(str(settings[field])):
                    kinds.append(kind)
        if settings.get("weigh_reminder") and local.weekday() == int(settings["weigh_weekday"]):
            if due(str(settings["weigh_time"])):
                kinds.append("weigh")
        if settings.get("weekly_report") and local.weekday() == 6 and due(str(settings["weekly_time"]), 30):
            kinds.append("weekly")
    return local.date(), kinds
