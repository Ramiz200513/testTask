"""Командная строка: python -m advance_report --date 10.11.2026"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .allocation import Strategy
from .dates import parse_date
from .loader import DataError, load_dataset
from .render import RENDERERS
from .report import build_report


def _date_arg(text: str) -> date:
    try:
        return parse_date(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m advance_report",
        description=(
            "Отчёт на дату: по каждой работе - цена, сколько выплачено и сколько осталось "
            "оплатить; итоги по каждому договору."
        ),
    )
    parser.add_argument(
        "--date", required=True, type=_date_arg,
        help="дата отчёта, включительно: 10.11.2026 или 2026-11-10",
    )
    parser.add_argument(
        "--data", type=Path, default=Path("data"),
        help="папка с contracts.csv, works.csv, payments.csv (по умолчанию ./data)",
    )
    parser.add_argument(
        "--format", choices=sorted(RENDERERS), default="table",
        help="table - таблица для чтения (по умолчанию), csv - для Excel, json - для программ",
    )
    parser.add_argument(
        "--output", "-o", type=Path,
        help="записать отчёт в файл (UTF-8; для CSV - с BOM, чтобы Excel понял кириллицу)",
    )
    parser.add_argument(
        "--allocation", choices=[s.value for s in Strategy], default=Strategy.FIFO.value,
        help=(
            "как раскладывать платежи без привязки к работе: fifo (по умолчанию) - сначала "
            "принятые работы по дате приёмки, остаток авансом на непринятые; pro-rata - "
            "на все действующие работы пропорционально цене"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        dataset = load_dataset(args.data)
    except DataError as exc:
        print("Отчёт не построен: в данных есть ошибки.", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    report = build_report(dataset, args.date, Strategy(args.allocation))
    text = RENDERERS[args.format](report)

    if args.format != "table":
        # В таблице это печатается внутри отчёта; в CSV места нет - пишем в stderr.
        for title, items in (("Не вошло в отчёт:", report.skipped),
                             ("Замечания к выгрузке:", report.data_warnings)):
            if items:
                print(title, file=sys.stderr)
                for item in items:
                    print(f"  - {item}", file=sys.stderr)

    if args.output:
        encoding = "utf-8-sig" if args.format == "csv" else "utf-8"
        newline = "\r\n" if args.format == "csv" else "\n"
        try:
            with open(args.output, "w", encoding=encoding, newline=newline) as handle:
                handle.write(text)
        except OSError as exc:
            print(f"Не удалось записать отчёт в {args.output}: {exc.strerror or exc}", file=sys.stderr)
            return 1
        print(f"Отчёт записан в {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0
