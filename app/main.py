from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
import httpx
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.ai_service import NutritionAI
from app.api import router as api_router
from app.bot import create_dispatcher
from app.config import Settings, get_settings
from app.database import Database


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
logger = logging.getLogger("uvicorn.error")


async def check_local_http(settings: Settings) -> None:
    """Probe our listener independently of proxy environment variables."""
    host = "127.0.0.1" if settings.host == "0.0.0.0" else settings.host
    async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
        for _ in range(10):
            await asyncio.sleep(0.5)
            try:
                response = await client.get(f"http://{host}:{settings.port}/health")
                if response.status_code == 200 and response.json().get("version") == VERSION:
                    logger.info("LOCAL_HTTP_OK version=%s port=%s status=200", VERSION, settings.port)
                    return
            except (httpx.HTTPError, ValueError):
                pass
    logger.error("LOCAL_HTTP_FAILED port=%s; inspect listener and container logs", settings.port)


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
            # Only Uvicorn owns SIGTERM/SIGINT in this combined process.
            bot_task = asyncio.create_task(dispatcher.start_polling(
                bot, handle_signals=False, close_bot_session=False,
            ))
            def polling_done(task):
                if not task.cancelled():
                    error = task.exception()
                    logger.error("BOT_POLLING_STOPPED type=%s", type(error).__name__ if error else "normal_exit")
            bot_task.add_done_callback(polling_done)
        app.state.bot_task = bot_task
        probe_task = asyncio.create_task(check_local_http(settings))
        logger.info("MASSUP_START version=%s bind=%s:%s", VERSION, settings.host, settings.port)
        try:
            yield
        finally:
            probe_task.cancel()
            with suppress(asyncio.CancelledError):
                await probe_task
            if bot_task:
                bot_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await bot_task
            if bot:
                await bot.session.close()

    app = FastAPI(
        title="MassUp AI",
        description="Персональный дневник КБЖУ и планировщик питания для набора веса",
        version=VERSION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = Database(settings.database_path)
    app.state.ai = NutritionAI(settings)
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.middleware("http")
    async def cache_policy(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache" if request.url.path.startswith("/static/") else "no-store"
        return response

    @app.get("/health")
    def health():
        database_ok = True
        try:
            with app.state.db.connect() as connection:
                connection.execute("SELECT 1 FROM profiles LIMIT 1")
        except Exception:
            database_ok = False
        task = getattr(app.state, "bot_task", None)
        return JSONResponse(status_code=200 if database_ok else 503, content={
            "status": "ok" if database_ok else "degraded",
            "version": VERSION,
            "port": settings.port,
            "database": "ok" if database_ok else "unavailable",
            "bot_polling": "active" if task and not task.done() else "stopped" if task else "disabled",
            "bot_configured": bool(settings.bot_token),
            "ai_configured": bool(settings.openai_api_key),
            "public_access": settings.public_access,
        })

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    return app


app = create_app()
