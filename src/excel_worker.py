"""
excel_worker.py — Создание и заполнение отчёта Excel (.xlsx).

Структура таблицы:
  A: №                    — порядковый номер
  B: Маркетплейс          — Ozon / Wildberries / Yandex Market
  C: Исходная ссылка      — активная гиперссылка
  D: Цена (число)         — числовой формат (float)
  E: Валюта               — RUB
  F: Статус               — SUCCESS / CAPTCHA_BLOCKED / NOT_FOUND / UNSUPPORTED_URL / ERROR
  G: Скриншот             — гиперссылка на локальный png
  H: Дата и время         — datetime проверки

Автосохранение каждые EXCEL_AUTOSAVE_EVERY строк.
"""
from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.styles import (
    Alignment,
    Font,
    PatternFill,
    Border,
    Side,
    numbers,
)
from openpyxl.utils import get_column_letter
from loguru import logger

from config import OUTPUT_DIR, EXCEL_AUTOSAVE_EVERY

# ─── Цветовые метки по статусу ───────────────────────────────────────────────
STATUS_COLORS: dict[str, str] = {
    "SUCCESS": "C6EFCE",            # зелёный
    "CAPTCHA_BLOCKED": "FFEB9C",    # жёлтый
    "PRODUCT_DELETED": "FCE4D6",    # пастельный персиковый
    "SELECTOR_NOT_FOUND": "BDD7EE", # светло-голубой
    "NOT_FOUND": "FFC7CE",          # светло-красный
    "UNSUPPORTED_URL": "D9D9D9",    # серый
    "ERROR": "FF9999",              # ярко-красный
}


# ─── Заголовки столбцов ───────────────────────────────────────────────────────
HEADERS = [
    "№",
    "Маркетплейс",
    "Исходная ссылка",
    "Цена (число)",
    "Валюта",
    "Статус",
    "Скриншот",
    "Дата и время проверки",
]

# ─── Ширина столбцов (символы) ────────────────────────────────────────────────
COL_WIDTHS = [5, 18, 60, 16, 10, 20, 50, 24]

# Человекочитаемые имена маркетплейсов
PLATFORM_DISPLAY = {
    "ozon": "Ozon",
    "wildberries": "Wildberries",
    "yandex_market": "Yandex Market",
    "avito": "Avito",
    "unknown": "—",
}


class ExcelWriter:
    """Потоковая запись результатов в xlsx с автосохранением."""

    def __init__(self):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        self.filepath = OUTPUT_DIR / f"prices_report_{date_str}.xlsx"
        self._row = 2          # Текущая строка (1 — заголовки)
        self._unsaved = 0      # Счётчик несохранённых строк
        self._wb = openpyxl.Workbook()
        self._ws = self._wb.active
        self._ws.title = "Цены"
        self._setup_sheet()
        logger.info(f"Excel отчёт: {self.filepath}")

    # ── Внутренние методы ────────────────────────────────────────────────────

    def _setup_sheet(self) -> None:
        """Создаёт заголовочную строку с форматированием."""
        ws = self._ws

        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_fill = PatternFill("solid", fgColor="2F5496")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin = Side(style="thin", color="BFBFBF")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        for col_idx, (header, width) in enumerate(zip(HEADERS, COL_WIDTHS), start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = border
            ws.column_dimensions[get_column_letter(col_idx)].width = width

        ws.row_dimensions[1].height = 30
        ws.freeze_panes = "A2"

    def _cell_border(self):
        thin = Side(style="thin", color="E0E0E0")
        return Border(left=thin, right=thin, top=thin, bottom=thin)

    def _write_row(
        self,
        number: int,
        platform: str,
        url: str,
        price: Optional[float],
        status: str,
        screenshot_path: Optional[str],
        checked_at: datetime,
    ) -> None:
        ws = self._ws
        r = self._row
        border = self._cell_border()

        status_color = STATUS_COLORS.get(status, "FFFFFF")
        status_fill = PatternFill("solid", fgColor=status_color)
        base_align = Alignment(vertical="center")

        # A: №
        ws.cell(r, 1, number).alignment = Alignment(horizontal="center", vertical="center")

        # B: Маркетплейс
        ws.cell(r, 2, PLATFORM_DISPLAY.get(platform, platform)).alignment = base_align

        # C: URL (гиперссылка)
        url_cell = ws.cell(r, 3, url)
        url_cell.hyperlink = url
        url_cell.font = Font(color="0563C1", underline="single")
        url_cell.alignment = Alignment(vertical="center", wrap_text=False)

        # D: Цена
        price_cell = ws.cell(r, 4, price if price is not None else "")
        if price is not None:
            price_cell.number_format = "#,##0.00"
        price_cell.alignment = Alignment(horizontal="right", vertical="center")

        # E: Валюта
        ws.cell(r, 5, "RUB").alignment = Alignment(horizontal="center", vertical="center")

        # F: Статус
        status_cell = ws.cell(r, 6, status)
        status_cell.fill = status_fill
        status_cell.alignment = Alignment(horizontal="center", vertical="center")
        status_cell.font = Font(bold=True)

        # G: Скриншот (гиперссылка на файл)
        if screenshot_path and Path(screenshot_path).exists():
            shot_cell = ws.cell(r, 7, Path(screenshot_path).name)
            shot_cell.hyperlink = Path(screenshot_path).as_uri()
            shot_cell.font = Font(color="0563C1", underline="single")
        else:
            ws.cell(r, 7, "—")
        ws.cell(r, 7).alignment = base_align

        # H: Дата и время
        dt_cell = ws.cell(r, 8, checked_at)
        dt_cell.number_format = "YYYY-MM-DD HH:MM:SS"
        dt_cell.alignment = Alignment(horizontal="center", vertical="center")

        # Применяем border ко всем ячейкам строки
        for col in range(1, len(HEADERS) + 1):
            ws.cell(r, col).border = border

        ws.row_dimensions[r].height = 20
        self._row += 1

    # ── Публичный API ────────────────────────────────────────────────────────

    def write(
        self,
        platform: str,
        url: str,
        price: Optional[float],
        status: str,
        screenshot_path: Optional[str],
        checked_at: Optional[datetime] = None,
    ) -> None:
        """Записывает одну строку результата в таблицу."""
        number = self._row - 1
        when = checked_at or datetime.now()
        self._write_row(number, platform, url, price, status, screenshot_path, when)
        self._unsaved += 1
        logger.debug(f"Строка {number} записана: [{status}] {platform} | {price}")

        # Автосохранение
        if self._unsaved >= EXCEL_AUTOSAVE_EVERY:
            self.save()

    def save(self) -> None:
        """Сохраняет книгу на диск."""
        try:
            self._wb.save(str(self.filepath))
            self._unsaved = 0
            logger.info(f"Excel сохранён: {self.filepath}")
        except PermissionError:
            ts = datetime.now().strftime("%H%M%S")
            alt_path = OUTPUT_DIR / f"prices_report_{datetime.now().strftime('%Y-%m-%d')}_{ts}.xlsx"
            logger.warning(
                f"Файл {self.filepath.name} заблокирован другой программой. "
                f"Сохраняю в {alt_path.name}"
            )
            self.filepath = alt_path
            self._wb.save(str(self.filepath))
            self._unsaved = 0
            logger.info(f"Excel сохранён: {self.filepath}")


    def close(self) -> None:
        """Финальное сохранение и закрытие."""
        self.save()
        logger.success(f"Отчёт завершён: {self.filepath} ({self._row - 2} строк)")
