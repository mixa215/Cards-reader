"""
browser_cdp_manager.py — Реэкспорт контроллера ChromeCDPController для обратной совместимости.
"""
from browser_manager import (
    is_chrome_running,
    kill_chrome_processes,
    reset_chrome_crash_state,
    check_cdp_status,
    check_cdp_endpoint,
    check_single_endpoint,
    is_cdp_ready,
    ChromeCDPController,
    BrowserManager,
    ChromeCDPManager,
)

__all__ = [
    "is_chrome_running",
    "kill_chrome_processes",
    "reset_chrome_crash_state",
    "check_cdp_status",
    "check_cdp_endpoint",
    "check_single_endpoint",
    "is_cdp_ready",
    "ChromeCDPController",
    "BrowserManager",
    "ChromeCDPManager",
]
