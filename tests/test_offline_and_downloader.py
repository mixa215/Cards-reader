import sys
import unittest
import tempfile
import time
from pathlib import Path
from datetime import datetime, timedelta

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from browser_downloader import extract_sku, cleanup_old_dumps
from offline_parser import OfflinePageParser, clean_price
from excel_worker import ExcelWriter
import openpyxl


class TestBrowserDownloader(unittest.TestCase):
    def test_extract_sku(self):
        # Wildberries
        wb_url = "https://www.wildberries.ru/catalog/71742568/detail.aspx?targetUrl=MI"
        self.assertEqual(extract_sku(wb_url, "wildberries"), "71742568")

        # Ozon
        ozon_url = "https://www.ozon.ru/product/velosiped-fizard-flow-2085983408/?at=123"
        self.assertEqual(extract_sku(ozon_url, "ozon"), "2085983408")

        # Avito
        avito_url = "https://www.avito.ru/moskva/audio_i_video/umnaya_kolonka_8279766214"
        self.assertEqual(extract_sku(avito_url, "avito"), "8279766214")

    def test_cleanup_old_dumps(self):
        with tempfile.TemporaryDirectory() as td:
            dump_dir = Path(td)
            # Создаем старый файл (10 дней назад)
            old_file = dump_dir / "old_page.html"
            old_file.write_text("old", encoding="utf-8")
            old_time = time.time() - (10 * 86400)
            import os
            os.utime(old_file, (old_time, old_time))

            # Создаем свежий файл
            new_file = dump_dir / "new_page.html"
            new_file.write_text("new", encoding="utf-8")

            # Очищаем файлы старше 7 дней
            deleted = cleanup_old_dumps(dump_dir, max_days=7)
            self.assertEqual(deleted, 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(new_file.exists())

    def test_download_url_soft_catches_err_aborted(self):
        from unittest.mock import MagicMock
        from browser_downloader import ChromeProfileDownloader
        from config_loader import AppConfig

        with tempfile.TemporaryDirectory() as td:
            cfg = AppConfig(dump_pages_dir=td)
            downloader = ChromeProfileDownloader(cfg)

            mock_page = MagicMock()
            # Имитируем сетевой сброс net::ERR_ABORTED из-за редиректа маркетплейса
            mock_page.goto.side_effect = Exception("Page.goto: net::ERR_ABORTED at https://ozon.ru/product/123")
            mock_page.content.return_value = "<html><body><div data-widget='webPrice'><span>1500 ₽</span></div></body></html>"

            res = downloader.download_url(mock_page, "https://www.ozon.ru/product/item-12345678/")
            # Должен сработать мягкий перехват: page.wait_for_timeout(3000) и успешное сохранение дампа
            mock_page.wait_for_timeout.assert_any_call(3000)
            self.assertEqual(res["status"], "SUCCESS")
            self.assertTrue(Path(res["dump_path"]).exists())
            self.assertIn("1500 ₽", Path(res["dump_path"]).read_text(encoding="utf-8"))

    def test_download_all_anchor_page_retention(self):
        from unittest.mock import MagicMock, patch
        from browser_downloader import ChromeProfileDownloader
        from config_loader import AppConfig

        with tempfile.TemporaryDirectory() as td:
            urls_file = Path(td) / "urls.txt"
            urls_file.write_text("https://www.ozon.ru/product/item-111/\nhttps://www.ozon.ru/product/item-222/\n", encoding="utf-8")

            cfg = AppConfig(dump_pages_dir=td)
            downloader = ChromeProfileDownloader(cfg)

            # Мокаем auth_ready = True
            downloader.browser_manager.is_auth_ready = MagicMock(return_value=True)

            mock_anchor = MagicMock()
            mock_anchor.url = "about:blank"
            mock_work_page = MagicMock()

            mock_context = MagicMock()
            mock_context.pages = [mock_anchor]
            mock_context.new_page.return_value = mock_work_page

            downloader.browser_manager.get_context = MagicMock(return_value=(mock_context, mock_context))
            downloader.download_url = MagicMock(return_value={"status": "SUCCESS", "sku": "111", "url": "test"})

            with patch("browser_downloader.sync_playwright") as mock_pw:
                mock_pw_inst = MagicMock()
                mock_pw.return_value.__enter__.return_value = mock_pw_inst

                results = downloader.download_all(urls_file)

                # Проверяем, что якорь не был закрыт в цикле
                mock_anchor.close.assert_not_called()
                # Проверяем, что рабочая страница открывалась и закрывалась для каждого URL (2 раза)
                self.assertEqual(mock_work_page.close.call_count, 2)
                self.assertEqual(len(results), 2)



class TestOfflineParser(unittest.TestCase):
    def test_clean_price(self):
        self.assertEqual(clean_price("10 490 ₽"), 10490.0)
        self.assertEqual(clean_price("1 250,50 руб."), 1250.50)
        self.assertEqual(clean_price("10\xa0490&nbsp;₽"), 10490.0)

    def test_parse_real_avito_dump(self):
        html_file = Path(r"c:\Users\Lenovo\Downloads\Умная колонка Яндекс Станция Мини 3 Про с Алисой купить в Москве по низкой цене с доставкой _ Электроника _ Авито (8279766214).html")
        if not html_file.exists():
            self.skipTest("Файл HTML не найден")

        parser = OfflinePageParser()
        res = parser.parse_file(html_file, url="https://avito.ru/8279766214", platform="avito")
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.price, 10490.0)
        self.assertIn("Яндекс Станция Мини 3", res.title)
        self.assertEqual(res.platform, "avito")
        self.assertEqual(res.sku, "8279766214")

    def test_parse_ozon_personal_price_mock(self):
        html_mock = """
        <html><body>
        <h1>Смартфон Apple iPhone 15</h1>
        <div data-widget="webPrice">
            <span>95 000 ₽ с Ozon Картой</span>
            <span>100 000 ₽ без Ozon Карты</span>
        </div>
        </body></html>
        """
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html_mock)
            tmp_path = Path(f.name)

        try:
            parser = OfflinePageParser()
            res = parser.parse_file(tmp_path, url="https://ozon.ru/product/123", platform="ozon")
            self.assertEqual(res.status, "SUCCESS")
            self.assertEqual(res.price, 95000.0)
            self.assertEqual(res.base_price, 100000.0)
            self.assertEqual(res.price_type, "Персональная (Ozon Карта)")
            self.assertEqual(res.title, "Смартфон Apple iPhone 15")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_parse_wb_wallet_price_mock(self):
        html_mock = """
        <html><body>
        <h1>Кроссовки Nike Air</h1>
        <div class="price-block">
            <span class="price-block__wallet-price">4 500 ₽ с WB Кошельком</span>
            <span class="price-block__final-price">5 000 ₽</span>
        </div>
        </body></html>
        """
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html_mock)
            tmp_path = Path(f.name)

        try:
            parser = OfflinePageParser()
            res = parser.parse_file(tmp_path, url="https://wildberries.ru/catalog/999/detail.aspx", platform="wildberries")
            self.assertEqual(res.status, "SUCCESS")
            self.assertEqual(res.price, 4500.0)
            self.assertEqual(res.base_price, 5000.0)
            self.assertEqual(res.price_type, "Персональная (WB Кошелек)")
            self.assertEqual(res.title, "Кроссовки Nike Air")
        finally:
            tmp_path.unlink(missing_ok=True)


class TestExcelNewColumns(unittest.TestCase):
    def test_write_with_personal_price_and_dump_path(self):
        with tempfile.TemporaryDirectory() as td:
            excel_path = Path(td) / "test_personal.xlsx"
            writer = ExcelWriter(filepath=excel_path)

            writer.write(
                platform="ozon",
                url="https://ozon.ru/item1",
                price=9500.0,
                status="SUCCESS",
                screenshot_path=None,
                checked_at=datetime(2026, 11, 7, 12, 0, 0),
                price_type="Персональная (Ozon Карта)",
                base_price=10500.0,
                dump_path=str(Path(td) / "dump.html"),
            )
            writer.close()

            wb = openpyxl.load_workbook(str(excel_path))
            ws = wb.active
            # Проверяем колонки
            self.assertEqual(ws.cell(1, 6).value, "Тип цены")
            self.assertEqual(ws.cell(1, 7).value, "Базовая цена")
            self.assertEqual(ws.cell(1, 10).value, "Путь к дампу HTML")

            # Проверяем строку данных
            self.assertEqual(ws.cell(2, 4).value, 9500.0)
            self.assertEqual(ws.cell(2, 6).value, "Персональная (Ozon Карта)")
            self.assertEqual(ws.cell(2, 7).value, 10500.0)
            wb.close()


if __name__ == "__main__":
    unittest.main()

