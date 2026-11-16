import unittest
from datetime import date

from advance_report.asof import PaymentStage, WorkStage, payment_state_on, work_state_on
from advance_report.model import Payment, PaymentStatus, Work, WorkStatus


def work(status, completed=None, cancelled=None):
    return Work("W-1", "D-1", "Работа", 100_00, status, completed, cancelled)


def payment(status, created, paid=None, refunded=None, amount=500_00):
    return Payment("P-1", "D-1", None, "", amount, status, created, paid, refunded)


D = date


class WorkStateTest(unittest.TestCase):
    def test_done_work_is_accepted_only_from_completion_date_inclusive(self):
        w = work(WorkStatus.DONE, completed=D(2026, 11, 11))
        self.assertEqual(work_state_on(w, D(2026, 11, 10)).stage, WorkStage.OPEN)
        self.assertEqual(work_state_on(w, D(2026, 11, 10)).later, "принята 11.11.2026")
        self.assertEqual(work_state_on(w, D(2026, 11, 11)).stage, WorkStage.ACCEPTED)
        self.assertEqual(work_state_on(w, D(2026, 11, 11)).since, D(2026, 11, 11))

    def test_cancelled_work_is_open_before_cancellation(self):
        w = work(WorkStatus.CANCELLED, cancelled=D(2026, 10, 12))
        self.assertEqual(work_state_on(w, D(2026, 10, 11)).stage, WorkStage.OPEN)
        self.assertEqual(work_state_on(w, D(2026, 10, 12)).stage, WorkStage.CANCELLED)

    def test_planned_work_is_open(self):
        state = work_state_on(work(WorkStatus.PLANNED), D(2030, 1, 1))
        self.assertEqual((state.stage, state.since, state.later), (WorkStage.OPEN, None, None))


class PaymentStateTest(unittest.TestCase):
    def test_paid_counts_from_payment_date_inclusive(self):
        p = payment(PaymentStatus.PAID, created=D(2026, 8, 30), paid=D(2026, 9, 1))
        self.assertEqual(payment_state_on(p, D(2026, 8, 29)).stage, PaymentStage.NOT_YET)
        queued = payment_state_on(p, D(2026, 8, 31))
        self.assertEqual((queued.stage, queued.counted), (PaymentStage.QUEUED, 0))
        self.assertEqual(queued.later, "оплачен 01.09.2026")
        paid = payment_state_on(p, D(2026, 9, 1))
        self.assertEqual((paid.stage, paid.counted), (PaymentStage.PAID, 500_00))

    def test_refunded_payment_counts_only_between_payment_and_refund(self):
        p = payment(PaymentStatus.REFUNDED, created=D(2026, 9, 2), paid=D(2026, 9, 2),
                    refunded=D(2026, 9, 10))
        before_refund = payment_state_on(p, D(2026, 9, 9))
        self.assertEqual((before_refund.stage, before_refund.counted), (PaymentStage.PAID, 500_00))
        self.assertEqual(before_refund.later, "возвращён 10.09.2026")
        after_refund = payment_state_on(p, D(2026, 9, 10))
        self.assertEqual((after_refund.stage, after_refund.counted), (PaymentStage.REFUNDED, 0))

    def test_queued_payment_is_never_counted(self):
        p = payment(PaymentStatus.QUEUED, created=D(2026, 11, 1))
        self.assertEqual(payment_state_on(p, D(2026, 10, 31)).stage, PaymentStage.NOT_YET)
        state = payment_state_on(p, D(2030, 1, 1))
        self.assertEqual((state.stage, state.counted, state.since),
                         (PaymentStage.QUEUED, 0, D(2026, 11, 1)))


if __name__ == "__main__":
    unittest.main()
