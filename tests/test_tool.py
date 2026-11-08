"""Independent exhaustive and mathematical regression tests (standard library)."""
import csv
from datetime import date
from fractions import Fraction
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from itertools import combinations, product
from math import comb
from pathlib import Path
import random
import tempfile
import unittest

from bclc.analysis import analyze, holm_adjust, sum_probabilities
from bclc.compare import compare_week, draw_comparison
from bclc.games import GAMES, Game, Draw, load_history, load_receipt, money_cents
from bclc.generate import (build_wheel, coverage, filter_reasons, generate_coverage,
                           strict_wheel_obstruction, verify_wheel)
from bclc.odds import (Tier, UnionCounter, WorkLimit, exact_tier_probability,
                       iid_quick_pick_series, single_probability, series_probability)


def brute_probability(game, lines, tier):
    hit, total = 0, 0
    for main in combinations(range(1, game.n + 1), game.k):
        for bonus in set(range(1, game.n + 1)) - set(main):
            total += 1
            hit += any(len(set(line) & set(main)) == tier.matches and
                       (tier.bonus is None or (bonus in line) == tier.bonus) for line in lines)
    return Fraction(hit, total)


class ExactOddsTests(unittest.TestCase):
    def test_every_tier_against_enumeration_with_overlap_and_duplicates(self):
        game = Game("max", "Toy", 9, 4, 600, 4)
        portfolios = [[], [(1, 2, 3, 4)], [(1, 2, 3, 4), (1, 2, 5, 6)],
                      [(1, 2, 3, 4), (1, 2, 3, 5), (1, 5, 7, 9), (1, 2, 3, 4)],
                      [(1, 2, 3, 4), (1, 2, 3, 5), (6, 7, 8, 9)],
                      [(1, 2, 3, 4), (5, 6, 7, 8)]]
        for lines in portfolios:
            for matches in range(game.k + 1):
                for bonus in (None, False, True) if matches < game.k else (None, False):
                    tier = Tier("toy", matches, bonus, "test")
                    with self.subTest(lines=lines, matches=matches, bonus=bonus):
                        self.assertEqual(exact_tier_probability(lines, game, tier, 0),
                                         brute_probability(game, lines, tier))

    def test_random_unions_of_intervals_against_exhaustive_draws(self):
        rng = random.Random(40)
        for _ in range(80):
            n, k = 10, rng.randint(1, 5)
            events = []
            for _ in range(rng.randint(1, 6)):
                values = rng.sample(range(n), rng.randint(1, n))
                lo = rng.randint(0, k)
                hi = rng.randint(lo, k)
                events.append((sum(1 << x for x in values), lo, hi))
            expected = sum(any(lo <= (sum(1 << x for x in draw) & mask).bit_count() <= hi
                               for mask, lo, hi in events) for draw in combinations(range(n), k))
            self.assertEqual(UnionCounter(0).count(n, k, events), expected)

    def test_known_jackpot_odds_and_prize_partition(self):
        from bclc.odds import TIERS
        self.assertEqual(comb(49, 6), 13_983_816)
        self.assertEqual(comb(52, 7), 133_784_560)
        for key, game in GAMES.items():
            actual = sum(single_probability(game, tier) for tier in TIERS[key])
            minimum = 2 if key == "649" else 3
            expected = sum(Fraction(comb(game.k, r) * comb(game.n - game.k, game.k - r),
                                    comb(game.n, game.k)) for r in range(minimum, game.k + 1))
            self.assertEqual(actual, expected)

    def test_additional_draw_preissue_model_against_complete_enumeration(self):
        # All random lines and all winning combinations are enumerated in a
        # three-outcome toy lottery; catches dependency across extra draws.
        outcomes = 3
        for manual in range(3):
            for qps in range(3):
                for draws in range(1, 4):
                    hit, total = 0, 0
                    for random_lines in product(range(outcomes), repeat=qps):
                        owned = set(range(manual)) | set(random_lines)
                        for winning in product(range(outcomes), repeat=draws):
                            total += 1
                            hit += bool(owned & set(winning))
                    self.assertEqual(iid_quick_pick_series(manual, qps, outcomes, draws), Fraction(hit, total))
        self.assertEqual(series_probability(4, 52, 7, 2), 1 - (1 - Fraction(4, comb(52, 7))) ** 2)

    def test_work_limit_does_not_return_an_approximation(self):
        with self.assertRaises(WorkLimit):
            UnionCounter(1).count(12, 5, [(0b111111, 2, 2), (0b11110000, 2, 2),
                                         (0b111100000000, 2, 2)])

    def test_large_fraction_serialization_retains_every_digit(self):
        from bclc.odds import integer_string
        self.assertEqual(integer_string(10 ** 5000 + 123), "1" + "0" * 4997 + "123")


class DataTests(unittest.TestCase):
    def write_csv(self, folder, text):
        path = Path(folder) / "input.csv"
        path.write_text(text, encoding="utf-8-sig")
        return str(path)

    def test_money_is_exact_and_never_rounds_up(self):
        self.assertEqual(money_cents("29.99"), 2999)
        self.assertEqual(money_cents("0"), 0)
        for text in ("-1", "NaN", "Infinity", "6.001", "$30"):
            with self.assertRaises(ValueError):
                money_cents(text)

    def test_csv_regimes_and_bonus_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_csv(folder, 'date,numbers,bonus\n2026-04-10,"1 2 3 4 5 6 50",7\n'
                                  '2026-04-14,"1 2 3 4 5 51 52",7\n')
            draws = load_history(path, GAMES["max"])
            self.assertEqual([d.pool for d in draws], [50, 52])
            for text in ('date,numbers,bonus\n2026-04-10,"1 2 3 4 5 6 52",7\n',
                         'date,numbers,bonus\n2026-04-14\n',
                         'date,numbers,bonus\n2026-04-14,"1 2 3 4 5 6 7",7\n',
                         'date,numbers,bonus\n2026-04-14,"1 1 3 4 5 6 7",8\n',
                         'date,numbers,bonus\n2026-04-14,"1 2 3 4 5 6 7",8\n'
                         '2026-04-14,"2 3 4 5 6 7 8",9\n'):
                with self.assertRaises(ValueError):
                    load_history(self.write_csv(folder, text), GAMES["max"])

    def test_receipt_requires_every_issued_line(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_csv(folder, 'play,line,numbers\n1,1,"1 2 3 4 5 6 7"\n')
            with self.assertRaises(ValueError):
                load_receipt(path, GAMES["max"])
            with open(path, "a", encoding="utf-8") as stream:
                for i in range(2, 5):
                    stream.write(f'1,{i},"1 2 3 4 5 6 {i+7}"\n')
            self.assertEqual(len(load_receipt(path, GAMES["max"])), 4)

    def test_sum_distribution_and_holm(self):
        distribution = sum_probabilities(9, 4)
        expected = {}
        for line in combinations(range(1, 10), 4):
            band = sum(line) // 25 * 25
            expected[band] = expected.get(band, 0) + 1 / comb(9, 4)
        for band, p in expected.items():
            self.assertAlmostEqual(distribution[band], p)
        self.assertEqual(holm_adjust([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])

    def test_biased_history_is_detected_and_gaps_are_censored(self):
        draws = [Draw(date(2026, 1, i + 1), (1, 2, 3, 4, 5, 6), 7, 49) for i in range(25)]
        report = analyze(draws, GAMES["649"], simulations=199, seed=6)
        self.assertTrue(report["regimes"][0]["tests"][0]["significant"])
        rows = report["regimes"][0]["frequencies"]
        self.assertEqual(rows[0]["gap_draws"], 0)
        self.assertEqual(rows[48]["gap_draws"], 25)
        self.assertTrue(rows[48]["gap_is_lower_bound"])


class GenerationTests(unittest.TestCase):
    def test_filters_and_pairwise_constraint(self):
        game = GAMES["649"]
        lines = generate_coverage(game, 8, seed=5, candidates=150, restarts=2)
        self.assertTrue(lines)
        self.assertLessEqual(len(lines), 8)
        self.assertTrue(all(not filter_reasons(line) for line in lines))
        self.assertLessEqual(coverage(lines)["maximum_pairwise_overlap"], 2)
        self.assertEqual(coverage(lines)["duplicate_lines"], 0)
        self.assertTrue(filter_reasons((2, 4, 6, 33, 39, 46)))
        self.assertTrue(filter_reasons((1, 3, 9, 32, 33, 34)))
        self.assertTrue(filter_reasons((1, 2, 4, 8, 16, 32)))
        self.assertTrue(filter_reasons(lines[0], {lines[0]}))

    def test_strict_full_wheel_is_impossible_for_all_requested_sizes(self):
        for k in (6, 7):
            for v in range(10, 16):
                self.assertIsNotNone(strict_wheel_obstruction(v, k))

    def test_impossible_popularity_filtered_wheel_is_not_guaranteed(self):
        result = build_wheel(GAMES["649"], tuple(range(1, 11)), 100, allow_overlap=True)
        self.assertFalse(result["guaranteed"])
        self.assertTrue(result["uncoverable_triples"])

    def test_independent_wheel_verifier_and_insufficient_budget(self):
        favourites = (2, 4, 8, 9, 22, 24, 30, 31, 33, 34, 43, 45, 48)
        full = build_wheel(GAMES["649"], favourites, 100, allow_overlap=True, restarts=2)
        self.assertTrue(full["guaranteed"])
        self.assertEqual(full["guaranteed"], verify_wheel(full["lines"], favourites)["guaranteed"])
        partial = build_wheel(GAMES["649"], favourites, 1, allow_overlap=True, restarts=2)
        self.assertFalse(partial["guaranteed"])
        self.assertLessEqual(len(partial["lines"]), 1)


class ComparisonTests(unittest.TestCase):
    def test_unknown_sales_remain_unknown_and_weeks_are_separated(self):
        snapshot = {"as_of": "2026-11-07", "draws": [
            {"game": "649", "date": "2026-11-07", "gold_balls": 17, "gold_issued": None},
            {"game": "max", "date": "2026-11-10", "jackpot_cad": 10_000_000, "maxmillions": 0, "maxplus": 10}]}
        report = compare_week(snapshot, 3000, date(2026, 11, 7))
        self.assertEqual(len(report["draws"]), 1)
        self.assertIsNone(report["draws"][0]["gold_any_million_category"])
        self.assertIn("No cross-game ranking", report["ranking"])

    def test_gold_ball_mutually_exclusive_awards(self):
        result = draw_comparison({"game": "649", "date": "2026-11-07", "gold_balls": 1,
                                  "gold_issued": 5_000_000}, 3000)
        self.assertEqual(result["gold_exact_1m"]["numerator"], "0")
        self.assertEqual(result["gold_any_million_category"], result["gold_jackpot"])

    def test_invalid_maxmillions_and_budget_floor(self):
        draw = {"game": "max", "date": "2026-11-10", "jackpot_cad": 10_000_000,
                "maxmillions": 0, "maxplus": 10}
        self.assertEqual(draw_comparison(draw, 599)["plays"], 0)
        draw["maxmillions"] = 1
        with self.assertRaises(ValueError):
            draw_comparison(draw, 600)


class CommandTests(unittest.TestCase):
    def test_budget_floor_and_work_limit_are_saved_with_explicit_exit_code(self):
        import json
        from bclc.cli import main
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(StringIO()):
            code = main(["generate", "--game", "649", "--budget", "29.99",
                         "--allow-missing-history", "--candidates", "100", "--restarts", "1",
                         "--exact-work-limit", "1", "--out", folder])
            report = json.loads((Path(folder) / "report.json").read_text())
            self.assertEqual(code, 4)
            self.assertLessEqual(report["cost_cents"], 2999)
            self.assertEqual(report["cost_cents"], 300 * len(report["lines"]))
            self.assertEqual(report["unspent_cents"], 2999 - report["cost_cents"])
            self.assertFalse(report["odds"]["exact_all_main_tiers_complete"])

    def test_wheel_cli_does_not_claim_guarantee_outside_budget(self):
        import json
        from bclc.cli import main
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            code = main(["wheel", "--game", "649", "--budget", "3", "--allow-missing-history",
                         "--favourites", "2 4 8 9 22 24 30 31 33 34 43 45 48",
                         "--allow-wheel-overlap", "--restarts", "1", "--out", folder])
            report = json.loads((Path(folder) / "report.json").read_text())
            self.assertEqual(code, 3)
            self.assertFalse(report["wheel"]["guaranteed"])
            self.assertEqual(report["cost_cents"], 300)


class VerifiedRulesTests(unittest.TestCase):
    """Rules read from the rendered PlayNow prize tables on 2026-11-07."""

    # Published "1 in X" per $3 (6/49) or per $6 four-line (Max) play, highest tier first.
    PUBLISHED = {"649": [13_983_816, 2_330_636, 55_492, 1_033, 56.7, 81.2, 8.3],
                 "max": [33_446_140, 4_778_020, 108_591, 36_197, 1_684, 1_010, 72.2, 72.2, 7.0]}
    PUBLISHED_ANY_PRIZE = {"649": 6.6, "max": 5.8}

    def test_tier_definitions_reproduce_published_odds(self):
        from bclc.odds import TIERS
        for key, game in GAMES.items():
            self.assertEqual(len(TIERS[key]), len(self.PUBLISHED[key]))
            total = Fraction(0)
            for tier, published in zip(TIERS[key], self.PUBLISHED[key]):
                single = single_probability(game, tier)
                total += single
                # PlayNow's per-play figure is the expected-count approximation 1/(lines * p).
                per_play = float(1 / (game.lines_per_play * single))
                with self.subTest(game=key, tier=tier.label):
                    self.assertAlmostEqual(per_play, published, delta=1 if published >= 100 else 0.05)
            any_prize = float(1 / (game.lines_per_play * total))
            self.assertAlmostEqual(any_prize, self.PUBLISHED_ANY_PRIZE[key], delta=0.05)

    def test_game_constants(self):
        self.assertEqual((GAMES["649"].n, GAMES["649"].k, GAMES["649"].price_cents, GAMES["649"].lines_per_play),
                         (49, 6, 300, 1))
        self.assertEqual((GAMES["max"].n, GAMES["max"].k, GAMES["max"].price_cents, GAMES["max"].lines_per_play),
                         (52, 7, 600, 4))

    def test_maxplus_count_and_jackpot_cap_are_enforced(self):
        from bclc.compare import validate_draw
        base = {"game": "max", "date": "2026-11-10", "jackpot_cad": 10_000_000, "maxmillions": 0, "maxplus": 10}
        validate_draw(dict(base))
        validate_draw(dict(base, jackpot_cad=90_000_000, maxplus=90, maxmillions=5))
        for change in ({"maxplus": 11}, {"maxplus": 9}, {"jackpot_cad": 95_000_000, "maxplus": 90}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_draw(dict(base, **change))

    def test_gold_ball_reports_never_shared_and_classic_shared(self):
        result = draw_comparison({"game": "649", "date": "2026-11-07", "gold_balls": 17,
                                  "gold_issued": 5_000_000}, 3000)
        self.assertIn("Never shared", result["sharing"]["gold_ball"])
        self.assertIn("share", result["sharing"]["classic_jackpot"])
        self.assertEqual(result["gold_receive_at_least_1m"], result["gold_any_million_category"])
        self.assertEqual(result["gold_any_million_category"]["numerator"], "1")
        self.assertEqual(result["gold_any_million_category"]["denominator"], "500000")
        maxrow = draw_comparison({"game": "max", "date": "2026-11-10", "jackpot_cad": 10_000_000,
                                  "maxmillions": 0, "maxplus": 10}, 3000)
        self.assertIn("share", maxrow["sharing"]["maxmillions"])

    def test_history_dates_must_be_iso_and_not_in_future(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "h.csv"
            for bad_date in ("20261104", "2026-11-4", "2999-01-02", "2026/11/04"):
                path.write_text(f'date,numbers,bonus\n{bad_date},"1 3 22 28 36 41",48\n', encoding="utf-8")
                with self.subTest(date=bad_date), self.assertRaises(ValueError):
                    load_history(str(path), GAMES["649"])
            path.write_text('date,numbers,bonus\n2026-11-04,"1 3 22 28 36 41",48\n', encoding="utf-8")
            self.assertEqual(load_history(str(path), GAMES["649"])[0].numbers, (1, 3, 22, 28, 36, 41))


class InterfaceTests(unittest.TestCase):
    def test_top_level_help_describes_every_command(self):
        from bclc.cli import make_parser
        text = make_parser().format_help()
        for command in ("analyze", "generate", "wheel", "odds", "compare"):
            self.assertRegex(text, rf"\n\s+{command}\s+\S")

    def test_partial_wheel_report_lists_uncovered_triples(self):
        from bclc.cli import main
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            code = main(["wheel", "--game", "649", "--budget", "6", "--allow-missing-history",
                         "--favourites", "2 4 8 9 22 24 30 31 33 34 43 45 48",
                         "--allow-wheel-overlap", "--restarts", "1", "--out", folder])
            text = (Path(folder) / "report.txt").read_text(encoding="utf-8")
        self.assertEqual(code, 3)
        self.assertIn("NO GUARANTEE", text)
        self.assertIn("Uncovered triples (", text)

    def test_max_plan_never_exceeds_budget_and_marks_pending_quick_picks(self):
        import json
        from bclc.cli import main
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(StringIO()):
            code = main(["generate", "--game", "max", "--budget", "17.99", "--allow-missing-history",
                         "--candidates", "100", "--restarts", "1", "--out", folder])
            report = json.loads((Path(folder) / "report.json").read_text(encoding="utf-8"))
            with (Path(folder) / "tickets.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(code, 0)
        self.assertEqual(report["paid_plays"], 2)
        self.assertEqual(report["cost_cents"], 1200)
        self.assertEqual(report["unspent_cents"], 599)
        self.assertEqual(sum(row["kind"] == "issued_quick_pick_pending" for row in rows), 6)
        self.assertEqual(report["odds"]["pending_quick_picks"], 6)


if __name__ == "__main__":
    unittest.main()
