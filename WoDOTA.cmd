@echo off
rem Запуск пульта двойным кликом. Бот при этом выключен -
rem включается кнопкой на странице.
cd /d "%~dp0"

set "PY=py -X utf8"
where py >nul 2>nul || set "PY=python -X utf8"

%PY% -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python не найден. Поставь его с python.org
  echo и при установке отметь "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

%PY% -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo Ставлю библиотеку Pillow, это один раз...
  %PY% -m pip install -r requirements.txt
  echo.
)

%PY% app\wodota.py %*
pause
