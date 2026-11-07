"""
config_loader.py — Модуль загрузки и валидации параметров из main_config.json.

Обеспечивает:
  - Безопасное чтение JSON с отказоустойчивостью (fallback к значениям по умолчанию).
  - Строгую валидацию типов данных и граничных условий.
  - Надёжный поиск файла конфигурации относительно корня проекта BASE_DIR.
  - Автоматическое добавление префикса 'multi_' к имени Excel-отчёта при активном планировщике.
"""
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from loguru import logger

import config as base_config


@dataclass
class AppConfig:
    """Конфигурация приложения с валидированными параметрами."""
    check_interval_minutes: int = 10
    enabled: bool = True
    excel_file_path: str = "output/multi_prices_report_{date}.xlsx"
    input_urls_path: str = "urls.txt"

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

        # Проверяем необходимость префикса multi_
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
    Находит существующий файл конфигурации, проверяя:
      1. Переданный путь (напрямую и относительно BASE_DIR).
      2. BASE_DIR / "main_config.json".
      3. CWD / "main_config.json".
      4. BASE_DIR / "configs" / "main_config.json".
    """
    candidates: list[Path] = []

    if config_path:
        p = Path(config_path)
        if p.is_absolute():
            candidates.append(p)
        else:
            candidates.append(base_config.BASE_DIR / p)
            candidates.append(p)

    # Стандартные расположения в проекте
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

    При отсутствии файла или ошибках синтаксиса возвращает дефолтный AppConfig.
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
            logger.error(
                f"Корневой элемент {target_path.name} должен быть JSON-объектом. "
                f"Применяются стандартные настройки."
            )
            return AppConfig()

        # Поддержка основных и альтернативных ключей
        raw_interval = (
            raw_data.get("check_interval_minutes")
            if "check_interval_minutes" in raw_data
            else raw_data.get("interval_minutes", raw_data.get("interval"))
        )
        interval = _validate_interval(raw_interval, default=10)

        raw_enabled = (
            raw_data.get("enabled")
            if "enabled" in raw_data
            else raw_data.get("active", raw_data.get("enable"))
        )
        enabled = _validate_bool(raw_enabled, default=True)

        raw_excel = (
            raw_data.get("excel_file_path")
            or raw_data.get("excel_path")
            or raw_data.get("output_excel")
        )
        excel_path = _validate_str(
            raw_excel,
            default="output/multi_prices_report_{date}.xlsx",
            field_name="excel_file_path",
        )

        raw_urls = (
            raw_data.get("input_urls_path")
            or raw_data.get("urls_path")
            or raw_data.get("urls_file")
        )
        urls_path = _validate_str(
            raw_urls,
            default="urls.txt",
            field_name="input_urls_path",
        )

        cfg = AppConfig(
            check_interval_minutes=interval,
            enabled=enabled,
            excel_file_path=excel_path,
            input_urls_path=urls_path,
        )
        logger.debug(
            f"Конфиг успешно загружен [{target_path}]: "
            f"интервал={cfg.check_interval_minutes}м, active={cfg.enabled}, urls={cfg.input_urls_path}, "
            f"excel={cfg.resolve_excel_path().name}"
        )
        return cfg

    except json.JSONDecodeError as e:
        logger.error(
            f"Ошибка синтаксиса JSON в {target_path}: {e}. "
            f"Используются дефолтные параметры."
        )
        return AppConfig()
    except Exception as e:
        logger.error(
            f"Не удалось прочитать {target_path}: {e}. "
            f"Используются дефолтные параметры."
        )
        return AppConfig()
