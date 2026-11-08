"""
main.py — Точка входа. Диспетчер пайплайна парсера цен с поддержкой:
  1. Стандартного парсинга (Camoufox stealth + EasyOCR).
  2. Двухэтапного парсинга персональных цен (авторизованный Google Chrome + оффлайн-разбор).
  3. Циклического планировщика по расписанию (main_config.json).
"""
import sys
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Добавляем src/ в sys.path для корректных импортов
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger

import config
from config_loader import load_config, AppConfig
from router import route_urls
from browser_worker import fetch
from ocr_worker import extract_price
from excel_worker import ExcelWriter


# ─── Настройка Loguru ─────────────────────────────────────────────────────────
def _setup_logging() -> None:
    log_dir = config.BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logger.remove()  # Убираем дефолтный обработчик

    # Консоль — только INFO и выше, с цветом
    logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{message}</cyan>"
        ),
        colorize=True,
    )

    # Файл — всё включая DEBUG
    logger.add(
        str(log_file),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        encoding="utf-8",
        rotation="50 MB",
    )

    logger.info(f"Лог сессии: {log_file}")


# ─── Стандартная одиночная итерация парсинга ──────────────────────────────────
def run_standard_iteration(urls_file: Path, excel_file: Optional[Path] = None) -> bool:
    """
    Выполняет стандартный цикл (Camoufox / Playwright + кроп-скриншот + OCR).
    """
    logger.info("=" * 60)
    logger.info("  СТАНДАРТНЫЙ ПАРСЕР ЦЕН — Ozon / WB / Яндекс Маркет / Avito")
    logger.info("=" * 60)

    # 1. Маршрутизация ссылок
    routed = route_urls(urls_file)
    if not routed:
        logger.error("Нет URL для обработки.")
        return False

    total = len(routed)
    logger.info(f"Всего URL к обработке: {total}")

    # 2. Инициализация Excel (в режиме append / дозаписи)
    excel = ExcelWriter(filepath=excel_file)

    success_count = 0
    blocked_count = 0
    error_count = 0

    for idx, (url, platform) in enumerate(routed, start=1):
        logger.info(f"\n[{idx}/{total}] Обработка: {url}")
        checked_at = datetime.now()

        if platform == "unknown":
            excel.write(
                platform="unknown",
                url=url,
                price=None,
                status="UNSUPPORTED_URL",
                screenshot_path=None,
                checked_at=checked_at,
            )
            error_count += 1
            continue

        browser_result = fetch(url, platform)
        status = browser_result["status"]
        screenshot_path = browser_result.get("screenshot_path")
        raw_text = browser_result.get("raw_price_text", "")

        price: float | None = None
        if status == "SUCCESS":
            if browser_result.get("price") is not None:
                price = browser_result["price"]
            else:
                price = extract_price(screenshot_path, raw_text_hint=raw_text)

            if price is None:
                logger.warning(f"Цена не извлечена для {url}")
                status = "NOT_FOUND"

        excel.write(
            platform=platform,
            url=url,
            price=price,
            status=status,
            screenshot_path=screenshot_path,
            checked_at=checked_at,
        )

        if status == "SUCCESS":
            success_count += 1
        elif status in ("CAPTCHA_BLOCKED", "PRODUCT_DELETED", "SELECTOR_NOT_FOUND", "NOT_FOUND"):
            blocked_count += 1
        else:
            error_count += 1

    excel.close()

    logger.info("\n" + "=" * 60)
    logger.success(f"✓ Успешно:   {success_count}")
    logger.warning(f"⚠ Заблокировано / не найдено: {blocked_count}")
    logger.error(  f"✗ Ошибки:    {error_count}")
    logger.info(   f"  Итого:     {total}")
    logger.info(   f"  Отчёт:     {excel.filepath}")
    logger.info("=" * 60)
    return True


# ─── Двухэтапная итерация (авторизованный Chrome + оффлайн-разбор) ────────────
def run_two_stage_iteration(cfg: AppConfig, urls_file: Path, excel_file: Optional[Path] = None) -> bool:
    """
    Двухэтапная обработка:
      Этап 1: Выгрузка страниц через постоянный профиль Chrome (chrome_automation_profile) в downloads/pages_cache/.
      Этап 2: Оффлайн-парсинг сохраненных HTML-дампов и запись персональных цен в Excel.
    """
    from browser_manager import BrowserManager
    from browser_downloader import ChromeProfileDownloader
    from offline_parser import OfflinePageParser

    logger.info("=" * 60)
    logger.info("  ДВУХЭТАПНЫЙ ПАРСИНГ — ПОСТОЯННЫЙ ПРОФИЛЬ CHROME + ОФФЛАЙН РАЗБОР")
    logger.info("=" * 60)

    # 1. Подготовка постоянного профиля автоматизации
    browser_manager = BrowserManager(cfg)
    browser_manager.ensure_chrome_ready()

    # 2. Онлайн-выгрузка через постоянный профиль Chrome
    downloader = ChromeProfileDownloader(cfg)
    download_results = downloader.download_all(urls_file)
    if not download_results:
        logger.warning("Нет выгруженных страниц для обработки.")
        return False

    # 2. Локальная оффлайн-обработка и запись в Excel
    excel = ExcelWriter(filepath=excel_file)
    parser = OfflinePageParser()

    success_count = 0
    blocked_count = 0
    error_count = 0

    for idx, item in enumerate(download_results, start=1):
        url = item["url"]
        platform = item["platform"]
        dump_path = Path(item["dump_path"])
        checked_at = item["timestamp"]
        prefix = f"[{platform.upper()}]"

        if item.get("status") == "ERROR" and not dump_path.exists():
            logger.warning(f"\n[{idx}/{len(download_results)}] {prefix} Пропуск оффлайн-разбора: дамп не выгружен ({url})")
            excel.write(
                platform=platform,
                url=url,
                price=None,
                status="DOWNLOAD_ERROR",
                screenshot_path=None,
                checked_at=checked_at,
                price_type="Обычная",
                base_price=None,
                dump_path=None,
            )
            error_count += 1
            continue

        logger.info(f"\n[{idx}/{len(download_results)}] Оффлайн-разбор: {dump_path.name}")

        parsed = parser.parse_file(dump_path, url=url, platform=platform)

        prefix = f"[{platform.upper()}]"
        logger.info(
            f"{prefix} Товар: '{parsed.title or 'N/A'}' | "
            f"Цена: {parsed.price} руб. ({parsed.price_type}) | Статус: {parsed.status}"
        )

        excel.write(
            platform=platform,
            url=url,
            price=parsed.price,
            status=parsed.status,
            screenshot_path=None,
            checked_at=checked_at,
            price_type=parsed.price_type,
            base_price=parsed.base_price,
            dump_path=str(dump_path),
        )

        if parsed.status == "SUCCESS":
            success_count += 1
        elif parsed.status in ("CAPTCHA_BLOCKED", "PRODUCT_DELETED", "NOT_FOUND"):
            blocked_count += 1
        else:
            error_count += 1

    excel.close()

    logger.info("\n" + "=" * 60)
    logger.success(f"✓ Успешно:   {success_count}")
    logger.warning(f"⚠ Заблокировано / не найдено: {blocked_count}")
    logger.error(  f"✗ Ошибки:    {error_count}")
    logger.info(   f"  Итого:     {len(download_results)}")
    logger.info(   f"  Отчёт:     {excel.filepath}")
    logger.info("=" * 60)
    return True


# ─── Планировщик периодического выполнения ────────────────────────────────────
def run_scheduler(cfg: AppConfig, urls_override: Optional[Path] = None, run_once: bool = False) -> None:
    """
    Запускает циклический планировщик проверок по расписанию.
    """
    _setup_logging()

    urls_path = urls_override or cfg.resolve_urls_path()
    interval_minutes = cfg.check_interval_minutes
    is_scheduled = cfg.enabled and not run_once

    excel_target = cfg.resolve_excel_path(is_scheduled=is_scheduled)

    mode_title = "Двухэтапный (Chrome профиль)" if cfg.use_local_profile_downloader else "Стандартный (Camoufox OCR)"
    logger.info(f"Запуск планировщика Cards-reader:")
    logger.info(f"  - Режим работы:      {mode_title}")
    logger.info(f"  - Файл конфигурации: enabled={cfg.enabled}, interval={interval_minutes} мин.")
    logger.info(f"  - Входные ссылки:    {urls_path}")
    logger.info(f"  - Файл отчёта:       {excel_target}")

    def execute_pass(target_file: Path):
        if cfg.use_local_profile_downloader:
            return run_two_stage_iteration(cfg, urls_path, excel_file=target_file)
        else:
            return run_standard_iteration(urls_path, excel_file=target_file)

    # Если планировщик отключен или передан флаг --once — выполняем разовый прогон
    if run_once or not cfg.enabled:
        logger.info("Режим разового запуска (--once или enabled=false).")
        execute_pass(excel_target)
        return

    iteration = 1
    try:
        while True:
            logger.info(f"\n{'#' * 60}")
            logger.info(f"  ЦИКЛ #{iteration} — Срез цен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"{'#' * 60}")

            # Резолвим имя отчёта (на случай смены суток в 00:00)
            current_excel_target = cfg.resolve_excel_path(is_scheduled=is_scheduled)
            execute_pass(current_excel_target)

            next_run = datetime.now() + timedelta(minutes=interval_minutes)
            next_run_str = next_run.strftime("%H:%M:%S")

            logger.info(
                f"[INFO] Проверка завершена. Следующий запуск через {interval_minutes} минут "
                f"(в {next_run_str})..."
            )

            iteration += 1

            # Посекундный сон для мгновенной реакции на Ctrl+C / SIGINT
            total_sleep_sec = interval_minutes * 60
            for _ in range(total_sleep_sec):
                time.sleep(1)

    except KeyboardInterrupt:
        logger.info(
            "\n[INTERRUPT] Получен сигнал прерывания (Ctrl+C). "
            "Штатное завершение сессии планировщика."
        )


# ─── Точка входа CLI ─────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Парсер цен Ozon, Wildberries, Яндекс Маркет и Avito с поддержкой персональных цен"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Путь к файлу конфигурации (по умолчанию: main_config.json)",
    )
    parser.add_argument(
        "--urls",
        type=Path,
        default=None,
        help="Переопределить путь к файлу ссылок (по умолчанию из конфига)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Переопределить интервал проверки в минутах",
    )
    parser.add_argument(
        "--downloader",
        action="store_true",
        help="Принудительно включить двухэтапный режим через Chrome профиль",
    )
    parser.add_argument(
        "--standard",
        action="store_true",
        help="Принудительно использовать стандартный движок Camoufox OCR",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Выполнить один цикл проверки и выйти (без циклического ожидания)",
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Открыть вкладки маркетплейсов для авторизации в профиле (по флагам из main_config.json)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Принудительно открыть все 4 маркетплейса независимо от текущих флагов",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.auth:
        from auth_setup import run_auth_setup
        run_auth_setup(
            profile_dir=cfg.browser.resolve_profile_dir(),
            channel=cfg.browser.channel,
            headless=cfg.browser.headless,
            force=args.force,
            auth_config=cfg.marketplace_auth,
            config_path=cfg.config_file_path,
        )
        return
    if args.interval and args.interval > 0:
        cfg.check_interval_minutes = args.interval
    if args.downloader:
        cfg.use_local_profile_downloader = True
    elif args.standard:
        cfg.use_local_profile_downloader = False

    run_scheduler(cfg, urls_override=args.urls, run_once=args.once)


if __name__ == "__main__":
    main()
