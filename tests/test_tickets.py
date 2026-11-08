"""Ticket checker: tier decisions, extra draws, Gold Ball and input validation (no network)."""
from datetime import date
import random
import unittest

from bclc.results import OfficialDraw, classify
from bclc.tickets import check_ticket, parse_lines, tier_for

DRAW_649 = {"date": "2026-11-07", "numbers": [9, 15, 20, 23, 26, 31], "bonus": 6,
            "gold_ball": [["4282745802", "$1,000,000.00"]], "ball_drawn": "White",
            "super_draws": [["0456932901", "$10,000.00"]],
            "details_url": "https://www.wclc.com/lotto-649-prize-details.htm?drawNumber=4466"}
DRAW_MAX = {"date": "2026-11-06", "numbers": [10, 23, 32, 43, 46, 48, 51], "bonus": 30,
            "maxmillions_sets": [[6, 7, 25, 35, 39, 42, 47]], "maxplus_sets": [[1, 2, 12, 13, 16, 25, 34]],
            "details_url": "https://www.wclc.com/lotto-max-prize-details.htm?drawNumber=1285"}


def max_rule(line, numbers, bonus):
    """Independent statement of PlayNow's Lotto Max table."""
    hits, on = len(set(line) & set(numbers)), bonus in line
    if hits == 7:
        return "7/7"
    if 3 <= hits <= 6:
        return f"{hits}/7" + ("+bonus" if on else "")
    return None


class ParseTests(unittest.TestCase):
    def test_accepts_common_formats_and_skips_blank_rows(self):
        self.assertEqual(parse_lines(" 45, 1 14\t30 32 43 \n\n9-15-20-23-26-31\n", "649"),
                         [(1, 14, 30, 32, 43, 45), (9, 15, 20, 23, 26, 31)])

    def test_rejects_bad_lines_with_the_row_number(self):
        cases = {"1 2 3 4 5": "Line 1", "1 2 3 4 5 5": "Line 1", "1 2 3 4 5 50": "Line 1",
                 "1 2 3 4 5 6\nseven": "Line 2", "": "at least one", "\n".join(["1 2 3 4 5 6"] * 101): "at most"}
        for text, message in cases.items():
            with self.subTest(text=text[:20]), self.assertRaisesRegex(ValueError, message):
                parse_lines(text, "649")
        with self.assertRaisesRegex(ValueError, "7 different numbers from 1 to 52"):
            parse_lines("1 2 3 4 5 6", "max")


class TierTests(unittest.TestCase):
    def test_649_tiers_agree_with_the_results_checker(self):
        draw = OfficialDraw(date(2026, 11, 7), tuple(DRAW_649["numbers"]), 6, (), "White", (), "")
        rng = random.Random(649)
        lines = [tuple(sorted(rng.sample(range(1, 50), 6))) for _ in range(3000)]
        # Make sure every tier, with and without the bonus, actually occurs.
        lines += [(9, 15, 20, 23, 26, 31), (6, 9, 15, 20, 23, 26), (1, 9, 15, 20, 23, 26), (1, 2, 9, 15, 20, 23),
                  (1, 2, 3, 9, 15, 20), (1, 2, 6, 9, 15, 40), (1, 2, 3, 4, 9, 15), (1, 2, 3, 6, 9, 40)]
        seen = set()
        for line in lines:
            label = tier_for("649", line, DRAW_649["numbers"], 6)
            self.assertEqual(label or "", classify(line, draw)[0], line)
            seen.add(label)
        self.assertEqual(seen - {None}, {"6/6", "5/6+bonus", "5/6", "4/6", "3/6", "2/6+bonus", "2/6"})

    def test_max_tiers_match_the_published_table(self):
        rng = random.Random(52)
        numbers, bonus = DRAW_MAX["numbers"], DRAW_MAX["bonus"]
        lines = [tuple(sorted(rng.sample(range(1, 53), 7))) for _ in range(3000)]
        for hits in range(3, 8):
            for with_bonus in (False, True):
                others = [x for x in range(1, 53) if x not in numbers and x != bonus][: 7 - hits]
                line = numbers[:hits] + others
                if with_bonus and hits < 7:
                    line = line[:-1] + [bonus]
                lines.append(tuple(sorted(line)))
        seen = set()
        for line in lines:
            label = tier_for("max", line, numbers, bonus)
            self.assertEqual(label, max_rule(line, numbers, bonus), line)
            seen.add(label)
        self.assertEqual(len(seen - {None}), 9)


class CheckTests(unittest.TestCase):
    def test_ken_line_from_november_7_has_no_prize(self):
        report = check_ticket("649", DRAW_649, [(1, 14, 30, 32, 43, 45)])
        self.assertFalse(report["any_win"])
        self.assertEqual(report["lines"][0]["matched_count"], 0)
        self.assertFalse(report["gold_ball"]["checked"])
        self.assertEqual(report["gold_ball"]["winning_numbers"], ["42827458-02"])
        self.assertTrue(any("Gold Ball number" in n for n in report["notes"]))

    def test_649_prizes_gold_ball_and_super_draw(self):
        lines = [(9, 15, 20, 23, 26, 31), (1, 2, 6, 9, 15, 40)]
        report = check_ticket("649", DRAW_649, lines, "42827458-02")
        self.assertEqual([r["tier"] for r in report["lines"]], ["6/6", "2/6+bonus"])
        self.assertEqual(report["lines"][1]["prize"], "$5")
        self.assertTrue(report["gold_ball"]["won"])
        self.assertEqual(report["gold_ball"]["kind"], "Gold Ball $1,000,000")
        self.assertTrue(any("Pools Fund" not in w and "never shared" in w for w in report["wins"]))
        jackpot = check_ticket("649", {**DRAW_649, "ball_drawn": "Gold"}, lines[:1], "4282745802")
        self.assertEqual(jackpot["gold_ball"]["kind"], "Gold Ball jackpot")
        superdraw = check_ticket("649", DRAW_649, [(1, 2, 3, 4, 5, 7)], "04569329-01")
        self.assertEqual(superdraw["gold_ball"]["super_draw_prizes"], ["$10,000.00"])
        self.assertTrue(superdraw["any_win"])
        with self.assertRaises(ValueError):
            check_ticket("649", DRAW_649, lines, "12345")

    def test_max_extra_draw_exact_matches(self):
        lines = [(6, 7, 25, 35, 39, 42, 47), (1, 2, 12, 13, 16, 25, 34), (10, 23, 32, 1, 2, 3, 4)]
        report = check_ticket("max", DRAW_MAX, [tuple(sorted(l)) for l in lines])
        self.assertEqual([x["draw"] for x in report["lines"][0]["extras"]], ["MAXMILLIONS"])
        self.assertEqual([x["draw"] for x in report["lines"][1]["extras"]], ["MAXPLUS"])
        self.assertEqual(report["lines"][2]["tier"], "3/7")
        self.assertEqual(report["lines"][2]["prize"], "$6 Free Play")
        self.assertTrue(any("Free Play" in n for n in report["notes"]))
        self.assertTrue(any("Quick Pick" in n for n in report["notes"]))
        self.assertIsNone(report["gold_ball"])
        with self.assertRaises(ValueError):
            check_ticket("max", DRAW_MAX, lines[:1], "05426542-01")

    def test_pool_prize_points_to_breakdown(self):
        report = check_ticket("649", DRAW_649, [(1, 2, 9, 15, 20, 23)])
        self.assertEqual(report["lines"][0]["tier"], "4/6")
        self.assertTrue(any(DRAW_649["details_url"] in n for n in report["notes"]))


if __name__ == "__main__":
    unittest.main()
