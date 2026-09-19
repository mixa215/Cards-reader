"""
ocr_worker.py — Распознавание цены из скриншота через EasyOCR.

Пайплайн:
  1. Загрузка изображения через Pillow.
  2. Предобработка: grayscale + повышение контраста (если нужно).
  3. Распознавание текста EasyOCR (ru + en).
  4. Фильтрация и нормализация: ищем числовые блоки, убираем
     неразрывные пробелы, знаки валют, лишние символы.
  5. Возвращаем float или None.
"""
import re
from pathlib import Path
from typing import Optional

from loguru import logger

from config import OCR_MIN_CONFIDENCE

# ─── Шаблон для извлечения числа цены из OCR-текста ─────────────────────────
#   Примеры: "1 234 ₽", "12 345.00 руб.", "1234", "99,99"
_PRICE_RE = re.compile(
    r"""
    (?<!\d)                   # не предшествует цифра
    (\d[\d\s\u00a0.,]*\d      # число с возможными пробелами/точками/запятыми
    |\d+)                     # или одиночная цифра
    (?!\d)                    # не следует цифра
    """,
    re.VERBOSE,
)

# Символы для удаления перед парсингом числа
_STRIP_CHARS_RE = re.compile(r"[₽руб\.BYNbyn\s\u00a0]", re.IGNORECASE)


def _preprocess_image(image_path: str):
    """
    Предобрабатывает изображение для улучшения OCR:
    - Конвертирует в grayscale
    - Увеличивает контраст при необходимости
    Возвращает PIL.Image.
    """
    from PIL import Image, ImageEnhance, ImageFilter

    img = Image.open(image_path)

    # Конвертируем в grayscale если не в нём
    if img.mode != "L":
        img = img.convert("L")

    # Масштабируем если изображение слишком маленькое (EasyOCR хуже на малых)
    w, h = img.size
    if w < 200 or h < 50:
        scale = max(200 / w, 50 / h, 2.0)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # Повышаем контраст
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)

    # Лёгкое сглаживание для устранения шума
    img = img.filter(ImageFilter.MedianFilter(size=3))

    return img


def _parse_price_from_texts(texts: list[str]) -> Optional[float]:
    """
    Принимает список строк от OCR, ищет и возвращает числовое значение цены.
    Возвращает float или None если цена не распознана.
    """
    candidates: list[float] = []

    for text in texts:
        # Убираем валютные символы и лишние пробелы
        cleaned = _STRIP_CHARS_RE.sub("", text).strip()

        for match in _PRICE_RE.finditer(cleaned):
            raw = match.group(0).replace(" ", "").replace("\u00a0", "")
            # Нормализуем разделитель: запятая → точка
            raw = raw.replace(",", ".")
            # Убираем лишние точки (оставляем только последнюю как десятичный)
            parts = raw.split(".")
            if len(parts) > 2:
                raw = "".join(parts[:-1]) + "." + parts[-1]

            try:
                value = float(raw)
                # Фильтруем нереальные значения (< 1 или > 10 млн)
                if 1.0 <= value <= 10_000_000.0:
                    candidates.append(value)
            except ValueError:
                continue

    if not candidates:
        return None

    # Берём наибольшее значение — обычно это и есть основная цена
    return max(candidates)


def extract_price(image_path: Optional[str], raw_text_hint: str = "") -> Optional[float]:
    """
    Основная функция извлечения цены.

    Args:
        image_path: путь к файлу скриншота (png/jpg).
        raw_text_hint: необязательный текст напрямую из DOM (используется
                       как первичный источник, если доступен).

    Returns:
        float — распознанная цена, или None при неудаче.
    """
    # 1. Попытка разобрать DOM-текст без OCR (быстрый путь)
    if raw_text_hint.strip():
        price = _parse_price_from_texts([raw_text_hint])
        if price is not None:
            logger.success(f"Цена из DOM-текста: {price:.2f} ₽")
            return price

    # 2. OCR из скриншота
    if not image_path or not Path(image_path).exists():
        logger.warning("Скриншот не найден, цена не извлечена")
        return None

    try:
        import easyocr
        import numpy as np

        img = _preprocess_image(image_path)
        img_array = np.array(img)

        reader = easyocr.Reader(["ru", "en"], gpu=False, verbose=False)
        raw_results = reader.readtext(img_array)

        # Фильтруем по минимальной уверенности
        texts = [
            text
            for (_, text, conf) in raw_results
            if conf >= OCR_MIN_CONFIDENCE
        ]

        logger.debug(f"OCR распознал {len(texts)} блок(ов): {texts}")

        price = _parse_price_from_texts(texts)

        if price is not None:
            logger.success(f"Цена распознана OCR: {price:.2f} ₽")
        else:
            logger.warning(f"OCR не нашёл цену в тексте: {texts}")

        return price

    except Exception as e:
        logger.error(f"Ошибка OCR: {e}")
        return None
