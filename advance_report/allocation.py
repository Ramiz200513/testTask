"""Раскладка платежей договора по его работам на дату отчёта.

Правила (обоснование - в DECISIONS.md):

1. Платёж, привязанный к работе (work_id), засчитывается за эту работу,
   но не больше её неоплаченной части. Излишек, а также платёж за отменённую
   работу, уходит в общий котёл договора - как платёж без привязки.
2. Платежи без привязки складываются в общий котёл договора и раскладываются
   по работам очередями. Очереди задаёт стратегия:
     fifo     - сначала принятые работы по дате приёмки (раньше принята -
                раньше оплачена), последней очередью - ещё не принятые работы
                (на них остаток ложится авансом);
     pro-rata - одна очередь из всех действующих работ: аванс ложится на все
                работы одинаковой долей от цены.
   Внутри очереди деньги делятся пропорционально цене работ, но каждой - не
   больше её неоплаченной части.
3. Что не поместилось ни в одну работу - переплата по договору.

Плюс расшифровка: какой платёж за какие работы засчитан. Котёл тратится
по порядку: более ранние платежи - на работы из более ранних очередей.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from .asof import WorkStage
from .money import allocate_capped


class Strategy(Enum):
    FIFO = "fifo"
    PRO_RATA = "pro-rata"


@dataclass(frozen=True)
class WorkInput:
    work_id: str
    stage: WorkStage
    accepted_on: Optional[date]
    due: int  # сколько нужно заплатить за работу на дату: цена, у отменённой - 0


@dataclass(frozen=True)
class PaymentInput:
    payment_id: str
    work_id: Optional[str]
    counted: int  # учитывается на дату (0 - в очереди, возвращён, ещё не заведён)
    paid_on: Optional[date]


# Куда ушли деньги платежа: (work_id, сумма); work_id None - переплата по договору.
Applied = List[Tuple[Optional[str], int]]


@dataclass
class AllocationResult:
    direct: Dict[str, int]   # по работе: из привязанных к ней платежей
    pooled: Dict[str, int]   # по работе: из общего котла договора
    overpaid: int            # не поместилось ни в одну работу
    applied: Dict[str, Applied]
    redirected: List[Tuple[str, str, int]] = field(default_factory=list)
    # (платёж, работа, сумма): часть привязанного платежа, не влезшая в свою работу


def allocate(
    works: Sequence[WorkInput],
    payments: Sequence[PaymentInput],
    strategy: Strategy,
) -> AllocationResult:
    due = {w.work_id: w.due for w in works}
    direct = {w.work_id: 0 for w in works}
    pooled = {w.work_id: 0 for w in works}
    applied: Dict[str, Applied] = {p.payment_id: [] for p in payments}
    redirected: List[Tuple[str, str, int]] = []
    pool_sources: List[Tuple[str, int]] = []

    # Хронологический порядок (sorted стабилен: в один день - как в выгрузке).
    counted = sorted(
        (p for p in payments if p.counted > 0),
        key=lambda p: p.paid_on or date.min,
    )

    # 1. Привязанные платежи - в свою работу, излишек - в котёл.
    for p in counted:
        if p.work_id is None:
            pool_sources.append((p.payment_id, p.counted))
            continue
        take = min(p.counted, due[p.work_id] - direct[p.work_id])
        if take > 0:
            direct[p.work_id] += take
            applied[p.payment_id].append((p.work_id, take))
        excess = p.counted - take
        if excess > 0:
            redirected.append((p.payment_id, p.work_id, excess))
            pool_sources.append((p.payment_id, excess))

    # 2. Котёл - по очередям стратегии.
    pool = sum(amount for _, amount in pool_sources)
    sinks: List[Tuple[Optional[str], int]] = []
    for queue in _queues(works, strategy):
        if pool == 0:
            break
        ids = [w.work_id for w in queue]
        shares = allocate_capped(
            pool,
            weights=[due[i] for i in ids],
            caps=[due[i] - direct[i] for i in ids],
        )
        for work_id, share in zip(ids, shares):
            if share > 0:
                pooled[work_id] += share
                sinks.append((work_id, share))
        pool -= sum(shares)

    # 3. Остаток котла - переплата.
    overpaid = pool
    if overpaid > 0:
        sinks.append((None, overpaid))

    _attribute(pool_sources, sinks, applied)
    return AllocationResult(direct, pooled, overpaid, applied, redirected)


def _queues(works: Sequence[WorkInput], strategy: Strategy) -> List[List[WorkInput]]:
    active = [w for w in works if w.stage is not WorkStage.CANCELLED and w.due > 0]
    if strategy is Strategy.PRO_RATA:
        return [active] if active else []

    accepted = sorted(
        (w for w in active if w.stage is WorkStage.ACCEPTED),
        key=lambda w: w.accepted_on,
    )
    queues = [list(group) for _, group in itertools.groupby(accepted, key=lambda w: w.accepted_on)]
    still_open = [w for w in active if w.stage is WorkStage.OPEN]
    if still_open:
        queues.append(still_open)
    return queues


def _attribute(
    sources: List[Tuple[str, int]],
    sinks: List[Tuple[Optional[str], int]],
    applied: Dict[str, Applied],
) -> None:
    """Сопоставляет деньги котла (платежи по порядку) с тем, куда они легли."""
    remaining = [[payment_id, amount] for payment_id, amount in sources]
    index = 0
    for work_id, amount in sinks:
        while amount > 0:
            payment_id, available = remaining[index]
            take = min(available, amount)
            applied[payment_id].append((work_id, take))
            amount -= take
            remaining[index][1] -= take
            if remaining[index][1] == 0:
                index += 1
    if any(available for _, available in remaining):
        raise RuntimeError("внутренняя ошибка: деньги котла распределены не полностью")
