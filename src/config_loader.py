"""
config_loader.py — Модуль загрузки и валидации параметров из main_config.json.

Обеспечивает:
  - Безопасное чтение JSON с отказоустойчивостью (fallback к значениям по умолчанию).
  - Строгую валидацию типов данных и граничных условий.
  - Надёжный поиск файла конфигурации относительно корня проекта BASE_DIR.
  - Автоматическое добавление префикса 'multi_' к имени Excel-отчёта при активном планировщике.
  - Поддержку блока настроек browser (режимы CDP и Persistent Context).
"""
import os
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from loguru import logger

import config as base_config


@dataclass
class BrowserConfig:
    """Настройки браузера Chrome для авторизованных сессий через Dedicated Persistent Context."""
    mode: str = "persistent"
    profile_dir: str = "chrome_automation_profile"
    headless: bool = False
    channel: str = "chrome"
    cdp_url: str = "http://127.0.0.1:9225"
    port: int = 9225
    force_restart_chrome: bool = False
    chrome_executable_path: str = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    user_data_dir: str = ""
    profile_directory: str = "Default"
    kill_existing_chrome: Optional[bool] = None

    def resolve_profile_dir(self, base_dir: Optional[Path] = None) -> Path:
        """Возвращает абсолютный путь к каталогу выделенного профиля автоматизации."""
        dir_val = self.profile_dir or self.user_data_dir or "chrome_automation_profile"
        p = Path(os.path.expandvars(str(dir_val).strip()))
        if p.is_absolute():
            return p
        base = base_dir or base_config.BASE_DIR
        return (base / p).resolve()

    def resolve_port(self) -> int:
        """Возвращает числовой порт CDP (для обратной совместимости)."""
        if self.port and self.port > 0:
            return self.port
        import urllib.parse
        try:
            parsed = urllib.parse.urlparse(self.cdp_url)
            if parsed.port:
                return parsed.port
        except Exception:
            pass
        return 9225

    def resolve_user_data_dir(self) -> Path:
        """Возвращает путь к каталогу профиля автоматизации (совместимость)."""
        if self.user_data_dir and self.user_data_dir.strip() and "User Data" in self.user_data_dir:
            expanded = os.path.expandvars(self.user_data_dir.strip())
            return Path(expanded)
        return self.resolve_profile_dir()

    def resolve_chrome_exe(self) -> Path:
        """Возвращает путь к исполняемому файлу chrome.exe."""
        if self.chrome_executable_path and Path(self.chrome_executable_path).exists():
            return Path(self.chrome_executable_path)
        for cand in [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]:
            if cand.exists():
                return cand
        return Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


@dataclass
class MarketplaceAuthConfig:
    """Статусы авторизации для каждого маркетплейса и флаг принудительной переавторизации."""
    ozon: bool = False
    wildberries: bool = False
    yandex_market: bool = False
    avito: bool = False
    force_reauth: bool = False
    _is_explicit: bool = False

    def is_authorized(self, platform: str) -> bool:
        """Возвращает True, если маркетплейс авторизован и не установлен force_reauth."""
        if self.force_reauth:
            return False
        return bool(getattr(self, platform, False))

    def get_pending_marketplaces(self) -> list[str]:
        """Возвращает список платформ (ключей), требующих авторизации."""
        all_platforms = ["ozon", "wildberries", "yandex_market", "avito"]
        if self.force_reauth:
            return all_platforms
        return [p for p in all_platforms if not getattr(self, p, False)]

    def is_all_authorized(self) -> bool:
        """Возвращает True, если все маркетплейсы авторизованы и force_reauth=False."""
        return len(self.get_pending_marketplaces()) == 0

    def to_dict(self) -> dict[str, bool]:
        return {
            "ozon": self.ozon,
            "wildberries": self.wildberries,
            "yandex_market": self.yandex_market,
            "avito": self.avito,
            "force_reauth": self.force_reauth,
        }


@dataclass
class AppConfig:
    """Конфигурация приложения с валидированными параметрами."""
    check_interval_minutes: int = 10
    enabled: bool = True
    excel_file_path: str = "output/multi_prices_report_{date}.xlsx"
    input_urls_path: str = "urls.txt"

    # Параметры авторизованного Chrome профиля
    use_local_profile_downloader: bool = True
    dump_pages_dir: str = "downloads/pages_cache"
    cleanup_cache_days: int = 7

    # Блок настроек браузера
    browser: BrowserConfig = field(default_factory=BrowserConfig)

    # Блок гранулярного контроля авторизации маркетплейсов
    marketplace_auth: MarketplaceAuthConfig = field(default_factory=MarketplaceAuthConfig)

    # Путь к исходному файлу конфигурации (для обратной записи)
    config_file_path: Optional[Path] = None

    def resolve_excel_path(
        self,
        target_date: Optional[datetime] = None,
        is_scheduled: Optional[bool] = None,
    ) -> Path:
        """
        Формирует реальный путь к Excel-файлу, подставляя дату вместо {date}.

        При активном планировщике (enabled=True или is_scheduled=True)
        автоматически гарантирует префикс 'multi_' у файла отчёта.
        """
        dt = target_date or datetime.now()
        date_str = dt.strftime("%Y-%m-%d")
        raw_path = self.excel_file_path.replace("{date}", date_str)
        path = Path(raw_path)

        need_multi = self.enabled if is_scheduled is None else is_scheduled
        if need_multi:
            fname = path.name
            if not fname.startswith("multi_"):
                path = path.with_name(f"multi_{fname}")

        if not path.is_absolute():
            path = base_config.BASE_DIR / path

        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def resolve_urls_path(self) -> Path:
        """Возвращает абсолютный путь к файлу входных ссылок."""
        path = Path(self.input_urls_path)
        if not path.is_absolute():
            path = base_config.BASE_DIR / path
        return path

    def resolve_dump_pages_dir(self) -> Path:
        """Возвращает абсолютный путь к каталогу дампов страниц."""
        path = Path(self.dump_pages_dir)
        if not path.is_absolute():
            path = base_config.BASE_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def chrome_user_data_dir(self) -> str:
        """Обратная совместимость: строковый путь к User Data."""
        return self.browser.user_data_dir

    @property
    def chrome_profile_name(self) -> str:
        """Обратная совместимость: имя директории профиля."""
        return self.browser.profile_directory

    @property
    def chrome_cdp_port(self) -> int:
        """Обратная совместимость: номер порта CDP из cdp_url."""
        import urllib.parse
        try:
            parsed = urllib.parse.urlparse(self.browser.cdp_url)
            if parsed.port:
                return parsed.port
        except Exception:
            pass
        return 9225

    def resolve_chrome_user_data_dir(self) -> Path:
        """Обратная совместимость: Path к User Data."""
        return self.browser.resolve_user_data_dir()

    @property
    def force_restart_chrome(self) -> bool:
        """Флаг перезапуска Chrome для открытия порта CDP."""
        return self.browser.force_restart_chrome or self.browser.kill_existing_chrome

    @property
    def kill_existing_chrome(self) -> bool:
        """Обратная совместимость: флаг завершения процессов Chrome."""
        return self.force_restart_chrome



def _validate_interval(value: Any, default: int = 10) -> int:
    """Валидирует интервал проверки в минутах (целое положительное число)."""
    if value is None:
        return default
    try:
        val = int(value)
        if val <= 0:
            logger.warning(
                f"check_interval_minutes должен быть > 0 (получено: {value}). "
                f"Используется значение по умолчанию: {default} мин."
            )
            return default
        return val
    except (ValueError, TypeError):
        logger.warning(
            f"Некорректное значение check_interval_minutes: {value!r}. "
            f"Используется значение по умолчанию: {default} мин."
        )
        return default


def _validate_bool(value: Any, default: bool = True) -> bool:
    """Валидирует булев флаг активности."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        val_clean = value.strip().lower()
        if val_clean in ("true", "1", "yes", "да"):
            return True
        if val_clean in ("false", "0", "no", "нет"):
            return False
    logger.warning(
        f"Некорректное булево значение: {value!r}. "
        f"Используется значение по умолчанию: {default}"
    )
    return default


def _validate_str(value: Any, default: str, field_name: str) -> str:
    """Валидирует непустую строку пути."""
    if value is not None and isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _find_config_file(config_path: Optional[Path] = None) -> Optional[Path]:
    """
    Находит существующий файл конфигурации.
    """
    candidates: list[Path] = []

    if config_path:
        p = Path(config_path)
        if p.is_absolute():
            candidates.append(p)
        else:
            candidates.append(base_config.BASE_DIR / p)
            candidates.append(p)

    candidates.extend([
        base_config.BASE_DIR / "main_config.json",
        Path.cwd() / "main_config.json",
        base_config.BASE_DIR / "configs" / "main_config.json",
    ])

    for cand in candidates:
        if cand.is_file():
            return cand.resolve()

    return None


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """
    Загружает и валидирует конфигурацию из main_config.json.
    """
    target_path = _find_config_file(config_path)

    if not target_path:
        fallback_target = config_path or (base_config.BASE_DIR / "main_config.json")
        logger.warning(
            f"Файл конфигурации не найден: {fallback_target}. "
            f"Применяются стандартные настройки."
        )
        return AppConfig()

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        if not isinstance(raw_data, dict):
            logger.error(f"Корневой элемент {target_path.name} должен быть JSON-объектом.")
            return AppConfig()

        interval = _validate_interval(
            raw_data.get("check_interval_minutes") or raw_data.get("interval_minutes", raw_data.get("interval")),
            default=10,
        )

        enabled = _validate_bool(
            raw_data.get("enabled") if "enabled" in raw_data else raw_data.get("active", raw_data.get("enable")),
            default=True,
        )

        excel_path = _validate_str(
            raw_data.get("excel_file_path") or raw_data.get("excel_path") or raw_data.get("output_excel"),
            default="output/multi_prices_report_{date}.xlsx",
            field_name="excel_file_path",
        )

        urls_path = _validate_str(
            raw_data.get("input_urls_path") or raw_data.get("urls_path") or raw_data.get("urls_file"),
            default="urls.txt",
            field_name="input_urls_path",
        )

        use_downloader = _validate_bool(
            raw_data.get("use_local_profile_downloader"),
            default=True,
        )

        dump_dir = _validate_str(
            raw_data.get("dump_pages_dir"),
            default="downloads/pages_cache",
            field_name="dump_pages_dir",
        )

        cleanup_days = _validate_interval(
            raw_data.get("cleanup_cache_days"),
            default=7,
        )

        # Парсинг блока browser
        raw_browser = raw_data.get("browser", {})
        if not isinstance(raw_browser, dict):
            raw_browser = {}

        mode = str(raw_browser.get("mode") or raw_data.get("browser_mode") or "persistent").strip().lower()
        if mode not in ("persistent", "cdp"):
            mode = "persistent"

        profile_dir = str(
            raw_browser.get("profile_dir")
            or raw_browser.get("user_data_dir")
            or raw_data.get("profile_dir")
            or "chrome_automation_profile"
        ).strip()

        headless = _validate_bool(
            raw_browser.get("headless") if "headless" in raw_browser else raw_data.get("headless"),
            default=False,
        )

        channel = str(raw_browser.get("channel") or "chrome").strip()

        port = _validate_interval(
            raw_browser.get("port")
            or raw_browser.get("cdp_port")
            or raw_data.get("chrome_cdp_port")
            or raw_data.get("cdp_port"),
            default=9225,
        )

        default_cdp_url = f"http://127.0.0.1:{port}"
        cdp_url = str(raw_browser.get("cdp_url") or default_cdp_url).strip()

        chrome_exe = str(
            raw_browser.get("chrome_executable_path")
            or raw_data.get("chrome_executable_path")
            or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        ).strip()

        profile_sub_dir = str(
            raw_browser.get("profile_directory")
            or raw_data.get("chrome_profile_name")
            or "Default"
        ).strip()

        browser_cfg = BrowserConfig(
            mode=mode,
            profile_dir=profile_dir,
            headless=headless,
            channel=channel,
            cdp_url=cdp_url,
            port=port,
            chrome_executable_path=chrome_exe,
            user_data_dir=profile_dir,
            profile_directory=profile_sub_dir,
            force_restart_chrome=False,
            kill_existing_chrome=False,
        )

        # Парсинг блока marketplace_auth
        has_explicit_auth = "marketplace_auth" in raw_data
        raw_auth = raw_data.get("marketplace_auth", {})
        if not isinstance(raw_auth, dict):
            raw_auth = {}

        auth_ozon = _validate_bool(raw_auth.get("ozon"), default=False)
        auth_wb = _validate_bool(raw_auth.get("wildberries"), default=False)
        auth_ym = _validate_bool(raw_auth.get("yandex_market"), default=False)
        auth_avito = _validate_bool(raw_auth.get("avito"), default=False)
        force_reauth = _validate_bool(raw_auth.get("force_reauth"), default=False)

        marketplace_auth_cfg = MarketplaceAuthConfig(
            ozon=auth_ozon,
            wildberries=auth_wb,
            yandex_market=auth_ym,
            avito=auth_avito,
            force_reauth=force_reauth,
            _is_explicit=has_explicit_auth,
        )

        cfg = AppConfig(
            check_interval_minutes=interval,
            enabled=enabled,
            excel_file_path=excel_path,
            input_urls_path=urls_path,
            use_local_profile_downloader=use_downloader,
            dump_pages_dir=dump_dir,
            cleanup_cache_days=cleanup_days,
            browser=browser_cfg,
            marketplace_auth=marketplace_auth_cfg,
            config_file_path=target_path,
        )

        logger.debug(
            f"Конфиг успешно загружен [{target_path}]: "
            f"интервал={cfg.check_interval_minutes}м, active={cfg.enabled}, "
            f"browser_mode={cfg.browser.mode}, profile_dir={cfg.browser.profile_dir}, "
            f"headless={cfg.browser.headless}, marketplace_auth={marketplace_auth_cfg.to_dict()}"
        )
        return cfg

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка синтаксиса JSON в {target_path}: {e}. Применены параметры по умолчанию.")
        return AppConfig()
    except Exception as e:
        logger.error(f"Не удалось прочитать {target_path}: {e}. Применены параметры по умолчанию.")
        return AppConfig()


def update_marketplace_auth(
    config_path: Optional[Path] = None,
    platforms: Optional[list[str]] = None,
    all_true: bool = False,
    reset_force: bool = True,
    **kwargs: bool,
) -> bool:
    """
    Обновляет блок 'marketplace_auth' в main_config.json.
    Устанавливает True для указанных платформ, применяет kwargs и опционально сбрасывает force_reauth в False.
    """
    target_path = _find_config_file(config_path)
    if not target_path or not target_path.exists():
        logger.warning(f"Не удалось найти файл конфигурации для обновления marketplace_auth: {config_path}")
        return False

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        if not isinstance(raw_data, dict):
            raw_data = {}

        auth_dict = raw_data.setdefault("marketplace_auth", {})
        if not isinstance(auth_dict, dict):
            auth_dict = {}
            raw_data["marketplace_auth"] = auth_dict

        all_platforms = ["ozon", "wildberries", "yandex_market", "avito"]

        if all_true:
            for p in all_platforms:
                auth_dict[p] = True
        elif platforms:
            for p in platforms:
                if p in all_platforms:
                    auth_dict[p] = True

        for k, v in kwargs.items():
            if k in all_platforms or k == "force_reauth":
                auth_dict[k] = bool(v)

        if reset_force and "force_reauth" not in kwargs:
            auth_dict["force_reauth"] = False

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)

        logger.success(f"✓ Блок marketplace_auth в {target_path.name} успешно обновлён: {auth_dict}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении marketplace_auth в {target_path}: {e}")
        return False
