"""Даты: в выгрузке ISO (2026-11-10), бухгалтер пишет 10.11.2026 - принимаем оба."""

from __future__ import annotations

from datetime import date, datetime

_FORMATS = ("%Y-%m-%d", "%d.%m.%Y")


def parse_date(text: str) -> date:
    value = text.strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"не дата: {text!r} (ожидается 2026-11-10 или 10.11.2026)")


def format_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")
