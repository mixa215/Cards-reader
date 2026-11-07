"""
main.py — Точка входа. Диспетчер пайплайна парсера цен с поддержкой периодического запуска.

Режимы запуска:
    1. По расписанию (из main_config.json):
       python src/main.py

    2. Однократный запуск (флаг --once):
       python src/main.py --once

    3. С указанием кастомного конфига или файла ссылок:
       python src/main.py --config main_config.json --urls urls.txt
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


# ─── Одиночная итерация парсинга ──────────────────────────────────────────────
def run_iteration(urls_file: Path, excel_file: Optional[Path] = None) -> bool:
    """
    Выполняет один полный цикл обработки всех ссылок и дозаписи в Excel.

    Returns:
        True если итерация успешно обработана, иначе False.
    """
    logger.info("=" * 60)
    logger.info("  ПАРСЕР ЦЕН — Ozon / Wildberries / Яндекс Маркет / Avito")
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

    # 3. Обход всех URL
    success_count = 0
    blocked_count = 0
    error_count = 0

    for idx, (url, platform) in enumerate(routed, start=1):
        logger.info(f"\n[{idx}/{total}] Обработка: {url}")
        checked_at = datetime.now()

        # Для неизвестных платформ — сразу в отчёт со статусом
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

        # Загрузка страницы через соответствующий адаптер
        browser_result = fetch(url, platform)
        status = browser_result["status"]
        screenshot_path = browser_result.get("screenshot_path")
        raw_text = browser_result.get("raw_price_text", "")

        # Извлечение цены
        price: float | None = None
        if status == "SUCCESS":
            if browser_result.get("price") is not None:
                price = browser_result["price"]
            else:
                price = extract_price(screenshot_path, raw_text_hint=raw_text)

            if price is None:
                logger.warning(f"Цена не извлечена для {url}")
                status = "NOT_FOUND"

        # Дозапись в Excel с фиксацией точного времени текущего среза
        excel.write(
            platform=platform,
            url=url,
            price=price,
            status=status,
            screenshot_path=screenshot_path,
            checked_at=checked_at,
        )

        # Счётчики
        if status == "SUCCESS":
            success_count += 1
        elif status in ("CAPTCHA_BLOCKED", "PRODUCT_DELETED", "SELECTOR_NOT_FOUND", "NOT_FOUND"):
            blocked_count += 1
        else:
            error_count += 1

    # 4. Финальное сохранение
    excel.close()

    # 5. Итоговая сводка
    logger.info("\n" + "=" * 60)
    logger.success(f"✓ Успешно:   {success_count}")
    logger.warning(f"⚠ Заблокировано / не найдено: {blocked_count}")
    logger.error(  f"✗ Ошибки:    {error_count}")
    logger.info(   f"  Итого:     {total}")
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

    logger.info(f"Запуск планировщика Cards-reader:")
    logger.info(f"  - Файл конфигурации: enabled={cfg.enabled}, interval={interval_minutes} мин.")
    logger.info(f"  - Входные ссылки:    {urls_path}")
    logger.info(f"  - Файл отчёта:       {excel_target}")

    # Если планировщик отключен или передан флаг --once — выполняем разовый прогон
    if run_once or not cfg.enabled:
        logger.info("Режим разового запуска (--once или enabled=false).")
        run_iteration(urls_path, excel_file=excel_target)
        return

    iteration = 1
    try:
        while True:
            logger.info(f"\n{'#' * 60}")
            logger.info(f"  ЦИКЛ #{iteration} — Срез цен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"{'#' * 60}")

            # Резолвим имя отчёта (на случай смены суток в 00:00)
            current_excel_target = cfg.resolve_excel_path(is_scheduled=is_scheduled)
            run_iteration(urls_path, excel_file=current_excel_target)

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
        description="Парсер цен Ozon, Wildberries, Яндекс Маркет и Avito с планировщиком"
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
        "--once",
        action="store_true",
        help="Выполнить один цикл проверки и выйти (без циклического ожидания)",
    )
    args = parser.parse_args()

    # Загружаем конфигурацию
    cfg = load_config(args.config)
    if args.interval and args.interval > 0:
        cfg.check_interval_minutes = args.interval

    run_scheduler(cfg, urls_override=args.urls, run_once=args.once)


if __name__ == "__main__":
    main()
