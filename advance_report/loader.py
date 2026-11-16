"""Чтение и проверка выгрузки из папки data/ (формат - data/FORMAT.md).

Ошибки, при которых считать нельзя (нет колонки, не число, ссылка на
несуществующий договор...), собираются все сразу и выбрасываются одним
DataError - чтобы бухгалтер исправил выгрузку за один проход, а не по одной.
Противоречия, которые можно разрешить по статусу (например, у запланированной
работы заполнена дата приёмки), не останавливают расчёт, а попадают
в Dataset.warnings и показываются в отчёте.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Set, Type, TypeVar

from .dates import format_date, parse_date
from .model import Contract, Dataset, Payment, PaymentStatus, Work, WorkStatus
from .money import parse_money

CONTRACTS_FILE = "contracts.csv"
WORKS_FILE = "works.csv"
PAYMENTS_FILE = "payments.csv"

COLUMNS: Mapping[str, tuple] = {
    CONTRACTS_FILE: ("contract_id", "contractor", "amount", "signed_at"),
    WORKS_FILE: ("work_id", "contract_id", "title", "price", "status", "completed_at", "cancelled_at"),
    PAYMENTS_FILE: (
        "payment_id", "contract_id", "work_id", "purpose", "amount",
        "status", "created_at", "paid_at", "refunded_at",
    ),
}

E = TypeVar("E", bound=Enum)


class DataError(Exception):
    """Выгрузку нельзя посчитать. В problems - все найденные ошибки."""

    def __init__(self, problems: List[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


class _Row:
    """Строка CSV: достаёт поля, ошибки пишет с указанием файла и номера строки."""

    def __init__(self, file_name: str, line: int, values: Mapping, problems: List[str]):
        self.file_name = file_name
        self.line = line
        self._values = values
        self._problems = problems
        self.errors = 0

    @property
    def where(self) -> str:
        return f"{self.file_name}, строка {self.line}"

    def problem(self, message: str) -> None:
        self.errors += 1
        self._problems.append(f"{self.where}: {message}")

    def raw(self, column: str) -> str:
        value = self._values.get(column)
        return value.strip() if isinstance(value, str) else ""

    def text(self, column: str, required: bool = True) -> str:
        value = self.raw(column)
        if required and not value:
            self.problem(f"не заполнено поле {column}")
        return value

    def money(self, column: str) -> int:
        raw = self.text(column)
        if not raw:
            return 0
        try:
            return parse_money(raw)
        except ValueError as exc:
            self.problem(f"{column}: {exc}")
            return 0

    def date(self, column: str, required: bool = True) -> Optional[date]:
        raw = self.text(column, required)
        if not raw:
            return None
        try:
            return parse_date(raw)
        except ValueError as exc:
            self.problem(f"{column}: {exc}")
            return None

    def enum(self, column: str, enum_type: Type[E]) -> Optional[E]:
        raw = self.text(column).lower()
        if not raw:
            return None
        try:
            return enum_type(raw)
        except ValueError:
            allowed = ", ".join(item.value for item in enum_type)
            self.problem(f"{column}: неизвестное значение {raw!r}, допустимо: {allowed}")
            return None


def load_dataset(data_dir: Path) -> Dataset:
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise DataError([f"папка с данными не найдена: {data_dir}"])

    problems: List[str] = []
    warnings: List[str] = []

    contract_rows = _read_rows(data_dir, CONTRACTS_FILE, problems, warnings)
    work_rows = _read_rows(data_dir, WORKS_FILE, problems, warnings)
    payment_rows = _read_rows(data_dir, PAYMENTS_FILE, problems, warnings)
    # Ссылки проверяем по всем номерам из файла, а не только по разобранным без ошибок:
    # иначе одна кривая сумма в договоре дала бы ещё десяток ложных «договор не найден».
    contract_ids = {row.raw("contract_id") for row in contract_rows} - {""}
    work_ids = {row.raw("work_id") for row in work_rows} - {""}

    contracts = _parse_contracts(contract_rows)
    contracts_by_id = {c.contract_id: c for c in contracts}
    works = _parse_works(work_rows, contract_ids, contracts_by_id, warnings)
    works_by_id = {w.work_id: w for w in works}
    payments = _parse_payments(payment_rows, contract_ids, work_ids, contracts_by_id, works_by_id, warnings)

    _check_unique(CONTRACTS_FILE, "contract_id", [c.contract_id for c in contracts], problems)
    _check_unique(WORKS_FILE, "work_id", [w.work_id for w in works], problems)
    _check_unique(PAYMENTS_FILE, "payment_id", [p.payment_id for p in payments], problems)

    if problems:
        raise DataError(problems)
    return Dataset(contracts=contracts, works=works, payments=payments, warnings=warnings)


def _read_rows(
    data_dir: Path, file_name: str, problems: List[str], warnings: List[str]
) -> List[_Row]:
    path = data_dir / file_name
    if not path.is_file():
        problems.append(f"{file_name}: файл не найден в {data_dir}")
        return []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        problems.append(f"{file_name}: не удалось прочитать файл: {exc.strerror or exc}")
        return []
    try:
        # utf-8-sig: Excel любит дописывать BOM в начало файла.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Русский Excel сохраняет CSV в Windows-1251. Суммы и номера от кодировки
        # не зависят, а названия прочитаются правильно - читаем, но предупреждаем.
        text = raw.decode("cp1251", errors="replace")
        warnings.append(f"{file_name}: файл не в UTF-8, прочитан как Windows-1251")

    first_line = text.split("\n", 1)[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
        header = [name.strip() for name in (reader.fieldnames or [])]
        missing = [name for name in COLUMNS[file_name] if name not in header]
        if missing:
            problems.append(f"{file_name}: нет колонок {', '.join(missing)}")
            return []
        reader.fieldnames = header
        rows = []
        for values in reader:
            if not any(isinstance(v, str) and v.strip() for v in values.values()):
                continue  # пустая строка, например в конце файла
            rows.append(_Row(file_name, reader.line_num, values, problems))
        return rows
    except csv.Error as exc:
        problems.append(f"{file_name}: не удалось разобрать CSV: {exc}")
        return []


def _parse_contracts(rows: List[_Row]) -> List[Contract]:
    contracts = []
    for row in rows:
        contract = Contract(
            contract_id=row.text("contract_id"),
            contractor=row.text("contractor"),
            amount=row.money("amount"),
            signed_at=row.date("signed_at"),  # type: ignore[arg-type]
        )
        if not row.errors:
            contracts.append(contract)
    return contracts


def _parse_works(
    rows: List[_Row],
    contract_ids: Set[str],
    contracts: Dict[str, Contract],
    warnings: List[str],
) -> List[Work]:
    works = []
    for row in rows:
        work_id = row.text("work_id")
        contract_id = row.text("contract_id")
        title = row.text("title", required=False)
        price = row.money("price")
        status = row.enum("status", WorkStatus)
        completed_at = row.date("completed_at", required=False)
        cancelled_at = row.date("cancelled_at", required=False)

        if contract_id and contract_id not in contract_ids:
            row.problem(f"договор {contract_id} не найден в {CONTRACTS_FILE}")

        # Статус - последнее событие на момент выгрузки. Вторая дата допустима как
        # более ранняя история (принята 01.10, потом отменена 20.10): её учитываем,
        # чтобы отчёт на 10.10 показал работу принятой. Дата, противоречащая
        # статусу (позже финального события), игнорируется с предупреждением.
        if status is WorkStatus.DONE:
            if completed_at is None:
                row.problem("работа done, но не заполнена дата приёмки completed_at")
            elif cancelled_at is not None:
                if cancelled_at < completed_at:
                    _history(row, work_id, "отменена", cancelled_at, "принята", completed_at, warnings)
                else:
                    cancelled_at = _ignore(row, cancelled_at, "cancelled_at", "работа done", warnings)
        elif status is WorkStatus.CANCELLED:
            if cancelled_at is None:
                row.problem("работа cancelled, но не заполнена дата отмены cancelled_at")
            elif completed_at is not None:
                if completed_at < cancelled_at:
                    _history(row, work_id, "принята", completed_at, "отменена", cancelled_at, warnings)
                else:
                    completed_at = _ignore(row, completed_at, "completed_at", "работа cancelled", warnings)
        elif status is WorkStatus.PLANNED:
            completed_at = _ignore(row, completed_at, "completed_at", "работа planned", warnings)
            cancelled_at = _ignore(row, cancelled_at, "cancelled_at", "работа planned", warnings)

        if row.errors:
            continue
        contract = contracts.get(contract_id)  # None, если строка договора с ошибкой
        event = completed_at or cancelled_at
        if contract and event is not None and event < contract.signed_at:
            warnings.append(
                f"{row.where}: работа {work_id} закрыта {format_date(event)}, "
                f"раньше подписания договора {contract_id} ({format_date(contract.signed_at)})"
            )
        works.append(Work(
            work_id=work_id,
            contract_id=contract_id,
            title=title,
            price=price,
            status=status,  # type: ignore[arg-type]
            completed_at=completed_at,
            cancelled_at=cancelled_at,
        ))
    return works


def _parse_payments(
    rows: List[_Row],
    contract_ids: Set[str],
    work_ids: Set[str],
    contracts: Dict[str, Contract],
    works: Dict[str, Work],
    warnings: List[str],
) -> List[Payment]:
    payments = []
    for row in rows:
        payment_id = row.text("payment_id")
        contract_id = row.text("contract_id")
        work_id = row.text("work_id", required=False) or None
        purpose = row.text("purpose", required=False)
        amount = row.money("amount")
        status = row.enum("status", PaymentStatus)
        created_at = row.date("created_at")
        paid_at = row.date("paid_at", required=False)
        refunded_at = row.date("refunded_at", required=False)

        if contract_id and contract_id not in contract_ids:
            row.problem(f"договор {contract_id} не найден в {CONTRACTS_FILE}")
        if work_id is not None:
            work = works.get(work_id)  # None и для строки работы с ошибкой
            if work_id not in work_ids:
                row.problem(f"работа {work_id} не найдена в {WORKS_FILE}")
            elif work is not None and contract_id and work.contract_id != contract_id:
                row.problem(
                    f"платёж по договору {contract_id} привязан к работе {work_id} "
                    f"из другого договора ({work.contract_id})"
                )

        if status is PaymentStatus.PAID:
            if paid_at is None:
                row.problem("платёж paid, но не заполнена дата оплаты paid_at")
            refunded_at = _ignore(row, refunded_at, "refunded_at", "платёж paid", warnings)
        elif status is PaymentStatus.REFUNDED:
            if paid_at is None:
                row.problem("платёж refunded, но не заполнена дата оплаты paid_at")
            if refunded_at is None:
                row.problem("платёж refunded, но не заполнена дата возврата refunded_at")
            if paid_at and refunded_at and refunded_at < paid_at:
                row.problem("дата возврата refunded_at раньше даты оплаты paid_at")
        elif status is PaymentStatus.QUEUED:
            paid_at = _ignore(row, paid_at, "paid_at", "платёж queued", warnings)
            refunded_at = _ignore(row, refunded_at, "refunded_at", "платёж queued", warnings)

        if row.errors:
            continue
        if paid_at is not None and paid_at < created_at:
            warnings.append(
                f"{row.where}: платёж {payment_id} оплачен {format_date(paid_at)}, "
                f"раньше, чем заведён в учёт ({format_date(created_at)}); считаем по дате оплаты"
            )
        contract = contracts.get(contract_id)
        if contract and paid_at is not None and paid_at < contract.signed_at:
            warnings.append(
                f"{row.where}: платёж {payment_id} оплачен {format_date(paid_at)}, "
                f"раньше подписания договора {contract_id} ({format_date(contract.signed_at)})"
            )
        payments.append(Payment(
            payment_id=payment_id,
            contract_id=contract_id,
            work_id=work_id,
            purpose=purpose,
            amount=amount,
            status=status,  # type: ignore[arg-type]
            created_at=created_at,  # type: ignore[arg-type]
            paid_at=paid_at,
            refunded_at=refunded_at,
        ))
    return payments


def _ignore(
    row: _Row, value: Optional[date], column: str, reason: str, warnings: List[str]
) -> None:
    """Поле заполнено, хотя при таком статусе его быть не должно: верим статусу."""
    if value is not None:
        warnings.append(
            f"{row.where}: {reason}, но заполнено {column} = {format_date(value)}; "
            f"поле не учитывается, верим статусу"
        )
    return None


def _history(
    row: _Row, work_id: str, first: str, first_on: date, then: str, then_on: date,
    warnings: List[str],
) -> None:
    warnings.append(
        f"{row.where}: работа {work_id} {first} {format_date(first_on)}, затем {then} "
        f"{format_date(then_on)}; учитываются обе даты"
    )


def _check_unique(file_name: str, column: str, ids: List[str], problems: List[str]) -> None:
    for value, count in Counter(ids).items():
        if count > 1:
            problems.append(f"{file_name}: {column} {value} повторяется (строк с ним: {count})")
