import unittest
from datetime import date

from advance_report.loader import DataError, load_dataset
from advance_report.model import PaymentStatus, WorkStatus

from .helpers import SAMPLE_DATA, write_data

CONTRACT = "D-1,Подрядчик,1000.00,2026-01-10"


class SampleDataTest(unittest.TestCase):
    def test_sample_data_loads_cleanly(self):
        ds = load_dataset(SAMPLE_DATA)
        self.assertEqual([c.contract_id for c in ds.contracts], ["D-1", "D-2"])
        self.assertEqual(len(ds.works), 9)
        self.assertEqual(len(ds.payments), 8)
        self.assertEqual(ds.warnings, [])

    def test_quoted_comma_and_typographic_quotes(self):
        ds = load_dataset(SAMPLE_DATA)
        self.assertEqual(ds.contracts[0].contractor, "Студия «Свет»")
        self.assertEqual(ds.contracts[1].contractor, "Иван Петров, дизайнер")
        p2 = next(p for p in ds.payments if p.payment_id == "P-2")
        self.assertEqual(p2.purpose, "Аванс, ошибочный платёж")
        self.assertEqual(p2.amount, 50_000_00)
        self.assertIsNone(p2.work_id)

    def test_empty_optional_fields_become_none(self):
        ds = load_dataset(SAMPLE_DATA)
        p4 = next(p for p in ds.payments if p.payment_id == "P-4")
        self.assertEqual((p4.status, p4.paid_at, p4.refunded_at), (PaymentStatus.QUEUED, None, None))


class FileFormatTest(unittest.TestCase):
    def test_bom_crlf_spaces_case_and_blank_lines_are_tolerated(self):
        folder = write_data(
            contracts=CONTRACT,
            works=" W-1 , D-1 , Работа , 1000.00 , DONE , 2026-02-01 , \r\n\r\n",
            payments="P-1,D-1,W-1,,1000.00,Paid,2026-02-02,02.02.2026,\r\n",
            encoding="utf-8-sig",
        )
        ds = load_dataset(folder)
        self.assertEqual(ds.works[0].work_id, "W-1")
        self.assertEqual(ds.works[0].status, WorkStatus.DONE)
        self.assertEqual(ds.payments[0].paid_at, date(2026, 2, 2))

    def test_missing_column_is_reported(self):
        folder = write_data(contracts=CONTRACT)
        (folder / "works.csv").write_text("work_id,contract_id,title\n", encoding="utf-8")
        with self.assertRaises(DataError) as ctx:
            load_dataset(folder)
        self.assertIn("works.csv: нет колонок price, status, completed_at, cancelled_at",
                      ctx.exception.problems)

    def test_file_resaved_by_russian_excel_semicolons_and_cp1251(self):
        folder = write_data(contracts=CONTRACT)
        (folder / "contracts.csv").write_bytes(
            "contract_id;contractor;amount;signed_at\r\n"
            "D-1;Студия «Свет», ООО;1000,50;10.01.2026\r\n".encode("cp1251")
        )
        ds = load_dataset(folder)
        self.assertEqual(ds.contracts[0].contractor, "Студия «Свет», ООО")
        self.assertEqual(ds.contracts[0].amount, 1000_50)
        self.assertEqual(ds.contracts[0].signed_at, date(2026, 1, 10))
        self.assertIn("contracts.csv: файл не в UTF-8, прочитан как Windows-1251", ds.warnings)


class ValidationTest(unittest.TestCase):
    def test_all_problems_are_collected_at_once(self):
        folder = write_data(
            contracts=CONTRACT + "\nD-2,Другой,1000.00,2026-01-10\nD-1,Дубль,1.00,2026-01-01",
            works="\n".join([
                "W-1,D-1,Работа,сто рублей,done,2026-02-01,",
                "W-2,D-1,Работа,100.00,finished,,",
                "W-3,D-9,Работа,100.00,planned,,",
                "W-4,D-1,Работа,100.00,done,,",
                "W-5,D-2,Работа,100.00,planned,,",
            ]),
            payments="\n".join([
                "P-1,D-1,W-5,,10.00,paid,2026-02-01,2026-02-01,",
                "P-2,D-1,W-404,,10.00,paid,2026-02-01,2026-02-01,",
                "P-3,D-1,,,10.001,paid,2026-02-01,2026-02-01,",
                "P-4,D-1,,,10.00,refunded,2026-02-01,2026-02-05,2026-02-01",
                "P-5,D-1,,,10.00,paid,2026-02-01,,",
            ]),
        )
        with self.assertRaises(DataError) as ctx:
            load_dataset(folder)
        text = "\n".join(ctx.exception.problems)
        expected = [
            "contracts.csv: contract_id D-1 повторяется",
            "works.csv, строка 2: price: не сумма в рублях: 'сто рублей'",
            "works.csv, строка 3: status: неизвестное значение 'finished'",
            "works.csv, строка 4: договор D-9 не найден",
            "works.csv, строка 5: работа done, но не заполнена дата приёмки",
            "payments.csv, строка 2: платёж по договору D-1 привязан к работе W-5 из другого договора (D-2)",
            "payments.csv, строка 3: работа W-404 не найдена",
            "payments.csv, строка 4: amount: не сумма в рублях: '10.001'",
            "payments.csv, строка 5: дата возврата refunded_at раньше даты оплаты paid_at",
            "payments.csv, строка 6: платёж paid, но не заполнена дата оплаты paid_at",
        ]
        for fragment in expected:
            self.assertIn(fragment, text)

    def test_broken_contract_row_does_not_cascade_into_false_reference_errors(self):
        folder = write_data(
            contracts="D-1,Подрядчик,много денег,2026-01-10",
            works="W-1,D-1,Работа,100.00,done,2026-02-01,",
            payments="P-1,D-1,W-1,,10.00,paid,2026-02-01,2026-02-01,",
        )
        with self.assertRaises(DataError) as ctx:
            load_dataset(folder)
        self.assertEqual(len(ctx.exception.problems), 1, ctx.exception.problems)
        self.assertIn("amount: не сумма в рублях: 'много денег'", ctx.exception.problems[0])

    def test_contradictions_resolved_by_status_become_warnings(self):
        folder = write_data(
            contracts=CONTRACT,
            works="W-1,D-1,Работа,100.00,planned,2026-02-01,",
            payments="\n".join([
                "P-1,D-1,,,10.00,queued,2026-02-01,2026-02-03,",
                "P-2,D-1,,,10.00,paid,2026-01-01,2026-01-05,",
            ]),
        )
        ds = load_dataset(folder)
        self.assertIsNone(ds.works[0].completed_at)
        self.assertIsNone(ds.payments[0].paid_at)
        warnings = "\n".join(ds.warnings)
        self.assertIn("работа planned, но заполнено completed_at = 01.02.2026", warnings)
        self.assertIn("платёж queued, но заполнено paid_at = 03.02.2026", warnings)
        self.assertIn("раньше подписания договора D-1 (10.01.2026)", warnings)

    def test_missing_folder(self):
        with self.assertRaises(DataError) as ctx:
            load_dataset(SAMPLE_DATA / "nope")
        self.assertIn("папка с данными не найдена", ctx.exception.problems[0])


if __name__ == "__main__":
    unittest.main()
