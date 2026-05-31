@echo off
title Stock Analysis Dashboard
echo.
echo  ==========================================
echo   📊 Starting Stock Analysis Dashboard...
echo  ==========================================
echo.
echo  Opening http://localhost:5000
echo  Press Ctrl+C in this window to stop.
echo.

cd /d "%~dp0"
py app.py

pause
