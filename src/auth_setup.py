"""
auth_setup.py — Модуль первичной авторизации на маркетплейсах через выделенный профиль Playwright
(Dedicated Persistent Context).

Запуск:
  python src/auth_setup.py
"""
import sys
import time
from pathlib import Path
from typing import Optional, List

from loguru import logger
from playwright.sync_api import sync_playwright, BrowserContext, Page

# Добавляем родительский каталог и src в sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import config as base_config
from config_loader import load_config, BrowserConfig

MARKETPLACE_URLS = {
    "Ozon": "https://www.ozon.ru/",
    "Wildberries": "https://www.wildberries.ru/",
    "Яндекс Маркет": "https://market.yandex.ru/",
    "Avito": "https://www.avito.ru/",
}

AUTH_MESSAGE = (
    "\n" + "=" * 70 + "\n"
    "[АВТОРИЗАЦИЯ] Открыты 4 вкладки: Ozon, Wildberries, Яндекс Маркет, Avito.\n"
    "Войдите в свои аккаунты во всех нужных сервисах.\n"
    "После завершения входа вернитесь сюда и нажмите [ENTER] для запуска сбора...\n"
    + "=" * 70 + "\n"
)


def setup_all_marketplace_tabs(context: BrowserContext) -> List[Page]:
    """
    Открывает 4 вкладки со всеми целевыми маркетплейсами одновременно:
      - Ozon: https://www.ozon.ru/
      - Wildberries: https://www.wildberries.ru/
      - Яндекс Маркет: https://market.yandex.ru/
      - Avito: https://www.avito.ru/
    Использует wait_until="commit" для быстрой отрисовки без зависания на фоновой аналитике.
    """
    opened_pages: List[Page] = []
    for name, url in MARKETPLACE_URLS.items():
        logger.info(f"[АВТОРИЗАЦИЯ] Открытие вкладки {name}: {url}")
        try:
            if not opened_pages and context.pages and context.pages[0].url in ("about:blank", ""):
                page = context.pages[0]
            else:
                page = context.new_page()
            opened_pages.append(page)
            page.goto(url, wait_until="commit", timeout=30_000)
        except Exception as e:
            logger.warning(f"[АВТОРИЗАЦИЯ] Замечание при открытии {name} ({url}): {e}")

    return opened_pages


def run_auth_setup(
    profile_dir: Optional[Path] = None,
    channel: str = "chrome",
    headless: bool = False,
    force: bool = True,
) -> bool:
    """
    Запускает Google Chrome под выделенным профилем автоматизации и открывает 4 вкладки маркетплейсов.
    Ожидает нажатия [ENTER] от пользователя и сохраняет маркер .auth_confirmed.
    """
    target_profile_dir = profile_dir or (base_config.BASE_DIR / "chrome_automation_profile")
    target_profile_dir = Path(target_profile_dir).resolve()
    target_profile_dir.mkdir(parents=True, exist_ok=True)

    auth_marker = target_profile_dir / ".auth_confirmed"
    if auth_marker.exists() and not force:
        logger.info(f"✓ Профиль {target_profile_dir.name} уже авторизован (.auth_confirmed обнаружен).")
        return True

    logger.info("=" * 70)
    logger.info("  ЗАПУСК РЕЖИМА ПЕРВИЧНОЙ АВТОРИЗАЦИИ МАРКЕТПЛЕЙСОВ")
    logger.info(f"  Профиль: {target_profile_dir}")
    logger.info("=" * 70)

    args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
    ]

    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(target_profile_dir),
                channel=channel,
                headless=headless,
                args=args,
                ignore_default_args=["--enable-automation"],
                no_viewport=True,
            )
        except Exception as e:
            logger.warning(f"Не удалось запустить через channel='{channel}': {e}. Попытка запуска встроенного Chromium...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(target_profile_dir),
                headless=headless,
                args=args,
                ignore_default_args=["--enable-automation"],
                no_viewport=True,
            )

        try:
            opened_pages = setup_all_marketplace_tabs(context)

            print(AUTH_MESSAGE)
            logger.warning("[АВТОРИЗАЦИЯ] Ожидание завершения авторизации пользователем в 4 открытых вкладках...")

            try:
                input(">>> После завершения входа во всех сервисах нажмите [ENTER]: ")
            except (EOFError, OSError):
                logger.debug("Консольный ввод недоступен (неинтерактивный режим).")

            try:
                auth_marker.write_text(f"Auth confirmed at {time.ctime()}\n", encoding="utf-8")
            except Exception as e:
                logger.warning(f"Не удалось записать файл .auth_confirmed: {e}")

            for page in opened_pages:
                try:
                    page.close()
                except Exception:
                    pass

            logger.success(f"✓ Авторизация успешно сохранена в {target_profile_dir.name}!")
            return True
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    cfg = load_config()
    p_dir = cfg.browser.resolve_profile_dir()
    run_auth_setup(profile_dir=p_dir, channel=cfg.browser.channel, headless=cfg.browser.headless, force=True)

