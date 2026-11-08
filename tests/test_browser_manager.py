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

    def test_check_or_request_auth_existing_marker(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = BrowserConfig(profile_dir=td)
            manager = BrowserManager(cfg)

            marker_file = manager.profile_dir / ".auth_confirmed"
            marker_file.write_text("confirmed", encoding="utf-8")

            mock_context = MagicMock()
            with patch("builtins.input") as mock_input:
                res = manager.check_or_request_auth(mock_context)
                self.assertTrue(res)
                mock_input.assert_not_called()
                mock_context.new_page.assert_not_called()

    def test_check_or_request_auth_new_session_creates_marker(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = BrowserConfig(profile_dir=td)
            manager = BrowserManager(cfg)

            marker_file = manager.profile_dir / ".auth_confirmed"
            self.assertFalse(marker_file.exists())

            mock_context = MagicMock()
            mock_page = MagicMock()
            mock_context.new_page.return_value = mock_page

            with patch("builtins.input", return_value=""):
                res = manager.check_or_request_auth(mock_context, test_url="https://www.avito.ru")
                self.assertTrue(res)
                self.assertTrue(marker_file.exists())
                mock_page.close.assert_called_once()

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
