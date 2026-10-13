@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Сборка TimeCue.exe
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv
)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python tests\test_core.py || goto :fail
.venv\Scripts\python main.py --selftest selftest_out || goto :fail
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name TimeCue --icon icon.ico ^
  --add-data "icon.ico;." ^
  --hidden-import mido.backends.rtmidi --hidden-import rtmidi ^
  main.py || goto :fail
echo.
echo Готово: dist\TimeCue.exe
pause
exit /b 0
:fail
echo.
echo Сборка не удалась — посмотрите сообщения выше.
pause
exit /b 1
