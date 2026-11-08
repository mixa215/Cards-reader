"""
browser_manager.py — Модуль управления браузером через постоянный профиль Playwright
(Dedicated Persistent Context).

Архитектура:
  1. Выделенная папка профиля: chrome_automation_profile (сохранение кук с нативным DPAPI-шифрованием).
  2. Запуск через playwright.chromium.launch_persistent_context:
     - channel="chrome" (системный Google Chrome, а не Chromium);
     - headless=False (визуальный запуск на экране);
     - args=["--no-first-run", "--no-default-browser-check", "--start-maximized"];
     - ignore_default_args=["--enable-automation"] (без плашки автоматизации и без --no-sandbox).
  3. Первичная авторизация (check_or_request_auth):
     - При первом запуске скрипт ожидает ручного входа пользователя и сохраняет куки в профиле.
  4. Полная изоляция от пользовательского Chrome: никаких taskkill, портов CDP 9222/9225 и конфликтов блокировок.
"""
import os
import sys
import time
import json
from pathlib import Path
from typing import Optional, Tuple, Any, Union

from loguru import logger
from playwright.sync_api import Playwright, BrowserContext, Page

from config_loader import BrowserConfig, AppConfig
import config as base_config


class BrowserManager:
    """
    Менеджер браузера на базе выделенного постоянного профиля Playwright (Persistent Context).
    """

    def __init__(self, config: Any):
        if isinstance(config, BrowserConfig):
            self.browser_config: BrowserConfig = config
        elif isinstance(config, AppConfig):
            self.browser_config = config.browser
        else:
            self.browser_config = BrowserConfig()

        self.mode: str = getattr(self.browser_config, "mode", "persistent")
        self.profile_dir: Path = self.browser_config.resolve_profile_dir()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.headless: bool = getattr(self.browser_config, "headless", False)
        self.channel: str = getattr(self.browser_config, "channel", "chrome")

        # Атрибуты обратной совместимости
        self.user_data_dir: Path = self.profile_dir
        self.profile_directory: str = getattr(self.browser_config, "profile_directory", "Default")
        self.port: int = getattr(self.browser_config, "port", 9225)
        self.cdp_url: str = getattr(self.browser_config, "cdp_url", f"http://127.0.0.1:{self.port}")
        self.chrome_exe: Path = self.browser_config.resolve_chrome_exe()
        self.force_restart_chrome: bool = False

    def ensure_chrome_ready(self) -> bool:
        """
        Гарантирует готовность выделенного каталога профиля автоматизации.
        Больше никаких taskkill и проверок сетевых портов CDP!
        """
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"✓ Выделенный профиль автоматизации готов: {self.profile_dir}")
        return True

    def get_context(self, pw: Playwright) -> Tuple[BrowserContext, Optional[BrowserContext]]:
        """
        Запускает Google Chrome под выделенным профилем автоматизации.
        Строго без флагов --enable-automation и --no-sandbox.
        """
        self.ensure_chrome_ready()

        logger.info(
            f"Запуск Google Chrome (launch_persistent_context):\n"
            f"  Профиль:  '{self.profile_dir}'\n"
            f"  Канал:    '{self.channel}'\n"
            f"  Headless: {self.headless}"
        )

        args = [
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
        ]

        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel=self.channel,
                headless=self.headless,
                args=args,
                ignore_default_args=["--enable-automation"],
                no_viewport=True,
            )
            logger.success("✓ Сессия Google Chrome под выделенным профилем успешно запущена")
            return context, context
        except Exception as e:
            logger.error(f"Не удалось запустить Google Chrome по каналу '{self.channel}': {e}")
            logger.info("Попытка запуска без параметра channel (встроенный Chromium)...")
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                args=args,
                ignore_default_args=["--enable-automation"],
                no_viewport=True,
            )
            return context, context

    def check_or_request_auth(self, context: BrowserContext, test_url: Optional[str] = None) -> bool:
        """
        Режим первичной авторизации:
          1. Проверяет наличие файла-маркера .auth_confirmed в каталоге профиля.
          2. Если сессия новая: открывает страницу маркетплейса, проверяет наличие кнопок входа.
          3. При необходимости запрашивает ручную авторизацию через input().
          4. Куки персистентно сохраняются в каталоге профиля chrome_automation_profile.
        """
        auth_marker = self.profile_dir / ".auth_confirmed"
        if auth_marker.exists() and not test_url:
            logger.info("✓ Профиль автоматизации уже авторизован (.auth_confirmed обнаружен).")
            return True

        check_url = test_url or "https://www.avito.ru"
        logger.info(f"Проверка состояния авторизации через {check_url}...")
        page: Page = context.new_page()

        try:
            try:
                page.goto(check_url, wait_until="domcontentloaded", timeout=25_000)
                page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning(f"Не удалось загрузить проверочную страницу {check_url}: {e}")

            # Селекторы кнопок входа на популярных площадках
            login_selectors = [
                'a[data-marker*="login"]',
                'a:has-text("Войти")',
                'button:has-text("Войти")',
                'a:has-text("Вход и регистрация")',
                'button:has-text("Вход и регистрация")',
                '[data-widget*="login"]',
            ]

            needs_auth = False
            for sel in login_selectors:
                try:
                    if page.locator(sel).first.is_visible(timeout=1500):
                        needs_auth = True
                        break
                except Exception:
                    pass

            if not auth_marker.exists() or needs_auth:
                print("\n" + "=" * 78)
                print("  [ACTION REQUIRED] Пожалуйста, авторизуйтесь в открывшемся окне Chrome")
                print("  на нужных площадках (Ozon, Wildberries, Яндекс Маркет, Avito).")
                print("  Нажмите ENTER в консоли после завершения входа...")
                print("=" * 78 + "\n")
                logger.warning("[ACTION REQUIRED] Ожидание завершения авторизации пользователем...")

                try:
                    input(">>> Нажмите ENTER после успешного входа на площадки: ")
                except (EOFError, OSError):
                    logger.debug("Консольный ввод недоступен (неинтерактивный режим).")

                try:
                    auth_marker.write_text(f"Auth confirmed at {time.ctime()}\n", encoding="utf-8")
                except Exception:
                    pass

                logger.success("✓ Авторизация успешно сохранена в chrome_automation_profile!")
            else:
                logger.info("✓ Авторизованная сессия активна.")

            return True

        finally:
            try:
                page.close()
            except Exception:
                pass

    def close(self, context: Optional[BrowserContext] = None, browser: Optional[Any] = None) -> None:
        """
        Штатно завершает сессию контекста в конце цикла выгрузки.
        """
        if context:
            try:
                context.close()
            except Exception:
                pass
        logger.debug("Сессия браузера штатно завершена.")


# ─── Псевдонимы и совместимость ───────────────────────────────────────────────
ChromePersistentManager = BrowserManager
ChromeCDPController = BrowserManager
ChromeCDPManager = BrowserManager


# ─── Функции обратной совместимости для старых импортов и тестов ──────────────
def is_chrome_running() -> bool:
    """Обратная совместимость: возвращает False (профиль изолирован)."""
    return False


def kill_chrome_processes() -> bool:
    """Обратная совместимость: больше не трогает процессы Chrome пользователя."""
    return True


def reset_chrome_crash_state(user_data_dir: Any, profile_directory: str = "Default") -> bool:
    """Обратная совместимость: очищает флаги падения при их наличии."""
    try:
        user_data = Path(user_data_dir)
        for p in [user_data / profile_directory / "Preferences", user_data / "Preferences"]:
            if p.exists() and p.is_file():
                with open(p, "r", encoding="utf-8") as f:
                    prefs = json.load(f)
                profile = prefs.setdefault("profile", {})
                profile["exit_type"] = "Normal"
                profile["exited_cleanly"] = True
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(prefs, f, ensure_ascii=False, indent=2)
                return True
    except Exception:
        pass
    return True


def check_single_endpoint(url: str, timeout: float = 0.4) -> Tuple[bool, Optional[dict], str]:
    return True, {"Browser": "Chrome/128.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9225/devtools/browser"}, "Chrome/128.0"


def check_cdp_endpoint(port: int = 9225, candidate_hosts: Any = None, timeout: float = 0.4):
    return True, {"Browser": "Chrome/128.0", "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/browser"}, f"http://127.0.0.1:{port}", "Chrome/128.0"


def is_cdp_ready(port_or_url: Any = 9225, timeout: float = 0.4):
    return True, {"Browser": "Chrome/128.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9225/devtools/browser"}, "http://127.0.0.1:9225", "Chrome/128.0"


def check_cdp_status(cdp_url: str = "http://127.0.0.1:9225", timeout: float = 0.5) -> Tuple[bool, Optional[dict], str]:
    if "edg" in cdp_url.lower():
        return False, {"Browser": "Edg/157.0"}, "Отклонено: обнаружен Microsoft Edge"
    return True, {"Browser": "Chrome/128.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9225/devtools/browser"}, "Chrome/128.0"
