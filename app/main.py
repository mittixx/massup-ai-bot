from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.ai_service import NutritionAI
from app.api import router as api_router
from app.bot import create_dispatcher
from app.config import Settings, get_settings
from app.database import Database


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def create_app(custom_settings: Settings | None = None) -> FastAPI:
    settings = custom_settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.db = Database(settings.database_path)
        app.state.db.initialize()
        app.state.ai = NutritionAI(settings)
        bot_task = None
        bot = None
        if settings.run_bot and settings.bot_token:
            bot, dispatcher = create_dispatcher(app.state.db, app.state.ai, settings)
            bot_task = asyncio.create_task(dispatcher.start_polling(bot))
        yield
        if bot_task:
            bot_task.cancel()
            with suppress(asyncio.CancelledError):
                await bot_task
        if bot:
            await bot.session.close()

    app = FastAPI(
        title="MassUp AI",
        description="Персональный дневник КБЖУ и планировщик питания для набора веса",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = Database(settings.database_path)
    app.state.ai = NutritionAI(settings)
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "bot_configured": bool(settings.bot_token),
            "ai_configured": bool(settings.openai_api_key),
        }

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    return app


app = create_app()

