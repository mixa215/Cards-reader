@echo off
chcp 65001 >nul
echo ========================================================
echo   Настройка авторизации Cards-reader (4 маркетплейса)
echo ========================================================
echo.
.\.venv\Scripts\python.exe src\auth_setup.py
pause

