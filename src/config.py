"""
config.py — Централизованные настройки проекта.
Пути к директориям, таймауты, селекторы цен, параметры браузеров.
"""
from pathlib import Path

# ─── Корневые пути ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
URLS_FILE = BASE_DIR / "urls.txt"
OUTPUT_DIR = BASE_DIR / "output"
SCREENSHOTS_PRICES_DIR = BASE_DIR / "screenshots" / "prices"
SCREENSHOTS_ERRORS_DIR = BASE_DIR / "screenshots" / "errors"
USER_DATA_DIR = BASE_DIR / ".browser_profiles" / "yandex"

# ─── Таймауты (мс) ───────────────────────────────────────────────────────────
PAGE_LOAD_TIMEOUT = 20_000
PRICE_SELECTOR_TIMEOUT = 15_000
NETWORK_IDLE_TIMEOUT = 15_000

# ─── Задержка Ozon (секунды) — рандомная пауза для обхода антибота ────────────
OZON_DELAY_MIN = 3.0
OZON_DELAY_MAX = 6.0

# ─── Десктопный viewport Wildberries ──────────────────────────────────────────
WB_VIEWPORT = {"width": 1920, "height": 1080}

# ─── Селекторы элементов цены (несколько вариантов — fallback-цепочка) ────────
PRICE_SELECTORS: dict[str, list[str]] = {
    "ozon": [
        '[data-widget="webPrice"]',
        '[data-widget="webPrice"] span',
        "span[data-widget='webPrice'] span.l2p",
        ".pv8_22.p8v_22",
        "[data-testid='price'] span",
        "span.price-number",
    ],
    "wildberries": [
        "ins.price-block__final-price",
        ".price-block__price-string",
        "[class*='priceBlockFinalPrice']",
        "[class*='priceBlockPrice']",
        ".price-block__final-price",
        ".price-block__wallet-price",

        "span.price-block__wallet-price",
        ".product-page__price ins",
        ".product-page__price",
        "[data-tag='priceText']",
        ".redesigned-price-block__final-price",
        ".priceBlockFinalPrice",
        "ins.priceBlockFinalPrice",
        "span[class*='price-block__final-price']",
        "span[class*='price-block__wallet-price']",
        "[class*='final-price']",
        "[class*='wallet-price']",
    ],

    "yandex_market": [
        "[data-auto='snippet-price-current']",
        "span[data-auto='snippet-price-current']",
        "[data-auto='price-value']",
        "[data-auto='mainPrice']",
        "[data-auto='cpa-price']",
        "h3[data-auto='snippet-price-current']",
        "[data-baobab-name='price']",
        "[data-baobab-name='price'] span",
        ".ds-valueLine[data-auto='snippet-price-current']",
        "span.ds-valueLine",
        "[class*='ds-text_color_price-term']",
        "[data-zone-name='price']",
    ],
    "avito": [
        '[data-marker="item-view/item-price"]',
        '[data-marker="item-view/item-price-container"]',
        'span[data-marker="item-view/item-price"]',
        '[itemprop="price"]',
        '[class*="style-item-price-"]',
        '[class*="price-price-"]',
        '.js-item-price',
    ],
}

# ─── Селекторы капчи / блокировки ────────────────────────────────────────────
CAPTCHA_SELECTORS: dict[str, list[str]] = {
    "ozon": [
        "[data-widget='error404']",
        ".error-page",
        "#error-block",
    ],
    "wildberries": [
        ".captcha__wrapper",
        "#g-recaptcha",
        "[class*='captcha']",
    ],
    "yandex_market": [
        ".CheckboxCaptcha",
        ".SmartCaptcha",
        "[data-testid='captcha']",
        "#js-button",
    ],
    "avito": [
        ".firewall-container",
        "#firewall",
        "[data-marker='firewall']",
        ".form-captcha",
        ".captcha-container",
        "[class*='firewall']",
    ],
}

# ─── Текстовые маркеры капчи и блокировок ────────────────────────────────────
CAPTCHA_TEXT_MARKERS = [
    "подозрительная активность",
    "smartcaptcha",
    "checkboxcaptcha",
    "подтвердите, что запросы отправляли вы",
    "я не робот",
    "showcaptcha",
    "проверяем браузер",
    "доступ ограничен",
    "firewall",
    "проверка безопасности",
    "ip-адрес временно заблокирован",
]

# ─── Текстовые маркеры удаленного товара ─────────────────────────────────────
DELETED_TEXT_MARKERS = [
    "продавец удалил этот товар",
    "товар не найден",
    "страница не существует",
    "нет такого товара",
    "объявление снято с публикации",
    "объявление закрыто",
    "снято с продажи",
    "товар зарезервирован",
    "объявление удалено",
]

# ─── Ключевые слова 404-страниц ───────────────────────────────────────────────
NOT_FOUND_TITLE_KEYWORDS = ["404", "не найдено", "not found", "страница не существует"]

# ─── Минимальная уверенность OCR для принятия результата ─────────────────────
OCR_MIN_CONFIDENCE = 0.4

# ─── Автосохранение Excel каждые N строк ─────────────────────────────────────
EXCEL_AUTOSAVE_EVERY = 5

