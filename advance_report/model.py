"""Данные выгрузки в том виде, как они описаны в data/FORMAT.md. Суммы - в копейках."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import List, Optional


class WorkStatus(Enum):
    """Статус работы на момент выгрузки (не на дату отчёта!)."""

    PLANNED = "planned"
    DONE = "done"
    CANCELLED = "cancelled"


class PaymentStatus(Enum):
    """Статус платежа на момент выгрузки (не на дату отчёта!)."""

    PAID = "paid"
    QUEUED = "queued"
    REFUNDED = "refunded"


@dataclass(frozen=True)
class Contract:
    contract_id: str
    contractor: str
    amount: int
    signed_at: date


@dataclass(frozen=True)
class Work:
    work_id: str
    contract_id: str
    title: str
    price: int
    status: WorkStatus
    completed_at: Optional[date]
    cancelled_at: Optional[date]


@dataclass(frozen=True)
class Payment:
    payment_id: str
    contract_id: str
    work_id: Optional[str]
    purpose: str
    amount: int
    status: PaymentStatus
    created_at: date
    paid_at: Optional[date]
    refunded_at: Optional[date]


@dataclass
class Dataset:
    contracts: List[Contract]
    works: List[Work]
    payments: List[Payment]
    warnings: List[str] = field(default_factory=list)
