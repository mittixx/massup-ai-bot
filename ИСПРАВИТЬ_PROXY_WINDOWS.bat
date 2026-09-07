@echo off
chcp 65001 >nul
title Исправление прокси MassUp AI
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo Виртуальное окружение не найдено. Сначала запустите УСТАНОВИТЬ_WINDOWS.bat
  pause
  exit /b 1
)

echo Устанавливаю поддержку SOCKS-прокси...
.venv\Scripts\python.exe -m pip install "httpx[socks]"
if errorlevel 1 (
  echo Не удалось установить пакет. Проверьте интернет и повторите запуск.
  pause
  exit /b 1
)

.venv\Scripts\python.exe -c "import socksio; print('Поддержка SOCKS установлена успешно')"
echo.
echo Готово. Теперь запустите ЗАПУСТИТЬ_WINDOWS.bat
pause

