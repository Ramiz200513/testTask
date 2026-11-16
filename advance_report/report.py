"""Сборка отчёта на дату: строки по работам, итоги по договорам, замечания."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List

from .allocation import AllocationResult, Applied, PaymentInput, Strategy, WorkInput, allocate
from .asof import (
    PaymentStage,
    PaymentState,
    WorkStage,
    WorkState,
    payment_state_on,
    work_state_on,
)
from .dates import format_date
from .model import Contract, Dataset, Payment, Work
from .money import format_money


@dataclass(frozen=True)
class WorkRow:
    work: Work
    state: WorkState
    due: int     # к оплате: цена, у отменённой на дату работы - 0
    direct: int  # выплачено платежами, привязанными к этой работе
    pooled: int  # выплачено из платежей по договору без привязки

    @property
    def paid(self) -> int:
        return self.direct + self.pooled

    @property
    def remaining(self) -> int:
        return self.due - self.paid


@dataclass(frozen=True)
class PaymentRow:
    payment: Payment
    state: PaymentState
    applied: Applied  # куда зачтены деньги: [(work_id | None = переплата, сумма)]


@dataclass(frozen=True)
class ContractTotals:
    works_price: int         # цены всех работ договора
    cancelled: int           # отменено к дате отчёта
    due: int                 # к оплате: работы, не отменённые на дату
    accepted: int            # стоимость работ, принятых на дату
    paid: int                # выплачено на дату - все учтённые платежи
    remaining: int           # осталось оплатить по работам
    remaining_accepted: int  #   в т.ч. за уже принятые работы
    remaining_open: int      #   в т.ч. за ещё не принятые
    advance: int             # выплачено сверх стоимости принятых работ
    overpaid: int            # выплачено сверх стоимости всех работ в силе
    queued: int              # в очереди на оплату на дату
    refunded: int            # возвращено подрядчиком к дате


@dataclass(frozen=True)
class ContractReport:
    contract: Contract
    works: List[WorkRow]
    payments: List[PaymentRow]
    totals: ContractTotals
    notes: List[str]


@dataclass(frozen=True)
class Report:
    report_date: date
    strategy: Strategy
    contracts: List[ContractReport]
    skipped: List[str]        # договоры, не попавшие в отчёт, с причиной
    data_warnings: List[str]  # замечания к выгрузке


def build_report(dataset: Dataset, on: date, strategy: Strategy) -> Report:
    contracts: List[ContractReport] = []
    skipped: List[str] = []
    for contract in dataset.contracts:
        works = [w for w in dataset.works if w.contract_id == contract.contract_id]
        payments = [p for p in dataset.payments if p.contract_id == contract.contract_id]
        if contract.signed_at > on:
            # Договор подписан позже даты отчёта. Пропускаем его, только если по нему
            # к этой дате ещё ничего не было, - иначе пропали бы реальные деньги.
            early = [p for p in payments if payment_state_on(p, on).stage is not PaymentStage.NOT_YET]
            if not early:
                skipped.append(
                    f"Договор {contract.contract_id} ({contract.contractor}) подписан "
                    f"{format_date(contract.signed_at)} - после даты отчёта, в отчёт не включён."
                )
                continue
        contracts.append(_contract_report(contract, works, payments, on, strategy))
    return Report(on, strategy, contracts, skipped, list(dataset.warnings))


def _contract_report(
    contract: Contract,
    works: List[Work],
    payments: List[Payment],
    on: date,
    strategy: Strategy,
) -> ContractReport:
    work_states = {w.work_id: work_state_on(w, on) for w in works}
    payment_states = {p.payment_id: payment_state_on(p, on) for p in payments}

    def due_of(work: Work) -> int:
        return 0 if work_states[work.work_id].stage is WorkStage.CANCELLED else work.price

    result = allocate(
        [
            WorkInput(w.work_id, work_states[w.work_id].stage, work_states[w.work_id].since, due_of(w))
            for w in works
        ],
        [
            PaymentInput(p.payment_id, p.work_id, payment_states[p.payment_id].counted, p.paid_at)
            for p in payments
        ],
        strategy,
    )

    work_rows = [
        WorkRow(w, work_states[w.work_id], due_of(w), result.direct[w.work_id], result.pooled[w.work_id])
        for w in works
    ]
    payment_rows = [
        PaymentRow(p, payment_states[p.payment_id], result.applied[p.payment_id]) for p in payments
    ]
    totals = _totals(work_rows, payment_rows, result)
    _check_invariants(contract, work_rows, totals)
    notes = _notes(contract, work_rows, payment_rows, totals, result, on)
    return ContractReport(contract, work_rows, payment_rows, totals, notes)


def _totals(
    works: List[WorkRow], payments: List[PaymentRow], result: AllocationResult
) -> ContractTotals:
    def total(rows, value) -> int:
        return sum(value(r) for r in rows)

    accepted_rows = [r for r in works if r.state.stage is WorkStage.ACCEPTED]
    open_rows = [r for r in works if r.state.stage is WorkStage.OPEN]
    accepted = total(accepted_rows, lambda r: r.due)
    paid = total(payments, lambda r: r.state.counted)
    return ContractTotals(
        works_price=total(works, lambda r: r.work.price),
        cancelled=total(
            [r for r in works if r.state.stage is WorkStage.CANCELLED], lambda r: r.work.price
        ),
        due=total(works, lambda r: r.due),
        accepted=accepted,
        paid=paid,
        remaining=total(works, lambda r: r.remaining),
        remaining_accepted=total(accepted_rows, lambda r: r.remaining),
        remaining_open=total(open_rows, lambda r: r.remaining),
        advance=max(0, paid - accepted),
        overpaid=result.overpaid,
        queued=total(
            [r for r in payments if r.state.stage is PaymentStage.QUEUED], lambda r: r.payment.amount
        ),
        refunded=total(
            [r for r in payments if r.state.stage is PaymentStage.REFUNDED], lambda r: r.payment.amount
        ),
    )


def _check_invariants(contract: Contract, works: List[WorkRow], t: ContractTotals) -> None:
    """Сверка «до копейки»: если не сходится - это ошибка программы, а не данных."""
    checks = [
        (all(0 <= r.paid <= r.due for r in works), "выплачено по работе вне [0, к оплате]"),
        (sum(r.paid for r in works) + t.overpaid == t.paid, "по работам + переплата != выплачено"),
        (t.due - sum(r.paid for r in works) == t.remaining, "к оплате - выплачено != осталось"),
        (t.remaining_accepted + t.remaining_open == t.remaining, "остаток не делится на части"),
        (t.works_price - t.cancelled == t.due, "цена работ - отменено != к оплате"),
        (t.overpaid == 0 or t.remaining == 0, "переплата при неоплаченных работах"),
    ]
    for ok, message in checks:
        if not ok:
            raise RuntimeError(f"внутренняя ошибка расчёта по договору {contract.contract_id}: {message}")


def _notes(
    contract: Contract,
    works: List[WorkRow],
    payments: List[PaymentRow],
    t: ContractTotals,
    result: AllocationResult,
    on: date,
) -> List[str]:
    """Что бухгалтеру стоит проверить: события после даты, расхождения, переплаты."""
    notes: List[str] = []
    money = format_money
    titles: Dict[str, str] = {r.work.work_id: r.work.title for r in works}

    if contract.signed_at > on:
        notes.append(
            f"Договор подписан {format_date(contract.signed_at)} - после даты отчёта, но платежи "
            f"по нему к этой дате уже были, поэтому он в отчёте."
        )

    if contract.amount != t.works_price:
        notes.append(
            f"Сумма договора {money(contract.amount)} не совпадает с суммой цен его работ "
            f"{money(t.works_price)}. «Осталось оплатить» считается по работам."
        )
    if t.cancelled:
        notes.append(
            f"Отменены работы на {money(t.cancelled)}: они не входят в «к оплате» и «осталось оплатить»."
        )

    state_on_date = {
        WorkStage.OPEN: "ещё не принята и не отменена",
        WorkStage.ACCEPTED: "принята",
        WorkStage.CANCELLED: "отменена",
    }
    for r in works:
        if r.state.later:
            now = state_on_date[r.state.stage]
            notes.append(
                f"{r.work.work_id} «{r.work.title}»: в выгрузке {r.state.later} - уже после "
                f"даты отчёта, поэтому на {format_date(on)} работа {now}."
            )
    for r in payments:
        p, s = r.payment, r.state
        if s.stage is PaymentStage.NOT_YET:
            notes.append(
                f"{p.payment_id} ({money(p.amount)}, «{p.purpose}»): {s.later} - после даты "
                f"отчёта, не учтён."
            )
        elif s.stage is PaymentStage.QUEUED and s.later:
            notes.append(
                f"{p.payment_id} ({money(p.amount)}): на дату отчёта был в очереди, "
                f"{s.later} - уже после неё."
            )
        elif s.stage is PaymentStage.PAID and s.later:
            notes.append(
                f"{p.payment_id} ({money(p.amount)}): {s.later} - после даты отчёта, "
                f"поэтому на дату считается выплаченным."
            )

    for payment_id, work_id, amount in result.redirected:
        row = next(r for r in works if r.work.work_id == work_id)
        reason = (
            "работа отменена"
            if row.state.stage is WorkStage.CANCELLED
            else "платёж больше неоплаченной части работы"
        )
        notes.append(
            f"{payment_id} привязан к {work_id} «{titles[work_id]}», но {reason}: "
            f"{money(amount)} зачтено как платёж по договору без привязки."
        )

    if t.overpaid:
        notes.append(
            f"Переплата: выплачено {money(t.paid)} при стоимости работ в силе {money(t.due)}, "
            f"лишние {money(t.overpaid)} не относятся ни к одной работе."
        )
    if t.paid > contract.amount and contract.amount != t.due:  # иначе это та же переплата
        notes.append(
            f"Выплачено больше суммы договора ({money(contract.amount)}) на {money(t.paid - contract.amount)}."
        )
    if t.queued:
        after = t.paid + t.queued
        notes.append(
            f"В очереди на оплату {money(t.queued)} (в «выплачено» не входит). Если оплатить, "
            f"будет выплачено {money(after)} при стоимости принятых работ {money(t.accepted)} "
            f"и всех работ в силе {money(t.due)}."
            + (f" Это больше стоимости работ в силе на {money(after - t.due)}." if after > t.due else "")
        )
    return notes
