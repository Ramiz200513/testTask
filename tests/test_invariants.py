"""Случайные выгрузки: деньги должны сходиться до копейки при любых данных и датах.

build_report сам проверяет сверки и падает с RuntimeError, если что-то не сходится;
здесь дополнительно проверяем свойства, которые видны снаружи.
"""

import random
import unittest
from dataclasses import replace
from datetime import date, timedelta

from advance_report.allocation import Strategy
from advance_report.model import (
    Contract, Dataset, Payment, PaymentStatus, Work, WorkStatus,
)
from advance_report.report import build_report

START = date(2026, 1, 1)


def day(n: int) -> date:
    return START + timedelta(days=n)


def random_dataset(rnd: random.Random) -> Dataset:
    contracts, works, payments = [], [], []
    for c in range(rnd.randint(1, 3)):
        cid = f"D-{c}"
        contracts.append(Contract(cid, f"Подрядчик {c}", rnd.randint(0, 10**8), day(rnd.randint(0, 30))))
        work_ids = []
        for i in range(rnd.randint(0, 6)):
            wid = f"W-{c}{i}"
            work_ids.append(wid)
            status = rnd.choice(list(WorkStatus))
            when = day(rnd.randint(0, 120))
            works.append(Work(
                wid, cid, f"Работа {wid}", rnd.choice([0, rnd.randint(1, 10**7)]), status,
                when if status is WorkStatus.DONE else None,
                when if status is WorkStatus.CANCELLED else None,
            ))
        for i in range(rnd.randint(0, 8)):
            status = rnd.choice(list(PaymentStatus))
            created = day(rnd.randint(0, 120))
            paid = created + timedelta(days=rnd.randint(0, 10)) if status is not PaymentStatus.QUEUED else None
            refunded = paid + timedelta(days=rnd.randint(0, 20)) if status is PaymentStatus.REFUNDED else None
            linked = rnd.choice(work_ids) if work_ids and rnd.random() < 0.3 else None
            payments.append(Payment(
                f"P-{c}{i}", cid, linked, "", rnd.randint(0, 10**7), status, created, paid, refunded,
            ))
    return Dataset(contracts, works, payments)


class InvariantsTest(unittest.TestCase):
    def test_money_always_adds_up(self):
        rnd = random.Random(2026_11_10)
        for case in range(300):
            ds = random_dataset(rnd)
            on = day(rnd.randint(0, 150))
            results = {s: build_report(ds, on, s) for s in Strategy}  # падает, если не сходится
            for strategy, report in results.items():
                for cr in report.contracts:
                    t = cr.totals
                    msg = f"случай {case}, {strategy.value}, {cr.contract.contract_id}"
                    for r in cr.payments:
                        self.assertEqual(sum(a for _, a in r.applied), r.state.counted, msg)
                    self.assertGreaterEqual(t.remaining, 0, msg)
                    self.assertEqual(
                        t.paid,
                        sum(p.amount for p in ds.payments
                            if p.contract_id == cr.contract.contract_id
                            and p.paid_at and p.paid_at <= on
                            and not (p.refunded_at and p.refunded_at <= on)),
                        msg,
                    )
            neutral = dict(remaining_accepted=0, remaining_open=0)
            for a, b in zip(results[Strategy.FIFO].contracts, results[Strategy.PRO_RATA].contracts):
                self.assertEqual(replace(a.totals, **neutral), replace(b.totals, **neutral))

    def test_fifo_never_leaves_accepted_work_unpaid_while_money_sits_on_open_work(self):
        rnd = random.Random(42)
        for _ in range(300):
            ds = random_dataset(rnd)
            on = day(rnd.randint(0, 150))
            for cr in build_report(ds, on, Strategy.FIFO).contracts:
                unpaid_accepted = any(r.remaining > 0 and r.state.since and r.due and
                                      r.state.stage.value == "accepted" for r in cr.works)
                pooled_on_open = any(r.pooled > 0 and r.state.stage.value == "open" for r in cr.works)
                self.assertFalse(unpaid_accepted and pooled_on_open)


if __name__ == "__main__":
    unittest.main()
