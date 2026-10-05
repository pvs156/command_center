@echo off
rem Starts the Job Search Command Center on http://localhost:8765
rem Keep this window open (minimize it): Scout's hourly search runs only while it is running.
cd /d "%~dp0"
title Job Search Command Center
if not exist .venv\Scripts\python.exe (
  echo First run: creating the Python environment...
  python -m venv .venv || exit /b 1
  .venv\Scripts\python -m pip install -q -r requirements.txt || exit /b 1
)
rem Open the browser a few seconds after the server starts.
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8765"
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
