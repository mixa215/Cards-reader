"""
avito_worker.py — Модуль парсинга карточек/лотов Avito (Авито).

Архитектура:
  1. AvitoParser:
     - Очистка и нормализация цены (удаление \xa0, валюты, приведение к float).
     - Трёхуровневая fallback-стратегия:
         а) Schema.org JSON-LD (<script type="application/ld+json">, @type: Product)
         б) Гидратационные данные SSR (window.__staticRouterHydrationData / window.__initialData__)
         в) DOM CSS-селекторы и Meta-теги (data-marker="item-view/item-price", product:price:amount)
     - Детектирование защит (капча, Cloudflare/Qrator firewall, блокировка IP).
     - Детектирование снятия с публикации / закрытия объявления.

  2. fetch_avito():
     - Адаптер браузера на базе stealth-движка Camoufox (с обходом фингерпринтов).
     - Сохранение скриншотов цены (screenshots/prices/) и экранов ошибок (screenshots/errors/).
     - Возврат унифицированного BrowserResult для общего пайплайна.
"""
import json
import re
import random
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from config import (
    SCREENSHOTS_PRICES_DIR,
    SCREENSHOTS_ERRORS_DIR,
    PAGE_LOAD_TIMEOUT,
    NETWORK_IDLE_TIMEOUT,
    PRICE_SELECTORS,
    CAPTCHA_SELECTORS,
    CAPTCHA_TEXT_MARKERS,
    DELETED_TEXT_MARKERS,
    NOT_FOUND_TITLE_KEYWORDS,
)


@dataclass
class AvitoItemData:
    """Структура данных карточки/лота Avito."""
    url: str
    title: Optional[str] = None
    price: Optional[float] = None
    old_price: Optional[float] = None
    currency: str = "RUB"
    status: str = "SUCCESS"  # SUCCESS, CAPTCHA_BLOCKED, PRODUCT_DELETED, NOT_FOUND, ERROR
    city: Optional[str] = None
    seller: Optional[str] = None
    is_active: bool = True
    raw_price_text: str = ""
    parse_source: Optional[str] = None  # 'json_ld', 'hydration', 'dom'
    screenshot_path: Optional[str] = None


class AvitoParser:
    """Парсер данных Avito с трёхуровневым каскадным fallback."""

    @staticmethod
    def clean_price(raw_price: Any) -> Optional[float]:
        """
        Очищает значение цены от неразрывных пробелов, знаков валюты и приводит к float.

        Примеры:
            "10 490 ₽" -> 10490.0
            "10\xa0490&nbsp;₽" -> 10490.0
            10490 -> 10490.0
            "99,90 руб." -> 99.9
        """
        if raw_price is None:
            return None
        if isinstance(raw_price, (int, float)):
            return float(raw_price)

        text = str(raw_price)
        # Замена сущностей HTML и неразрывных пробелов
        text = text.replace("&nbsp;", " ").replace("\u00a0", " ").replace("\xa0", " ")
        # Удаление валютных символов и пробелов
        text = re.sub(r"[₽руб\.BYNbyn\s]", "", text, flags=re.IGNORECASE)
        # Запятая в качестве разделителя десятичной части -> точка
        text = text.replace(",", ".")

        # Если несколько точек, оставляем только последнюю
        parts = text.split(".")
        if len(parts) > 2:
            text = "".join(parts[:-1]) + "." + parts[-1]

        try:
            val = float(text)
            if 0 < val < 100_000_000:
                return val
        except ValueError:
            pass
        return None

    def check_anti_fraud(self, html: str) -> Optional[str]:
        """Проверяет страницу на наличие капчи, экрана firewall или блокировки."""
        lower = html.lower()
        for marker in CAPTCHA_TEXT_MARKERS:
            if marker in lower:
                return "CAPTCHA_BLOCKED"
        return None

    def check_is_closed(self, html: str) -> bool:
        """Проверяет страницу на маркеры завершённого / удалённого объявления."""
        lower = html.lower()
        for marker in DELETED_TEXT_MARKERS:
            if marker in lower:
                return True
        return False

    def parse_json_ld(self, html: str) -> Optional[dict]:
        """
        Стратегия (а): разбор Schema.org (<script type="application/ld+json"> с @type: Product).
        """
        scripts = re.findall(
            r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        for s in scripts:
            try:
                data = json.loads(s.strip())
                items = data.get("@graph", [data]) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        name = item.get("name")
                        offers = item.get("offers", {})
                        price = None
                        currency = "RUB"
                        if isinstance(offers, dict):
                            price = self.clean_price(offers.get("price"))
                            currency = offers.get("priceCurrency", "RUB")
                        elif isinstance(offers, list) and offers:
                            price = self.clean_price(offers[0].get("price"))
                            currency = offers[0].get("priceCurrency", "RUB")

                        if name or price is not None:
                            return {
                                "title": name,
                                "price": price,
                                "currency": currency,
                                "description": item.get("description"),
                            }
            except Exception:
                continue
        return None

    def parse_hydration_data(self, html: str) -> Optional[dict]:
        """
        Стратегия (б): разбор гидратационных данных SSR:
        - window.__staticRouterHydrationData (современный Remix/React роутер)
        - window.__initialData__ (классический формат Avito)
        """
        # 1. Попытка window.__staticRouterHydrationData
        m_router = re.search(
            r"window\.__staticRouterHydrationData\s*=\s*(.*?);\s*</script>",
            html,
            re.DOTALL,
        )
        if m_router:
            raw = m_router.group(1).strip()
            try:
                if raw.startswith("JSON.parse("):
                    inner = re.search(r"JSON\.parse\((['\"])(.*?)\1\)", raw, re.DOTALL)
                    json_str = json.loads(f'"{inner.group(2)}"') if inner else raw
                    data = json.loads(json_str)
                else:
                    data = json.loads(raw)

                loader_data = data.get("loaderData", {})
                item_route = loader_data.get("catalog-or-main-or-item", {})
                buyer_item = item_route.get("buyerItem", {})
                if buyer_item:
                    item_info = buyer_item.get("item", {})
                    title = item_info.get("title") or buyer_item.get("seoTitle")
                    price = self.clean_price(item_info.get("price") or buyer_item.get("priceString"))
                    city = item_info.get("address")
                    seller = buyer_item.get("seller", {}).get("name")
                    is_closed = buyer_item.get("closedItem", False) or not buyer_item.get("isItemActiveOrUserHasAccess", True)

                    return {
                        "title": title,
                        "price": price,
                        "city": city,
                        "seller": seller,
                        "is_closed": is_closed,
                        "raw_price_text": buyer_item.get("priceString", ""),
                    }
            except Exception:
                pass

        # 2. Попытка window.__initialData__
        m_init = re.search(
            r"window\.__initialData__\s*=\s*(.*?);\s*</script>",
            html,
            re.DOTALL,
        )
        if m_init:
            raw = m_init.group(1).strip()
            try:
                if raw.startswith("JSON.parse("):
                    inner = re.search(r"JSON\.parse\((['\"])(.*?)\1\)", raw, re.DOTALL)
                    json_str = json.loads(f'"{inner.group(2)}"') if inner else raw
                    data = json.loads(json_str)
                else:
                    data = json.loads(raw)

                for key in data:
                    if "item" in key.lower() and isinstance(data[key], dict):
                        item_data = data[key]
                        return {
                            "title": item_data.get("title"),
                            "price": self.clean_price(item_data.get("price")),
                            "city": item_data.get("address") or item_data.get("city"),
                            "seller": item_data.get("seller", {}).get("name") if isinstance(item_data.get("seller"), dict) else None,
                            "is_closed": item_data.get("closed", False),
                            "raw_price_text": str(item_data.get("price", "")),
                        }
            except Exception:
                pass

        return None

    def parse_dom(self, html: str) -> Optional[dict]:
        """
        Стратегия (в): разбор CSS-маркеров, data-marker и мета-тегов в разметке страницы.
        """
        result = {}

        # 1. Цена из meta-тегов (product:price:amount)
        m_meta = re.search(
            r'<meta[^>]+property=[\"\']product:price:amount[\"\'][^>]+content=[\"\'](\d+)[\"\']',
            html,
            re.IGNORECASE,
        )
        if not m_meta:
            m_meta = re.search(
                r'<meta[^>]+content=[\"\'](\d+)[\"\'][^>]+property=[\"\']product:price:amount[\"\']',
                html,
                re.IGNORECASE,
            )
        if m_meta:
            result["price"] = float(m_meta.group(1))

        # 2. Цена из data-marker="item-view/item-price" или itemprop="price"
        if "price" not in result:
            m_marker = re.search(
                r'data-marker=[\"\']item-view/item-price[\"\'][^>]*>(.*?)</div>',
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if m_marker:
                clean_snippet = re.sub(r'<[^>]+>', ' ', m_marker.group(1))
                clean_snippet = clean_snippet.replace("&nbsp;", " ").replace("\u00a0", " ")
                num_m = re.search(r'(\d[\d\s]*\d|\d+)', clean_snippet)
                if num_m:
                    p = self.clean_price(num_m.group(1))
                    if p is not None:
                        result["price"] = p
                        result["raw_price_text"] = num_m.group(1).strip()

        # 3. Название лота
        title_patterns = [
            r'<h1[^>]*data-marker=[\"\']item-view/title-info[\"\'][^>]*>(.*?)</h1>',
            r'<meta[^>]+property=[\"\']og:title[\"\'][^>]+content=[\"\'](.*?)[\"\']',
            r'<meta[^>]+content=[\"\'](.*?)[\"\'][^>]+property=[\"\']og:title[\"\']',
            r'<title>(.*?)</title>',
        ]
        for pat in title_patterns:
            m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
            if m:
                clean_t = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                clean_t = clean_t.replace("&nbsp;", " ").replace("\u00a0", " ")
                clean_t = re.sub(r'\s+', ' ', clean_t)
                # Убираем стандартные суффиксы заголовков Avito
                clean_t = clean_t.split(" купить в ")[0].split(" | ")[0]
                if clean_t:
                    result["title"] = clean_t
                    break

        # 4. Адрес / Город
        city_patterns = [
            r'data-marker=[\"\']item-view/item-address[\"\'][^>]*>(.*?)</span>',
            r'class=[\"\'][^\"\']*style-item-address-[^\"\']*[\"\'][^>]*>(.*?)</span>',
        ]
        for pat in city_patterns:
            m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
            if m:
                c = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                if c:
                    result["city"] = c
                    break

        return result if (result.get("price") or result.get("title")) else None

    def parse_html(self, html: str, url: str = "") -> AvitoItemData:
        """
        Главный метод извлечения данных из HTML Avito с каскадным fallback.
        """
        data = AvitoItemData(url=url)

        # 1. Проверка капчи и антифрода
        status = self.check_anti_fraud(html)
        if status:
            data.status = status
            return data

        # 2. Проверка закрытия/снятия объявления
        if self.check_is_closed(html):
            data.status = "PRODUCT_DELETED"
            data.is_active = False

        # 3. Каскадный Fallback:
        # а) Schema.org JSON-LD
        json_ld = self.parse_json_ld(html)
        if json_ld:
            data.title = json_ld.get("title")
            data.price = json_ld.get("price")
            data.currency = json_ld.get("currency", "RUB")
            data.parse_source = "json_ld"

        # б) SSR Hydration Data
        hydration = self.parse_hydration_data(html)
        if hydration:
            if not data.title or not data.title.strip():
                data.title = hydration.get("title")
            if data.price is None:
                data.price = hydration.get("price")
            if hydration.get("city"):
                data.city = hydration.get("city")
            if hydration.get("seller"):
                data.seller = hydration.get("seller")
            if hydration.get("is_closed"):
                data.status = "PRODUCT_DELETED"
                data.is_active = False
            if hydration.get("raw_price_text"):
                data.raw_price_text = hydration.get("raw_price_text")
            if not data.parse_source:
                data.parse_source = "hydration"

        # в) DOM / CSS-селекторы
        dom = self.parse_dom(html)
        if dom:
            if not data.title:
                data.title = dom.get("title")
            if data.price is None:
                data.price = dom.get("price")
            if not data.city and dom.get("city"):
                data.city = dom.get("city")
            if not data.raw_price_text and dom.get("raw_price_text"):
                data.raw_price_text = dom.get("raw_price_text")
            if not data.parse_source:
                data.parse_source = "dom"

        # Финализация статуса
        if data.status != "PRODUCT_DELETED":
            if data.price is not None:
                data.status = "SUCCESS"
            else:
                data.status = "NOT_FOUND"

        return data


# ─── Вспомогательные функции для скриншотов ───────────────────────────────────

def _make_filename(platform: str, url: str) -> str:
    url_id = url.rstrip("/").split("/")[-1][:40].replace("?", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{platform}_{url_id}_{ts}.png"


def _screenshot_price(page, platform: str, url: str) -> Optional[str]:
    """Делает скриншот целевого элемента цены."""
    for selector in PRICE_SELECTORS.get(platform, []):
        try:
            locators = page.locator(selector)
            locators.first.wait_for(state="visible", timeout=3000)
            count = locators.count()
            for i in range(min(count, 5)):
                loc = locators.nth(i)
                if loc.is_visible(timeout=500):
                    path = SCREENSHOTS_PRICES_DIR / _make_filename(platform, url)
                    loc.screenshot(path=str(path))
                    logger.success(f"[AVITO] ✓ Скриншот элемента цены ({selector}) → {path.name}")
                    return str(path)
        except Exception:
            continue
    return None


def _screenshot_error(page, platform: str, url: str) -> Optional[str]:
    """Делает скриншот экрана блокировки или ошибки."""
    try:
        path = SCREENSHOTS_ERRORS_DIR / _make_filename(platform, url)
        page.screenshot(path=str(path), full_page=False)
        logger.warning(f"[AVITO] Скриншот ошибки сохранен → {path.name}")
        return str(path)
    except Exception as e:
        logger.error(f"[AVITO] Ошибка сохранения скриншота ошибки: {e}")
        return None


# ─── Адаптер загрузки карточки Avito ──────────────────────────────────────────

def fetch_avito(url: str) -> dict:
    """
    Загружает страницу Avito с обходом антибот-защит через Camoufox stealth.

    Returns:
        Словарь BrowserResult:
        {url, platform, status, screenshot_path, raw_price_text, price, title, city, seller}
    """
    from camoufox.sync_api import Camoufox

    result: dict = {
        "url": url,
        "platform": "avito",
        "status": "ERROR",
        "screenshot_path": None,
        "raw_price_text": "",
        "price": None,
        "title": None,
        "city": None,
        "seller": None,
    }

    parser = AvitoParser()

    try:
        logger.info(f"[AVITO] → {url}")
        with Camoufox(headless=True, geoip=True) as browser:
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                extra_http_headers={
                    "Referer": "https://www.avito.ru/",
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            page = context.new_page()

            # Рандомная задержка 1-2.5с для имитации человека
            time.sleep(random.uniform(1.0, 2.5))

            response = page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")

            # Проверка HTTP-статуса
            if response and response.status in (403, 429):
                logger.warning(f"[AVITO] HTTP {response.status} (Блокировка доступа / Firewall)")
                result["status"] = "CAPTCHA_BLOCKED"
                result["screenshot_path"] = _screenshot_error(page, "avito", url)
                context.close()
                return result

            try:
                page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT)
            except Exception:
                pass

            # Получаем HTML содержимое страницы
            html_content = page.content()

            # Парсим структурированные данные через AvitoParser
            parsed_data = parser.parse_html(html_content, url=url)
            result["status"] = parsed_data.status
            result["price"] = parsed_data.price
            result["title"] = parsed_data.title
            result["city"] = parsed_data.city
            result["seller"] = parsed_data.seller
            result["raw_price_text"] = parsed_data.raw_price_text

            if parsed_data.status == "CAPTCHA_BLOCKED":
                logger.warning(f"[AVITO] Обнаружена капча или экран проверки")
                result["screenshot_path"] = _screenshot_error(page, "avito", url)
            elif parsed_data.status == "PRODUCT_DELETED":
                logger.warning(f"[AVITO] Объявление снято с публикации или удалено")
                result["screenshot_path"] = _screenshot_error(page, "avito", url)
            else:
                # Делаем скриншот блока цены для отчета
                shot = _screenshot_price(page, "avito", url)
                if shot:
                    result["screenshot_path"] = shot
                elif parsed_data.price is not None:
                    # Если цена извлечена из JSON-LD / Hydration, но кроп не удался
                    result["status"] = "SUCCESS"
                else:
                    result["status"] = "SELECTOR_NOT_FOUND"

            context.close()

    except Exception as e:
        logger.error(f"[AVITO] Критическая ошибка при обработке: {e}")
        result["status"] = "ERROR"

    return result

