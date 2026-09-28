@echo off
setlocal
cd /d "%~dp0"
python -m pytest master_bot.py -s --headed --browser chromium
pause
endlocal
