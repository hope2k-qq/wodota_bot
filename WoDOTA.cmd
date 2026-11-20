@echo off
rem Запуск пульта двойным кликом. Бот при этом выключен —
rem включается кнопкой на странице.
cd /d "%~dp0"
py -X utf8 app\wodota.py %*
pause
