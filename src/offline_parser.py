"""
offline_parser.py — Модуль автономной оффлайн-обработки скачанных HTML-дампов (Stage 2).

Особенности:
  - Извлечение реальных персональных цен (Ozon Карта, WB Кошелек, Яндекс Пэй) и базовых цен.
  - Определение типа цены: 'Персональная (Ozon Карта)', 'Персональная (WB Кошелек)', 'Персональная (Пэй)', 'Обычная'.
  - Трёхуровневая каскадная fallback-стратегия:
      а) SSR Hydration данные (window.__staticRouterHydrationData, window.__initialData__, __WB_INIT_DATA__)
      б) Schema.org JSON-LD (<script type="application/ld+json">)
      в) Специфические DOM-селекторы и регулярные выражения
  - Детекция статусов доступности (CAPTCHA_BLOCKED, PRODUCT_DELETED, NOT_FOUND, SUCCESS).
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

from loguru import logger


@dataclass
class ParsedProduct:
    """Результат оффлайн-парсинга сохраненного HTML-дампа."""
    url: str
    platform: str
    sku: str
    title: Optional[str] = None
    price: Optional[float] = None              # Итоговая цена (с учетом персональной скидки, если есть)
    base_price: Optional[float] = None         # Базовая цена без персональной скидки
    price_type: str = "Обычная"                # "Персональная (Ozon Карта)", "Персональная (WB Кошелек)", "Обычная"
    currency: str = "RUB"
    status: str = "SUCCESS"                    # SUCCESS, CAPTCHA_BLOCKED, PRODUCT_DELETED, NOT_FOUND, ERROR
    is_available: bool = True
    dump_path: Optional[str] = None
    parse_source: Optional[str] = None         # 'hydration', 'json_ld', 'dom'


def clean_price(val: Any) -> Optional[float]:
    """
    Нормализует цену к типу float, находя числовое значение и очищая
    валюту, пробелы и пояснительный текст (например, 'с WB Кошельком').
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).replace("&nbsp;", " ").replace("\u00a0", " ").replace("\xa0", " ")
    # Ищем числовой блок
    m = re.search(r'(\d[\d\s.,]*\d|\d+)', s)
    if not m:
        return None

    num_str = re.sub(r"\s+", "", m.group(1))
    num_str = num_str.replace(",", ".")
    parts = num_str.split(".")
    if len(parts) > 2:
        num_str = "".join(parts[:-1]) + "." + parts[-1]

    try:
        num = float(num_str)
        if 0 < num < 100_000_000:
            return num
    except ValueError:
        pass
    return None


class OfflinePageParser:
    """Парсер сохраненных HTML файлов маркетплейсов."""

    # ── Проверка на капчу и ошибки ───────────────────────────────────────────
    @staticmethod
    def check_page_status(html: str) -> Optional[str]:
        lower = html.lower()
        if any(m in lower for m in ["smartcaptcha", "checkboxcaptcha", "firewall", "доступ ограничен", "проверяем браузер", "showcaptcha"]):
            return "CAPTCHA_BLOCKED"
        if any(m in lower for m in ["товар не найден", "страница не существует", "объявление снято", "продавец удалил", "нет такого товара"]):
            return "PRODUCT_DELETED"
        return None

    # ── Парсинг JSON-LD ──────────────────────────────────────────────────────
    @staticmethod
    def parse_json_ld(html: str) -> Optional[dict]:
        scripts = re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.DOTALL | re.I)
        for s in scripts:
            try:
                data = json.loads(s.strip())
                items = data.get("@graph", [data]) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                for it in items:
                    if isinstance(it, dict) and it.get("@type") == "Product":
                        name = it.get("name")
                        offers = it.get("offers", {})
                        price = None
                        if isinstance(offers, dict):
                            price = clean_price(offers.get("price"))
                        elif isinstance(offers, list) and offers:
                            price = clean_price(offers[0].get("price"))
                        if name or price:
                            return {"title": name, "price": price}
            except Exception:
                continue
        return None

    # ── Обработчики маркетплейсов ─────────────────────────────────────────────

    def parse_ozon(self, html: str, res: ParsedProduct) -> None:
        """Парсинг карточки Ozon: поиск цены с Ozon Картой и обычной цены."""
        # 1. Поиск в SSR гидратации
        m_hydr = re.search(r'window\.__staticRouterHydrationData\s*=\s*(.*?);\s*</script>', html, re.DOTALL)
        if m_hydr:
            try:
                raw = m_hydr.group(1).strip()
                if raw.startswith("JSON.parse("):
                    inner = re.search(r'JSON\.parse\(([\'\"])(.*?)\1\)', raw, re.DOTALL)
                    json_str = json.loads(f'"{inner.group(2)}"') if inner else raw
                    data = json.loads(json_str)
                else:
                    data = json.loads(raw)

                # Ищем блок webPrice в дереве виджетов
                data_str = json.dumps(data, ensure_ascii=False)
                # Поиск цены по карте Ozon
                m_card = re.search(r'\"price\":\s*\"?(\d[\d\s]*\d|\d+)\"?.*?(?:Ozon\s*Карт|карте)', data_str, re.I)
                if m_card:
                    res.price = clean_price(m_card.group(1))
                    res.price_type = "Персональная (Ozon Карта)"
                    res.parse_source = "hydration"

                # Поиск названия
                m_name = re.search(r'\"webProductHeading.*?\"title\":\s*\"(.*?)\"', data_str)
                if m_name:
                    res.title = m_name.group(1).encode('utf-8').decode('unicode_escape', errors='ignore')
            except Exception:
                pass

        # 2. DOM-селекторы Ozon
        # Ищем блок [data-widget="webPrice"]
        m_widget = re.search(r'data-widget=[\"\']webPrice[\"\'][^>]*>(.*?)</div>\s*</div>', html, re.DOTALL | re.I)
        widget_html = m_widget.group(1) if m_widget else html

        # Проверка Ozon Карты в верстке
        if "ozon карт" in widget_html.lower() or "по карте" in widget_html.lower() or "c ozon" in widget_html.lower():
            # Извлекаем числа цен
            nums = re.findall(r'(\d[\d\s\xa0]*\d|\d+)\s*(?:₽|руб)', widget_html, re.I)
            cleaned_nums = [p for p in (clean_price(n) for n in nums) if p]
            if cleaned_nums:
                # Меньшая из отображаемых цен с картой — персональная, большая — базовая
                res.price = min(cleaned_nums)
                res.base_price = max(cleaned_nums) if len(cleaned_nums) > 1 else None
                res.price_type = "Персональная (Ozon Карта)"
                res.parse_source = res.parse_source or "dom"

        if not res.price:
            # Обычная цена без карты
            nums = re.findall(r'(\d[\d\s\xa0]*\d|\d+)\s*(?:₽|руб)', widget_html, re.I)
            cleaned_nums = [p for p in (clean_price(n) for n in nums) if p]
            if cleaned_nums:
                res.price = cleaned_nums[0]
                res.price_type = "Обычная"
                res.parse_source = res.parse_source or "dom"

        # Название из H1
        if not res.title:
            m_h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
            if m_h1:
                res.title = re.sub(r'<[^>]+>', '', m_h1.group(1)).strip()

    def parse_wildberries(self, html: str, res: ParsedProduct) -> None:
        """Парсинг карточки Wildberries: цена с WB Кошельком vs базовая."""
        # 1. Поиск в SSR/State скриптах
        for pat in [r'window\.initData\s*=\s*(.*?);\s*</script>', r'__WB_INIT_DATA__\s*=\s*(.*?);\s*</script>']:
            m = re.search(pat, html, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1).strip())
                    # Извлечение из объекта
                    if "price" in data:
                        res.price = clean_price(data["price"])
                        res.parse_source = "hydration"
                except Exception:
                    pass

        # 2. Поиск элементов WB Кошелька
        # .price-block__wallet-price или .redesigned-price-block__wallet-price
        m_wallet = re.search(
            r'(?:price-block__wallet-price|wallet-price)[^>]*>(.*?)</span>',
            html,
            re.DOTALL | re.I,
        )
        if m_wallet:
            w_price = clean_price(re.sub(r'<[^>]+>', '', m_wallet.group(1)))
            if w_price:
                res.price = w_price
                res.price_type = "Персональная (WB Кошелек)"
                res.parse_source = res.parse_source or "dom"

        # Базовая цена
        m_base = re.search(
            r'(?:price-block__final-price|priceBlockFinalPrice)[^>]*>(.*?)</(?:ins|span)>',
            html,
            re.DOTALL | re.I,
        )
        if m_base:
            b_price = clean_price(re.sub(r'<[^>]+>', '', m_base.group(1)))
            if b_price:
                if res.price and b_price != res.price:
                    res.base_price = b_price
                elif not res.price:
                    res.price = b_price
                    res.price_type = "Обычная"
                res.parse_source = res.parse_source or "dom"

        # Название из h1 или meta
        if not res.title:
            m_h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
            if m_h1:
                res.title = re.sub(r'<[^>]+>', '', m_h1.group(1)).strip()

    def parse_yandex_market(self, html: str, res: ParsedProduct) -> None:
        """Парсинг Яндекс Маркета: цена с Яндекс Пэй / Сплит vs базовая."""
        # 1. Поиск элементов Пэй
        m_pay = re.search(r'data-auto=[\"\']cpa-price[\"\'][^>]*>(.*?)</div>', html, re.DOTALL | re.I)
        if m_pay and ("пэй" in m_pay.group(1).lower() or "pay" in m_pay.group(1).lower()):
            clean_t = re.sub(r'<[^>]+>', ' ', m_pay.group(1))
            num = re.search(r'(\d[\d\s]*\d|\d+)', clean_t)
            if num:
                res.price = clean_price(num.group(1))
                res.price_type = "Персональная (Яндекс Пэй)"
                res.parse_source = "dom"

        # 2. Селектор snippet-price-current / mainPrice
        if not res.price:
            m_curr = re.search(r'data-auto=[\"\']snippet-price-current[\"\'][^>]*>(.*?)</(?:span|h3|div)>', html, re.DOTALL | re.I)
            if m_curr:
                res.price = clean_price(re.sub(r'<[^>]+>', '', m_curr.group(1)))
                res.price_type = "Обычная"
                res.parse_source = res.parse_source or "dom"

        # Название
        if not res.title:
            m_h1 = re.search(r'<h1[^>]*data-auto=[\"\']productCardTitle[\"\'][^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
            if not m_h1:
                m_h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
            if m_h1:
                res.title = re.sub(r'<[^>]+>', '', m_h1.group(1)).strip()

    def parse_avito(self, html: str, res: ParsedProduct) -> None:
        """Парсинг Avito с использованием алгоритма AvitoParser."""
        from avito_worker import AvitoParser
        p = AvitoParser()
        data = p.parse_html(html, url=res.url)
        res.price = data.price
        res.title = data.title
        res.price_type = "Обычная"
        res.status = data.status
        res.is_available = data.is_active
        res.parse_source = data.parse_source

    def parse_file(self, file_path: Path, url: str = "", platform: str = "") -> ParsedProduct:
        """
        Главный метод: открывает локальный HTML-дамп и извлекает структурированные данные.
        """
        path = Path(file_path)
        if not path.exists():
            return ParsedProduct(url=url, platform=platform, sku="", status="NOT_FOUND", dump_path=str(path))

        # Определение платформы по имени файла, если не передана
        if not platform:
            fname = path.name.lower()
            if "ozon" in fname:
                platform = "ozon"
            elif "wildberries" in fname or "wb" in fname:
                platform = "wildberries"
            elif "yandex" in fname:
                platform = "yandex_market"
            elif "avito" in fname:
                platform = "avito"
            else:
                platform = "unknown"

        # Извлекаем артикул/ID из URL или имени файла
        sku = ""
        if url:
            from browser_downloader import extract_sku
            sku = extract_sku(url, platform)
        if not sku:
            m_sku = re.search(r"\((\d{6,})\)", path.stem) or re.search(r"_(\d{6,})", path.stem) or re.search(r"(\d{6,})", path.stem)
            sku = m_sku.group(1) if m_sku else ""
        res = ParsedProduct(
            url=url,
            platform=platform,
            sku=sku,
            dump_path=str(path.resolve()),
        )

        try:
            html = path.read_text(encoding="utf-8", errors="replace")

            # 1. Проверка защитных экранов
            page_err = self.check_page_status(html)
            if page_err:
                res.status = page_err
                res.is_available = False
                return res

            # 2. Платформенный разбор
            if platform == "ozon":
                self.parse_ozon(html, res)
            elif platform == "wildberries":
                self.parse_wildberries(html, res)
            elif platform == "yandex_market":
                self.parse_yandex_market(html, res)
            elif platform == "avito":
                self.parse_avito(html, res)

            # 3. Fallback: Schema.org JSON-LD
            if not res.price or not res.title:
                json_ld = self.parse_json_ld(html)
                if json_ld:
                    if not res.title and json_ld.get("title"):
                        res.title = json_ld["title"]
                    if not res.price and json_ld.get("price"):
                        res.price = json_ld["price"]
                        res.price_type = "Обычная"
                        res.parse_source = res.parse_source or "json_ld"

            # 4. Финализация статуса
            if res.price is not None:
                res.status = "SUCCESS"
            elif res.status != "PRODUCT_DELETED":
                res.status = "NOT_FOUND"

        except Exception as e:
            logger.error(f"Ошибка при разборе дампа {path.name}: {e}")
            res.status = "ERROR"

        return res
