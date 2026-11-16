"""Общие помощники для тестов."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict

from advance_report.loader import COLUMNS, CONTRACTS_FILE, PAYMENTS_FILE, WORKS_FILE
from advance_report.report import ContractReport, Report

SAMPLE_DATA = Path(__file__).resolve().parent.parent / "data"

HEADERS = {name: ",".join(columns) for name, columns in COLUMNS.items()}


def rub(text: str) -> int:
    """'7 500,00' / '7500' -> копейки, чтобы ожидания в тестах читались как в отчёте."""
    cleaned = text.replace(" ", "").replace(",", ".")
    rubles, _, kopecks = cleaned.partition(".")
    return int(rubles) * 100 + int(kopecks.ljust(2, "0"))


def write_data(contracts: str, works: str = "", payments: str = "",
               encoding: str = "utf-8") -> Path:
    """Создаёт временную папку data с тремя CSV. Строки - без заголовков."""
    folder = Path(tempfile.mkdtemp(prefix="advance_report_test_"))
    for name, body in (
        (CONTRACTS_FILE, contracts), (WORKS_FILE, works), (PAYMENTS_FILE, payments),
    ):
        text = HEADERS[name] + "\n" + body.strip("\n") + ("\n" if body.strip() else "")
        (folder / name).write_text(text, encoding=encoding)
    return folder


def contract(report: Report, contract_id: str) -> ContractReport:
    return next(c for c in report.contracts if c.contract.contract_id == contract_id)


def work_amounts(cr: ContractReport) -> Dict[str, tuple]:
    """{work_id: (к оплате, выплачено, из них без привязки, осталось)}"""
    return {r.work.work_id: (r.due, r.paid, r.pooled, r.remaining) for r in cr.works}


def applied(cr: ContractReport) -> Dict[str, list]:
    return {r.payment.payment_id: r.applied for r in cr.payments}
