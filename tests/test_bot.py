import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot import (
    backup_database,
    configure_bot,
    create_dispatcher,
    restore_database,
    send_due_reminders,
    start,
    text_budget,
)
from app.config import Settings
from app.database import Database
from app.ai_service import NutritionAI


def test_start_button_and_owner_access(tmp_path):
    settings = Settings(bot_token="123:fake", owner_telegram_id=42,
                        webapp_url="https://example.com", run_bot=False)
    configure_bot(Database(str(tmp_path / "bot.db")), NutritionAI(settings), settings)
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())
    asyncio.run(start(message))
    keyboard = message.answer.call_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].web_app.url == settings.webapp_url
    outsider = SimpleNamespace(from_user=SimpleNamespace(id=43), answer=AsyncMock(), text="hello")
    asyncio.run(text_budget(outsider))
    outsider.answer.assert_awaited_once_with("Это персональный бот. Доступ закрыт.")


def test_public_access_allows_users_but_not_owner_backup(tmp_path):
    settings = Settings(
        bot_token="123:fake", owner_telegram_id=42, public_access=True,
        webapp_url="https://example.com", run_bot=False,
    )
    db = Database(str(tmp_path / "public.db"))
    db.initialize()
    configure_bot(db, NutritionAI(settings), settings)
    user = SimpleNamespace(from_user=SimpleNamespace(id=43), answer=AsyncMock())
    asyncio.run(start(user))
    assert "Привет" in user.answer.call_args.args[0]

    user.answer.reset_mock()
    user.answer_document = AsyncMock()
    asyncio.run(backup_database(user))
    user.answer_document.assert_not_awaited()
    user.answer.assert_awaited_once_with("Это персональный бот. Доступ закрыт.")


def test_dispatcher_can_be_created_again(tmp_path):
    settings = Settings(bot_token="123:fake", run_bot=False)
    db = Database(str(tmp_path / "bot.db"))
    async def scenario():
        for _ in range(2):
            bot, dispatcher = create_dispatcher(db, NutritionAI(settings), settings)
            assert len(dispatcher.sub_routers) == 1
            await bot.session.close()
    asyncio.run(scenario())


def test_backup_is_owner_only(tmp_path):
    settings = Settings(bot_token="123:fake", owner_telegram_id=42, run_bot=False)
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    configure_bot(db, NutritionAI(settings), settings)
    outsider = SimpleNamespace(from_user=SimpleNamespace(id=43), answer=AsyncMock(),
                              answer_document=AsyncMock())
    asyncio.run(backup_database(outsider))
    outsider.answer_document.assert_not_awaited()
    outsider.answer.assert_awaited_once_with("Это персональный бот. Доступ закрыт.")


def test_restore_is_owner_only_and_restores_backup(tmp_path):
    settings = Settings(bot_token="123:fake", owner_telegram_id=42, run_bot=False)
    live = Database(str(tmp_path / "live.db"))
    live.initialize()
    source = Database(str(tmp_path / "source.db"))
    source.initialize()
    source.upsert_profile({
        "telegram_user_id": 42, "name": "Restored", "sex": "male", "age": 20,
        "height_cm": 180, "weight_kg": 70, "target_weight_kg": 78,
        "activity": "medium", "meals_per_day": 4, "allergies": "",
        "dislikes": "", "city": "", "stores": "", "weekly_budget": 4500,
    })
    configure_bot(live, NutritionAI(settings), settings)

    async def scenario():
        status = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            document=SimpleNamespace(file_name="nutrition.db", file_size=1024, file_id="file-id"),
            answer=AsyncMock(return_value=status),
        )
        bot = SimpleNamespace(
            get_file=AsyncMock(return_value=SimpleNamespace(file_path="remote.db")),
            download_file=AsyncMock(side_effect=lambda _, destination: __import__("shutil").copy(source.path, destination)),
        )
        await restore_database(message, bot)
        status.edit_text.assert_awaited_once_with(
            "База восстановлена. Проверь данные командами /today и /app."
        )
        assert live.get_profile(42)["name"] == "Restored"

        outsider = SimpleNamespace(from_user=SimpleNamespace(id=43), answer=AsyncMock())
        await restore_database(outsider, bot)
        outsider.answer.assert_awaited_once_with("Это персональный бот. Доступ закрыт.")
    asyncio.run(scenario())


def test_due_reminder_is_sent_only_once(tmp_path):
    from datetime import UTC, datetime

    settings = Settings(
        bot_token="123:fake", public_access=True, webapp_url="https://example.com", run_bot=False
    )
    db = Database(str(tmp_path / "reminder.db"))
    db.initialize()
    db.upsert_profile({
        "telegram_user_id": 42, "name": "Reminder", "sex": "male", "age": 20,
        "height_cm": 180, "weight_kg": 70, "target_weight_kg": 78,
        "activity": "medium", "meals_per_day": 4, "allergies": "",
        "dislikes": "", "city": "", "stores": "", "weekly_budget": 4500,
    })
    reminder = db.default_reminders(42)
    reminder.update(enabled=True, timezone="UTC", breakfast_time="09:00")
    db.save_reminders(reminder)
    configure_bot(db, NutritionAI(settings), settings)
    bot = SimpleNamespace(send_message=AsyncMock())
    now = datetime(2026, 9, 10, 9, 4, tzinfo=UTC)

    assert asyncio.run(send_due_reminders(bot, db, now)) == 1
    assert asyncio.run(send_due_reminders(bot, db, now)) == 0
    bot.send_message.assert_awaited_once()
