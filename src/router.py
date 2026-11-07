"""
router.py — Чтение и классификация URL из urls.txt.

Задачи:
  - Считать файл, отфильтровать пустые строки и комментарии (#).
  - Определить маркетплейс по регулярным выражениям домена.
  - Вернуть список (url, platform) для дальнейшей обработки.
"""
import re
from pathlib import Path
from typing import Generator

from loguru import logger

# ─── Регулярные выражения для определения маркетплейса ────────────────────────
PLATFORM_PATTERNS: dict[str, re.Pattern] = {
    "ozon": re.compile(r"(?:https?://)?(?:www\.)?ozon\.ru/(?:product/|t/)", re.IGNORECASE),
    "wildberries": re.compile(r"(?:https?://)?(?:www\.)?wildberries\.ru/catalog/", re.IGNORECASE),
    "yandex_market": re.compile(r"(?:https?://)?market\.yandex\.ru/(?:product|card|cc|catalog)/?", re.IGNORECASE),
    "avito": re.compile(r"(?:https?://)?(?:www\.)?avito\.ru/", re.IGNORECASE),
}


# Человекочитаемые имена платформ для отчёта
PLATFORM_NAMES: dict[str, str] = {
    "ozon": "Ozon",
    "wildberries": "Wildberries",
    "yandex_market": "Yandex Market",
    "avito": "Avito",
    "unknown": "Неизвестно",
}


def _iter_urls(filepath: Path) -> Generator[str, None, None]:
    """Читает файл и возвращает валидные строки-URL (без комментариев и пустых)."""
    text = filepath.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield stripped


def classify_url(url: str) -> str:
    """Возвращает идентификатор платформы или 'unknown'."""
    for platform, pattern in PLATFORM_PATTERNS.items():
        if pattern.search(url):
            return platform
    return "unknown"


def route_urls(filepath: Path) -> list[tuple[str, str]]:
    """
    Загружает URLs из файла и классифицирует каждый.

    Returns:
        Список кортежей (url, platform), где platform — один из:
        'ozon', 'wildberries', 'yandex_market', 'unknown'.
    """
    if not filepath.exists():
        logger.error(f"Файл с URL не найден: {filepath}")
        return []

    routed: list[tuple[str, str]] = []

    for url in _iter_urls(filepath):
        platform = classify_url(url)
        if platform == "unknown":
            logger.warning(f"[UNSUPPORTED] {url}")
        else:
            logger.info(f"[{platform.upper()}] {url}")
        routed.append((url, platform))

    logger.info(
        f"Загружено {len(routed)} URL: "
        + ", ".join(
            f"{p}={sum(1 for _, pl in routed if pl == p)}"
            for p in [*PLATFORM_PATTERNS.keys(), "unknown"]
        )
    )
    return routed
