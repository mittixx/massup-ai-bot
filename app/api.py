from __future__ import annotations

import asyncio
from datetime import date, timedelta
import logging

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from app.ai_service import AIUnavailableError
from app.auth import current_owner_id, current_user_id
from app.insights import build_insights, meal_totals
from app.nutrition import calculate_targets, percent
from app.schemas import (
    AdviceRequest,
    BodyMeasurementInput,
    BroadcastInput,
    ConfirmedPhotoMealInput,
    DeleteDataInput,
    FavoriteInput,
    FavoriteUseInput,
    ManualMealInput,
    MealCorrection,
    PlanRequest,
    ProfileInput,
    ProfileResponse,
    ReminderSettingsInput,
    ShoppingToggleInput,
    UserControlInput,
    WaterInput,
    WeightInput,
    WorkoutInput,
)


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api")


def _uid(request: Request) -> int:
    user_id = current_user_id(request, request.app.state.settings)
    settings = request.app.state.settings
    if user_id != settings.owner_telegram_id and request.app.state.db.is_blocked(user_id):
        raise HTTPException(status_code=403, detail="Доступ к боту ограничен")
    return user_id


def _admin_uid(request: Request) -> int:
    return current_owner_id(request, request.app.state.settings)


def _record_event(
    request: Request,
    user_id: int | None,
    event_type: str,
    status: str = "ok",
    detail: str = "",
) -> None:
    try:
        request.app.state.db.record_event(user_id, event_type, status, detail)
    except Exception as exc:
        logger.warning("ADMIN_EVENT_WRITE_FAILED type=%s", type(exc).__name__)


def _limit(request: Request, user_id: int, bucket: str, limit: int, window: int) -> None:
    if user_id == request.app.state.settings.owner_telegram_id:
        return
    if not request.app.state.rate_limiter.allow(user_id, bucket, limit, window):
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов подряд. Подождите немного и повторите.",
        )


def _check_payload_user(request: Request, payload_user_id: int) -> int:
    user_id = _uid(request)
    if payload_user_id != user_id:
        raise HTTPException(status_code=403, detail="Нельзя изменять данные другого пользователя")
    return user_id


def _profile_and_context(request: Request, user_id: int):
    record = request.app.state.db.get_profile(user_id)
    if not record:
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    profile = ProfileInput(**record)
    targets = calculate_targets(profile)
    meals = request.app.state.db.meals_for_day(user_id, str(date.today()))
    totals = meal_totals(meals)
    remaining = {
        key: max(0, round(getattr(targets, key if key != "kcal" else "calories") - totals[key], 1))
        for key in ("kcal", "protein", "fat", "carbs")
    }
    return profile, targets, totals, remaining


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
    _record_event(request, user_id, "profile_saved")
    return ProfileResponse(**profile.model_dump(), targets=calculate_targets(profile))


@router.get("/dashboard")
def dashboard(request: Request, day: date | None = None):
    day = day or date.today()
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
    meal = request.app.state.db.add_meal({
        **payload.model_dump(), "telegram_user_id": user_id, "source": "manual"
    })
    _record_event(request, user_id, "meal_manual")
    return meal


@router.post("/meals/photo")
async def add_photo_meal(
    request: Request,
    telegram_user_id: int = Form(...),
    meal_type: str = Form("Приём пищи"),
    save: bool = Form(True),
    image: UploadFile = File(...),
):
    user_id = _check_payload_user(request, telegram_user_id)
    _limit(request, user_id, "ai", 20, 600)
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=415, detail="Нужна фотография JPG, PNG или WEBP")
    content = await image.read(12 * 1024 * 1024 + 1)
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Фотография должна быть меньше 12 МБ")
    try:
        analysis = await request.app.state.ai.analyze_photo(content, image.content_type or "image/jpeg")
    except AIUnavailableError as exc:
        _record_event(request, user_id, "ai_photo", "error", "AIUnavailableError")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        _record_event(request, user_id, "ai_photo", "error", type(exc).__name__)
        logger.error("Unexpected photo analysis error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Не удалось проанализировать фото. Повторите позже.",
        ) from exc

    if not save:
        _record_event(request, user_id, "ai_photo")
        return {"meal": None, "analysis": analysis}
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
    _record_event(request, user_id, "ai_photo")
    return {"meal": meal, "analysis": analysis}


@router.post("/meals/photo/confirm")
def confirm_photo_meal(request: Request, payload: ConfirmedPhotoMealInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    meal = request.app.state.db.add_meal({
        **payload.model_dump(),
        "telegram_user_id": user_id,
        "source": "photo_confirmed",
        "details": {"confirmed_by_user": True},
    })
    _record_event(request, user_id, "meal_photo_confirmed")
    return meal


@router.patch("/meals/{meal_id}")
def correct_meal(request: Request, meal_id: int, payload: MealCorrection):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    meal = request.app.state.db.update_meal(meal_id, user_id, payload.model_dump())
    if not meal:
        raise HTTPException(status_code=404, detail="Приём пищи не найден")
    _record_event(request, user_id, "meal_corrected")
    return meal


@router.delete("/meals/{meal_id}")
def remove_meal(request: Request, meal_id: int):
    user_id = _uid(request)
    if not request.app.state.db.delete_meal(meal_id, user_id):
        raise HTTPException(status_code=404, detail="Приём пищи не найден")
    _record_event(request, user_id, "meal_deleted")
    return {"ok": True}


@router.post("/plan")
async def create_plan(request: Request, payload: PlanRequest):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    _limit(request, user_id, "ai", 20, 600)
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
        _record_event(request, user_id, "ai_plan", "error", "AIUnavailableError")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        _record_event(request, user_id, "ai_plan", "error", type(exc).__name__)
        logger.error("Unexpected week plan error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Не удалось составить план. Повторите позже.",
        ) from exc
    request.app.state.db.save_plan(user_id, payload.budget, plan.model_dump())
    _record_event(request, user_id, "ai_plan")
    return plan


@router.post("/weight")
def add_weight(request: Request, payload: WeightInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    if payload.measured_on > date.today():
        raise HTTPException(status_code=422, detail="Дата взвешивания не может быть в будущем")
    request.app.state.db.save_weight(user_id, str(payload.measured_on), payload.weight_kg)
    _record_event(request, user_id, "weight_saved")
    return {
        "ok": True,
        "weight": {
            "measured_on": str(payload.measured_on),
            "weight_kg": payload.weight_kg,
        },
    }


@router.get("/progress")
def progress(request: Request):
    return {"weights": request.app.state.db.weights(_uid(request))}


@router.get("/insights")
def insights(request: Request):
    user_id = _uid(request)
    record = request.app.state.db.get_profile(user_id)
    if not record:
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    profile = ProfileInput(**record)
    targets = calculate_targets(profile).model_dump()
    today = date.today()
    meals = request.app.state.db.meals_between(
        user_id, str(today - timedelta(days=365)), str(today)
    )
    weights = request.app.state.db.weights(user_id, 365)
    return build_insights(record, targets, meals, weights, today)


@router.post("/advice")
async def advice(request: Request, payload: AdviceRequest):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    _limit(request, user_id, "ai", 20, 600)
    if payload.mode in {"recipe", "swap", "portion"} and not payload.query.strip():
        raise HTTPException(status_code=422, detail="Опишите продукты или блюдо")
    profile, targets, totals, remaining = _profile_and_context(request, user_id)
    try:
        result = await request.app.state.ai.make_advice(
            profile,
            targets.model_dump(),
            totals,
            remaining,
            request.app.state.db.weights(user_id, 90),
            payload.mode,
            payload.query.strip(),
        )
    except AIUnavailableError as exc:
        _record_event(request, user_id, "ai_advice", "error", "AIUnavailableError")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        _record_event(request, user_id, "ai_advice", "error", type(exc).__name__)
        logger.error("Unexpected advice error: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Не удалось подготовить рекомендацию. Повторите позже.") from exc
    _record_event(request, user_id, "ai_advice", detail=payload.mode)
    return result


@router.post("/label")
async def label_analysis(request: Request, image: UploadFile = File(...)):
    user_id = _uid(request)
    _limit(request, user_id, "ai", 20, 600)
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=415, detail="Нужна фотография JPG, PNG или WEBP")
    content = await image.read(12 * 1024 * 1024 + 1)
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Фотография должна быть меньше 12 МБ")
    try:
        result = await request.app.state.ai.analyze_label(
            content, image.content_type or "image/jpeg"
        )
    except AIUnavailableError as exc:
        _record_event(request, user_id, "ai_label", "error", "AIUnavailableError")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        _record_event(request, user_id, "ai_label", "error", type(exc).__name__)
        logger.error("Unexpected label analysis error: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Не удалось прочитать этикетку. Повторите позже.") from exc
    _record_event(request, user_id, "ai_label")
    return result


@router.get("/reminders")
def get_reminders(request: Request):
    return request.app.state.db.get_reminders(_uid(request))


@router.put("/reminders")
def save_reminders(request: Request, payload: ReminderSettingsInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    data = payload.model_dump()
    data["telegram_user_id"] = user_id
    result = request.app.state.db.save_reminders(data)
    _record_event(request, user_id, "reminders_saved", detail="enabled" if payload.enabled else "disabled")
    return result


@router.get("/admin/session")
def admin_session(request: Request):
    owner_id = _admin_uid(request)
    return {"is_owner": True, "owner_telegram_id": owner_id}


@router.get("/admin/overview")
def admin_overview(request: Request):
    _admin_uid(request)
    result = request.app.state.db.admin_overview()
    settings = request.app.state.settings
    bot_task = getattr(request.app.state, "bot_task", None)
    result["runtime"] = {
        "version": getattr(request.app.state, "version", "unknown"),
        "bot_polling": "active" if bot_task and not bot_task.done() else "stopped" if bot_task else "disabled",
        "ai_configured": bool(settings.openai_api_key),
        "public_access": settings.public_access,
        "port": settings.port,
    }
    return result


@router.get("/admin/users")
def admin_users(
    request: Request,
    search: str = "",
    limit: int = 50,
    offset: int = 0,
):
    _admin_uid(request)
    return request.app.state.db.admin_users(search=search, limit=limit, offset=offset)


@router.get("/favorites")
def favorites(request: Request):
    return {"items": request.app.state.db.favorites(_uid(request))}


@router.post("/favorites")
def save_favorite(request: Request, payload: FavoriteInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    result = request.app.state.db.save_favorite(user_id, payload.model_dump())
    _record_event(request, user_id, "favorite_saved")
    return result


@router.post("/favorites/{favorite_id}/use")
def use_favorite(request: Request, favorite_id: int, payload: FavoriteUseInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    favorite = request.app.state.db.favorite(user_id, favorite_id)
    if not favorite:
        raise HTTPException(status_code=404, detail="Избранное блюдо не найдено")
    meal = request.app.state.db.add_meal({
        **favorite,
        "telegram_user_id": user_id,
        "eaten_on": payload.eaten_on,
        "meal_type": payload.meal_type or favorite["meal_type"],
        "source": "favorite",
    })
    _record_event(request, user_id, "favorite_used")
    return meal


@router.delete("/favorites/{favorite_id}")
def remove_favorite(request: Request, favorite_id: int):
    user_id = _uid(request)
    if not request.app.state.db.delete_favorite(user_id, favorite_id):
        raise HTTPException(status_code=404, detail="Избранное блюдо не найдено")
    return {"ok": True}


@router.get("/water")
def water(request: Request, day: date | None = None):
    user_id = _uid(request)
    profile = request.app.state.db.get_profile(user_id)
    if not profile:
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    result = request.app.state.db.water_for_day(user_id, str(day or date.today()))
    result["target_ml"] = max(1500, min(4000, round(float(profile["weight_kg"]) * 35 / 50) * 50))
    return result


@router.post("/water")
def add_water(request: Request, payload: WaterInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    if payload.drank_on > date.today():
        raise HTTPException(status_code=422, detail="Дата не может быть в будущем")
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    result = request.app.state.db.add_water(user_id, str(payload.drank_on), payload.ml)
    _record_event(request, user_id, "water_added", detail=str(payload.ml))
    return result


@router.delete("/water/{entry_id}")
def remove_water(request: Request, entry_id: int):
    user_id = _uid(request)
    if not request.app.state.db.delete_water(user_id, entry_id):
        raise HTTPException(status_code=404, detail="Запись воды не найдена")
    return {"ok": True}


@router.get("/workouts")
def workouts(request: Request):
    return {"items": request.app.state.db.workouts(_uid(request))}


@router.post("/workouts")
def add_workout(request: Request, payload: WorkoutInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    if payload.performed_on > date.today():
        raise HTTPException(status_code=422, detail="Дата тренировки не может быть в будущем")
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    result = request.app.state.db.add_workout(user_id, payload.model_dump())
    _record_event(request, user_id, "workout_added")
    return result


@router.delete("/workouts/{workout_id}")
def remove_workout(request: Request, workout_id: int):
    user_id = _uid(request)
    if not request.app.state.db.delete_workout(user_id, workout_id):
        raise HTTPException(status_code=404, detail="Тренировка не найдена")
    return {"ok": True}


@router.get("/measurements")
def measurements(request: Request):
    return {
        "items": request.app.state.db.measurements(_uid(request)),
    }


@router.post("/measurements")
def save_measurement(request: Request, payload: BodyMeasurementInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    if payload.measured_on > date.today():
        raise HTTPException(status_code=422, detail="Дата замера не может быть в будущем")
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    result = request.app.state.db.save_measurement(user_id, payload.model_dump())
    _record_event(request, user_id, "measurement_saved")
    return result


@router.get("/progress-photos")
def progress_photos(request: Request):
    return {"items": request.app.state.db.progress_photos(_uid(request))}


@router.post("/progress-photos")
async def add_progress_photo(
    request: Request,
    telegram_user_id: int = Form(...),
    taken_on: date | None = Form(None),
    image: UploadFile = File(...),
):
    user_id = _check_payload_user(request, telegram_user_id)
    taken_on = taken_on or date.today()
    if not request.app.state.db.get_profile(user_id):
        raise HTTPException(status_code=409, detail="Сначала заполните профиль")
    if len(request.app.state.db.progress_photos(user_id, 50)) >= 12:
        raise HTTPException(
            status_code=409,
            detail="Можно хранить до 12 фотографий. Удалите старую фотографию.",
        )
    if taken_on > date.today():
        raise HTTPException(status_code=422, detail="Дата фотографии не может быть в будущем")
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=415, detail="Нужна фотография JPG, PNG или WEBP")
    content = await image.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Фотография должна быть меньше 2 МБ")
    result = request.app.state.db.add_progress_photo(
        user_id, str(taken_on), image.content_type or "image/jpeg", content
    )
    _record_event(request, user_id, "progress_photo_added")
    return result


@router.get("/progress-photos/{photo_id}")
def progress_photo(request: Request, photo_id: int):
    user_id = _uid(request)
    item = request.app.state.db.progress_photo(user_id, photo_id)
    if not item:
        raise HTTPException(status_code=404, detail="Фотография не найдена")
    return Response(
        content=item["image"],
        media_type=item["mime_type"],
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.delete("/progress-photos/{photo_id}")
def remove_progress_photo(request: Request, photo_id: int):
    user_id = _uid(request)
    if not request.app.state.db.delete_progress_photo(user_id, photo_id):
        raise HTTPException(status_code=404, detail="Фотография не найдена")
    return {"ok": True}


@router.get("/shopping")
def shopping(request: Request):
    return request.app.state.db.shopping_list(_uid(request))


@router.patch("/shopping/{item_id}")
def toggle_shopping(request: Request, item_id: int, payload: ShoppingToggleInput):
    user_id = _check_payload_user(request, payload.telegram_user_id)
    if not request.app.state.db.toggle_shopping_item(user_id, item_id, payload.checked):
        raise HTTPException(status_code=404, detail="Позиция списка не найдена")
    return request.app.state.db.shopping_list(user_id)


@router.post("/feedback")
async def submit_feedback(
    request: Request,
    message: str = Form(..., min_length=5, max_length=1500),
    screenshot: UploadFile | None = File(None),
):
    user_id = _uid(request)
    content = None
    mime_type = None
    if screenshot and screenshot.filename:
        if screenshot.content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise HTTPException(status_code=415, detail="Скриншот должен быть JPG, PNG или WEBP")
        content = await screenshot.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Скриншот должен быть меньше 2 МБ")
        mime_type = screenshot.content_type
    result = request.app.state.db.save_feedback(
        user_id, message.strip(), getattr(request.app.state, "version", "unknown"), mime_type, content
    )
    _record_event(request, user_id, "feedback_sent")
    return result


@router.delete("/account")
def delete_account(request: Request, payload: DeleteDataInput):
    user_id = _uid(request)
    request.app.state.db.delete_user_data(user_id)
    return {"ok": True, "message": "Все ваши данные удалены"}


@router.get("/barcode/{barcode}")
async def barcode_lookup(request: Request, barcode: str):
    user_id = _uid(request)
    _limit(request, user_id, "barcode", 8, 60)
    if not barcode.isdigit() or not 8 <= len(barcode) <= 14:
        raise HTTPException(status_code=422, detail="Штрихкод должен содержать от 8 до 14 цифр")
    cached = request.app.state.db.cached_barcode(barcode)
    if cached:
        return {**cached, "cached": True}
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.get(
                f"https://world.openfoodfacts.org/api/v3.6/product/{barcode}.json",
                params={
                    "fields": "code,product_name,brands,quantity,image_front_small_url,nutriments,allergens_tags,ingredients_text"
                },
                headers={"User-Agent": "MassUpAI/1.3 (contact: @MassUpAIBot)"},
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("BARCODE_LOOKUP_FAILED type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Сервис штрихкодов временно недоступен") from exc
    product = body.get("product") or {}
    if not product or not product.get("product_name"):
        raise HTTPException(status_code=404, detail="Продукт с таким штрихкодом не найден")
    nutrients = product.get("nutriments") or {}

    def number(key: str) -> float | None:
        try:
            value = nutrients.get(key)
            return round(float(value), 1) if value is not None else None
        except (TypeError, ValueError):
            return None

    result = {
        "barcode": barcode,
        "name": str(product.get("product_name") or "Продукт")[:160],
        "brand": str(product.get("brands") or "")[:120],
        "quantity": str(product.get("quantity") or "")[:80],
        "image_url": product.get("image_front_small_url"),
        "ingredients": str(product.get("ingredients_text") or "")[:1000],
        "allergens": [str(value).split(":")[-1] for value in (product.get("allergens_tags") or [])][:20],
        "kcal_per_100g": number("energy-kcal_100g"),
        "protein_per_100g": number("proteins_100g"),
        "fat_per_100g": number("fat_100g"),
        "carbs_per_100g": number("carbohydrates_100g"),
        "source": "Open Food Facts",
    }
    request.app.state.db.cache_barcode(barcode, result)
    _record_event(request, user_id, "barcode_lookup")
    return {**result, "cached": False}


@router.get("/admin/users/{user_id}")
def admin_user_detail(request: Request, user_id: int):
    _admin_uid(request)
    result = request.app.state.db.admin_user_detail(user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return result


@router.put("/admin/users/{user_id}/control")
def admin_user_control(request: Request, user_id: int, payload: UserControlInput):
    owner_id = _admin_uid(request)
    if user_id == owner_id and payload.blocked:
        raise HTTPException(status_code=422, detail="Нельзя заблокировать владельца")
    result = request.app.state.db.set_user_control(user_id, payload.blocked, payload.note)
    _record_event(request, owner_id, "admin_user_control", detail=f"user:{user_id}")
    return result


@router.get("/admin/feedback")
def admin_feedback(request: Request):
    _admin_uid(request)
    return {"items": request.app.state.db.feedback_items()}


@router.get("/admin/feedback/{feedback_id}/screenshot")
def admin_feedback_screenshot(request: Request, feedback_id: int):
    _admin_uid(request)
    item = request.app.state.db.feedback_screenshot(feedback_id)
    if not item:
        raise HTTPException(status_code=404, detail="Скриншот не найден")
    return Response(content=item["screenshot"], media_type=item["mime_type"])


@router.patch("/admin/feedback/{feedback_id}")
def resolve_feedback(request: Request, feedback_id: int, status: str = "resolved"):
    _admin_uid(request)
    if status not in {"new", "resolved"}:
        raise HTTPException(status_code=422, detail="Неизвестный статус")
    if not request.app.state.db.set_feedback_status(feedback_id, status):
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    return {"ok": True, "status": status}


@router.get("/admin/broadcast/estimate")
def broadcast_estimate(request: Request, audience: str = "all"):
    _admin_uid(request)
    if audience not in {"all", "active30"}:
        raise HTTPException(status_code=422, detail="Неизвестная аудитория")
    return {"audience": audience, "recipients": len(request.app.state.db.broadcast_recipients(audience))}


@router.post("/admin/broadcast")
async def send_broadcast(request: Request, payload: BroadcastInput):
    owner_id = _admin_uid(request)
    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        raise HTTPException(status_code=503, detail="Telegram-бот сейчас не запущен")
    recipients = request.app.state.db.broadcast_recipients(payload.audience)
    sent = 0
    failed = 0
    for index, user_id in enumerate(recipients, start=1):
        try:
            await bot.send_message(user_id, payload.message)
            sent += 1
        except Exception:
            failed += 1
        if index % 25 == 0:
            await asyncio.sleep(1)
    request.app.state.db.save_broadcast(
        owner_id, payload.message, payload.audience, sent, failed
    )
    _record_event(request, owner_id, "admin_broadcast", detail=f"sent:{sent};failed:{failed}")
    return {"ok": True, "sent": sent, "failed": failed}
