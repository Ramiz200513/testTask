"""Случаи, найденные независимым ревью кода."""

import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from advance_report.allocation import Strategy
from advance_report.asof import WorkStage, work_state_on
from advance_report.loader import DataError, load_dataset
from advance_report.render import render_csv, render_table
from advance_report.report import build_report

from .helpers import SAMPLE_DATA, contract, rub, write_data
from .test_cli import run_cli


class UnreadableFileTest(unittest.TestCase):
    def test_locked_file_is_a_data_problem_not_a_traceback(self):
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(DataError) as ctx:
                load_dataset(SAMPLE_DATA)
        self.assertIn("contracts.csv: не удалось прочитать файл: Permission denied", ctx.exception.problems)


class PaymentBeforeSigningTest(unittest.TestCase):
    def setUp(self):
        self.folder = write_data(
            contracts="D-1,Подрядчик,100000.00,2026-08-25",
            works="W-1,D-1,Работа,100000.00,planned,,",
            payments="P-1,D-1,,Аванс,30000.00,paid,2026-08-20,2026-08-20,",
        )

    def test_money_paid_before_signing_does_not_disappear(self):
        report = build_report(load_dataset(self.folder), date(2026, 8, 22), Strategy.FIFO)
        self.assertEqual(report.skipped, [])
        d1 = contract(report, "D-1")
        self.assertEqual(d1.totals.paid, rub("30000"))
        self.assertIn("Договор подписан 25.08.2026 - после даты отчёта", "\n".join(d1.notes))

    def test_contract_without_any_activity_yet_is_still_skipped(self):
        report = build_report(load_dataset(self.folder), date(2026, 8, 19), Strategy.FIFO)
        self.assertEqual(report.contracts, [])
        self.assertEqual(len(report.skipped), 1)

    def test_csv_mode_reports_skipped_contracts_in_stderr(self):
        code, out, err = run_cli("--date", "05.09.2026", "--data", str(SAMPLE_DATA), "--format", "csv")
        self.assertEqual(code, 0)
        self.assertIn("Не вошло в отчёт:", err)
        self.assertIn("Договор D-2", err)


class WorkHistoryTest(unittest.TestCase):
    def test_accepted_then_cancelled_work_is_accepted_in_between(self):
        folder = write_data(
            contracts="D-1,Подрядчик,200000.00,2026-09-01",
            works="W-1,D-1,Первая,100000.00,cancelled,2026-10-01,2026-10-20\n"
                  "W-2,D-1,Вторая,100000.00,planned,,",
            payments="P-1,D-1,,Аванс,100000.00,paid,2026-09-02,2026-09-02,",
        )
        ds = load_dataset(folder)
        self.assertIn("работа W-1 принята 01.10.2026, затем отменена 20.10.2026", "\n".join(ds.warnings))

        on_10_10 = contract(build_report(ds, date(2026, 10, 10), Strategy.FIFO), "D-1")
        w1 = on_10_10.works[0]
        self.assertEqual((w1.state.stage, w1.paid), (WorkStage.ACCEPTED, rub("100000")))
        self.assertIn("W-1 «Первая»: в выгрузке отменена 20.10.2026 - уже после даты отчёта, "
                      "поэтому на 10.10.2026 работа принята.", on_10_10.notes)

        on_10_25 = contract(build_report(ds, date(2026, 10, 25), Strategy.FIFO), "D-1")
        self.assertEqual(on_10_25.works[0].state.stage, WorkStage.CANCELLED)
        self.assertEqual([r.paid for r in on_10_25.works], [0, rub("100000")])

    def test_date_contradicting_the_final_status_is_ignored(self):
        folder = write_data(
            contracts="D-1,Подрядчик,100.00,2026-09-01",
            works="W-1,D-1,Работа,100.00,cancelled,2026-10-25,2026-10-20",
        )
        ds = load_dataset(folder)
        self.assertIsNone(ds.works[0].completed_at)
        self.assertIn("работа cancelled, но заполнено completed_at = 25.10.2026", "\n".join(ds.warnings))
        self.assertEqual(work_state_on(ds.works[0], date(2026, 10, 30)).stage, WorkStage.CANCELLED)


class OutputSafetyTest(unittest.TestCase):
    def setUp(self):
        folder = write_data(
            contracts="D-1,=HYPERLINK(\"http://x\"),100.00,2026-09-01",
            works='W-1,D-1,"-15% скидка,\nмонтаж",100.00,planned,,\nW-2,D-1,=1+1,0.00,planned,,',
        )
        self.report = build_report(load_dataset(folder), date(2026, 10, 1), Strategy.FIFO)

    def test_csv_cells_that_look_like_formulas_are_text(self):
        csv_text = render_csv(self.report)
        self.assertIn(';"\'=HYPERLINK(""http://x"")";', csv_text)  # кавычки удвоены по правилам CSV
        self.assertIn(";'=1+1;", csv_text)
        self.assertIn("'-15% скидка,", csv_text)

    def test_newline_in_title_does_not_break_table(self):
        table = render_table(self.report)
        self.assertIn("-15% скидка, монтаж", table)
        self.assertNotIn("\nмонтаж", table)


if __name__ == "__main__":
    unittest.main()
