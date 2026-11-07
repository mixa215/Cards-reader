import sys
import unittest
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from avito_worker import AvitoParser, AvitoItemData


class TestAvitoParser(unittest.TestCase):
    def setUp(self):
        self.parser = AvitoParser()
        self.html_file = Path(r"c:\Users\Lenovo\Downloads\Умная колонка Яндекс Станция Мини 3 Про с Алисой купить в Москве по низкой цене с доставкой _ Электроника _ Авито (8279766214).html")
        if self.html_file.exists():
            self.html = self.html_file.read_text(encoding="utf-8", errors="ignore")
        else:
            self.html = ""

    def test_clean_price(self):
        # Строка с неразрывным пробелом и знаком рубля
        self.assertEqual(AvitoParser.clean_price("10\xa0490 ₽"), 10490.0)
        self.assertEqual(AvitoParser.clean_price("10 490&nbsp;₽"), 10490.0)
        # Число с запятой
        self.assertEqual(AvitoParser.clean_price("1 250,50 руб."), 1250.50)
        # Числовые типы
        self.assertEqual(AvitoParser.clean_price(10490), 10490.0)
        self.assertEqual(AvitoParser.clean_price(10490.99), 10490.99)
        # Невалидные значения
        self.assertIsNone(AvitoParser.clean_price(None))
        self.assertIsNone(AvitoParser.clean_price("Бесплатно"))

    def test_parse_real_html_json_ld(self):
        if not self.html:
            self.skipTest("Файл HTML не найден")
        res = self.parser.parse_html(self.html, url="https://www.avito.ru/8279766214")
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.price, 10490.0)
        self.assertIn("Яндекс Станция Мини 3", res.title)
        self.assertEqual(res.seller, "Даниил")
        self.assertIn("Москва", res.city)

    def test_fallback_hydration(self):
        if not self.html:
            self.skipTest("Файл HTML не найден")
        import re
        no_ld = re.sub(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>.*?</script>', '', self.html, flags=re.DOTALL)
        res = self.parser.parse_html(no_ld, url="https://www.avito.ru/8279766214")
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.price, 10490.0)
        self.assertEqual(res.parse_source, "hydration")

    def test_fallback_dom_meta(self):
        if not self.html:
            self.skipTest("Файл HTML не найден")
        import re
        no_ld = re.sub(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>.*?</script>', '', self.html, flags=re.DOTALL)
        no_hydr = re.sub(r'window\.__staticRouterHydrationData\s*=.*?</script>', '', no_ld, flags=re.DOTALL)
        res = self.parser.parse_html(no_hydr, url="https://www.avito.ru/8279766214")
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.price, 10490.0)
        self.assertEqual(res.parse_source, "dom")

    def test_closed_item(self):
        closed_html = "<html><body><h1>Объявление</h1><div>Объявление снято с публикации</div></body></html>"
        res = self.parser.parse_html(closed_html, url="https://www.avito.ru/closed")
        self.assertEqual(res.status, "PRODUCT_DELETED")
        self.assertFalse(res.is_active)

    def test_captcha_blocked(self):
        blocked_html = "<html><head><title>Доступ ограничен</title></head><body>Подозрительная активность IP. Firewall.</body></html>"
        res = self.parser.parse_html(blocked_html, url="https://www.avito.ru/blocked")
        self.assertEqual(res.status, "CAPTCHA_BLOCKED")


if __name__ == "__main__":
    unittest.main()

