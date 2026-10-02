@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
  .venv\Scripts\python.exe -m pip install -r DM\requirements.txt
  if errorlevel 1 goto failed
)
echo Open http://127.0.0.1:8766 in your browser. Ctrl+C stops the server.
.venv\Scripts\python.exe DM\server.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Setup or startup failed. Check the messages above and the README.
pause
exit /b 1
