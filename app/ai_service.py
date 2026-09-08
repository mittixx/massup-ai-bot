from __future__ import annotations

import asyncio
import base64
import os

from openai import APIConnectionError, APIStatusError, AuthenticationError, OpenAI, RateLimitError

from app.config import Settings
from app.schemas import PhotoAnalysis, ProfileInput, WeekPlan


class AIUnavailableError(RuntimeError):
    pass


class NutritionAI:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _client(self) -> OpenAI:
        if not self.settings.openai_api_key:
            raise AIUnavailableError("Добавьте OPENAI_API_KEY в файл .env")
        proxy = next(
            (os.getenv(name, "") for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY")
             if os.getenv(name, "").lower().startswith("socks")),
            "",
        )
        if proxy:
            try:
                import socksio  # noqa: F401
            except ImportError as exc:
                raise AIUnavailableError(
                    "Обнаружен SOCKS-прокси, но не установлен модуль socksio. "
                    "Запустите УСТАНОВИТЬ_WINDOWS.bat повторно."
                ) from exc
        return OpenAI(api_key=self.settings.openai_api_key)

    async def _call(self, request):
        try:
            return await asyncio.to_thread(request)
        except AuthenticationError as exc:
            raise AIUnavailableError(
                "AI-сервис не подключён. Обновите OPENAI_API_KEY в настройках сервера."
            ) from exc
        except RateLimitError as exc:
            raise AIUnavailableError(
                "Лимит запросов к AI временно исчерпан. Повторите позже."
            ) from exc
        except APIConnectionError as exc:
            raise AIUnavailableError(
                "Не удалось связаться с AI-сервисом. Повторите позже."
            ) from exc
        except APIStatusError as exc:
            raise AIUnavailableError(
                "AI-сервис временно не выполнил запрос. Повторите позже."
            ) from exc

    async def analyze_photo(self, image: bytes, mime_type: str = "image/jpeg") -> PhotoAnalysis:
        encoded = base64.b64encode(image).decode("ascii")

        def request() -> PhotoAnalysis:
            response = self._client().responses.parse(
                model=self.settings.openai_model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Ты эксперт по оценке состава блюд и КБЖУ. Анализируй только видимое. "
                            "Оцени граммовку реалистичным диапазоном, учитывай возможное масло и соусы. "
                            "Не выдавай результат за лабораторно точный. Все названия и пояснения пиши по-русски. "
                            "Суммы КБЖУ должны арифметически соответствовать списку компонентов. "
                            "Если порция или скрытый ингредиент критично неясны, добавь короткий вопрос."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Определи блюдо, компоненты, порции и примерные КБЖУ."},
                            {
                                "type": "input_image",
                                "image_url": f"data:{mime_type};base64,{encoded}",
                                "detail": "high",
                            },
                        ],
                    },
                ],
                text_format=PhotoAnalysis,
            )
            if response.output_parsed is None:
                raise AIUnavailableError("Модель не вернула результат анализа")
            return response.output_parsed

        return await self._call(request)

    async def make_week_plan(
        self,
        profile: ProfileInput,
        targets: dict,
        budget: int,
        pantry: str,
        wishes: str,
    ) -> WeekPlan:
        prompt = f"""
Составь практичный рацион на 7 дней для плавного набора веса.
Профиль: {profile.age} лет, пол {profile.sex}, рост {profile.height_cm} см,
вес {profile.weight_kg} кг, цель {profile.target_weight_kg} кг, активность {profile.activity}.
Цель на день: {targets['calories']} ккал, Б {targets['protein']} г,
Ж {targets['fat']} г, У {targets['carbs']} г. Приёмов пищи: {profile.meals_per_day}.
Бюджет на неделю: {budget} RUB. Город: {profile.city or 'не указан'}.
Предпочтительные магазины: {profile.stores or 'не указаны'}.
Аллергии/ограничения: {profile.allergies or 'нет'}.
Не любит: {profile.dislikes or 'не указано'}.
Уже есть дома: {pantry or 'не указано'}.
Пожелания: {wishes or 'простые блюда и готовка на 2 дня вперёд'}.

Условия:
- ровно 7 дней;
- не используй продукты из аллергий;
- цены укажи как осторожную оценку, не как цену конкретного магазина;
- estimated_total не должен превышать бюджет;
- ингредиенты пиши с количеством на одну порцию;
- блюда должны быть обычными, доступными и повторно использовать закупленные продукты;
- КБЖУ дня приблизь к цели в пределах 10–15%;
- рецепты короткие, но понятные;
- все тексты по-русски.
"""

        def request() -> WeekPlan:
            response = self._client().responses.parse(
                model=self.settings.openai_model,
                input=[
                    {
                        "role": "system",
                        "content": "Ты экономный нутрициолог-планировщик. Не ставь диагнозы и не назначай лечение.",
                    },
                    {"role": "user", "content": prompt},
                ],
                text_format=WeekPlan,
            )
            if response.output_parsed is None:
                raise AIUnavailableError("Модель не вернула недельный план")
            return response.output_parsed

        return await self._call(request)
