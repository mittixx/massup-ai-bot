
@echo off
chcp 65001 >nul
title MassUp AI
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo Сначала запустите УСТАНОВИТЬ_WINDOWS.bat
  pause
  exit /b 1
)

call .venv\Scripts\activate.bat
python run.py
pause

