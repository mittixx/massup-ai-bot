from __future__ import annotations

from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


Sex = Literal["male", "female"]
Activity = Literal["low", "light", "medium", "high", "very_high"]


class ProfileInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=80)
    sex: Sex
    age: int = Field(ge=14, le=90)
    height_cm: float = Field(ge=120, le=230)
    weight_kg: float = Field(ge=35, le=300)
    target_weight_kg: float = Field(ge=35, le=300)
    activity: Activity = "medium"
    meals_per_day: int = Field(default=4, ge=2, le=8)
    allergies: str = Field(default="", max_length=500)
    dislikes: str = Field(default="", max_length=500)
    city: str = Field(default="", max_length=100)
    stores: str = Field(default="", max_length=200)
    weekly_budget: int = Field(default=4000, ge=500, le=200000)


class NutritionTargets(BaseModel):
    bmr: int
    maintenance_kcal: int
    calories: int
    protein: int
    fat: int
    carbs: int
    surplus: int


class ProfileResponse(ProfileInput):
    targets: NutritionTargets


class ManualMealInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    eaten_on: date = Field(default_factory=date.today)
    meal_type: str = Field(default="Перекус", max_length=40)
    name: str = Field(min_length=1, max_length=160)
    grams: float = Field(default=0, ge=0, le=10000)
    kcal: float = Field(ge=0, le=20000)
    protein: float = Field(ge=0, le=1000)
    fat: float = Field(ge=0, le=1000)
    carbs: float = Field(ge=0, le=2000)


class MealCorrection(BaseModel):
    telegram_user_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=160)
    grams: float = Field(ge=0, le=10000)
    kcal: float = Field(ge=0, le=20000)
    protein: float = Field(ge=0, le=1000)
    fat: float = Field(ge=0, le=1000)
    carbs: float = Field(ge=0, le=2000)


class ConfirmedPhotoMealInput(MealCorrection):
    eaten_on: date = Field(default_factory=date.today)
    meal_type: str = Field(default="Приём пищи", min_length=1, max_length=40)
    confidence: float | None = Field(default=None, ge=0, le=1)


class FavoriteInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=160)
    meal_type: str = Field(default="Приём пищи", max_length=40)
    grams: float = Field(default=0, ge=0, le=10000)
    kcal: float = Field(ge=0, le=20000)
    protein: float = Field(ge=0, le=1000)
    fat: float = Field(ge=0, le=1000)
    carbs: float = Field(ge=0, le=2000)


class FavoriteUseInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    eaten_on: date = Field(default_factory=date.today)
    meal_type: str | None = Field(default=None, max_length=40)


class WaterInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    ml: int = Field(ge=50, le=3000)
    drank_on: date = Field(default_factory=date.today)


class WorkoutInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    performed_on: date = Field(default_factory=date.today)
    name: str = Field(min_length=1, max_length=120)
    duration_minutes: int = Field(ge=1, le=600)
    notes: str = Field(default="", max_length=1000)


class BodyMeasurementInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    measured_on: date = Field(default_factory=date.today)
    chest_cm: float | None = Field(default=None, ge=20, le=300)
    waist_cm: float | None = Field(default=None, ge=20, le=300)
    hips_cm: float | None = Field(default=None, ge=20, le=300)
    arm_cm: float | None = Field(default=None, ge=10, le=150)
    thigh_cm: float | None = Field(default=None, ge=10, le=200)
    notes: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def at_least_one_measurement(self) -> "BodyMeasurementInput":
        if all(
            value is None
            for value in (self.chest_cm, self.waist_cm, self.hips_cm, self.arm_cm, self.thigh_cm)
        ):
            raise ValueError("Укажите хотя бы один замер")
        return self


class ShoppingToggleInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    checked: bool


class UserControlInput(BaseModel):
    blocked: bool
    note: str = Field(default="", max_length=300)


class BroadcastInput(BaseModel):
    message: str = Field(min_length=1, max_length=3000)
    audience: Literal["all", "active30"] = "all"
    confirmed: bool = False

    @model_validator(mode="after")
    def require_confirmation(self) -> "BroadcastInput":
        if not self.confirmed:
            raise ValueError("Подтвердите отправку рассылки")
        return self


class DeleteDataInput(BaseModel):
    confirmation: str = Field(min_length=1, max_length=20)

    @field_validator("confirmation")
    @classmethod
    def require_delete_word(cls, value: str) -> str:
        if value.strip().upper() != "УДАЛИТЬ":
            raise ValueError("Введите слово УДАЛИТЬ")
        return value


class FoodItem(BaseModel):
    name: str
    estimated_grams: float = Field(ge=0)
    kcal: float = Field(ge=0)
    protein: float = Field(ge=0)
    fat: float = Field(ge=0)
    carbs: float = Field(ge=0)


class PhotoAnalysis(BaseModel):
    dish_name: str
    items: list[FoodItem]
    total_grams: float = Field(ge=0)
    total_kcal: float = Field(ge=0)
    total_protein: float = Field(ge=0)
    total_fat: float = Field(ge=0)
    total_carbs: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    assumptions: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)


class MealPlanItem(BaseModel):
    meal_type: str
    dish: str
    ingredients: list[str]
    kcal: int = Field(ge=0)
    protein: int = Field(ge=0)
    fat: int = Field(ge=0)
    carbs: int = Field(ge=0)
    recipe: str


class DayPlan(BaseModel):
    day: str
    meals: list[MealPlanItem]
    total_kcal: int = Field(ge=0)
    total_protein: int = Field(ge=0)
    total_fat: int = Field(ge=0)
    total_carbs: int = Field(ge=0)


class GroceryItem(BaseModel):
    category: str
    name: str
    quantity: str
    estimated_price: int = Field(ge=0)


class WeekPlan(BaseModel):
    title: str
    currency: str = "RUB"
    budget: int = Field(ge=0)
    estimated_total: int = Field(ge=0)
    days: list[DayPlan]
    groceries: list[GroceryItem]
    prep_plan: list[str]
    substitutions: list[str]
    notes: list[str]

    @model_validator(mode="after")
    def validate_days(self) -> "WeekPlan":
        if len(self.days) != 7:
            raise ValueError("План должен содержать ровно 7 дней")
        return self


class PlanRequest(BaseModel):
    telegram_user_id: int = Field(gt=0)
    budget: int = Field(ge=500, le=200000)
    pantry: str = Field(default="", max_length=1000)
    wishes: str = Field(default="", max_length=1000)


class WeightInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    measured_on: date = Field(default_factory=date.today)
    weight_kg: float = Field(ge=35, le=300)


AdviceMode = Literal["top_up", "recipe", "swap", "portion", "coach", "review"]


class AdviceRequest(BaseModel):
    telegram_user_id: int = Field(gt=0)
    mode: AdviceMode
    query: str = Field(default="", max_length=1200)


class AdviceResult(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=700)
    recommendations: list[str] = Field(min_length=1, max_length=8)
    note: str = Field(default="", max_length=500)


class NutritionLabelAnalysis(BaseModel):
    product_name: str = Field(min_length=1, max_length=160)
    serving: str = Field(default="Не указано", max_length=100)
    kcal_per_100g: float | None = Field(default=None, ge=0, le=2000)
    protein_per_100g: float | None = Field(default=None, ge=0, le=100)
    fat_per_100g: float | None = Field(default=None, ge=0, le=100)
    carbs_per_100g: float | None = Field(default=None, ge=0, le=100)
    ingredients: list[str] = Field(default_factory=list, max_length=30)
    allergens: list[str] = Field(default_factory=list, max_length=20)
    notes: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)


class ReminderSettingsInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    enabled: bool = False
    meal_reminders: bool = True
    weigh_reminder: bool = True
    weekly_report: bool = True
    breakfast_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    lunch_time: str = Field(default="14:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    evening_time: str = Field(default="20:30", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weigh_weekday: int = Field(default=0, ge=0, le=6)
    weigh_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weekly_time: str = Field(default="19:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=80)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Неизвестный часовой пояс") from exc
        return value
