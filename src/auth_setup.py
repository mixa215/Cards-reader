"""
auth_setup.py — Модуль первичной каскадной авторизации на маркетплейсах через выделенный профиль Playwright
(Dedicated Persistent Context).

Поведение:
  1. Запускает Google Chrome в выделенном каталоге chrome_automation_profile.
  2. Каскадно открывает 4 соседние вкладки (context.new_page()) с паузой в 3 секунды:
       - Вкладка 1 (Ozon): https://www.ozon.ru/ (пауза 3 сек)
       - Вкладка 2 (Wildberries): https://www.wildberries.ru/ (пауза 3 сек)
       - Вкладка 3 (Яндекс Маркет): https://market.yandex.ru/ (пауза 3 сек)
       - Вкладка 4 (Avito): https://www.avito.ru/
  3. Неблокирующая загрузка: используется wait_until="commit", ни одна площадка не ждет полной загрузки.
  4. Ни одна из 4 вкладок не закрывается в процессе открытия.
  5. Единое ожидание: терминал ожидает нажатия клавиши [ENTER] от пользователя.
  6. Фиксация результата: создается маркерный файл .auth_ready (и .auth_confirmed) в каталоге профиля.

Запуск вручную:
  python src/auth_setup.py
  setup_auth.bat
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
from config_loader import load_config

MARKETPLACE_TABS = [
    ("Ozon", "https://www.ozon.ru/"),
    ("Wildberries", "https://www.wildberries.ru/"),
    ("Яндекс Маркет", "https://market.yandex.ru/"),
    ("Avito", "https://www.avito.ru/"),
]

MARKETPLACE_URLS = dict(MARKETPLACE_TABS)

AUTH_MARKERS = [".auth_ready", ".auth_confirmed"]

AUTH_MESSAGE = (
    "\n" + "=" * 76 + "\n"
    "  [АВТОРИЗАЦИЯ] В браузере открыты 4 соседние вкладки:\n"
    "    1. Ozon:           https://www.ozon.ru/\n"
    "    2. Wildberries:    https://www.wildberries.ru/\n"
    "    3. Яндекс Маркет:  https://market.yandex.ru/\n"
    "    4. Avito:          https://www.avito.ru/\n\n"
    "  ИНСТРУКЦИЯ ДЛЯ ПОЛЬЗОВАТЕЛЯ:\n"
    "  - Переключитесь в открытое окно Google Chrome;\n"
    "  - Войдите в свои учетные записи во всех необходимых сервисах\n"
    "    (по коду из SMS/Push, паролю или Ozon ID/WB ID);\n"
    "  - Все вкладки остаются открытыми и загружаются параллельно;\n"
    "  - После успешного входа вернитесь сюда и нажмите [ENTER].\n"
    + "=" * 76 + "\n"
)


def is_profile_authorized(profile_dir: Path) -> bool:
    """Проверяет наличие маркерного файла авторизации (.auth_ready или .auth_confirmed)."""
    p = Path(profile_dir)
    return any((p / marker).exists() for marker in AUTH_MARKERS)


def mark_profile_authorized(profile_dir: Path) -> None:
    """Создает маркерные файлы авторизации (.auth_ready и .auth_confirmed)."""
    p = Path(profile_dir)
    p.mkdir(parents=True, exist_ok=True)
    content = f"Auth completed at {time.ctime()}\n"
    for marker in AUTH_MARKERS:
        try:
            (p / marker).write_text(content, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Не удалось записать маркер {marker}: {e}")


def setup_all_marketplace_tabs(
    context: BrowserContext,
    delay_between_tabs: float = 3.0,
) -> List[Page]:
    """
    Каскадный запуск 4 вкладок маркетплейсов в едином окне браузера:
      - Вкладка 1 (Ozon): context.new_page() -> переход -> пауза 3 сек.
      - Вкладка 2 (Wildberries): context.new_page() -> переход -> пауза 3 сек.
      - Вкладка 3 (Яндекс Маркет): context.new_page() -> переход -> пауза 3 сек.
      - Вкладка 4 (Avito): context.new_page() -> переход.

    Каждый ресурс открывается строго в новой соседней вкладке (context.new_page()).
    Ни одна из 4 открытых вкладок не закрывается в процессе открытия.
    Использует wait_until="commit" для неблокирующей параллельной загрузки.
    """
    initial_blank_page: Optional[Page] = None
    if context.pages and context.pages[0].url in ("about:blank", ""):
        initial_blank_page = context.pages[0]

    opened_pages: List[Page] = []
    total = len(MARKETPLACE_TABS)

    for idx, (name, url) in enumerate(MARKETPLACE_TABS, start=1):
        logger.info(f"[АВТОРИЗАЦИЯ] Вкладка {idx}/{total} ({name}): открытие новой вкладки и переход на {url}...")
        page = context.new_page()
        opened_pages.append(page)

        try:
            page.goto(url, wait_until="commit", timeout=30_000)
        except Exception as e:
            logger.warning(f"[АВТОРИЗАЦИЯ] Замечание при навигации на {name} ({url}): {e}")

        # Если при старте браузера была создана дефолтная пустая вкладка about:blank,
        # закрываем её после открытия первой рабочей вкладки, чтобы не оставлять лишний пустой таб.
        if initial_blank_page is not None:
            try:
                initial_blank_page.close()
            except Exception:
                pass
            initial_blank_page = None

        # Задержка 3 секунды перед открытием следующей соседней вкладки (кроме последней)
        if idx < total and delay_between_tabs > 0:
            logger.info(f"[АВТОРИЗАЦИЯ] Пауза {delay_between_tabs} сек. перед открытием следующей вкладки...")
            time.sleep(delay_between_tabs)

    logger.success(f"✓ Все {len(opened_pages)} вкладки маркетплейсов открыты и загружаются параллельно.")
    return opened_pages


def run_auth_setup(
    profile_dir: Optional[Path] = None,
    channel: str = "chrome",
    headless: bool = False,
    force: bool = True,
    delay_between_tabs: float = 3.0,
) -> bool:
    """
    Запускает Google Chrome под выделенным профилем автоматизации и открывает 4 вкладки маркетплейсов.
    Ожидает нажатия [ENTER] от пользователя и сохраняет маркер .auth_ready.
    """
    target_profile_dir = profile_dir or (base_config.BASE_DIR / "chrome_automation_profile")
    target_profile_dir = Path(target_profile_dir).resolve()
    target_profile_dir.mkdir(parents=True, exist_ok=True)

    if is_profile_authorized(target_profile_dir) and not force:
        logger.info(f"✓ Профиль {target_profile_dir.name} уже авторизован (.auth_ready обнаружен).")
        return True

    logger.info("=" * 76)
    logger.info("  ЗАПУСК РЕЖИМА ПЕРВИЧНОЙ АВТОРИЗАЦИИ МАРКЕТПЛЕЙСОВ (4 ВКЛАДКИ)")
    logger.info(f"  Профиль: {target_profile_dir}")
    logger.info("=" * 76)

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
            opened_pages = setup_all_marketplace_tabs(context, delay_between_tabs=delay_between_tabs)

            print(AUTH_MESSAGE)
            logger.warning("[АВТОРИЗАЦИЯ] Ожидание завершения авторизации пользователем во всех открытых вкладках...")

            try:
                input(">>> После завершения входа во всех сервисах нажмите [ENTER]: ")
            except (EOFError, OSError):
                logger.debug("Консольный ввод недоступен (неинтерактивный режим).")

            mark_profile_authorized(target_profile_dir)

            for page in opened_pages:
                try:
                    page.close()
                except Exception:
                    pass

            logger.success(f"✓ Авторизация успешно зафиксирована в {target_profile_dir.name} (.auth_ready создан)!")
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
