import random
import unittest

from advance_report.money import allocate_capped, format_money, parse_money, split_proportional


class ParseMoneyTest(unittest.TestCase):
    def test_parses_rubles_and_kopecks_into_integer_kopecks(self):
        self.assertEqual(parse_money("100000.00"), 10_000_000)
        self.assertEqual(parse_money("0.5"), 50)
        self.assertEqual(parse_money("0.05"), 5)
        self.assertEqual(parse_money("7"), 700)

    def test_accepts_comma_and_digit_group_spaces(self):
        self.assertEqual(parse_money("1 234,56"), 123_456)
        self.assertEqual(parse_money(" 1 000.10 "), 100_010)

    def test_no_float_rounding_on_classic_cases(self):
        # 0.1 + 0.2 != 0.3 во float; в копейках - ровно.
        self.assertEqual(parse_money("0.1") + parse_money("0.2"), parse_money("0.3"))
        self.assertEqual(parse_money("19.99") * 3, parse_money("59.97"))

    def test_rejects_what_cannot_be_money_without_silent_rounding(self):
        for bad in ["", "-1.00", "1.234", "abc", "1e5", "1.2.3", "12,", ".5"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_money(bad)


class FormatMoneyTest(unittest.TestCase):
    def test_russian_format_by_default(self):
        self.assertEqual(format_money(10_000_000), "100 000,00")
        self.assertEqual(format_money(5), "0,05")
        self.assertEqual(format_money(-150), "-1,50")
        self.assertEqual(format_money(123_456_789_01), "123 456 789,01")

    def test_machine_formats(self):
        self.assertEqual(format_money(10_000_050, group_sep=""), "100000,50")
        self.assertEqual(format_money(10_000_050, decimal_sep=".", group_sep=""), "100000.50")

    def test_round_trip(self):
        for kopecks in [0, 1, 99, 100, 7_500_00, 123_456_789]:
            self.assertEqual(parse_money(format_money(kopecks)), kopecks)


class SplitProportionalTest(unittest.TestCase):
    def test_leftover_kopeck_goes_to_largest_remainder_then_to_earlier(self):
        self.assertEqual(split_proportional(100, [1, 1, 1]), [34, 33, 33])
        self.assertEqual(split_proportional(200, [1, 1, 1]), [67, 67, 66])
        self.assertEqual(split_proportional(7_500_000, [2, 3, 4]), [1_666_667, 2_500_000, 3_333_333])

    def test_zero_weight_gets_nothing(self):
        self.assertEqual(split_proportional(10, [0, 1, 0]), [0, 10, 0])

    def test_zero_cases(self):
        self.assertEqual(split_proportional(0, [1, 2]), [0, 0])
        self.assertEqual(split_proportional(0, [0, 0]), [0, 0])
        with self.assertRaises(ValueError):
            split_proportional(1, [0, 0])
        with self.assertRaises(ValueError):
            split_proportional(-1, [1])

    def test_sum_is_always_exact(self):
        rnd = random.Random(42)
        for _ in range(2000):
            weights = [rnd.randint(0, 10**7) for _ in range(rnd.randint(1, 8))]
            if sum(weights) == 0:
                continue
            total = rnd.randint(0, 10**9)
            shares = split_proportional(total, weights)
            self.assertEqual(sum(shares), total)
            exact = [total * w / sum(weights) for w in weights]
            for share, ideal in zip(shares, exact):
                self.assertLess(abs(share - ideal), 1)  # ошибка округления < 1 копейки


class AllocateCappedTest(unittest.TestCase):
    def test_capped_item_gives_the_rest_to_others(self):
        self.assertEqual(allocate_capped(100, [1, 1], [30, 100]), [30, 70])

    def test_cascade_of_caps(self):
        # Поровну было бы по 100, но первому можно только 10, второму 50:
        # они получают свой лимит, третьему - всё остальное.
        self.assertEqual(allocate_capped(300, [1, 1, 1], [10, 50, 1000]), [10, 50, 240])

    def test_more_money_than_caps(self):
        self.assertEqual(allocate_capped(1000, [1, 3], [100, 200]), [100, 200])

    def test_zero_weight_or_cap_gets_nothing(self):
        self.assertEqual(allocate_capped(100, [0, 1, 1], [500, 0, 500]), [0, 0, 100])

    def test_properties_on_random_inputs(self):
        rnd = random.Random(7)
        for _ in range(2000):
            n = rnd.randint(1, 7)
            weights = [rnd.choice([0, rnd.randint(1, 10**6)]) for _ in range(n)]
            caps = [rnd.choice([0, rnd.randint(1, 10**6)]) for _ in range(n)]
            total = rnd.randint(0, 3 * 10**6)
            shares = allocate_capped(total, weights, caps)
            reachable = sum(c for w, c in zip(weights, caps) if w > 0)
            self.assertEqual(sum(shares), min(total, reachable))
            for share, weight, cap in zip(shares, weights, caps):
                self.assertTrue(0 <= share <= cap)
                if weight == 0:
                    self.assertEqual(share, 0)


if __name__ == "__main__":
    unittest.main()
