"""
auth_setup.py — Модуль гранулярного каскадного запуска авторизации на маркетплейсах через выделенный профиль Playwright
(Dedicated Persistent Context).

Поведение:
  1. Читает блок marketplace_auth из main_config.json:
       - ozon: bool
       - wildberries: bool
       - yandex_market: bool
       - avito: bool
       - force_reauth: bool
  2. Запускает Google Chrome в выделенном каталоге chrome_automation_profile.
  3. Открывает вкладки ТОЛЬКО для тех маркетплейсов, у которых флаг равен false (или для всех при force_reauth=True):
       - Вкладка 1: Ozon (https://www.ozon.ru/) (пауза 3 сек)
       - Вкладка 2: Wildberries (https://www.wildberries.ru/) (пауза 3 сек)
       - Вкладка 3: Яндекс Маркет (https://market.yandex.ru/) (пауза 3 сек)
       - Вкладка 4: Avito (https://www.avito.ru/)
  4. Неблокирующая загрузка: используется wait_until="commit".
  5. Ни одна из открытых вкладок не закрывается в процессе открытия.
  6. Единое ожидание: терминал ожидает нажатия клавиши [ENTER] от пользователя.
  7. После нажатия [ENTER]:
       - Автоматически обновляет соответствующие флаги в main_config.json на true;
       - Сбрасывает force_reauth в false;
       - Создает маркерный файл .auth_ready (и .auth_confirmed) в каталоге профиля.

Запуск вручную:
  python src/auth_setup.py
  setup_auth.bat
"""
import sys
import time
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

from loguru import logger
from playwright.sync_api import sync_playwright, BrowserContext, Page

# Добавляем родительский каталог и src в sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import config as base_config
from config_loader import (
    load_config,
    update_marketplace_auth,
    MarketplaceAuthConfig,
)

MARKETPLACE_CATALOG: Dict[str, Dict[str, str]] = {
    "ozon": {
        "name": "Ozon",
        "url": "https://www.ozon.ru/",
    },
    "wildberries": {
        "name": "Wildberries",
        "url": "https://www.wildberries.ru/",
    },
    "yandex_market": {
        "name": "Яндекс Маркет",
        "url": "https://market.yandex.ru/",
    },
    "avito": {
        "name": "Avito",
        "url": "https://www.avito.ru/",
    },
}

ALL_PLATFORMS: List[str] = list(MARKETPLACE_CATALOG.keys())

# Совместимость с предыдущими импортами
MARKETPLACE_TABS = [(data["name"], data["url"]) for data in MARKETPLACE_CATALOG.values()]
MARKETPLACE_URLS = {data["name"]: data["url"] for data in MARKETPLACE_CATALOG.values()}

AUTH_MARKERS = [".auth_ready", ".auth_confirmed"]


def format_auth_banner(
    opened_platforms: List[str],
    all_statuses: Optional[Dict[str, bool]] = None,
) -> str:
    """Формирует наглядный баннер с информацией о статусах площадок."""
    lines = [
        "\n" + "=" * 76,
        "  [АВТОРИЗАЦИЯ] СТАТУС ВХОДА НА МАРКЕТПЛЕЙСАХ",
        "=" * 76,
    ]
    for key, data in MARKETPLACE_CATALOG.items():
        name = data["name"]
        url = data["url"]
        is_opened = key in opened_platforms
        if is_opened:
            status_str = "ТРЕБУЕТСЯ ВХОД (вкладка открыта)"
        elif all_statuses and all_statuses.get(key, False):
            status_str = "УЖЕ АВТОРИЗОВАН (пропущен)"
        else:
            status_str = "НЕ ТРЕБУЕТСЯ"
        lines.append(f"  • {name:<15} [{status_str}]: {url}")

    lines.extend([
        "",
        "  ИНСТРУКЦИЯ ДЛЯ ПОЛЬЗОВАТЕЛЯ:",
        "  1. Переключитесь в открытое окно Google Chrome.",
        "  2. Выполните вход в незалогиненных сервисах (по СМС, Пушу или ID).",
        "  3. Все вкладки остаются открытыми и загружаются параллельно.",
        "  4. После завершения вернитесь сюда и нажмите [ENTER].",
        "  5. Статусы в main_config.json будут автоматически обновлены на true.",
        "=" * 76 + "\n",
    ])
    return "\n".join(lines)


# Сообщение по умолчанию для обратной совместимости
AUTH_MESSAGE = format_auth_banner(ALL_PLATFORMS)


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


def setup_marketplace_tabs(
    context: BrowserContext,
    platforms: Optional[List[str]] = None,
    delay_between_tabs: float = 3.0,
) -> Tuple[List[Page], List[str]]:
    """
    Каскадный запуск вкладок только для запрошенных платформ (platforms) с паузой 3 сек.:
      - Открывает каждую вкладку через context.new_page();
      - Ни одна из открытых вкладок не закрывается в процессе открытия;
      - Неблокирующий переход: wait_until="commit";
      - Пауза delay_between_tabs между соседними вкладками (кроме последней).

    Возвращает (opened_pages, opened_platform_keys).
    """
    if platforms is None:
        target_keys = ALL_PLATFORMS
    else:
        target_keys = [p for p in platforms if p in MARKETPLACE_CATALOG]

    if not target_keys:
        logger.info("[АВТОРИЗАЦИЯ] Нет маркетплейсов для открытия вкладок.")
        return [], []

    initial_blank_page: Optional[Page] = None
    if context.pages and context.pages[0].url in ("about:blank", ""):
        initial_blank_page = context.pages[0]

    opened_pages: List[Page] = []
    opened_platforms: List[str] = []
    total = len(target_keys)

    for idx, key in enumerate(target_keys, start=1):
        item = MARKETPLACE_CATALOG[key]
        name = item["name"]
        url = item["url"]

        logger.info(f"[АВТОРИЗАЦИЯ] Вкладка {idx}/{total} ({name}): открытие новой вкладки и переход на {url}...")
        page = context.new_page()
        opened_pages.append(page)
        opened_platforms.append(key)

        try:
            page.goto(url, wait_until="commit", timeout=30_000)
        except Exception as e:
            logger.warning(f"[АВТОРИЗАЦИЯ] Замечание при навигации на {name} ({url}): {e}")

        # Закрываем начальную пустую вкладку браузера после открытия первой рабочей вкладки
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

    names_str = ", ".join([MARKETPLACE_CATALOG[k]["name"] for k in opened_platforms])
    logger.success(f"✓ Открыты и параллельно загружаются вкладки: {names_str}.")
    return opened_pages, opened_platforms


def setup_all_marketplace_tabs(
    context: BrowserContext,
    delay_between_tabs: float = 3.0,
) -> List[Page]:
    """
    Обратная совместимость: открывает все 4 маркетплейса одновременно.
    """
    pages, _ = setup_marketplace_tabs(context, platforms=None, delay_between_tabs=delay_between_tabs)
    return pages


def run_auth_setup(
    profile_dir: Optional[Path] = None,
    channel: str = "chrome",
    headless: bool = False,
    force: bool = False,
    delay_between_tabs: float = 3.0,
    auth_config: Optional[MarketplaceAuthConfig] = None,
    config_path: Optional[Path] = None,
) -> bool:
    """
    Запускает Google Chrome под выделенным профилем автоматизации.
    Открывает вкладки ТОЛЬКО для маркетплейсов с флагом false (или всех при force=True / force_reauth=True).
    После нажатия [ENTER] автоматически обновляет флаги в main_config.json на true.
    """
    if auth_config is None:
        cfg = load_config(config_path)
        auth_config = cfg.marketplace_auth
        config_path = config_path or cfg.config_file_path

    target_profile_dir = profile_dir or (base_config.BASE_DIR / "chrome_automation_profile")
    target_profile_dir = Path(target_profile_dir).resolve()
    target_profile_dir.mkdir(parents=True, exist_ok=True)

    # Определяем, какие платформы требуют авторизации
    if force or auth_config.force_reauth:
        pending_platforms = ALL_PLATFORMS
    else:
        pending_platforms = auth_config.get_pending_marketplaces()

    if not pending_platforms:
        logger.info(
            "✓ Все маркетплейсы уже авторизованы согласно main_config.json. "
            "Открытие вкладок не требуется (для повторного входа укажите 'force_reauth': true в main_config.json)."
        )
        return True

    names_list = [MARKETPLACE_CATALOG[p]["name"] for p in pending_platforms]
    logger.info("=" * 76)
    logger.info(f"  ЗАПУСК АВТОРИЗАЦИИ: требуется вход на {len(pending_platforms)} пл.: {', '.join(names_list)}")
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
            opened_pages, opened_platforms = setup_marketplace_tabs(
                context,
                platforms=pending_platforms,
                delay_between_tabs=delay_between_tabs,
            )

            banner = format_auth_banner(opened_platforms, all_statuses=auth_config.to_dict())
            print(banner)
            logger.warning(
                f"[АВТОРИЗАЦИЯ] Ожидание завершения авторизации пользователем "
                f"({', '.join([MARKETPLACE_CATALOG[k]['name'] for k in opened_platforms])})..."
            )

            try:
                input(">>> После завершения входа во всех открытых сервисах нажмите [ENTER]: ")
            except (EOFError, OSError):
                logger.debug("Консольный ввод недоступен (неинтерактивный режим).")

            # 1. Автоматически обновляем флаги в main_config.json на true
            update_marketplace_auth(
                config_path=config_path,
                platforms=opened_platforms,
                reset_force=True,
            )

            # 2. Фиксируем маркер .auth_ready в профиле
            mark_profile_authorized(target_profile_dir)

            # 3. Закрываем настроечные вкладки
            for page in opened_pages:
                try:
                    page.close()
                except Exception:
                    pass

            updated_names = ", ".join([MARKETPLACE_CATALOG[k]["name"] for k in opened_platforms])
            logger.success(
                f"✓ Авторизация для [{updated_names}] успешно зафиксирована! "
                f"Флаги в main_config.json обновлены на true."
            )
            return True
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    cfg = load_config()
    p_dir = cfg.browser.resolve_profile_dir()
    run_auth_setup(
        profile_dir=p_dir,
        channel=cfg.browser.channel,
        headless=cfg.browser.headless,
        force=False,
        auth_config=cfg.marketplace_auth,
        config_path=cfg.config_file_path,
    )
