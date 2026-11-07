import sys
import unittest
import tempfile
import json
from pathlib import Path
from datetime import datetime

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config_loader import load_config, AppConfig, _validate_interval, _validate_bool
from excel_worker import ExcelWriter
import openpyxl


class TestConfigLoader(unittest.TestCase):
    def test_default_config_loading(self):
        cfg = load_config()
        self.assertIsInstance(cfg, AppConfig)
        self.assertEqual(cfg.check_interval_minutes, 10)
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.excel_file_path, "output/multi_prices_report_{date}.xlsx")

    def test_corrupted_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{invalid_json: true")
            f_path = Path(f.name)

        try:
            cfg = load_config(f_path)
            # Должен безопасно вернуться дефолтный конфиг
            self.assertEqual(cfg.check_interval_minutes, 10)
            self.assertTrue(cfg.enabled)
        finally:
            f_path.unlink(missing_ok=True)

    def test_validation_rules(self):
        self.assertEqual(_validate_interval(-5, default=10), 10)
        self.assertEqual(_validate_interval("abc", default=10), 10)
        self.assertEqual(_validate_interval("15", default=10), 15)

        self.assertTrue(_validate_bool("yes", default=False))
        self.assertFalse(_validate_bool("0", default=True))
        self.assertTrue(_validate_bool("true", default=False))

    def test_path_resolution(self):
        # При enabled=True имя обязано содержать префикс multi_
        cfg_enabled = AppConfig(
            enabled=True,
            excel_file_path="output/custom_report_{date}.xlsx",
            input_urls_path="my_urls.txt"
        )
        resolved_excel = cfg_enabled.resolve_excel_path(datetime(2026, 11, 7))
        self.assertTrue(str(resolved_excel).endswith("multi_custom_report_2026-11-07.xlsx"))
        self.assertTrue(str(cfg_enabled.resolve_urls_path()).endswith("my_urls.txt"))

        # При enabled=False (разовый запуск) префикс multi_ не добавляется принудительно
        cfg_disabled = AppConfig(
            enabled=False,
            excel_file_path="output/custom_report_{date}.xlsx",
        )
        resolved_single = cfg_disabled.resolve_excel_path(datetime(2026, 11, 7))
        self.assertTrue(str(resolved_single).endswith("custom_report_2026-11-07.xlsx"))


class TestExcelAppendMode(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.excel_path = Path(self.temp_dir.name) / "test_report.xlsx"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_append_across_iterations(self):
        # Итерация 1: создание нового файла и запись 2 строк
        writer1 = ExcelWriter(filepath=self.excel_path)
        dt1 = datetime(2026, 11, 7, 10, 0, 0)
        writer1.write(
            platform="avito",
            url="https://avito.ru/item1",
            price=1500.0,
            status="SUCCESS",
            screenshot_path=None,
            checked_at=dt1,
        )
        writer1.write(
            platform="ozon",
            url="https://ozon.ru/item2",
            price=2500.0,
            status="SUCCESS",
            screenshot_path=None,
            checked_at=dt1,
        )
        writer1.close()

        # Проверяем, что файл создан и содержит 2 строки данных + 1 строку заголовка
        wb = openpyxl.load_workbook(str(self.excel_path))
        ws = wb.active
        self.assertEqual(ws.max_row, 3)
        self.assertEqual(ws.cell(2, 1).value, 1)
        self.assertEqual(ws.cell(3, 1).value, 2)
        wb.close()

        # Итерация 2: открытие того же файла в режиме append и запись ещё 1 строки
        writer2 = ExcelWriter(filepath=self.excel_path)
        dt2 = datetime(2026, 11, 7, 10, 10, 0)
        writer2.write(
            platform="wildberries",
            url="https://wildberries.ru/item3",
            price=3500.0,
            status="SUCCESS",
            screenshot_path=None,
            checked_at=dt2,
        )
        writer2.close()

        # Проверяем, что строки не перезаписались, а дополнились
        wb2 = openpyxl.load_workbook(str(self.excel_path))
        ws2 = wb2.active
        self.assertEqual(ws2.max_row, 4)
        # Проверяем сквозную нумерацию
        self.assertEqual(ws2.cell(2, 1).value, 1)
        self.assertEqual(ws2.cell(3, 1).value, 2)
        self.assertEqual(ws2.cell(4, 1).value, 3)

        # Проверяем платформы
        self.assertEqual(ws2.cell(2, 2).value, "Avito")
        self.assertEqual(ws2.cell(3, 2).value, "Ozon")
        self.assertEqual(ws2.cell(4, 2).value, "Wildberries")

        # Проверяем метки времени
        self.assertEqual(ws2.cell(2, 8).value, dt1)
        self.assertEqual(ws2.cell(4, 8).value, dt2)
        wb2.close()


if __name__ == "__main__":
    unittest.main()

