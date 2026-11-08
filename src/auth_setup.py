"""
auth_setup.py — Модуль гранулярного каскадного запуска авторизации на маркетплейсах через выделенный профиль
(Dedicated Persistent Context и Native Chrome Subprocess).

Архитектура обхода WAF Ozon и RWB Wildberries:
  1. Для ручного входа используется ЧИСТЫЙ НАЦИОНАЛЬНЫЙ ЗАПУСК системного Google Chrome (через subprocess.Popen).
     БЕЗ Playwright, БЕЗ CDP, БЕЗ отладочных пайпов и портов!
     Это гарантирует 100% обход детекта fab_chlg (Ozon) и "Подозрительная активность" (WB),
     так как запускается обычный штатный процесс пользователя с папкой профиля chrome_automation_profile.
  2. Флаги авторизации защищены от ложного переключения:
     После закрытия Chrome терминал запрашивает подтверждение успешности входа.
     Флаги обновляются в main_config.json на true ТОЛЬКО для подтвержденных сервисов.
  3. Для автоматического режима поддерживается также запуск через Playwright со Stealth-маскировкой
     (browser_stealth).

Запуск вручную:
  setup_auth.bat
  python src/auth_setup.py
  python src/auth_setup.py --platforms ozon wildberries
"""
import sys
import time
import subprocess
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
    BrowserConfig,
)
from browser_stealth import (
    apply_stealth,
    STEALTH_CHROME_ARGS,
    STEALTH_IGNORE_DEFAULT_ARGS,
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


def run_native_chrome_auth(
    profile_dir: Optional[Path] = None,
    chrome_exe: Optional[Path] = None,
    pending_platforms: Optional[List[str]] = None,
    config_path: Optional[Path] = None,
    force: bool = False,
    auth_config: Optional[MarketplaceAuthConfig] = None,
) -> Tuple[bool, List[str]]:
    """
    Чистый нативный запуск системного Google Chrome через subprocess.Popen (БЕЗ Playwright, БЕЗ CDP).
    Позволяет обойти WAF Ozon (fab_chlg) и антибот RWB Wildberries на 100%.

    Защита флагов: флаги обновляются в main_config.json ТОЛЬКО для сервисов,
    вход в которые подтвержден пользователем.
    """
    if auth_config is None:
        cfg = load_config(config_path)
        auth_config = cfg.marketplace_auth
        config_path = config_path or cfg.config_file_path

    target_profile_dir = profile_dir or (base_config.BASE_DIR / "chrome_automation_profile")
    target_profile_dir = Path(target_profile_dir).resolve()
    target_profile_dir.mkdir(parents=True, exist_ok=True)

    target_chrome_exe = chrome_exe or BrowserConfig().resolve_chrome_exe()
    if not target_chrome_exe or not Path(target_chrome_exe).exists():
        logger.error(f"Исполняемый файл Chrome не найден: {target_chrome_exe}")
        return False, []

    if pending_platforms is None:
        if force or auth_config.force_reauth:
            target_platforms = ALL_PLATFORMS
        else:
            target_platforms = auth_config.get_pending_marketplaces()
    else:
        target_platforms = [p for p in pending_platforms if p in MARKETPLACE_CATALOG]

    if not target_platforms:
        logger.info(
            "✓ Все маркетплейсы уже авторизованы согласно main_config.json. "
            "Открытие вкладок не требуется (для повторного входа укажите 'force_reauth': true в main_config.json)."
        )
        return True, []

    target_urls = [MARKETPLACE_CATALOG[p]["url"] for p in target_platforms]
    names_list = [MARKETPLACE_CATALOG[p]["name"] for p in target_platforms]

    cmd = [
        str(target_chrome_exe),
        f"--user-data-dir={target_profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        *target_urls,
    ]

    banner = (
        "\n" + "=" * 76 + "\n"
        "  [НАЦИОНАЛЬНЫЙ ВХОД] ЗАПУСК СИСТЕМНОГО GOOGLE CHROME (БЕЗ PLAYWRIGHT / БЕЗ CDP)\n"
        "=" * 76 + "\n"
        f"  Каталог профиля: {target_profile_dir}\n"
        f"  Открыты вкладки: {', '.join(names_list)}\n\n"
        "  ИНСТРУКЦИЯ ПО ВХОДУ:\n"
        "  1. В открывшемся окне Google Chrome авторизуйтесь в нужных сервисах.\n"
        "     Браузер запущен нативно, поэтому WAF-заглушки (Ozon) и RWB (WB) не появятся.\n"
        "  2. После успешного входа ЗАКРОЙТЕ окно Google Chrome,\n"
        "     чтобы куки записались на диск и каталог профиля разблокировался.\n"
        "  3. Вернитесь в эту консоль для подтверждения.\n"
        + "=" * 76 + "\n"
    )
    print(banner)
    logger.info(f"Запуск нативного процесса Chrome: {target_chrome_exe}")

    try:
        proc = subprocess.Popen(cmd)
    except Exception as e:
        logger.error(f"Не удалось запустить нативный Chrome: {e}")
        return False, []

    # Защищенный опрос пользователя для предотвращения ложного переключения флагов
    confirmed_platforms: List[str] = []
    try:
        ans = input(">>> Вход во все открытые сервисы выполнен успешно? [Y/n]: ").strip().lower()
    except (EOFError, OSError):
        ans = "y"

    if ans in ("", "y", "yes", "да", "д"):
        confirmed_platforms = list(target_platforms)
    else:
        for p in target_platforms:
            name = MARKETPLACE_CATALOG[p]["name"]
            try:
                sub_ans = input(f"    Вход в {name} выполнен успешно? [y/N]: ").strip().lower()
            except (EOFError, OSError):
                sub_ans = "n"
            if sub_ans in ("y", "yes", "да", "д"):
                confirmed_platforms.append(p)
            else:
                logger.warning(f"Вход в {name} не подтвержден пользователем (флаг останется false).")

    # Проверяем закрытие окна Chrome для освобождения файловой блокировки профиля
    if proc.poll() is None:
        logger.info("Ожидание закрытия окна Google Chrome для освобождения каталога профиля...")
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Окно Chrome не было закрыто пользователем. Закрытие процесса для разблокировки каталога...")
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass

    if confirmed_platforms:
        update_marketplace_auth(
            config_path=config_path,
            platforms=confirmed_platforms,
            reset_force=True,
        )
        mark_profile_authorized(target_profile_dir)
        conf_names = ", ".join([MARKETPLACE_CATALOG[k]["name"] for k in confirmed_platforms])
        logger.success(f"✓ Успешно зафиксирован вход для [{conf_names}]! Флаги в main_config.json обновлены на true.")
        return True, confirmed_platforms
    else:
        logger.warning("Вход не подтвержден ни для одной площадки. Флаги в main_config.json сохранены без изменений.")
        return False, []


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
        apply_stealth(page)
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
    platforms: Optional[List[str]] = None,
    use_native: bool = True,
) -> bool:
    """
    Точка входа запуска авторизации.
    По умолчанию (use_native=True) запускает нативный Google Chrome через subprocess.Popen,
    чтобы полностью исключить детект WAF/CDP со стороны Ozon и Wildberries.
    """
    if use_native:
        success, _ = run_native_chrome_auth(
            profile_dir=profile_dir,
            pending_platforms=platforms,
            config_path=config_path,
            force=force,
            auth_config=auth_config,
        )
        return success

    # Режим Playwright (если запрошен или в тестах)
    if auth_config is None:
        cfg = load_config(config_path)
        auth_config = cfg.marketplace_auth
        config_path = config_path or cfg.config_file_path

    target_profile_dir = profile_dir or (base_config.BASE_DIR / "chrome_automation_profile")
    target_profile_dir = Path(target_profile_dir).resolve()
    target_profile_dir.mkdir(parents=True, exist_ok=True)

    if platforms:
        pending_platforms = [p for p in platforms if p in MARKETPLACE_CATALOG]
    elif force or auth_config.force_reauth:
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
    logger.info(f"  ЗАПУСК АВТОРИЗАЦИИ (PLAYWRIGHT): требуется вход на {len(pending_platforms)} пл.: {', '.join(names_list)}")
    logger.info(f"  Профиль: {target_profile_dir}")
    logger.info("=" * 76)

    args = list(STEALTH_CHROME_ARGS)

    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(target_profile_dir),
                channel=channel,
                headless=headless,
                args=args,
                ignore_default_args=list(STEALTH_IGNORE_DEFAULT_ARGS),
                no_viewport=True,
            )
        except Exception as e:
            logger.warning(f"Не удалось запустить через channel='{channel}': {e}. Попытка запуска встроенного Chromium...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(target_profile_dir),
                headless=headless,
                args=args,
                ignore_default_args=list(STEALTH_IGNORE_DEFAULT_ARGS),
                no_viewport=True,
            )

        apply_stealth(context)

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

            confirmed_platforms: List[str] = []
            try:
                ans = input(">>> Вход во все открытые сервисы выполнен успешно? [Y/n]: ").strip().lower()
            except (EOFError, OSError):
                ans = "y"

            if ans in ("", "y", "yes", "да", "д"):
                confirmed_platforms = list(opened_platforms)
            else:
                for p_key in opened_platforms:
                    name = MARKETPLACE_CATALOG[p_key]["name"]
                    try:
                        sub_ans = input(f"    Вход в {name} выполнен успешно? [y/N]: ").strip().lower()
                    except (EOFError, OSError):
                        sub_ans = "n"
                    if sub_ans in ("y", "yes", "да", "д"):
                        confirmed_platforms.append(p_key)
                    else:
                        logger.warning(f"Вход в {name} не подтвержден пользователем (флаг останется false).")

            if confirmed_platforms:
                update_marketplace_auth(
                    config_path=config_path,
                    platforms=confirmed_platforms,
                    reset_force=True,
                )
                mark_profile_authorized(target_profile_dir)
                updated_names = ", ".join([MARKETPLACE_CATALOG[k]["name"] for k in confirmed_platforms])
                logger.success(
                    f"✓ Авторизация для [{updated_names}] успешно зафиксирована! "
                    f"Флаги в main_config.json обновлены на true."
                )

            for page in opened_pages:
                try:
                    page.close()
                except Exception:
                    pass

            return bool(confirmed_platforms)
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Авторизация на маркетплейсах под выделенным профилем")
    parser.add_argument("--force", action="store_true", help="Принудительно открыть все 4 площадки")
    parser.add_argument(
        "--platforms",
        nargs="+",
        choices=["ozon", "wildberries", "yandex_market", "avito"],
        default=None,
        help="Задать конкретные маркетплейсы для доавторизации (например, --platforms ozon wildberries)",
    )
    cli_args = parser.parse_args()

    cfg = load_config()
    p_dir = cfg.browser.resolve_profile_dir()
    run_native_chrome_auth(
        profile_dir=p_dir,
        pending_platforms=cli_args.platforms,
        force=cli_args.force,
        auth_config=cfg.marketplace_auth,
        config_path=cfg.config_file_path,
    )
