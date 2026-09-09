from __future__ import annotations

from datetime import date, timedelta
import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.ai_service import AIUnavailableError
from app.auth import current_owner_id, current_user_id
from app.insights import build_insights, meal_totals
from app.nutrition import calculate_targets, percent
from app.schemas import (
    AdviceRequest,
    ManualMealInput,
    MealCorrection,
    PlanRequest,
    ProfileInput,
    ProfileResponse,
    ReminderSettingsInput,
    WeightInput,
)


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api")


def _uid(request: Request) -> int:
    return current_user_id(request, request.app.state.settings)


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
    image: UploadFile = File(...),
):
    user_id = _check_payload_user(request, telegram_user_id)
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
