"""
browser_downloader.py — Модуль выгрузки страниц (Stage 1) через авторизованный профиль Google Chrome.

Особенности:
  - Использует BrowserManager для подключения по CDP или прямого запуска с реальным профилем Chrome.
  - Поддерживает безопасное сохранение сессионных данных без флага --no-sandbox и без флагов автоматизации.
  - Ожидание полной гидратации и отрисовки персонализированных цен (Ozon Карта, WB Кошелек, Яндекс Пэй, Авито).
  - Сохранение полного HTML/DOM в папку downloads/pages_cache/{marketplace}_{sku}_{timestamp}.html.
"""
import os
import re
import time
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.sync_api import sync_playwright, BrowserContext, Page

from config_loader import AppConfig
from browser_manager import BrowserManager
from router import classify_url


def extract_sku(url: str, platform: str) -> str:
    """Извлекает идентификатор (артикул/ID) товара для именования файла."""
    clean_url = url.split("?")[0].rstrip("/")

    if platform == "wildberries":
        m = re.search(r"/catalog/(\d+)/", clean_url)
        if m:
            return m.group(1)

    elif platform == "ozon":
        m = re.search(r"-(\d{6,})/?$", clean_url) or re.search(r"/product/.*?(\d{6,})", clean_url)
        if m:
            return m.group(1)

    elif platform == "yandex_market":
        m = re.search(r"/(?:product|card)/.*?(\d{6,})", clean_url) or re.search(r"/(\d{8,})", clean_url)
        if m:
            return m.group(1)

    elif platform == "avito":
        m = re.search(r"_(\d{8,})$", clean_url) or re.search(r"/(\d{8,})$", clean_url)
        if m:
            return m.group(1)

    # Fallback: хэш или последний фрагмент
    slug = clean_url.split("/")[-1][:30]
    return re.sub(r"[^a-zA-Z0-9_-]", "_", slug) or "item"


def cleanup_old_dumps(dump_dir: Path, max_days: int) -> int:
    """Удаляет сохраненные дампы страниц старше max_days дней."""
    if not dump_dir.exists() or max_days <= 0:
        return 0

    cutoff = datetime.now() - timedelta(days=max_days)
    deleted = 0
    for p in dump_dir.glob("*.html"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                p.unlink()
                deleted += 1
        except Exception:
            pass

    if deleted > 0:
        logger.info(f"Очистка кэша: удалено {deleted} устаревших дампов страниц (старше {max_days} дн.)")
    return deleted


class ChromeProfileDownloader:
    """Загрузчик страниц через авторизованный профиль Chrome с использованием BrowserManager."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.browser_manager = BrowserManager(config)
        self.user_data_dir = self.browser_manager.user_data_dir
        self.profile_name = self.browser_manager.profile_directory
        self.dump_dir = config.resolve_dump_pages_dir()
        self.cdp_port = config.chrome_cdp_port

    def download_url(self, page: Page, url: str) -> dict:
        """
        Загружает страницу товара, ожидает отрисовки персонализированных цен и сохраняет дамп.
        """
        platform = classify_url(url)
        prefix = f"[{platform.upper()}]"
        sku = extract_sku(url, platform)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{platform}_{sku}_{ts}.html"
        save_path = self.dump_dir / filename

        logger.info(f"{prefix} Загрузка страницы: {url}")

        result = {
            "url": url,
            "platform": platform,
            "sku": sku,
            "dump_path": str(save_path),
            "status": "ERROR",
            "timestamp": datetime.now(),
        }

        try:
            # Рандомная задержка 1-2.5с перед переходом
            time.sleep(random.uniform(1.0, 2.5))

            try:
                page.goto(url, wait_until="commit", timeout=30_000)
                page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception as e:
                if "ERR_ABORTED" in str(e):
                    logger.warning(f"{prefix} Мягкий перехват ERR_ABORTED: страница начала загрузку, продолжаем...")
                    page.wait_for_timeout(3000)
                else:
                    raise e

            # Проверка сетевой активности и рендеринга
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass

            # Платформо-специфичное ожидание отрисовки цен
            try:
                if platform == "ozon":
                    page.wait_for_selector('[data-widget="webPrice"]', timeout=6000)
                elif platform == "wildberries":
                    page.wait_for_selector('.price-block__wallet-price, .price-block__final-price, ins.priceBlockFinalPrice', timeout=6000)
                elif platform == "yandex_market":
                    page.wait_for_selector('[data-auto="snippet-price-current"], [data-auto="cpa-price"]', timeout=6000)
                elif platform == "avito":
                    page.wait_for_selector('[data-marker="item-view/item-price"]', timeout=6000)
            except Exception:
                pass

            # Пауза для гидратации и отрисовки персональных скидок (2–3 секунды)
            page.wait_for_timeout(2500)

            # Сохранение полного HTML/DOM
            html_content = page.content()
            if not html_content or len(html_content.strip()) < 20:
                logger.warning(f"{prefix} Получен пустой или некорректный HTML ({len(html_content) if html_content else 0} байт)")
                result["status"] = "ERROR"
                return result

            save_path.write_text(html_content, encoding="utf-8", errors="replace")

            result["status"] = "SUCCESS"
            logger.success(f"{prefix} ✓ Страница успешно выгружена ({len(html_content)} байт) → {filename}")

        except Exception as e:
            logger.error(f"{prefix} Ошибка выгрузки страницы {url}: {e}")
            result["status"] = "ERROR"

        return result

    def download_all(self, urls_file: Path) -> list[dict]:
        """
        Последовательно выгружает все ссылки из файла через сессию браузера,
        управляемую BrowserManager. Для каждого URL открывается и закрывается отдельная вкладка.
        """
        if not urls_file.exists():
            logger.error(f"Файл ссылок не найден: {urls_file}")
            return []

        # Запускаем очистку старых дампов
        if self.config.cleanup_cache_days > 0:
            cleanup_old_dumps(self.dump_dir, self.config.cleanup_cache_days)

        from router import _iter_urls
        urls = list(_iter_urls(urls_file))
        if not urls:
            logger.warning("Список ссылок пуст.")
            return []

        logger.info(
            f"Начало выгрузки {len(urls)} страниц через Chrome "
            f"(режим: {self.browser_manager.mode}, профиль: '{self.profile_name}')..."
        )
        results = []

        with sync_playwright() as pw:
            context, browser = self.browser_manager.get_context(pw)

            try:
                # Режим первичной авторизации: проверяет профиль и при необходимости ждет входа
                self.browser_manager.ensure_marketplaces_auth(context)

                for idx, url in enumerate(urls, start=1):
                    logger.info(f"[{idx}/{len(urls)}] Обработка: {url}")
                    page = context.new_page()
                    try:
                        res = self.download_url(page, url)
                        results.append(res)
                    finally:
                        try:
                            page.close()
                        except Exception:
                            pass
            finally:
                self.browser_manager.close(context, browser)

        return results
