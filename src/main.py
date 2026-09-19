"""
main.py — Точка входа. Диспетчер пайплайна парсера цен.

Запуск:
    python src/main.py [--urls путь/к/файлу.txt]

Пайплайн для каждого URL:
  1. router.py    — классификация URL по маркетплейсу
  2. browser_worker.py — загрузка страницы нужным адаптером
  3. ocr_worker.py     — распознавание цены из скриншота
  4. excel_worker.py   — запись строки в отчёт
"""
import sys
import argparse
from datetime import datetime
from pathlib import Path

# Добавляем src/ в sys.path для корректных импортов
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger

import config
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


# ─── Основной конвейер ────────────────────────────────────────────────────────
def run(urls_file: Path) -> None:
    _setup_logging()

    logger.info("=" * 60)
    logger.info("  ПАРСЕР ЦЕН — Ozon / Wildberries / Яндекс Маркет")
    logger.info("=" * 60)

    # 1. Маршрутизация
    routed = route_urls(urls_file)
    if not routed:
        logger.error("Нет URL для обработки. Завершение.")
        return

    total = len(routed)
    logger.info(f"Всего URL к обработке: {total}")

    # 2. Инициализация Excel
    excel = ExcelWriter()

    # 3. Обход всех URL
    success_count = 0
    blocked_count = 0
    error_count = 0

    for idx, (url, platform) in enumerate(routed, start=1):
        logger.info(f"\n[{idx}/{total}] Обработка: {url}")
        checked_at = datetime.now()

        # Для неизвестных платформ — сразу в отчёт
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

        # OCR или DOM-текст → цена
        price: float | None = None
        if status == "SUCCESS":
            price = extract_price(screenshot_path, raw_text_hint=raw_text)
            if price is None:
                logger.warning(f"Цена не извлечена для {url}")
                status = "NOT_FOUND"

        # Запись в Excel
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
    logger.info(f"  Отчёт:     {excel.filepath}")
    logger.info("=" * 60)


# ─── CLI ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Парсер цен Ozon, Wildberries и Яндекс Маркет"
    )
    parser.add_argument(
        "--urls",
        type=Path,
        default=config.URLS_FILE,
        help=f"Путь к файлу со ссылками (по умолчанию: {config.URLS_FILE})",
    )
    args = parser.parse_args()
    run(args.urls)


if __name__ == "__main__":
    main()
