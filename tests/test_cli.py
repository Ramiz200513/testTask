import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from advance_report.cli import main

from .helpers import SAMPLE_DATA, write_data


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_table_report_for_the_accountants_date(self):
        code, out, _ = run_cli("--date", "10.11.2026", "--data", str(SAMPLE_DATA))
        self.assertEqual(code, 0)
        self.assertIn("ОТЧЁТ ПО РАСЧЁТАМ С ПОДРЯДЧИКАМИ НА 10.11.2026", out)
        self.assertIn("D-1      Студия «Свет»          500 000,00  315 000,00         185 000,00", out)

    def test_iso_date_gives_the_same_report(self):
        _, ru, _ = run_cli("--date", "10.11.2026", "--data", str(SAMPLE_DATA))
        _, iso, _ = run_cli("--date", "2026-11-10", "--data", str(SAMPLE_DATA))
        self.assertEqual(ru, iso)

    def test_csv_file_for_excel(self):
        target = Path(tempfile.mkdtemp()) / "report.csv"
        code, _, _ = run_cli("--date", "10.11.2026", "--data", str(SAMPLE_DATA),
                             "--format", "csv", "--output", str(target))
        self.assertEqual(code, 0)
        raw = target.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))  # BOM: Excel откроет кириллицу
        lines = raw.decode("utf-8-sig").split("\r\n")
        self.assertTrue(lines[0].startswith("Дата отчёта;Договор;Подрядчик;Работа"))
        self.assertIn(
            "10.11.2026;D-1;Студия «Свет»;W-14;Съёмка: осень;принята;28.10.2026;"
            "100000,00;100000,00;100000,00;15000,00;85000,00;0,00;;",
            lines,
        )

    def test_json_amounts_are_exact_strings(self):
        code, out, _ = run_cli("--date", "10.11.2026", "--data", str(SAMPLE_DATA), "--format", "json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        d1 = doc["contracts"][0]
        self.assertEqual(d1["totals"]["remaining"], "185000.00")
        self.assertEqual(d1["works"][4]["paid"], "7500.00")
        self.assertEqual(doc["allocation"], "fifo")

    def test_bad_data_exits_with_1_and_lists_problems(self):
        folder = write_data(contracts="D-1,Подрядчик,много,2026-01-01")
        code, out, err = run_cli("--date", "10.11.2026", "--data", str(folder))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("contracts.csv, строка 2: amount: не сумма в рублях: 'много'", err)

    def test_unwritable_output_is_reported_without_traceback(self):
        target = Path(tempfile.mkdtemp()) / "нет такой папки" / "report.txt"
        code, _, err = run_cli("--date", "10.11.2026", "--data", str(SAMPLE_DATA), "-o", str(target))
        self.assertEqual(code, 1)
        self.assertIn("Не удалось записать отчёт", err)

    def test_bad_date_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(io.StringIO()):
            main(["--date", "31.02.2026", "--data", str(SAMPLE_DATA)])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
