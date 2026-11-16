"""Вывод отчёта: таблица в консоль, CSV для Excel, JSON для машин."""

from __future__ import annotations

import csv
import io
import json
import re
import textwrap
from typing import Callable, Dict, List, Optional, Sequence, Union

from .allocation import Strategy
from .asof import PaymentStage, PaymentState, WorkStage, WorkState
from .dates import format_date
from .money import format_money
from .report import ContractReport, PaymentRow, Report

STRATEGY_TEXT = {
    Strategy.FIFO: (
        "сначала оплачивают принятые работы в порядке приёмки (раньше принята - раньше "
        "оплачена), остаток ложится авансом на ещё не принятые работы пропорционально цене"
    ),
    Strategy.PRO_RATA: (
        "делятся между всеми действующими работами пропорционально цене, "
        "независимо от того, приняты они или нет"
    ),
}

WORK_STAGE_TEXT = {
    WorkStage.ACCEPTED: "принята",
    WorkStage.OPEN: "не принята",
    WorkStage.CANCELLED: "отменена",
}

PAYMENT_STAGE_TEXT = {
    PaymentStage.PAID: "оплачен",
    PaymentStage.REFUNDED: "возвращён",
    PaymentStage.QUEUED: "в очереди",
    PaymentStage.NOT_YET: "ещё не заведён",
}


def work_state_text(state: WorkState) -> str:
    text = WORK_STAGE_TEXT[state.stage]
    return f"{text} {format_date(state.since)}" if state.since else text


def payment_state_text(state: PaymentState) -> str:
    text = PAYMENT_STAGE_TEXT[state.stage]
    if state.since is None:
        return text
    preposition = "с " if state.stage is PaymentStage.QUEUED else ""
    return f"{text} {preposition}{format_date(state.since)}"


# --------------------------------------------------------------------------- таблица

Cell = Union[str, List[str]]
WIDTH = 100


def render_table(report: Report) -> str:
    on = format_date(report.report_date)
    out: List[str] = [
        f"ОТЧЁТ ПО РАСЧЁТАМ С ПОДРЯДЧИКАМИ НА {on}",
        f"Учтено всё, что произошло по {on} включительно.",
        *_wrap(
            f"Платежи без привязки к работе (режим {report.strategy.value}): "
            f"{STRATEGY_TEXT[report.strategy]}."
        ),
        "",
    ]
    if report.contracts:
        out += ["СВОДКА ПО ДОГОВОРАМ", *_summary_table(report), ""]
    else:
        out += ["На эту дату нет ни одного подписанного договора.", ""]

    for contract_report in report.contracts:
        out += _contract_section(contract_report, on)

    if report.skipped:
        out += ["=" * WIDTH, "НЕ ВОШЛО В ОТЧЁТ"]
        out += [line for item in report.skipped for line in _wrap(item, bullet=True)]
        out.append("")
    if report.data_warnings:
        out += ["=" * WIDTH, "ЗАМЕЧАНИЯ К ВЫГРУЗКЕ"]
        out += [line for item in report.data_warnings for line in _wrap(item, bullet=True)]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _summary_table(report: Report) -> List[str]:
    rows: List[Sequence[Cell]] = []
    sums = dict(due=0, paid=0, remaining=0, overpaid=0, queued=0)
    for cr in report.contracts:
        t = cr.totals
        rows.append([
            cr.contract.contract_id, cr.contract.contractor,
            format_money(t.due), format_money(t.paid), format_money(t.remaining),
            format_money(t.overpaid), format_money(t.queued),
        ])
        for key in sums:
            sums[key] += getattr(t, key)
    rows.append(["Итого", ""] + [format_money(sums[k]) for k in sums])
    return _table(
        ["Договор", "Подрядчик", "К оплате", "Выплачено", "Осталось оплатить", "Переплата", "В очереди"],
        rows,
        "llrrrrr",
        total_row=True,
    )


def _contract_section(cr: ContractReport, on: str) -> List[str]:
    c, t = cr.contract, cr.totals
    out = [
        "=" * WIDTH,
        f"ДОГОВОР {c.contract_id} - {c.contractor}",
        f"Подписан {format_date(c.signed_at)}, сумма договора {format_money(c.amount)}",
        "",
        "Работы",
    ]
    rows: List[Sequence[Cell]] = [
        [
            r.work.work_id, r.work.title, work_state_text(r.state),
            format_money(r.work.price), format_money(r.paid),
            format_money(r.pooled), format_money(r.remaining),
        ]
        for r in cr.works
    ]
    rows.append([
        "Итого", "", "",
        format_money(t.works_price), format_money(sum(r.paid for r in cr.works)),
        format_money(sum(r.pooled for r in cr.works)), format_money(t.remaining),
    ])
    out += _table(
        ["Работа", "Название", f"На {on}", "Цена", "Выплачено", "из них без привязки", "Осталось оплатить"],
        rows,
        "lllrrrr",
        total_row=True,
    )
    out += _wrap(
        "«Выплачено» = платежи, привязанные к этой работе, + доля платежей по договору "
        "без привязки (она же - в колонке «из них без привязки»)."
    )

    out += ["", f"Итоги по договору {c.contract_id}"]
    lines = [
        ("Цена всех работ", t.works_price),
        ("Отменено работ", t.cancelled),
        ("К оплате (работы в силе)", t.due),
        ("Принято работ на дату", t.accepted),
        ("Выплачено на дату", t.paid),
        ("Осталось оплатить", t.remaining),
        ("  за принятые работы", t.remaining_accepted),
        ("  за ещё не принятые работы", t.remaining_open),
        ("Выплачено сверх стоимости принятых работ (аванс)", t.advance),
        ("Переплата сверх стоимости работ в силе", t.overpaid),
        ("В очереди на оплату (не выплачено)", t.queued),
        ("Возвращено подрядчиком", t.refunded),
    ]
    label_width = max(len(label) for label, _ in lines)
    amount_width = max(len(format_money(value)) for _, value in lines)
    out += [f"  {label:<{label_width}}  {format_money(value):>{amount_width}}" for label, value in lines]

    out += ["", f"Платежи по договору {c.contract_id}"]
    if cr.payments:
        out += _table(
            ["Платёж", "Назначение", "Сумма", f"На {on}", "Зачтено за"],
            [_payment_cells(p) for p in cr.payments],
            "llrll",
        )
    else:
        out.append("  платежей нет")

    if cr.notes:
        out += ["", "Обратите внимание"]
        out += [line for note in cr.notes for line in _wrap(note, bullet=True)]
    out.append("")
    return out


def _payment_cells(row: PaymentRow) -> List[Cell]:
    p = row.payment
    applied: List[str] = []
    for work_id, amount in row.applied:
        target = work_id or "переплата"
        suffix = " (привязан)" if work_id is not None and work_id == p.work_id else ""
        applied.append(f"{target:<10}{format_money(amount):>12}{suffix}")
    return [
        p.payment_id,
        p.purpose,
        format_money(p.amount),
        payment_state_text(row.state),
        applied or ["-"],
    ]


def _table(headers: Sequence[str], rows: Sequence[Sequence[Cell]], align: str,
           total_row: bool = False) -> List[str]:
    # Перенос строки внутри названия (в CSV это допустимо) сломал бы колонки.
    cells = [
        [[_one_line(line) for line in (c if isinstance(c, list) else [c])] for c in row]
        for row in rows
    ]
    widths = [
        max([len(headers[i])] + [len(line) for row in cells for line in row[i]])
        for i in range(len(headers))
    ]

    def line(values: Sequence[str]) -> str:
        parts = [
            v.rjust(w) if a == "r" else v.ljust(w)
            for v, w, a in zip(values, widths, align)
        ]
        return ("  " + "  ".join(parts)).rstrip()

    rule = "  " + "-" * (sum(widths) + 2 * (len(widths) - 1))
    out = [line(headers), rule]
    for index, row in enumerate(cells):
        if total_row and index == len(cells) - 1:
            out.append(rule)
        for k in range(max(len(cell) for cell in row)):
            out.append(line([cell[k] if k < len(cell) else "" for cell in row]))
    return out


def _one_line(text: str) -> str:
    return re.sub(r"\s*[\r\n]+\s*", " ", text)


_DIGIT_GROUP = re.compile(r"(?<=\d) (?=\d{3}\b)")


def _wrap(text: str, bullet: bool = False) -> List[str]:
    # Пробелы между разрядами («465 000,00») на время переноса делаем неразрывными,
    # чтобы сумма не разъехалась на две строки.
    glued = _DIGIT_GROUP.sub(" ", text)
    indents = dict(initial_indent="  - ", subsequent_indent="    ") if bullet else {}
    return [line.replace(" ", " ") for line in textwrap.wrap(glued, WIDTH, **indents)]


# --------------------------------------------------------------------------- CSV

CSV_HEADERS = [
    "Дата отчёта", "Договор", "Подрядчик", "Работа", "Название",
    "Состояние на дату", "Дата приёмки/отмены", "Цена", "К оплате", "Выплачено",
    "в т.ч. по привязке к работе", "в т.ч. из платежей без привязки",
    "Осталось оплатить", "Переплата", "В очереди на оплату",
]


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _text(value: str) -> str:
    """Название вроде «=1+1» или «-15% скидка» Excel принял бы за формулу.
    Апостроф в начале делает ячейку текстом (рекомендация OWASP для CSV)."""
    return "'" + value if value.startswith(_FORMULA_START) else value


def render_csv(report: Report) -> str:
    """CSV для Excel с русскими настройками: разделитель «;», копейки через запятую."""

    def money(value: Optional[int]) -> str:
        return "" if value is None else format_money(value, group_sep="")

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    on = format_date(report.report_date)
    for cr in report.contracts:
        c, t = cr.contract, cr.totals
        for r in cr.works:
            writer.writerow([
                on, _text(c.contract_id), _text(c.contractor), _text(r.work.work_id),
                _text(r.work.title), WORK_STAGE_TEXT[r.state.stage],
                format_date(r.state.since) if r.state.since else "",
                money(r.work.price), money(r.due), money(r.paid),
                money(r.direct), money(r.pooled), money(r.remaining), "", "",
            ])
        writer.writerow([
            on, _text(c.contract_id), _text(c.contractor), "ИТОГО по договору", "", "", "",
            money(t.works_price), money(t.due), money(t.paid),
            money(sum(r.direct for r in cr.works)), money(sum(r.pooled for r in cr.works)),
            money(t.remaining), money(t.overpaid), money(t.queued),
        ])
    return buffer.getvalue()


# --------------------------------------------------------------------------- JSON

def render_json(report: Report) -> str:
    """Суммы - строками "100000.00": точно и без float на стороне читателя."""

    def money(value: int) -> str:
        return format_money(value, decimal_sep=".", group_sep="")

    def iso(value) -> Optional[str]:
        return value.isoformat() if value else None

    contracts = []
    for cr in report.contracts:
        c, t = cr.contract, cr.totals
        contracts.append({
            "contract_id": c.contract_id,
            "contractor": c.contractor,
            "signed_at": iso(c.signed_at),
            "amount": money(c.amount),
            "works": [
                {
                    "work_id": r.work.work_id,
                    "title": r.work.title,
                    "state": r.state.stage.value,
                    "state_date": iso(r.state.since),
                    "after_report_date": r.state.later,
                    "price": money(r.work.price),
                    "due": money(r.due),
                    "paid": money(r.paid),
                    "paid_direct": money(r.direct),
                    "paid_from_contract": money(r.pooled),
                    "remaining": money(r.remaining),
                }
                for r in cr.works
            ],
            "totals": {key: money(value) for key, value in vars(t).items()},
            "payments": [
                {
                    "payment_id": r.payment.payment_id,
                    "work_id": r.payment.work_id,
                    "purpose": r.payment.purpose,
                    "amount": money(r.payment.amount),
                    "state": r.state.stage.value,
                    "state_date": iso(r.state.since),
                    "after_report_date": r.state.later,
                    "counted": money(r.state.counted),
                    "applied": [
                        {"work_id": work_id, "amount": money(amount)} for work_id, amount in r.applied
                    ],
                }
                for r in cr.payments
            ],
            "notes": cr.notes,
        })
    document = {
        "report_date": report.report_date.isoformat(),
        "allocation": report.strategy.value,
        "contracts": contracts,
        "skipped": report.skipped,
        "data_warnings": report.data_warnings,
    }
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


RENDERERS: Dict[str, Callable[[Report], str]] = {
    "table": render_table,
    "csv": render_csv,
    "json": render_json,
}
