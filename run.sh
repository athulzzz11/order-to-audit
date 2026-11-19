#!/usr/bin/env sh
# One-command start on Mac/Linux: installs packages, then runs the app on http://localhost:8000
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt || exit 1
echo "Open http://localhost:8000  (password for every login: demo123)  -  Ctrl+C to stop"
python3 -m uvicorn app.main:app --port 8000
