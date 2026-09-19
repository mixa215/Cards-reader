"""
browser_worker.py — Адаптеры браузерной автоматизации для трёх маркетплейсов.

Адаптеры:
  - fetch_ozon()         — Camoufox stealth + случайная задержка 3–6с
  - fetch_wildberries()  — Playwright Chromium headless + мобильный viewport
  - fetch_yandex_market()— Camoufox + сохранение пользовательского профиля

Каждый адаптер возвращает словарь BrowserResult с полями:
  url, platform, status, screenshot_path, raw_price_text
"""
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from config import (
    SCREENSHOTS_PRICES_DIR,
    SCREENSHOTS_ERRORS_DIR,
    PAGE_LOAD_TIMEOUT,
    PRICE_SELECTOR_TIMEOUT,
    NETWORK_IDLE_TIMEOUT,
    OZON_DELAY_MIN,
    OZON_DELAY_MAX,
    WB_VIEWPORT,
    USER_DATA_DIR,

    PRICE_SELECTORS,
    CAPTCHA_SELECTORS,
    CAPTCHA_TEXT_MARKERS,
    DELETED_TEXT_MARKERS,
    NOT_FOUND_TITLE_KEYWORDS,
)

# ─── Тип результата адаптера ──────────────────────────────────────────────────
BrowserResult = dict  # {url, platform, status, screenshot_path, raw_price_text}


def _make_filename(platform: str, url: str) -> str:
    """Генерирует имя файла скриншота: platform_urlid_timestamp.png"""
    url_id = url.rstrip("/").split("/")[-1][:40].replace("?", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{platform}_{url_id}_{ts}.png"


def _check_page_status(page, platform: str) -> Optional[str]:
    """
    Проверяет страницу на наличие капчи/блокировки или удаленного товара.
    Возвращает:
      - 'CAPTCHA_BLOCKED' при обнаружении капчи или экрана блокировки
      - 'PRODUCT_DELETED' при обнаружении текста об удалении/отсутствии товара
      - None если страница валидна и готова к извлечению цены
    """
    # 1. На Яндекс Маркете — если появился экран SmartCaptcha, пробуем автоклик по чекбоксу
    if platform == "yandex_market":
        try:
            cb = page.locator(".CheckboxCaptcha, input[type='checkbox'], #js-button").first
            if cb.is_visible(timeout=1500):
                logger.info("Обнаружен чекбокс SmartCaptcha на Яндекс Маркете, выполняем клик...")
                cb.click()
                page.wait_for_timeout(4000)
        except Exception:
            pass

    # 2. Проверка URL на капчу (после попытки решения)
    current_url = getattr(page, "url", "").lower()
    if "showcaptcha" in current_url:
        logger.warning(f"Обнаружен URL капчи: {current_url}")
        return "CAPTCHA_BLOCKED"


    # 3. Извлечение и нормализация текста страницы
    norm_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=3000)
        norm_text = " ".join(body_text.replace("\u00a0", " ").split()).lower()
    except Exception:
        try:
            content = page.content()
            norm_text = " ".join(content.replace("\u00a0", " ").split()).lower()
        except Exception:
            pass

    title = ""
    try:
        title = page.title().lower()
    except Exception:
        pass

    # 4. На Wildberries — если экран 'Проверяем браузер', ждём перехода
    if platform == "wildberries" and "проверяем браузер" in norm_text:
        logger.info("Wildberries: экран 'Проверяем браузер', ожидание завершения проверки...")
        for _ in range(5):
            page.wait_for_timeout(1000)
            try:
                body_text = page.locator("body").inner_text(timeout=1000)
                norm_text = " ".join(body_text.replace("\u00a0", " ").split()).lower()
                if "проверяем браузер" not in norm_text:
                    break
            except Exception:
                pass

    # 5. Проверка текстовых маркеров капчи и блокировки
    for marker in CAPTCHA_TEXT_MARKERS:
        if marker in norm_text or marker in title:
            logger.warning(f"[{platform.upper()}] Обнаружен маркер капчи/блокировки: '{marker}'")
            return "CAPTCHA_BLOCKED"

    # 6. Проверка CSS-селекторов капчи
    for selector in CAPTCHA_SELECTORS.get(platform, []):
        try:
            if page.locator(selector).first.is_visible(timeout=300):
                logger.warning(f"[{platform.upper()}] Обнаружен селектор капчи: {selector}")
                return "CAPTCHA_BLOCKED"
        except Exception:
            continue

    # 7. Проверка маркеров удаленного товара
    for marker in DELETED_TEXT_MARKERS:
        if marker in norm_text:
            logger.warning(f"[{platform.upper()}] Обнаружен маркер удаленного товара: '{marker}'")
            return "PRODUCT_DELETED"

    # 8. Проверка ключевых слов 404 в заголовке
    if any(kw in title for kw in NOT_FOUND_TITLE_KEYWORDS):
        logger.warning(f"[{platform.upper()}] 404 в заголовке страницы: '{page.title()}'")
        return "PRODUCT_DELETED"

    return None


def _screenshot_price(page, platform: str, url: str) -> Optional[str]:
    """
    Делает скриншот исключительно с найденного селектора цены (locator.screenshot()).
    Fallback-OCR по всему экраку полностью отключен.
    Возвращает путь к файлу или None, если селектор не найден.
    """
    for selector in PRICE_SELECTORS.get(platform, []):
        try:
            locators = page.locator(selector)
            locators.first.wait_for(state="visible", timeout=min(PRICE_SELECTOR_TIMEOUT, 3000))
            count = locators.count()
            for i in range(min(count, 5)):
                loc = locators.nth(i)
                if not loc.is_visible(timeout=500):
                    continue

                # Обязательная проверка: перед вызовом screenshot() для Ozon
                # проверяем, что присутствует знак ₽ или слово руб
                if platform == "ozon":
                    text = loc.inner_text().strip()
                    if not any(cur in text.lower() for cur in ("₽", "руб")):
                        logger.debug(f"[OZON] Элемент {selector}[{i}] не содержит ₽/руб ('{text[:30]}'), ищем дальше...")
                        continue

                path = SCREENSHOTS_PRICES_DIR / _make_filename(platform, url)
                loc.screenshot(path=str(path))
                logger.success(f"✓ Скриншот элемента цены ({selector}) → {path.name}")
                return str(path)
        except Exception:
            continue

    logger.warning(f"[{platform.upper()}] Селектор цены не найден")
    return None


def _screenshot_error(page, platform: str, url: str) -> Optional[str]:
    """Делает скриншот страницы ошибки/капчи в папку screenshots/errors/."""
    try:
        path = SCREENSHOTS_ERRORS_DIR / _make_filename(platform, url)
        page.screenshot(path=str(path), full_page=False)
        logger.warning(f"Скриншот ошибки сохранен → {path.name}")
        return str(path)
    except Exception as e:
        logger.error(f"Ошибка при сохранении скриншота ошибки: {e}")
        return None


def _extract_price_text(page, platform: str) -> str:
    """Извлекает текст из элемента цены для передачи в OCR (как подсказка)."""
    for selector in PRICE_SELECTORS.get(platform, []):
        try:
            locators = page.locator(selector)
            count = locators.count()
            for i in range(min(count, 5)):
                loc = locators.nth(i)
                if loc.is_visible(timeout=500):
                    text = loc.inner_text().strip()
                    if platform == "ozon" and not any(cur in text.lower() for cur in ("₽", "руб")):
                        continue
                    if text:
                        return text
        except Exception:
            continue
    return ""


# ─── Адаптер Ozon (Camoufox stealth) ─────────────────────────────────────────
def fetch_ozon(url: str) -> BrowserResult:
    """Загружает карточку товара Ozon через stealth-движок Camoufox."""
    from camoufox.sync_api import Camoufox

    result: BrowserResult = {
        "url": url,
        "platform": "ozon",
        "status": "ERROR",
        "screenshot_path": None,
        "raw_price_text": "",
    }

    try:
        logger.info(f"[OZON] → {url}")
        with Camoufox(headless=True, geoip=True) as browser:
            page = browser.new_page()

            # Стелс-задержка перед переходом
            delay = random.uniform(OZON_DELAY_MIN, OZON_DELAY_MAX)
            logger.debug(f"Ozon: stealth-задержка {delay:.1f}с")
            time.sleep(delay)

            page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")

            try:
                page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT)
            except Exception:
                pass

            # Проверка статуса страницы перед обработкой цены
            page_status = _check_page_status(page, "ozon")
            if page_status:
                result["status"] = page_status
                result["screenshot_path"] = _screenshot_error(page, "ozon", url)
                return result

            # Ожидание рендеринга блока цены Ozon
            try:
                page.wait_for_selector('[data-widget="webPrice"]', timeout=8000)
                logger.debug("Ozon: блок [data-widget='webPrice'] найден")
            except Exception as e:
                logger.warning(f"Ozon: таймаут ожидания [data-widget='webPrice']: {e}")

            result["raw_price_text"] = _extract_price_text(page, "ozon")
            shot = _screenshot_price(page, "ozon", url)
            if shot:
                result["screenshot_path"] = shot
                result["status"] = "SUCCESS"
            else:
                result["status"] = "SELECTOR_NOT_FOUND"

    except Exception as e:
        logger.error(f"[OZON] Критическая ошибка: {e}")
        result["status"] = "ERROR"

    return result


# ─── Адаптер Wildberries (Camoufox stealth + desktop) ─────────────────────────
def fetch_wildberries(url: str) -> BrowserResult:
    """Загружает карточку товара WB через Camoufox stealth (десктопный режим)."""
    from camoufox.sync_api import Camoufox

    result: BrowserResult = {
        "url": url,
        "platform": "wildberries",
        "status": "ERROR",
        "screenshot_path": None,
        "raw_price_text": "",
    }

    # Очистка ссылки от параметров запроса вроде ?targetUrl=...
    clean_url = url.split("?")[0]

    try:
        logger.info(f"[WILDBERRIES] → {clean_url}")
        with Camoufox(headless=True, geoip=True) as browser:
            context = browser.new_context(
                viewport=WB_VIEWPORT,
                extra_http_headers={"Referer": "https://www.wildberries.ru/"},
            )
            page = context.new_page()
            page.goto(clean_url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")

            try:
                page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT)
            except Exception:
                pass

            # Проверка статуса страницы перед обработкой цены
            page_status = _check_page_status(page, "wildberries")
            if page_status:
                result["status"] = page_status
                result["screenshot_path"] = _screenshot_error(page, "wildberries", clean_url)
                context.close()
                return result

            result["raw_price_text"] = _extract_price_text(page, "wildberries")
            shot = _screenshot_price(page, "wildberries", clean_url)
            if shot:
                result["screenshot_path"] = shot
                result["status"] = "SUCCESS"
            else:
                result["status"] = "SELECTOR_NOT_FOUND"

            context.close()

    except Exception as e:
        logger.error(f"[WILDBERRIES] Критическая ошибка: {e}")
        result["status"] = "ERROR"

    return result



# ─── Адаптер Яндекс Маркет (Camoufox + persistent profile) ───────────────────
def fetch_yandex_market(url: str) -> BrowserResult:
    """Загружает карточку Яндекс Маркета через Camoufox с постоянным профилем."""
    from camoufox.sync_api import Camoufox

    result: BrowserResult = {
        "url": url,
        "platform": "yandex_market",
        "status": "ERROR",
        "screenshot_path": None,
        "raw_price_text": "",
    }

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        logger.info(f"[YANDEX MARKET] → {url}")
        with Camoufox(headless=True, geoip=True) as browser:
            storage_file = USER_DATA_DIR / "storage_state.json"
            ctx_opts = {}
            if storage_file.exists():
                ctx_opts["storage_state"] = str(storage_file)

            context = browser.new_context(**ctx_opts)
            page = context.new_page()
            page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")

            try:
                page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT)
            except Exception:
                pass

            # Проверка статуса страницы перед обработкой цены
            page_status = _check_page_status(page, "yandex_market")
            if page_status:
                result["status"] = page_status
                result["screenshot_path"] = _screenshot_error(page, "yandex_market", url)
                context.close()
                return result

            result["raw_price_text"] = _extract_price_text(page, "yandex_market")
            shot = _screenshot_price(page, "yandex_market", url)
            if shot:
                result["screenshot_path"] = shot
                result["status"] = "SUCCESS"
            else:
                result["status"] = "SELECTOR_NOT_FOUND"

            # Сохраняем состояние сессии
            try:
                context.storage_state(path=str(storage_file))
                logger.debug("Состояние сессии Яндекс Маркет сохранено")
            except Exception:
                pass
            context.close()

    except Exception as e:
        logger.error(f"[YANDEX MARKET] Критическая ошибка: {e}")
        result["status"] = "ERROR"

    return result


# ─── Единая точка входа ───────────────────────────────────────────────────────
_FETCHERS = {
    "ozon": fetch_ozon,
    "wildberries": fetch_wildberries,
    "yandex_market": fetch_yandex_market,
}


def fetch(url: str, platform: str) -> BrowserResult:
    """
    Маршрутизирует запрос к нужному адаптеру.
    Для неизвестных платформ возвращает статус UNSUPPORTED_URL.
    """
    fetcher = _FETCHERS.get(platform)
    if not fetcher:
        logger.warning(f"Нет адаптера для платформы: {platform} | URL: {url}")
        return {
            "url": url,
            "platform": platform,
            "status": "UNSUPPORTED_URL",
            "screenshot_path": None,
            "raw_price_text": "",
        }
    return fetcher(url)
