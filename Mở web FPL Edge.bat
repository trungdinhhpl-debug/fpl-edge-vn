@echo off
title FPL Edge VN launcher
cd /d "%~dp0"

echo ============================================
echo   FPL Edge VN - dang khoi dong...
echo ============================================
echo.

REM --- Backend (FastAPI, cong 8000) ---
start "FPL Edge - Backend" cmd /k "cd /d "%~dp0backend" && "%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --port 8000"

REM --- Frontend (Next.js, cong 3000) ---
start "FPL Edge - Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"

echo Doi 2 server khoi dong (~10s) roi mo trinh duyet...
timeout /t 10 /nobreak >nul

start "" http://localhost:3000

echo.
echo Da mo http://localhost:3000
echo Dong 2 cua so server de tat web.
echo.
pause
