@echo off
chcp 65001 >nul
title Установка MassUp AI
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python не найден. Установите Python 3.12 с сайта python.org
  pause
  exit /b 1
)

if not exist .venv py -3.12 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

if not exist .env copy .env.example .env >nul

echo.
echo Установка завершена.
echo Откройте файл .env и заполните BOT_TOKEN, OWNER_TELEGRAM_ID и OPENAI_API_KEY.
echo После этого запустите ЗАПУСТИТЬ_WINDOWS.bat
pause

