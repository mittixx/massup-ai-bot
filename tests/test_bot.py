import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot import backup_database, configure_bot, create_dispatcher, start, text_budget
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
