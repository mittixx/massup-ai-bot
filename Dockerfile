FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/app/data/nutrition.db

# BotHost mounts Git sources over /app at runtime. Keep executable code outside
# that mount so the container always runs the exact image that was just built.
WORKDIR /usr/src/massup

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import uvicorn, fastapi, aiogram, openai"

COPY . .
RUN mkdir -p /app/data

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python /usr/src/massup/scripts/healthcheck.py
CMD ["python", "run.py"]
