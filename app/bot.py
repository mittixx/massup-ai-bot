from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, date, datetime, timedelta
from tempfile import TemporaryDirectory

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from app.ai_service import AIUnavailableError, NutritionAI
from app.config import Settings
from app.database import Database
from app.insights import build_insights, due_reminder_kinds, meal_totals
from app.nutrition import calculate_targets
from app.schemas import ProfileInput


_db: Database | None = None
_ai: NutritionAI | None = None
_settings: Settings | None = None
logger = logging.getLogger("uvicorn.error")


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
        "Команды: /today, /suggest, /report, /plan 4000, /app.\n\n"
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


def _command_argument(message: Message) -> str:
    text = message.text or message.caption or ""
    return text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) == 2 else ""


def _context(user_id: int):
    if _db is None:
        return None
    record = _db.get_profile(user_id)
    if not record:
        return None
    profile = ProfileInput(**record)
    targets = calculate_targets(profile)
    meals = _db.meals_for_day(user_id, str(date.today()))
    totals = meal_totals(meals)
    remaining = {
        key: max(0, round(getattr(targets, key if key != "kcal" else "calories") - totals[key], 1))
        for key in ("kcal", "protein", "fat", "carbs")
    }
    return record, profile, targets, totals, remaining


def _format_advice(result) -> str:
    items = "\n".join(f"• {item}" for item in result.recommendations)
    note = f"\n\n{result.note}" if result.note else ""
    return f"{result.title}\n\n{result.summary}\n\n{items}{note}"


async def advice_for_message(message: Message, mode: str, query: str = ""):
    if await reject_if_needed(message) or not message.from_user or _db is None or _ai is None:
        return
    context = _context(message.from_user.id)
    if not context:
        await message.answer("Сначала открой Mini App и заполни профиль.", reply_markup=app_keyboard())
        return
    if mode in {"recipe", "swap", "portion"} and not query:
        examples = {
            "recipe": "/recipe яйца, творог, банан",
            "swap": "/swap овсянка с молоком",
            "portion": "/portion рис с курицей",
        }
        await message.answer(f"Добавь описание. Например: {examples[mode]}")
        return
    _, profile, targets, totals, remaining = context
    status = await message.answer("AI готовит персональную рекомендацию…")
    try:
        result = await _ai.make_advice(
            profile,
            targets.model_dump(),
            totals,
            remaining,
            _db.weights(message.from_user.id, 90),
            mode,
            query,
        )
    except AIUnavailableError as exc:
        await status.edit_text(str(exc))
        return
    except Exception:
        await status.edit_text("Не удалось подготовить рекомендацию. Повтори позже.")
        return
    await status.edit_text(_format_advice(result), reply_markup=app_keyboard())


async def suggest_command(message: Message):
    await advice_for_message(message, "top_up")


async def recipe_command(message: Message):
    await advice_for_message(message, "recipe", _command_argument(message))


async def swap_command(message: Message):
    await advice_for_message(message, "swap", _command_argument(message))


async def portion_command(message: Message):
    await advice_for_message(message, "portion", _command_argument(message))


async def coach_command(message: Message):
    await advice_for_message(
        message,
        "coach",
        _command_argument(message) or "Почему вес может не расти и что улучшить?",
    )


async def review_command(message: Message):
    await advice_for_message(message, "review")


def _report_text(user_id: int, report_day: date | None = None) -> str | None:
    if _db is None:
        return None
    report_day = report_day or date.today()
    record = _db.get_profile(user_id)
    if not record:
        return None
    profile = ProfileInput(**record)
    insights = build_insights(
        record,
        calculate_targets(profile).model_dump(),
        _db.meals_between(user_id, str(report_day - timedelta(days=365)), str(report_day)),
        _db.weights(user_id, 365),
        report_day,
    )
    week = insights["week"]
    forecast = insights["forecast"]
    achievement = (
        f"\nДостижения: {', '.join(item['title'] for item in insights['achievements'][-3:])}"
        if insights["achievements"] else ""
    )
    return (
        "Отчёт MassUp AI за 7 дней\n\n"
        f"Дней с записями: {week['days_logged']} из 7\n"
        f"Дней около нормы: {week['days_on_target']}\n"
        f"Среднее: {round(week['averages']['kcal'])} ккал · "
        f"Б {round(week['averages']['protein'])} г\n"
        f"Изменение веса: {week['weight_change_kg']:+g} кг\n"
        f"Серия: {insights['streak_days']} дн.\n\n"
        f"Прогноз: {forecast['message']}{achievement}"
    )


async def report_command(message: Message):
    if await reject_if_needed(message) or not message.from_user:
        return
    text = _report_text(message.from_user.id)
    if not text:
        await message.answer("Сначала открой Mini App и заполни профиль.", reply_markup=app_keyboard())
        return
    await message.answer(text, reply_markup=app_keyboard())


async def reminders_command(message: Message):
    if await reject_if_needed(message):
        return
    await message.answer(
        "Настрой время приёмов пищи, взвешивания и еженедельного отчёта во вкладке «AI-тренер».",
        reply_markup=app_keyboard(),
    )


async def label_photo(message: Message, bot: Bot):
    if await reject_if_needed(message) or not message.from_user or _db is None or _ai is None:
        return
    if not _db.get_profile(message.from_user.id):
        await message.answer("Сначала открой Mini App и заполни профиль.", reply_markup=app_keyboard())
        return
    status = await message.answer("Читаю состав и КБЖУ с этикетки…")
    try:
        file = await bot.get_file(message.photo[-1].file_id)
        stream = await bot.download_file(file.file_path)
        result = await _ai.analyze_label(stream.read(), "image/jpeg")
    except AIUnavailableError as exc:
        await status.edit_text(str(exc))
        return
    except Exception:
        await status.edit_text("Не удалось прочитать этикетку. Сделай фото ближе и без бликов.")
        return
    macros = (
        f"На 100 г: {result.kcal_per_100g if result.kcal_per_100g is not None else '—'} ккал · "
        f"Б {result.protein_per_100g if result.protein_per_100g is not None else '—'} · "
        f"Ж {result.fat_per_100g if result.fat_per_100g is not None else '—'} · "
        f"У {result.carbs_per_100g if result.carbs_per_100g is not None else '—'}"
    )
    allergens = ", ".join(result.allergens) or "не указаны"
    await status.edit_text(
        f"{result.product_name}\n{macros}\nПорция: {result.serving}\nАллергены: {allergens}\n\n"
        "Проверь цифры по оригинальной упаковке: качество распознавания зависит от фото."
    )


async def label_help(message: Message):
    if await reject_if_needed(message):
        return
    await message.answer("Отправь фотографию таблицы КБЖУ с подписью /label.")


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


def _reminder_message(user_id: int, kind: str, local_day: date) -> str:
    if kind == "breakfast":
        return "Доброе утро! Запиши завтрак — так дневная норма будет точнее."
    if kind == "lunch":
        return "Время свериться с дневником: добавь обед или перекус."
    if kind == "weigh":
        return "Пора взвеситься. Лучше делать это утром в одинаковых условиях."
    if kind == "weekly":
        return _report_text(user_id, local_day) or "Твой еженедельный отчёт готов в Mini App."
    context = _context(user_id)
    if not context:
        return "Заполни профиль в Mini App, чтобы получать персональные напоминания."
    remaining = context[-1]
    if remaining["kcal"] <= 0:
        return "Дневная цель по калориям выполнена. Проверь итог и не забудь сохранить прогресс."
    return (
        f"До дневной цели осталось примерно {round(remaining['kcal'])} ккал "
        f"и {round(remaining['protein'])} г белка. Нажми /suggest, чтобы подобрать добор."
    )


async def send_due_reminders(
    bot: Bot, db: Database, now: datetime | None = None
) -> int:
    sent = 0
    for settings in db.enabled_reminders():
        local_day, kinds = due_reminder_kinds(settings, now or datetime.now(UTC))
        user_id = int(settings["telegram_user_id"])
        for kind in kinds:
            if not db.claim_reminder(user_id, kind, str(local_day)):
                continue
            try:
                await bot.send_message(
                    user_id,
                    _reminder_message(user_id, kind, local_day),
                    reply_markup=app_keyboard(),
                )
                sent += 1
            except Exception as exc:
                logger.warning(
                    "REMINDER_SEND_FAILED user=%s kind=%s type=%s",
                    user_id,
                    kind,
                    type(exc).__name__,
                )
    return sent


async def reminder_loop(bot: Bot, db: Database) -> None:
    """Send opted-in reminders; the unique log prevents duplicates after restarts."""
    while True:
        try:
            await send_due_reminders(bot, db)
        except Exception as exc:
            logger.error("REMINDER_LOOP_ERROR type=%s", type(exc).__name__)
        await asyncio.sleep(60)


def create_dispatcher(db: Database, ai: NutritionAI, settings: Settings) -> tuple[Bot, Dispatcher]:
    configure_bot(db, ai, settings)
    bot = Bot(settings.bot_token)
    dispatcher = Dispatcher()
    router = Router()
    router.message.register(start, CommandStart())
    router.message.register(open_app, Command("app"))
    router.message.register(today, Command("today"))
    router.message.register(suggest_command, Command("suggest"))
    router.message.register(recipe_command, Command("recipe"))
    router.message.register(swap_command, Command("swap"))
    router.message.register(portion_command, Command("portion"))
    router.message.register(coach_command, Command("coach"))
    router.message.register(review_command, Command("review"))
    router.message.register(report_command, Command("report"))
    router.message.register(reminders_command, Command("reminders"))
    router.message.register(backup_database, Command("backup"))
    router.message.register(restore_database, Command("restore"), F.document)
    router.message.register(restore_help, Command("restore"))
    router.message.register(plan_command, Command("plan"))
    router.message.register(label_photo, Command("label"), F.photo)
    router.message.register(label_help, Command("label"))
    router.message.register(photo, F.photo)
    router.message.register(text_budget, F.text)
    dispatcher.include_router(router)
    return bot, dispatcher
