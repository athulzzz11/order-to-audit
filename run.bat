@echo off
REM One-click start on Windows: installs packages, then runs the app on http://localhost:8000
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 (echo Python 3.11+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
echo.
echo Open http://localhost:8000  (password for every login: demo123)  -  Ctrl+C to stop
python -m uvicorn app.main:app --port 8000
pause
