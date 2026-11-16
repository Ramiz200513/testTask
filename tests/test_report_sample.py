"""Отчёт по настоящим данным из data/. Ожидаемые числа посчитаны вручную (см. DECISIONS.md)."""

import unittest
from dataclasses import replace
from datetime import date

from advance_report.allocation import Strategy
from advance_report.loader import load_dataset
from advance_report.report import build_report

from .helpers import SAMPLE_DATA, applied, contract, rub, work_amounts


def report_on(day: date, strategy: Strategy = Strategy.FIFO):
    return build_report(load_dataset(SAMPLE_DATA), day, strategy)


NOV_10 = date(2026, 11, 10)


class ReportOnNovember10Test(unittest.TestCase):
    """Дата из письма бухгалтера."""

    @classmethod
    def setUpClass(cls):
        cls.report = report_on(NOV_10)
        cls.d1 = contract(cls.report, "D-1")
        cls.d2 = contract(cls.report, "D-2")

    def test_d1_works(self):
        # (к оплате, выплачено, из них без привязки, осталось)
        self.assertEqual(work_amounts(self.d1), {
            "W-11": (rub("100000"), rub("100000"), rub("100000"), 0),
            "W-12": (rub("100000"), rub("100000"), rub("100000"), 0),
            "W-13": (0, 0, 0, 0),                                       # отменена 12.10
            "W-14": (rub("100000"), rub("100000"), rub("85000"), 0),    # 15 000 по привязке (P-3)
            "W-15": (rub("100000"), rub("7500"), rub("7500"), rub("92500")),
            "W-16": (rub("100000"), rub("7500"), rub("7500"), rub("92500")),
        })

    def test_d1_totals(self):
        t = self.d1.totals
        self.assertEqual(t.works_price, rub("600000"))
        self.assertEqual(t.cancelled, rub("100000"))
        self.assertEqual(t.due, rub("500000"))
        self.assertEqual(t.accepted, rub("300000"))
        self.assertEqual(t.paid, rub("315000"))           # P-1 300 000 + P-3 15 000
        self.assertEqual(t.remaining, rub("185000"))
        self.assertEqual(t.remaining_accepted, 0)
        self.assertEqual(t.remaining_open, rub("185000"))
        self.assertEqual(t.advance, rub("15000"))
        self.assertEqual(t.overpaid, 0)
        self.assertEqual(t.queued, rub("150000"))         # P-4
        self.assertEqual(t.refunded, rub("50000"))        # P-2 вернули 10.09

    def test_d1_payment_breakdown(self):
        self.assertEqual(applied(self.d1), {
            "P-1": [("W-11", rub("100000")), ("W-12", rub("100000")), ("W-14", rub("85000")),
                    ("W-15", rub("7500")), ("W-16", rub("7500"))],
            "P-2": [],
            "P-3": [("W-14", rub("15000"))],
            "P-4": [],
        })

    def test_d2_works_and_totals(self):
        self.assertEqual(work_amounts(self.d2), {
            "W-21": (rub("20000"), rub("20000"), rub("20000"), 0),
            "W-22": (rub("30000"), rub("30000"), rub("30000"), 0),
            "W-23": (rub("40000"), rub("25000"), rub("25000"), rub("15000")),  # принята только 11.11
        })
        t = self.d2.totals
        self.assertEqual((t.due, t.accepted, t.paid, t.remaining), (
            rub("90000"), rub("50000"), rub("75000"), rub("15000")))
        self.assertEqual((t.overpaid, t.queued, t.refunded), (0, 0, 0))

    def test_d2_payment_breakdown_and_late_events(self):
        self.assertEqual(applied(self.d2), {
            "P-5": [("W-21", rub("20000")), ("W-22", rub("25000"))],
            "P-6": [("W-22", rub("5000")), ("W-23", rub("20000"))],
            "P-7": [("W-23", rub("5000"))],
            "P-8": [],                                     # оплачен 12.11
        })
        notes = "\n".join(self.d2.notes)
        self.assertIn("W-23 «Упаковка»: в выгрузке принята 11.11.2026", notes)
        self.assertIn("P-8 (20 000,00, «Остаток»): заведён 12.11.2026, оплачен 12.11.2026", notes)

    def test_queued_payment_note_for_d1(self):
        notes = "\n".join(self.d1.notes)
        self.assertIn("В очереди на оплату 150 000,00", notes)
        self.assertIn("будет выплачено 465 000,00 при стоимости принятых работ 300 000,00", notes)

    def test_nothing_skipped(self):
        self.assertEqual(self.report.skipped, [])
        self.assertEqual(self.report.data_warnings, [])


class OtherDatesTest(unittest.TestCase):
    def test_before_refund_the_erroneous_payment_counts(self):
        report = report_on(date(2026, 9, 5))
        d1 = contract(report, "D-1")
        self.assertEqual(d1.totals.paid, rub("350000"))   # P-1 + P-2 (вернут только 10.09)
        self.assertEqual(d1.totals.accepted, 0)
        self.assertEqual(d1.totals.cancelled, 0)          # W-13 отменят только 12.10
        # 350 000 на 6 работ по 100 000 - до копейки.
        self.assertEqual([r.paid for r in d1.works], [rub("58333.34")] * 2 + [rub("58333.33")] * 4)
        self.assertIn("P-2 (50 000,00): возвращён 10.09.2026", "\n".join(d1.notes))
        self.assertEqual(len(report.skipped), 1)
        self.assertIn("Договор D-2", report.skipped[0])  # подписан 20.09

    def test_after_export_d2_is_overpaid(self):
        d2 = contract(report_on(date(2026, 11, 16)), "D-2")
        self.assertEqual(d2.totals.paid, rub("95000"))
        self.assertEqual(d2.totals.overpaid, rub("5000"))
        self.assertEqual(d2.totals.remaining, 0)
        self.assertEqual(applied(d2)["P-8"], [("W-23", rub("15000")), (None, rub("5000"))])

    def test_before_any_contract(self):
        report = report_on(date(2026, 1, 1))
        self.assertEqual(report.contracts, [])
        self.assertEqual(len(report.skipped), 2)


class StrategyTest(unittest.TestCase):
    def test_pro_rata_on_november_10(self):
        d1 = contract(report_on(NOV_10, Strategy.PRO_RATA), "D-1")
        self.assertEqual({k: v[1] for k, v in work_amounts(d1).items()}, {
            "W-11": rub("60000"), "W-12": rub("60000"), "W-13": 0,
            "W-14": rub("75000"), "W-15": rub("60000"), "W-16": rub("60000"),
        })
        d2 = contract(report_on(NOV_10, Strategy.PRO_RATA), "D-2")
        self.assertEqual([r.paid for r in d2.works],
                         [rub("16666.67"), rub("25000"), rub("33333.33")])

    def test_contract_totals_do_not_depend_on_strategy(self):
        # Стратегия меняет только раскладку по работам, итоги по договору - нет
        # (кроме разбивки остатка на принятые/непринятые работы).
        for day in [date(2026, 9, 5), date(2026, 10, 15), NOV_10, date(2026, 11, 16)]:
            fifo = report_on(day, Strategy.FIFO)
            pro_rata = report_on(day, Strategy.PRO_RATA)
            for a, b in zip(fifo.contracts, pro_rata.contracts):
                neutral = dict(remaining_accepted=0, remaining_open=0)
                self.assertEqual(replace(a.totals, **neutral), replace(b.totals, **neutral), day)


if __name__ == "__main__":
    unittest.main()
