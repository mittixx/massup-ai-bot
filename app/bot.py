from __future__ import annotations

import re
from datetime import date
from tempfile import TemporaryDirectory

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from app.ai_service import AIUnavailableError, NutritionAI
from app.config import Settings
from app.database import Database
from app.nutrition import calculate_targets
from app.schemas import ProfileInput


_db: Database | None = None
_ai: NutritionAI | None = None
_settings: Settings | None = None


def configure_bot(db: Database, ai: NutritionAI, settings: Settings) -> None:
    global _db, _ai, _settings
    _db, _ai, _settings = db, ai, settings


def allowed(message: Message) -> bool:
    return bool(
        message.from_user
        and (_settings is not None)
        and (
            _settings.public_access
            or not _settings.owner_telegram_id
            or message.from_user.id == _settings.owner_telegram_id
        )
    )


async def reject_if_needed(message: Message) -> bool:
    if allowed(message):
        return False
    await message.answer("Это персональный бот. Доступ закрыт.")
    return True


def app_keyboard() -> InlineKeyboardMarkup | None:
    if not _settings or not _settings.webapp_url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть дневник питания", web_app=WebAppInfo(url=_settings.webapp_url))
    ]])


async def start(message: Message):
    if await reject_if_needed(message):
        return
    await message.answer(
        "Привет! Я твой AI-помощник для набора веса.\n\n"
        "Отправь фотографию еды — я оценю порцию и КБЖУ.\n"
        "Напиши «4000 на неделю» — соберу меню и список покупок.\n"
        "Команды: /today, /plan 4000, /app.\n\n"
        "Оценка еды по фото приблизительная: граммовку, масло и соусы лучше проверять.",
        reply_markup=app_keyboard(),
    )


async def open_app(message: Message):
    if await reject_if_needed(message):
        return
    await message.answer("В Mini App находятся дневник, меню, покупки и прогресс.", reply_markup=app_keyboard())


async def today(message: Message):
    if await reject_if_needed(message) or not message.from_user or _db is None:
        return
    record = _db.get_profile(message.from_user.id)
    if not record:
        await message.answer("Сначала открой Mini App и заполни профиль.", reply_markup=app_keyboard())
        return
    profile = ProfileInput(**record)
    target = calculate_targets(profile)
    meals = _db.meals_for_day(message.from_user.id, str(date.today()))
    totals = {key: round(sum(float(x[key]) for x in meals), 1) for key in ("kcal", "protein", "fat", "carbs")}
    await message.answer(
        f"Сегодня: {totals['kcal']} / {target.calories} ккал\n"
        f"Б {totals['protein']} / {target.protein} г · "
        f"Ж {totals['fat']} / {target.fat} г · У {totals['carbs']} / {target.carbs} г",
        reply_markup=app_keyboard(),
    )


async def backup_database(message: Message):
    if not message.from_user or _settings is None or _db is None:
        return
    if not _settings.owner_telegram_id:
        await message.answer("Резервная копия отключена: сначала укажите OWNER_TELEGRAM_ID на сервере.")
        return
    if message.from_user.id != _settings.owner_telegram_id:
        await message.answer("Это персональный бот. Доступ закрыт.")
        return
    with TemporaryDirectory(prefix="massup-backup-") as directory:
        filename = f"massup-backup-{date.today().isoformat()}.db"
        path = _db.backup_to(f"{directory}/{filename}")
        await message.answer_document(
            FSInputFile(path, filename=filename),
            caption="Резервная копия MassUp AI. Сохрани этот файл до пересоздания бота.",
        )


async def restore_database(message: Message, bot: Bot):
    if not message.from_user or _settings is None or _db is None:
        return
    if not _settings.owner_telegram_id:
        await message.answer("Восстановление отключено: сначала укажите OWNER_TELEGRAM_ID на сервере.")
        return
    if message.from_user.id != _settings.owner_telegram_id:
        await message.answer("Это персональный бот. Доступ закрыт.")
        return

    document = message.document
    filename = (document.file_name or "").lower() if document else ""
    if not document or not filename.endswith(".db"):
        await message.answer("Прикрепи файл nutrition.db как документ и добавь подпись /restore.")
        return
    if document.file_size and document.file_size > 20 * 1024 * 1024:
        await message.answer("Файл базы слишком большой. Максимальный размер — 20 МБ.")
        return

    status = await message.answer("Проверяю резервную копию…")
    try:
        with TemporaryDirectory(prefix="massup-restore-") as directory:
            destination = f"{directory}/nutrition.db"
            telegram_file = await bot.get_file(document.file_id)
            await bot.download_file(telegram_file.file_path, destination=destination)
            _db.restore_from(destination)
    except (ValueError, OSError):
        await status.edit_text(
            "Не удалось восстановить базу: файл повреждён или не является копией MassUp AI."
        )
        return
    except Exception:
        await status.edit_text("Не удалось восстановить базу. Действующая база не изменена.")
        return

    await status.edit_text(
        "База восстановлена. Проверь данные командами /today и /app."
    )


async def restore_help(message: Message):
    if await reject_if_needed(message):
        return
    await message.answer("Прикрепи файл nutrition.db как документ и добавь подпись /restore.")


async def generate_plan_for_message(message: Message, budget: int):
    if await reject_if_needed(message) or not message.from_user or _db is None or _ai is None:
        return
    if not 500 <= budget <= 200000:
        await message.answer("Укажи бюджет от 500 до 200000 рублей.")
        return
    record = _db.get_profile(message.from_user.id)
    if not record:
        await message.answer("Сначала открой Mini App и заполни профиль.", reply_markup=app_keyboard())
        return
    profile = ProfileInput(**record)
    wait = await message.answer("Составляю меню и считаю покупки…")
    try:
        plan = await _ai.make_week_plan(
            profile, calculate_targets(profile).model_dump(), budget, "", "простые блюда"
        )
    except AIUnavailableError as exc:
        await wait.edit_text(str(exc))
        return
    except Exception:
        await wait.edit_text("Не удалось составить меню. Повтори попытку позже.")
        return
    _db.save_plan(message.from_user.id, budget, plan.model_dump())
    first = plan.days[0]
    dishes = "\n".join(f"• {meal.meal_type}: {meal.dish}" for meal in first.meals)
    await wait.edit_text(
        f"Готово: ориентировочно {plan.estimated_total} ₽ из {budget} ₽.\n\n"
        f"Первый день:\n{dishes}\n\n"
        "Полные 7 дней, рецепты и список покупок — в Mini App.",
        reply_markup=app_keyboard(),
    )


async def plan_command(message: Message):
    if await reject_if_needed(message):
        return
    match = re.search(r"\d[\d\s]*", message.text or "")
    if not match:
        await message.answer("Напиши сумму, например: /plan 4000")
        return
    await generate_plan_for_message(message, int(match.group().replace(" ", "")))


async def photo(message: Message, bot: Bot):
    if await reject_if_needed(message) or not message.from_user or _db is None or _ai is None:
        return
    if not _db.get_profile(message.from_user.id):
        await message.answer("Сначала открой Mini App и заполни профиль.", reply_markup=app_keyboard())
        return
    status = await message.answer("Рассматриваю блюдо и считаю КБЖУ…")
    try:
        file = await bot.get_file(message.photo[-1].file_id)
        stream = await bot.download_file(file.file_path)
        analysis = await _ai.analyze_photo(stream.read(), "image/jpeg")
    except AIUnavailableError as exc:
        await status.edit_text(str(exc))
        return
    except Exception:
        await status.edit_text("Не удалось распознать фото. Повтори попытку позже.")
        return
    meal = _db.add_meal({
        "telegram_user_id": message.from_user.id,
        "eaten_on": date.today(), "meal_type": "Приём пищи", "name": analysis.dish_name,
        "grams": analysis.total_grams, "kcal": analysis.total_kcal,
        "protein": analysis.total_protein, "fat": analysis.total_fat,
        "carbs": analysis.total_carbs, "source": "photo",
        "confidence": analysis.confidence, "details": analysis.model_dump(),
    })
    assumptions = "\n".join(f"• {x}" for x in analysis.assumptions[:3])
    await status.edit_text(
        f"{analysis.dish_name}\n"
        f"≈ {round(analysis.total_grams)} г · {round(analysis.total_kcal)} ккал\n"
        f"Б {round(analysis.total_protein)} · Ж {round(analysis.total_fat)} · У {round(analysis.total_carbs)} г\n\n"
        f"Допущения:\n{assumptions or 'без дополнительных допущений'}\n\n"
        f"Записал в дневник (№{meal['id']}). Проверь оценку в приложении.",
        reply_markup=app_keyboard(),
    )


async def text_budget(message: Message):
    if await reject_if_needed(message):
        return
    text = (message.text or "").lower()
    match = re.search(r"\b(\d[\d\s]{2,})\s*(?:₽|руб)?\b", text)
    if match and any(word in text for word in ("недел", "бюджет", "купить", "меню")):
        await generate_plan_for_message(message, int(match.group(1).replace(" ", "")))
    else:
        await message.answer(
            "Отправь фото еды или напиши, например: «4500 рублей на неделю».",
            reply_markup=app_keyboard(),
        )


def create_dispatcher(db: Database, ai: NutritionAI, settings: Settings) -> tuple[Bot, Dispatcher]:
    configure_bot(db, ai, settings)
    bot = Bot(settings.bot_token)
    dispatcher = Dispatcher()
    router = Router()
    router.message.register(start, CommandStart())
    router.message.register(open_app, Command("app"))
    router.message.register(today, Command("today"))
    router.message.register(backup_database, Command("backup"))
    router.message.register(restore_database, Command("restore"), F.document)
    router.message.register(restore_help, Command("restore"))
    router.message.register(plan_command, Command("plan"))
    router.message.register(photo, F.photo)
    router.message.register(text_budget, F.text)
    dispatcher.include_router(router)
    return bot, dispatcher
