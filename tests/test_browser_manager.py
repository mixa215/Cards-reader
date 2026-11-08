"""
test_browser_manager.py — Тесты модуля BrowserManager (Dedicated Persistent Context в Playwright).
"""
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config_loader import BrowserConfig, AppConfig, load_config
from browser_manager import (
    BrowserManager,
    ChromePersistentManager,
    ChromeCDPController,
    ChromeCDPManager,
    is_chrome_running,
    kill_chrome_processes,
    reset_chrome_crash_state,
    check_cdp_status,
    check_cdp_endpoint,
    is_cdp_ready,
)


class TestBrowserPersistentManager(unittest.TestCase):

    def test_browser_config_defaults(self):
        cfg = BrowserConfig()
        self.assertEqual(cfg.mode, "persistent")
        self.assertEqual(cfg.profile_dir, "chrome_automation_profile")
        self.assertFalse(cfg.headless)
        self.assertEqual(cfg.channel, "chrome")

        profile_dir = cfg.resolve_profile_dir()
        self.assertIsInstance(profile_dir, Path)
        self.assertTrue(str(profile_dir).endswith("chrome_automation_profile"))

    def test_launch_persistent_context_parameters(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = BrowserConfig(
                mode="persistent",
                profile_dir=td,
                headless=False,
                channel="chrome",
            )
            manager = BrowserManager(cfg)

            mock_pw = MagicMock()
            mock_context = MagicMock()
            mock_pw.chromium.launch_persistent_context.return_value = mock_context

            ctx, _ = manager.get_context(mock_pw)

            mock_pw.chromium.launch_persistent_context.assert_called_once()
            call_kwargs = mock_pw.chromium.launch_persistent_context.call_args[1]

            # Проверяем ключевые параметры Playwright
            self.assertEqual(call_kwargs["user_data_dir"], str(manager.profile_dir))
            self.assertEqual(call_kwargs["channel"], "chrome")
            self.assertFalse(call_kwargs["headless"])
            self.assertEqual(call_kwargs["ignore_default_args"], ["--enable-automation"])
            self.assertTrue(call_kwargs["no_viewport"])

            args = call_kwargs["args"]
            self.assertIn("--no-first-run", args)
            self.assertIn("--no-default-browser-check", args)
            self.assertIn("--start-maximized", args)

            # Строгий запрет на флаги, ломающие DPAPI и триггерящие защиту
            self.assertNotIn("--no-sandbox", args)
            self.assertNotIn("--enable-automation", args)

            self.assertEqual(ctx, mock_context)

    def test_launch_fallback_without_channel(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = BrowserConfig(mode="persistent", profile_dir=td, channel="chrome")
            manager = BrowserManager(cfg)

            mock_pw = MagicMock()
            mock_context = MagicMock()
            # Первый вызов с channel="chrome" падает, второй без channel успешен
            mock_pw.chromium.launch_persistent_context.side_effect = [
                Exception("Channel chrome not found"),
                mock_context,
            ]

            ctx, _ = manager.get_context(mock_pw)
            self.assertEqual(mock_pw.chromium.launch_persistent_context.call_count, 2)
            second_call_kwargs = mock_pw.chromium.launch_persistent_context.call_args_list[1][1]
            self.assertNotIn("channel", second_call_kwargs)
            self.assertEqual(ctx, mock_context)

    @patch("auth_setup.time.sleep")
    def test_setup_all_marketplace_tabs(self, mock_sleep):
        with tempfile.TemporaryDirectory() as td:
            cfg = BrowserConfig(profile_dir=td)
            manager = BrowserManager(cfg)

            mock_context = MagicMock()
            mock_context.pages = []
            mock_page = MagicMock()
            mock_context.new_page.return_value = mock_page

            pages = manager.setup_all_marketplace_tabs(mock_context)
            self.assertEqual(len(pages), 4)
            self.assertEqual(mock_page.goto.call_count, 4)

            # Проверяем, что пауза 3 секунды вызвана ровно 3 раза (после вкладок 1, 2, 3)
            self.assertEqual(mock_sleep.call_count, 3)
            for call in mock_sleep.call_args_list:
                self.assertEqual(call[0][0], 3.0)

            # Проверяем, что передавался параметр wait_until="commit"
            for call in mock_page.goto.call_args_list:
                self.assertEqual(call[1]["wait_until"], "commit")

    @patch("auth_setup.time.sleep")
    def test_setup_all_marketplace_tabs_closes_initial_blank_page(self, mock_sleep):
        with tempfile.TemporaryDirectory() as td:
            cfg = BrowserConfig(profile_dir=td)
            manager = BrowserManager(cfg)

            mock_initial_page = MagicMock()
            mock_initial_page.url = "about:blank"
            mock_context = MagicMock()
            mock_context.pages = [mock_initial_page]
            mock_page = MagicMock()
            mock_context.new_page.return_value = mock_page

            pages = manager.setup_all_marketplace_tabs(mock_context)
            self.assertEqual(len(pages), 4)
            # Начальная пустая вкладка должна быть закрыта
            mock_initial_page.close.assert_called_once()
            # Сами открытые 4 вкладки не должны закрываться в процессе открытия
            mock_page.close.assert_not_called()

    def test_ensure_marketplaces_auth_existing_marker(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = BrowserConfig(profile_dir=td)
            manager = BrowserManager(cfg)

            marker_file = manager.profile_dir / ".auth_ready"
            marker_file.write_text("ready", encoding="utf-8")

            mock_context = MagicMock()
            with patch("builtins.input") as mock_input:
                res = manager.ensure_marketplaces_auth(mock_context)
                self.assertTrue(res)
                mock_input.assert_not_called()
                mock_context.new_page.assert_not_called()

    @patch("browser_manager.update_marketplace_auth")
    @patch("auth_setup.time.sleep")
    def test_ensure_marketplaces_auth_new_session_creates_marker(self, mock_sleep, mock_update_auth):
        with tempfile.TemporaryDirectory() as td:
            cfg = BrowserConfig(profile_dir=td)
            manager = BrowserManager(cfg)

            marker_file = manager.profile_dir / ".auth_ready"
            self.assertFalse(marker_file.exists())

            mock_context = MagicMock()
            mock_context.pages = []
            mock_page = MagicMock()
            mock_context.new_page.return_value = mock_page

            with patch("builtins.input", return_value=""):
                res = manager.ensure_marketplaces_auth(mock_context)
                self.assertTrue(res)
                self.assertTrue(marker_file.exists())
                self.assertTrue((manager.profile_dir / ".auth_confirmed").exists())
                self.assertEqual(mock_page.close.call_count, 4)
                mock_update_auth.assert_called_once()

    @patch("browser_manager.update_marketplace_auth")
    @patch("auth_setup.time.sleep")
    def test_check_or_request_auth_force_flag(self, mock_sleep, mock_update_auth):
        with tempfile.TemporaryDirectory() as td:
            cfg = BrowserConfig(profile_dir=td)
            manager = BrowserManager(cfg)

            marker_file = manager.profile_dir / ".auth_ready"
            marker_file.write_text("old_auth", encoding="utf-8")

            mock_context = MagicMock()
            mock_context.pages = []
            mock_page = MagicMock()
            mock_context.new_page.return_value = mock_page

            with patch("builtins.input", return_value=""):
                res = manager.check_or_request_auth(mock_context, force=True)
                self.assertTrue(res)
                self.assertEqual(mock_page.close.call_count, 4)
                mock_update_auth.assert_called_once()

    @patch("auth_setup.update_marketplace_auth")
    @patch("auth_setup.time.sleep")
    @patch("auth_setup.sync_playwright")
    def test_run_auth_setup_creates_marker(self, mock_sync_pw, mock_sleep, mock_update_auth):
        from auth_setup import run_auth_setup

        with tempfile.TemporaryDirectory() as td:
            mock_pw_instance = MagicMock()
            mock_sync_pw.return_value.__enter__.return_value = mock_pw_instance
            mock_context = MagicMock()
            mock_context.pages = []
            mock_page = MagicMock()
            mock_context.new_page.return_value = mock_page
            mock_pw_instance.chromium.launch_persistent_context.return_value = mock_context

            with patch("builtins.input", return_value=""):
                success = run_auth_setup(profile_dir=Path(td), force=True)
                self.assertTrue(success)
                self.assertTrue((Path(td) / ".auth_ready").exists())
                self.assertTrue((Path(td) / ".auth_confirmed").exists())
                self.assertEqual(mock_page.goto.call_count, 4)
                mock_context.close.assert_called_once()
                mock_update_auth.assert_called_once()

    @patch("auth_setup.time.sleep")
    def test_granular_auth_opens_only_false_platforms(self, mock_sleep):
        """
        Проверяет, что при avito=True открываются только 3 оставшиеся вкладки (Ozon, WB, YM),
        а Avito не открывается. После подтверждения флаги обновляются на true.
        """
        from config_loader import MarketplaceAuthConfig, update_marketplace_auth

        with tempfile.TemporaryDirectory() as td:
            config_file = Path(td) / "main_config.json"
            initial_cfg = {
                "marketplace_auth": {
                    "ozon": False,
                    "wildberries": False,
                    "yandex_market": False,
                    "avito": True,
                    "force_reauth": False
                }
            }
            config_file.write_text(json.dumps(initial_cfg), encoding="utf-8")

            app_cfg = AppConfig(
                browser=BrowserConfig(profile_dir=td),
                marketplace_auth=MarketplaceAuthConfig(
                    ozon=False,
                    wildberries=False,
                    yandex_market=False,
                    avito=True,
                    force_reauth=False,
                    _is_explicit=True,
                ),
                config_file_path=config_file,
            )
            manager = BrowserManager(app_cfg)

            mock_context = MagicMock()
            mock_context.pages = []
            mock_page = MagicMock()
            mock_context.new_page.return_value = mock_page

            with patch("builtins.input", return_value=""):
                res = manager.ensure_marketplaces_auth(mock_context)
                self.assertTrue(res)
                # Должно открыться ровно 3 вкладки, а не 4
                self.assertEqual(mock_page.goto.call_count, 3)
                opened_urls = [call[0][0] for call in mock_page.goto.call_args_list]
                self.assertIn("https://www.ozon.ru/", opened_urls)
                self.assertIn("https://www.wildberries.ru/", opened_urls)
                self.assertIn("https://market.yandex.ru/", opened_urls)
                self.assertNotIn("https://www.avito.ru/", opened_urls)

                # Проверяем, что в config_file обновились флаги на true
                saved_data = json.loads(config_file.read_text(encoding="utf-8"))
                auth_data = saved_data["marketplace_auth"]
                self.assertTrue(auth_data["ozon"])
                self.assertTrue(auth_data["wildberries"])
                self.assertTrue(auth_data["yandex_market"])
                self.assertTrue(auth_data["avito"])
                self.assertFalse(auth_data["force_reauth"])

    def test_granular_auth_all_true_skips(self):
        """
        Проверяет, что если все 4 маркетплейса равны true, браузер не открывает вкладки.
        """
        from config_loader import MarketplaceAuthConfig

        with tempfile.TemporaryDirectory() as td:
            app_cfg = AppConfig(
                browser=BrowserConfig(profile_dir=td),
                marketplace_auth=MarketplaceAuthConfig(
                    ozon=True,
                    wildberries=True,
                    yandex_market=True,
                    avito=True,
                    force_reauth=False,
                    _is_explicit=True,
                ),
            )
            manager = BrowserManager(app_cfg)
            mock_context = MagicMock()

            with patch("builtins.input") as mock_input:
                res = manager.ensure_marketplaces_auth(mock_context)
                self.assertTrue(res)
                mock_input.assert_not_called()
                mock_context.new_page.assert_not_called()

    @patch("auth_setup.time.sleep")
    def test_force_reauth_opens_all_and_resets(self, mock_sleep):
        """
        Проверяет, что при force_reauth=True открываются все 4 вкладки, даже если все true,
        и флаг force_reauth сбрасывается в false.
        """
        from config_loader import MarketplaceAuthConfig

        with tempfile.TemporaryDirectory() as td:
            config_file = Path(td) / "main_config.json"
            initial_cfg = {
                "marketplace_auth": {
                    "ozon": True,
                    "wildberries": True,
                    "yandex_market": True,
                    "avito": True,
                    "force_reauth": True
                }
            }
            config_file.write_text(json.dumps(initial_cfg), encoding="utf-8")

            app_cfg = AppConfig(
                browser=BrowserConfig(profile_dir=td),
                marketplace_auth=MarketplaceAuthConfig(
                    ozon=True,
                    wildberries=True,
                    yandex_market=True,
                    avito=True,
                    force_reauth=True,
                    _is_explicit=True,
                ),
                config_file_path=config_file,
            )
            manager = BrowserManager(app_cfg)
            mock_context = MagicMock()
            mock_context.pages = []
            mock_page = MagicMock()
            mock_context.new_page.return_value = mock_page

            with patch("builtins.input", return_value=""):
                res = manager.ensure_marketplaces_auth(mock_context)
                self.assertTrue(res)
                # Открываются все 4 вкладки
                self.assertEqual(mock_page.goto.call_count, 4)

                saved_data = json.loads(config_file.read_text(encoding="utf-8"))
                self.assertFalse(saved_data["marketplace_auth"]["force_reauth"])

    def test_ensure_chrome_ready_creates_dir(self):
        with tempfile.TemporaryDirectory() as td:
            custom_dir = Path(td) / "sub" / "automation_profile"
            self.assertFalse(custom_dir.exists())

            cfg = BrowserConfig(profile_dir=str(custom_dir))
            manager = BrowserManager(cfg)
            res = manager.ensure_chrome_ready()
            self.assertTrue(res)
            self.assertTrue(custom_dir.exists())

    def test_close_context(self):
        cfg = BrowserConfig()
        manager = BrowserManager(cfg)
        mock_ctx = MagicMock()
        manager.close(mock_ctx)
        mock_ctx.close.assert_called_once()

    def test_config_loader_browser_section(self):
        config_content = {
            "enabled": True,
            "check_interval_minutes": 5,
            "browser": {
                "profile_dir": "custom_automation_dir",
                "headless": False
            }
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(config_content, f)
            tmp_path = Path(f.name)

        try:
            cfg = load_config(tmp_path)
            self.assertEqual(cfg.browser.profile_dir, "custom_automation_dir")
            self.assertFalse(cfg.browser.headless)
            self.assertEqual(cfg.browser.mode, "persistent")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_backward_compatibility_stubs(self):
        self.assertFalse(is_chrome_running())
        self.assertTrue(kill_chrome_processes())
        self.assertTrue(reset_chrome_crash_state(Path(".")))
        is_ok, data, msg = check_cdp_status()
        self.assertTrue(is_ok)
        is_ok, data, url, msg = check_cdp_endpoint()
        self.assertTrue(is_ok)
        is_ok, data, url, msg = is_cdp_ready()
        self.assertTrue(is_ok)


if __name__ == "__main__":
    unittest.main()
