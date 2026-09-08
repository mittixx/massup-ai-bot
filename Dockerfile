FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import uvicorn, fastapi, aiogram, openai"

COPY . .
RUN mkdir -p data

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python scripts/healthcheck.py
CMD ["python", "run.py"]
