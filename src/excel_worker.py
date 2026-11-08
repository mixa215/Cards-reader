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
    "Тип цены",
    "Базовая цена",
    "Статус",
    "Скриншот",
    "Путь к дампу HTML",
    "Дата и время проверки",
]

# ─── Ширина столбцов (символы) ────────────────────────────────────────────────
COL_WIDTHS = [5, 18, 55, 16, 10, 26, 16, 18, 30, 40, 24]

# Человекочитаемые имена маркетплейсов
PLATFORM_DISPLAY = {
    "ozon": "Ozon",
    "wildberries": "Wildberries",
    "yandex_market": "Yandex Market",
    "avito": "Avito",
    "unknown": "—",
}


class ExcelWriter:
    """Потоковая запись результатов в xlsx с режимом дозаписи (append) и автосохранением."""

    def __init__(self, filepath: Optional[Path] = None):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        if filepath is not None:
            self.filepath = Path(filepath)
        else:
            date_str = datetime.now().strftime("%Y-%m-%d")
            self.filepath = OUTPUT_DIR / f"prices_report_{date_str}.xlsx"

        self._unsaved = 0
        self._last_number = 0

        # Если файл уже существует — открываем для дозаписи (Append mode)
        if self.filepath.exists():
            try:
                self._wb = openpyxl.load_workbook(str(self.filepath))
                self._ws = self._wb["Цены"] if "Цены" in self._wb.sheetnames else self._wb.active
                self._row = self._ws.max_row + 1

                # Находим последний порядковый номер из первой колонки
                for r in range(self._ws.max_row, 1, -1):
                    val = self._ws.cell(r, 1).value
                    if isinstance(val, int):
                        self._last_number = val
                        break
                    elif isinstance(val, str) and val.isdigit():
                        self._last_number = int(val)
                        break

                logger.info(
                    f"Excel отчёт открыт в режиме дозаписи (append) со строки {self._row} "
                    f"(последний №={self._last_number}): {self.filepath}"
                )
            except Exception as e:
                logger.warning(f"Не удалось открыть существующий файл {self.filepath} ({e}). Создаю новый.")
                self._wb = openpyxl.Workbook()
                self._ws = self._wb.active
                self._ws.title = "Цены"
                self._setup_sheet()
                self._row = 2
                self._last_number = 0
        else:
            # Создаем новый файл с шапкой
            self._wb = openpyxl.Workbook()
            self._ws = self._wb.active
            self._ws.title = "Цены"
            self._setup_sheet()
            self._row = 2
            self._last_number = 0
            logger.info(f"Создан новый Excel отчёт: {self.filepath}")

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
        price_type: str = "Обычная",
        base_price: Optional[float] = None,
        dump_path: Optional[str] = None,
    ) -> None:
        ws = self._ws
        r = self._row
        border = self._cell_border()

        status_color = STATUS_COLORS.get(status, "FFFFFF")
        status_fill = PatternFill("solid", fgColor=status_color)
        base_align = Alignment(vertical="center")

        # 1: №
        ws.cell(r, 1, number).alignment = Alignment(horizontal="center", vertical="center")

        # 2: Маркетплейс
        ws.cell(r, 2, PLATFORM_DISPLAY.get(platform, platform)).alignment = base_align

        # 3: URL (гиперссылка)
        url_cell = ws.cell(r, 3, url)
        url_cell.hyperlink = url
        url_cell.font = Font(color="0563C1", underline="single")
        url_cell.alignment = Alignment(vertical="center", wrap_text=False)

        # 4: Цена
        price_cell = ws.cell(r, 4, price if price is not None else "")
        if price is not None:
            price_cell.number_format = "#,##0.00"
        price_cell.alignment = Alignment(horizontal="right", vertical="center")

        # 5: Валюта
        ws.cell(r, 5, "RUB").alignment = Alignment(horizontal="center", vertical="center")

        # 6: Тип цены (Персональная / Обычная)
        type_cell = ws.cell(r, 6, price_type or "Обычная")
        type_cell.alignment = Alignment(horizontal="center", vertical="center")
        if "персональная" in (price_type or "").lower():
            type_cell.font = Font(bold=True, color="006100")
            type_cell.fill = PatternFill("solid", fgColor="C6EFCE")

        # 7: Базовая цена (без скидки)
        base_cell = ws.cell(r, 7, base_price if base_price is not None else "")
        if base_price is not None:
            base_cell.number_format = "#,##0.00"
        base_cell.alignment = Alignment(horizontal="right", vertical="center")

        # 8: Статус
        status_cell = ws.cell(r, 8, status)
        status_cell.fill = status_fill
        status_cell.alignment = Alignment(horizontal="center", vertical="center")
        status_cell.font = Font(bold=True)

        # 9: Скриншот (гиперссылка на файл)
        if screenshot_path and Path(screenshot_path).exists():
            shot_cell = ws.cell(r, 9, Path(screenshot_path).name)
            shot_cell.hyperlink = Path(screenshot_path).as_uri()
            shot_cell.font = Font(color="0563C1", underline="single")
        else:
            ws.cell(r, 9, "—")
        ws.cell(r, 9).alignment = base_align

        # 10: Путь к дампу HTML (гиперссылка на сохраненную веб-страницу)
        if dump_path and Path(dump_path).exists():
            dump_cell = ws.cell(r, 10, Path(dump_path).name)
            dump_cell.hyperlink = Path(dump_path).as_uri()
            dump_cell.font = Font(color="0563C1", underline="single")
        else:
            ws.cell(r, 10, "—")
        ws.cell(r, 10).alignment = base_align

        # 11: Дата и время проверки
        dt_cell = ws.cell(r, 11, checked_at)
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
        screenshot_path: Optional[str] = None,
        checked_at: Optional[datetime] = None,
        price_type: str = "Обычная",
        base_price: Optional[float] = None,
        dump_path: Optional[str] = None,
    ) -> None:
        """Записывает одну строку результата в таблицу с фиксацией точной метки времени."""
        self._last_number += 1
        number = self._last_number
        when = checked_at or datetime.now()
        self._write_row(
            number,
            platform,
            url,
            price,
            status,
            screenshot_path,
            when,
            price_type=price_type,
            base_price=base_price,
            dump_path=dump_path,
        )
        self._unsaved += 1
        logger.debug(
            f"Строка {number} записана: [{status}] {platform} | {price} ({price_type}) "
            f"({when.strftime('%Y-%m-%d %H:%M:%S')})"
        )

        # Автосохранение
        if self._unsaved >= EXCEL_AUTOSAVE_EVERY:
            self.save()

    def save(self) -> None:
        """Сохраняет книгу на диск с обработкой PermissionError при открытом файле."""
        import time

        for attempt in range(2):
            try:
                self._wb.save(str(self.filepath))
                self._unsaved = 0
                logger.info(f"Excel сохранён: {self.filepath}")
                return
            except PermissionError:
                if attempt == 0:
                    logger.warning(
                        f"Файл {self.filepath.name} заблокирован (открыт в Excel). "
                        f"Повторная попытка сохранения через 1 сек..."
                    )
                    time.sleep(1.0)
                else:
                    break

        # Если файл так и не освободился пользователем:
        # Сохраняем во временный файл, чтобы не потерять спарсенные данные
        alt_path = self.filepath.parent / "prices_report_temp.xlsx"
        try:
            logger.warning(
                f"Файл {self.filepath.name} по-прежнему заблокирован в Excel. "
                f"Сохраняю во временный файл: {alt_path.name}"
            )
            self._wb.save(str(alt_path))
            self._unsaved = 0
            logger.success(f"✓ Данные сохранены во временный файл: {alt_path}")
        except Exception as e:
            logger.error(f"Не удалось сохранить во временный файл {alt_path}: {e}")


    def close(self) -> None:
        """Финальное сохранение и закрытие."""
        self.save()
        logger.success(f"Отчёт завершён: {self.filepath} ({self._row - 2} строк)")
