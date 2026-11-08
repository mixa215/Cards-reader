"""
browser_stealth.py — Модуль Stealth-маскировки сессий Playwright для обхода WAF (Ozon, WB RWB Captcha).

Реализует:
  1. Набор аргументов запуска браузера:
     - --disable-blink-features=AutomationControlled
     - --no-first-run
     - --no-default-browser-check
     - --start-maximized
     - --lang=ru-RU,ru
  2. Инъекцию скриптов маскировки (add_init_script):
     - Принудительно выставляет navigator.webdriver = undefined
     - Эмулирует реальные плагины Chrome (navigator.plugins)
     - Подменяет navigator.languages = ['ru-RU', 'ru', 'en-US']
     - Подменяет параметры window.chrome (runtime, loadTimes, csi, app)
     - Корректирует permissions.query для notifications
  3. Вызов библиотеки playwright-stealth (Stealth().apply_stealth_sync) для комплексного сокрытия отпечатков.
"""
from typing import Union, List, Any
from loguru import logger
from playwright.sync_api import BrowserContext, Page

STEALTH_CHROME_ARGS: List[str] = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--start-maximized",
    "--lang=ru-RU,ru",
]

STEALTH_IGNORE_DEFAULT_ARGS: List[str] = [
    "--enable-automation",
]

STEALTH_INIT_SCRIPT: str = """
(() => {
    // 1. Скрытие navigator.webdriver (принудительно undefined)
    try {
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true
        });
        delete Object.getPrototypeOf(navigator).webdriver;
    } catch (e) {}

    // 2. Эмуляция объекта window.chrome (runtime, loadTimes, csi, app)
    try {
        if (!window.chrome) {
            window.chrome = {};
        }
        window.chrome.app = {
            isInstalled: false,
            InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
            RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
        };
        window.chrome.runtime = {
            OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
            OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
            PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
            RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
            connect: function() {},
            sendMessage: function() {}
        };
        window.chrome.loadTimes = function() {
            return {
                commitLoadTime: Date.now() / 1000 - 0.5,
                connectionInfo: 'h2',
                finishDocumentLoadTime: Date.now() / 1000 - 0.1,
                finishLoadTime: Date.now() / 1000,
                firstPaintAfterLoadTime: 0,
                firstPaintTime: Date.now() / 1000 - 0.4,
                navigationType: 'Other',
                npnNegotiatedProtocol: 'h2',
                requestTime: Date.now() / 1000 - 0.8,
                startLoadTime: Date.now() / 1000 - 0.7,
                wasAlternateProtocolAvailable: false,
                wasFetchedViaSpdy: true,
                wasNpnNegotiated: true
            };
        };
        window.chrome.csi = function() {
            return {
                onloadT: Date.now(),
                pageT: Date.now() - 1000,
                startE: Date.now() - 1200,
                tran: 15
            };
        };
    } catch (e) {}

    // 3. Эмуляция локали и языков (ru-RU, ru, en-US)
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['ru-RU', 'ru', 'en-US'],
            configurable: true
        });
        Object.defineProperty(navigator, 'language', {
            get: () => 'ru-RU',
            configurable: true
        });
    } catch (e) {}

    // 4. Эмуляция реальных плагинов Chrome (navigator.plugins)
    try {
        const pluginData = [
            { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }
        ];
        const fakePlugins = Object.create(PluginArray.prototype);
        pluginData.forEach((p, idx) => {
            const fakePlugin = Object.create(Plugin.prototype);
            Object.defineProperties(fakePlugin, {
                name: { get: () => p.name },
                filename: { get: () => p.filename },
                description: { get: () => p.description },
                length: { get: () => 1 }
            });
            fakePlugins[idx] = fakePlugin;
            fakePlugins[p.name] = fakePlugin;
        });
        Object.defineProperty(fakePlugins, 'length', { get: () => pluginData.length });
        fakePlugins.item = (i) => fakePlugins[i] || null;
        fakePlugins.namedItem = (name) => fakePlugins[name] || null;

        Object.defineProperty(navigator, 'plugins', {
            get: () => fakePlugins,
            configurable: true
        });
    } catch (e) {}

    // 5. Обработка navigator.permissions.query
    try {
        if (window.navigator.permissions && window.navigator.permissions.query) {
            const origQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    origQuery(parameters)
            );
        }
    } catch (e) {}
})();
"""


def apply_stealth(target: Union[BrowserContext, Page]) -> None:
    """
    Применяет полный комплекс Stealth-маскировки к контексту или странице:
      1. playwright-stealth (если установлена библиотека);
      2. Дополнительный кастомный init-скрипт (гарантирует navigator.webdriver=undefined,
         plugins, languages и window.chrome.runtime/loadTimes/csi).
    """
    if hasattr(target, "__dict__") and target.__dict__.get("_stealth_custom_applied"):
        return

    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(target)
        logger.debug("[STEALTH] Модуль playwright-stealth успешно активирован.")
    except Exception as e:
        logger.debug(f"[STEALTH] playwright-stealth недоступен или выдал замечание: {e}. Применяется нативный init_script.")

    try:
        target.add_init_script(STEALTH_INIT_SCRIPT)
        if hasattr(target, "__dict__"):
            target.__dict__["_stealth_custom_applied"] = True
        logger.debug("[STEALTH] Пользовательский init-скрипт маскировки navigator.webdriver/plugins успешно инжектирован.")
    except Exception as e:
        logger.warning(f"[STEALTH] Не удалось инжектировать init-скрипт: {e}")
