import unittest
from datetime import date

from advance_report.allocation import PaymentInput, Strategy, WorkInput, allocate
from advance_report.asof import WorkStage

from .helpers import rub

ACC, OPEN, CANC = WorkStage.ACCEPTED, WorkStage.OPEN, WorkStage.CANCELLED


def w(work_id, stage, price, accepted_on=None):
    return WorkInput(work_id, stage, accepted_on, 0 if stage is CANC else rub(price))


def p(payment_id, amount, paid_on, work_id=None):
    return PaymentInput(payment_id, work_id, rub(amount), paid_on)


def run(works, payments, strategy=Strategy.FIFO):
    result = allocate(works, payments, strategy)
    # Свойство расшифровки: каждый учтённый платёж разложен целиком.
    for pay in payments:
        spent = sum(amount for _, amount in result.applied[pay.payment_id])
        assert spent == pay.counted, (pay.payment_id, spent, pay.counted)
    return result


def paid(result):
    return {k: result.direct[k] + result.pooled[k] for k in result.direct}


D1, D2, D3 = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)


class FifoTest(unittest.TestCase):
    def test_earlier_accepted_work_is_paid_first_regardless_of_file_order(self):
        works = [w("W-late", ACC, "100", D3), w("W-early", ACC, "100", D1), w("W-open", OPEN, "100")]
        result = run(works, [p("P-1", "150", D1)])
        self.assertEqual(paid(result), {"W-early": rub("100"), "W-late": rub("50"), "W-open": 0})

    def test_surplus_goes_to_open_works_proportionally_to_price(self):
        works = [w("W-1", ACC, "100", D1), w("W-2", OPEN, "100"), w("W-3", OPEN, "300")]
        result = run(works, [p("P-1", "180", D1)])
        self.assertEqual(paid(result), {"W-1": rub("100"), "W-2": rub("20"), "W-3": rub("60")})

    def test_same_day_acceptance_is_split_proportionally(self):
        works = [w("W-1", ACC, "100", D2), w("W-2", ACC, "300", D2)]
        result = run(works, [p("P-1", "100", D1)])
        self.assertEqual(paid(result), {"W-1": rub("25"), "W-2": rub("75")})

    def test_open_works_get_money_only_after_all_accepted_are_paid(self):
        works = [w("W-open", OPEN, "100"), w("W-acc", ACC, "100", D2)]
        result = run(works, [p("P-1", "100", D1)])
        self.assertEqual(paid(result), {"W-open": 0, "W-acc": rub("100")})

    def test_cancelled_work_gets_nothing(self):
        works = [w("W-1", CANC, "100"), w("W-2", OPEN, "100")]
        result = run(works, [p("P-1", "60", D1)])
        self.assertEqual(paid(result), {"W-1": 0, "W-2": rub("60")})

    def test_money_beyond_all_works_is_overpayment(self):
        works = [w("W-1", ACC, "100", D1)]
        result = run(works, [p("P-1", "80", D1), p("P-2", "50", D2)])
        self.assertEqual(paid(result), {"W-1": rub("100")})
        self.assertEqual(result.overpaid, rub("30"))
        # Переплату «делает» последний по времени платёж.
        self.assertEqual(result.applied["P-2"], [("W-1", rub("20")), (None, rub("30"))])

    def test_contract_without_works_is_all_overpayment(self):
        result = run([], [p("P-1", "10", D1)])
        self.assertEqual(result.overpaid, rub("10"))


class LinkedPaymentsTest(unittest.TestCase):
    def test_linked_payment_goes_to_its_work_first(self):
        works = [w("W-1", ACC, "100", D1), w("W-2", OPEN, "100")]
        result = run(works, [p("P-1", "30", D1, work_id="W-2"), p("P-2", "100", D2)])
        self.assertEqual(result.direct, {"W-1": 0, "W-2": rub("30")})
        self.assertEqual(paid(result), {"W-1": rub("100"), "W-2": rub("30")})

    def test_linked_excess_goes_to_contract_pool(self):
        works = [w("W-1", ACC, "100", D1), w("W-2", ACC, "100", D2)]
        result = run(works, [p("P-1", "130", D1, work_id="W-1")])
        self.assertEqual(paid(result), {"W-1": rub("100"), "W-2": rub("30")})
        self.assertEqual(result.redirected, [("P-1", "W-1", rub("30"))])
        self.assertEqual(result.applied["P-1"], [("W-1", rub("100")), ("W-2", rub("30"))])

    def test_payment_linked_to_cancelled_work_is_redistributed(self):
        works = [w("W-1", CANC, "100"), w("W-2", OPEN, "100")]
        result = run(works, [p("P-1", "40", D1, work_id="W-1")])
        self.assertEqual(paid(result), {"W-1": 0, "W-2": rub("40")})
        self.assertEqual(result.redirected, [("P-1", "W-1", rub("40"))])

    def test_not_counted_payments_are_ignored(self):
        works = [w("W-1", ACC, "100", D1)]
        result = run(works, [PaymentInput("P-q", None, 0, None)])
        self.assertEqual(paid(result), {"W-1": 0})
        self.assertEqual(result.applied["P-q"], [])


class ProRataTest(unittest.TestCase):
    def test_advance_is_spread_over_all_active_works_by_price(self):
        works = [w("W-1", ACC, "100", D1), w("W-2", OPEN, "300"), w("W-3", CANC, "500")]
        result = run(works, [p("P-1", "200", D1)], Strategy.PRO_RATA)
        self.assertEqual(paid(result), {"W-1": rub("50"), "W-2": rub("150"), "W-3": 0})

    def test_linked_payment_caps_its_work(self):
        works = [w("W-1", ACC, "100", D1), w("W-2", ACC, "100", D1)]
        # По 75 на работу не выйдет: у W-1 уже 90 по привязке, место только под 10.
        result = run(works, [p("P-1", "90", D1, "W-1"), p("P-2", "150", D1)], Strategy.PRO_RATA)
        self.assertEqual(paid(result), {"W-1": rub("100"), "W-2": rub("100")})
        self.assertEqual(result.overpaid, rub("40"))


if __name__ == "__main__":
    unittest.main()
