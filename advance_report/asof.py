"""Состояние работ и платежей на дату отчёта.

Поле status в выгрузке - статус на момент выгрузки, а не на дату отчёта.
Поэтому состояние на дату восстанавливается по датам событий:
работа принята, если completed_at <= дата отчёта; платёж выплачен, если
paid_at <= дата отчёта и он не возвращён к этой дате; и т.д.
Дата отчёта включается целиком: «на 10.11» = на конец дня 10.11.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

from .dates import format_date
from .model import Payment, Work


class WorkStage(Enum):
    OPEN = "open"            # на дату ещё не принята и не отменена
    ACCEPTED = "accepted"    # принята на дату или раньше
    CANCELLED = "cancelled"  # отменена на дату или раньше


@dataclass(frozen=True)
class WorkState:
    stage: WorkStage
    since: Optional[date] = None  # дата приёмки или отмены
    later: Optional[str] = None   # что случилось с работой уже после даты отчёта


_EVENT_TEXT = {WorkStage.ACCEPTED: "принята", WorkStage.CANCELLED: "отменена"}


def work_state_on(work: Work, on: date) -> WorkState:
    """Состояние на дату = последнее событие (приёмка или отмена) не позже этой даты.

    Обычно у работы одно событие. Загрузчик оставляет обе даты, только если они
    складываются в историю, согласную со статусом выгрузки (принята, потом отменена).
    """
    events = sorted(
        (when, stage)
        for when, stage in ((work.completed_at, WorkStage.ACCEPTED), (work.cancelled_at, WorkStage.CANCELLED))
        if when is not None
    )
    past = [(when, stage) for when, stage in events if when <= on]
    later = ", ".join(
        f"{_EVENT_TEXT[stage]} {format_date(when)}" for when, stage in events if when > on
    ) or None
    if past:
        when, stage = past[-1]
        return WorkState(stage, when, later)
    return WorkState(WorkStage.OPEN, None, later)


class PaymentStage(Enum):
    NOT_YET = "not_yet"    # заведён в учёт уже после даты отчёта
    QUEUED = "queued"      # заведён, но деньги на дату ещё не ушли
    PAID = "paid"          # деньги у подрядчика
    REFUNDED = "refunded"  # ушли и вернулись к дате отчёта


@dataclass(frozen=True)
class PaymentState:
    stage: PaymentStage
    counted: int                 # сколько входит в «выплачено» на дату, копейки
    since: Optional[date] = None # дата оплаты / возврата / постановки в очередь
    later: Optional[str] = None  # что случилось с платежом уже после даты отчёта


def payment_state_on(payment: Payment, on: date) -> PaymentState:
    paid_at, refunded_at = payment.paid_at, payment.refunded_at
    if paid_at is not None and paid_at <= on:
        if refunded_at is not None and refunded_at <= on:
            return PaymentState(PaymentStage.REFUNDED, 0, refunded_at)
        later = f"возвращён {format_date(refunded_at)}" if refunded_at else None
        return PaymentState(PaymentStage.PAID, payment.amount, paid_at, later)

    events = []
    if payment.created_at > on:
        events.append(f"заведён {format_date(payment.created_at)}")
    if paid_at is not None:
        events.append(f"оплачен {format_date(paid_at)}")
    if refunded_at is not None:
        events.append(f"возвращён {format_date(refunded_at)}")
    later = ", ".join(events) or None

    if payment.created_at <= on:
        return PaymentState(PaymentStage.QUEUED, 0, payment.created_at, later)
    return PaymentState(PaymentStage.NOT_YET, 0, None, later)
