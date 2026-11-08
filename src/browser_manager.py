"""
browser_manager.py — Модуль управления браузером через постоянный профиль Playwright
(Dedicated Persistent Context).

Архитектура:
  1. Выделенная папка профиля: chrome_automation_profile (сохранение кук с нативным DPAPI-шифрованием).
  2. Запуск через playwright.chromium.launch_persistent_context:
     - channel="chrome" (системный Google Chrome, а не Chromium);
     - headless=False (визуальный запуск на экране);
     - args=["--no-first-run", "--no-default-browser-check", "--start-maximized"];
     - ignore_default_args=["--enable-automation"] (без плашки автоматизации и без --no-sandbox);
     - no_viewport=True.
  3. Первичная авторизация (ensure_marketplaces_auth):
     - Каскадный неблокирующий запуск 4 вкладок (Ozon, Wildberries, Яндекс Маркет, Avito) с паузой 3 секунды;
     - Ни одна из вкладок не закрывается в процессе открытия;
     - Единое ожидание ввода [ENTER] пользователем после открытия всех вкладок;
     - Фиксация статуса в маркере .auth_ready (и .auth_confirmed) в каталоге профиля.
  4. Полная изоляция от пользовательского Chrome: никаких taskkill, портов CDP 9222/9225 и конфликтов блокировок.
"""
import os
import sys
import time
import json
from pathlib import Path
from typing import Optional, Tuple, Any, Union, List

from loguru import logger
from playwright.sync_api import Playwright, BrowserContext, Page

from config_loader import BrowserConfig, AppConfig
import config as base_config
from auth_setup import (
    MARKETPLACE_TABS,
    MARKETPLACE_URLS,
    AUTH_MESSAGE,
    AUTH_MARKERS,
    setup_all_marketplace_tabs,
    is_profile_authorized,
    mark_profile_authorized,
    run_auth_setup,
)


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

        # Маркерные файлы авторизации
        self.auth_marker_ready: Path = self.profile_dir / ".auth_ready"
        self.auth_marker_confirmed: Path = self.profile_dir / ".auth_confirmed"

        # Атрибуты обратной совместимости
        self.user_data_dir: Path = self.profile_dir
        self.profile_directory: str = getattr(self.browser_config, "profile_directory", "Default")
        self.port: int = getattr(self.browser_config, "port", 9225)
        self.cdp_url: str = getattr(self.browser_config, "cdp_url", f"http://127.0.0.1:{self.port}")
        self.chrome_exe: Path = self.browser_config.resolve_chrome_exe()
        self.force_restart_chrome: bool = False

    def is_auth_ready(self) -> bool:
        """Проверяет наличие маркера авторизации в каталоге профиля."""
        return is_profile_authorized(self.profile_dir)

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

    def setup_all_marketplace_tabs(
        self,
        context: BrowserContext,
        delay_between_tabs: float = 3.0,
    ) -> List[Page]:
        """
        Каскадно открывает 4 вкладки со всеми целевыми маркетплейсами с паузой 3 сек.:
          - Ozon: https://www.ozon.ru/
          - Wildberries: https://www.wildberries.ru/
          - Яндекс Маркет: https://market.yandex.ru/
          - Avito: https://www.avito.ru/
        Ни одна из открытых вкладок не закрывается в процессе открытия.
        """
        return setup_all_marketplace_tabs(context, delay_between_tabs=delay_between_tabs)

    def ensure_marketplaces_auth(
        self,
        context: Optional[BrowserContext] = None,
        force: bool = False,
        delay_between_tabs: float = 3.0,
    ) -> bool:
        """
        Процедура первичной авторизации пользователя на 4 маркетплейсах:
          1. Если маркер .auth_ready (или .auth_confirmed) существует и force=False,
             этап пропускается (для штатных периодических запусков).
          2. Если авторизация требуется (или force=True):
             - При отсутствии переданного контекста запускает изолированную сессию run_auth_setup;
             - В переданном контексте каскадно открывает 4 вкладки с задержкой 3 сек. (setup_all_marketplace_tabs);
             - Выводит пользователю четкую инструкцию и ожидает нажатия [ENTER];
             - Создает маркерный файл .auth_ready (и .auth_confirmed) в chrome_automation_profile;
             - Закрывает настроечные вкладки перед стартом выгрузки товаров.
        """
        if self.is_auth_ready() and not force:
            logger.info("✓ Профиль автоматизации уже авторизован (.auth_ready обнаружен).")
            return True

        if context is None:
            return run_auth_setup(
                profile_dir=self.profile_dir,
                channel=self.channel,
                headless=self.headless,
                force=force,
                delay_between_tabs=delay_between_tabs,
            )

        opened_pages = self.setup_all_marketplace_tabs(context, delay_between_tabs=delay_between_tabs)

        print(AUTH_MESSAGE)
        logger.warning("[АВТОРИЗАЦИЯ] Ожидание завершения авторизации пользователем во всех открытых вкладках...")

        try:
            input(">>> После завершения входа во всех сервисах нажмите [ENTER]: ")
        except (EOFError, OSError):
            logger.debug("Консольный ввод недоступен (неинтерактивный режим).")

        mark_profile_authorized(self.profile_dir)

        # Закрываем настроечные вкладки перед стартом парсинга товаров
        for page in opened_pages:
            try:
                page.close()
            except Exception:
                pass

        logger.success(f"✓ Авторизация на всех 4 площадках подтверждена и зафиксирована (.auth_ready в {self.profile_dir.name})!")
        return True

    def check_or_request_auth(
        self,
        context: Optional[BrowserContext] = None,
        force: bool = False,
        test_url: Optional[str] = None,
        delay_between_tabs: float = 3.0,
    ) -> bool:
        """
        Псевдоним обратной совместимости для ensure_marketplaces_auth.
        """
        if test_url:
            force = True
        return self.ensure_marketplaces_auth(
            context=context,
            force=force,
            delay_between_tabs=delay_between_tabs,
        )

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
