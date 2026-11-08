@echo off
chcp 65001 >nul
echo ========================================================
echo   Запуск выделенного профиля Cards-reader в Chrome
echo ========================================================
echo.

set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

set "PROFILE_DIR=%~dp0chrome_automation_profile"

echo Каталог профиля: "%PROFILE_DIR%"
echo Запуск 4 вкладок маркетплейсов в Google Chrome...

start "" "%CHROME_EXE%" --user-data-dir="%PROFILE_DIR%" --no-first-run --no-default-browser-check --start-maximized "https://www.ozon.ru/" "https://www.wildberries.ru/" "https://market.yandex.ru/" "https://www.avito.ru/"

echo.
echo ✓ Браузер запущен! Открыты 4 вкладки: Ozon, Wildberries, Яндекс Маркет, Avito.
echo   Войдите в свои аккаунты, куки автоматически сохранятся в этой папке.
echo.
timeout /t 3 >nul
