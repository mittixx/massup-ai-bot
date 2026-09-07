from __future__ import annotations

from datetime import date

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.ai_service import AIUnavailableError
from app.auth import current_user_id
from app.nutrition import calculate_targets, percent
from app.schemas import (
    ManualMealInput,
    MealCorrection,
    PlanRequest,
    ProfileInput,
    ProfileResponse,
    WeightInput,
)


router = APIRouter(prefix="/api")


def _uid(request: Request) -> int:
    return current_user_id(request, request.app.state.settings)


def _check_payload_user(request: Request, payload_user_id: int) -> int:
    user_id = _uid(request)
    if payload_user_id != user_id:
        raise HTTPException(status_code=403, detail="Нельзя изменять данные другого пользователя")
    return user_id


@router.get("/profile")
def get_profile(request: Request):
    user_id = _uid(request)
    record = request.app.state.db.get_profile(user_id)
    if not record:
        raise HTTPException(status_code=404, detail="Профиль ещё не заполнен")
    profile = ProfileInput(**record)
    return ProfileResponse(**profile.model_dump(), targets=calculate_targets(profile))


@router.post("/profile", response_model=ProfileResponse)
def save_profile(request: Request, payload: ProfileInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    data = payload.model_dump()
    data["telegram_user_id"] = user_id
    record = request.app.state.db.upsert_profile(data)
    profile = ProfileInput(**record)
    request.app.state.db.save_weight(user_id, str(date.today()), profile.weight_kg)
    return ProfileResponse(**profile.model_dump(), targets=calculate_targets(profile))


@router.get("/dashboard")
def dashboard(request: Request, day: date = date.today()):
    user_id = _uid(request)
    record = request.app.state.db.get_profile(user_id)
    if not record:
        raise HTTPException(status_code=404, detail="Профиль ещё не заполнен")
    profile = ProfileInput(**record)
    targets = calculate_targets(profile)
    meals = request.app.state.db.meals_for_day(user_id, str(day))
    totals = {
        key: round(sum(float(meal[key]) for meal in meals), 1)
        for key in ("kcal", "protein", "fat", "carbs")
    }
    return {
        "date": str(day),
        "profile": ProfileResponse(**profile.model_dump(), targets=targets),
        "totals": totals,
        "progress": {
            "kcal": percent(totals["kcal"], targets.calories),
            "protein": percent(totals["protein"], targets.protein),
            "fat": percent(totals["fat"], targets.fat),
            "carbs": percent(totals["carbs"], targets.carbs),
        },
        "remaining": {
            "kcal": max(0, round(targets.calories - totals["kcal"])),
            "protein": max(0, round(targets.protein - totals["protein"])),
            "fat": max(0, round(targets.fat - totals["fat"])),
            "carbs": max(0, round(targets.carbs - totals["carbs"])),
        },
        "meals": meals,
        "latest_plan": request.app.state.db.latest_plan(user_id),
    }


@router.post("/meals/manual")
def add_manual_meal(request: Request, payload: ManualMealInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    return request.app.state.db.add_meal({
        **payload.model_dump(), "telegram_user_id": user_id, "source": "manual"
    })


@router.post("/meals/photo")
async def add_photo_meal(
    request: Request,
    telegram_user_id: int = Form(...),
    meal_type: str = Form("Приём пищи"),
    image: UploadFile = File(...),
):
    user_id = _check_payload_user(request, telegram_user_id)
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=415, detail="Нужна фотография JPG, PNG или WEBP")
    content = await image.read()
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Фотография должна быть меньше 12 МБ")
    try:
        analysis = await request.app.state.ai.analyze_photo(content, image.content_type or "image/jpeg")
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось проанализировать фото: {exc}") from exc

    meal = request.app.state.db.add_meal({
        "telegram_user_id": user_id,
        "eaten_on": date.today(),
        "meal_type": meal_type,
        "name": analysis.dish_name,
        "grams": analysis.total_grams,
        "kcal": analysis.total_kcal,
        "protein": analysis.total_protein,
        "fat": analysis.total_fat,
        "carbs": analysis.total_carbs,
        "source": "photo",
        "confidence": analysis.confidence,
        "details": analysis.model_dump(),
    })
    return {"meal": meal, "analysis": analysis}


@router.patch("/meals/{meal_id}")
def correct_meal(request: Request, meal_id: int, payload: MealCorrection):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    meal = request.app.state.db.update_meal(meal_id, user_id, payload.model_dump())
    if not meal:
        raise HTTPException(status_code=404, detail="Приём пищи не найден")
    return meal


@router.delete("/meals/{meal_id}")
def remove_meal(request: Request, meal_id: int):
    user_id = _uid(request)
    if not request.app.state.db.delete_meal(meal_id, user_id):
        raise HTTPException(status_code=404, detail="Приём пищи не найден")
    return {"ok": True}


@router.post("/plan")
async def create_plan(request: Request, payload: PlanRequest):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    record = request.app.state.db.get_profile(user_id)
    if not record:
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    profile = ProfileInput(**record)
    targets = calculate_targets(profile).model_dump()
    try:
        plan = await request.app.state.ai.make_week_plan(
            profile, targets, payload.budget, payload.pantry, payload.wishes
        )
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось составить план: {exc}") from exc
    request.app.state.db.save_plan(user_id, payload.budget, plan.model_dump())
    return plan


@router.post("/weight")
def add_weight(request: Request, payload: WeightInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    request.app.state.db.save_weight(user_id, str(payload.measured_on), payload.weight_kg)
    return {"ok": True}


@router.get("/progress")
def progress(request: Request):
    return {"weights": request.app.state.db.weights(_uid(request))}
