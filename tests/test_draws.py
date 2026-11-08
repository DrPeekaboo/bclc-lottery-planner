"""Live draw-information builder, tested on synthetic pages in WCLC's layout (no network)."""
from datetime import date, datetime, timezone
import unittest

from bclc.compare import compare_week
from bclc.draws import (bc_now, build_snapshot, gold_jackpot_for, next_draw_after, parse_max_results,
                        parse_upcoming)

NOW = datetime(2026, 11, 8, 5, 0, tzinfo=timezone.utc)   # 10:00 PM BC on Saturday Nov 7


def play_now(day649="Saturday, November 7, 2026", gold_m=36, balls=17,
             day_max="Tuesday, November 10, 2026", max_m=10, extras="10 x \n $100,000"):
    return (f'<div class="nextJackpot"><!-- start next jackpot details l649--><div>'
            f'<div class="nextJackpotPrizeAmount">{gold_m}</div><div class="nextJackpotAmountMillion">Million</div>'
            f'<div class="nextJackpotGuaranteedPrize"><b>{balls}</b> \n Balls Remaining</b></div>'
            f'<div class="nextJackpotDateDate">{day649}</div></div><!-- end next jackpot details l649-->'
            f'<!-- start next jackpot details lmax--><div><div class="nextJackpotPrizeAmount">{max_m}</div>'
            f'<div class="nextJackpotAmountMillion">Million</div><div class="nextJackpotmaxplusDraw">'
            f'<span></span> {extras} </div><div class="nextJackpotDateDate">{day_max}</div></div>'
            f'<!-- end next jackpot details lmax--></div>')


def result_649(day_text, ball):
    balls = "".join(f'<li class="pastWinNumber">{n}</li>' for n in (1, 3, 22, 28, 36, 41))
    return (f'<div class="pastWinNum"><div class="pastWinNumDate">\n<h4>\n{day_text}\n</h4></div>'
            f'<ul class="pastWinNumbers">{balls}<li class="pastWinNumberBonus">'
            f'<span class="pastWinNumberBonusText">Bonus</span>48</li></ul>'
            f'<table><tr><td><div class="pastWinNumGPDNumber">05426542-01</div></td>\n'
            f'<td><div class="pastWinNumGPDPrize">\n$1,000,000.00\n</div></td></tr></table>'
            f'<div class="ballpos">{ball}</div>')


def page_649(*draws):
    return "<html>" + "".join(result_649(day, ball) for day, ball in draws) + "</html>"


def result_max(day_text, maxplus, maxmillions):
    main = "".join(f'<li class="pastWinNumber">{n}</li>' for n in (10, 23, 32, 43, 46, 48, 51))
    plus = "".join('<ul class="pastWinNumbers">' + "".join(
        f'<li class="winNumHomeNumberMaxPlus">{n}</li>' for n in range(1, 8)) + "</ul>" for _ in range(maxplus))
    if maxmillions:
        mm = ('<div class="pastWinNumMMGroup">' + "".join('<ul class="pastWinNumbers">' + "".join(
            f'<li class="pastWinNumMM">{n}</li>' for n in range(1, 8)) + "</ul>" for _ in range(maxmillions)) + "</div>")
    else:
        mm = '<div class="pastWinNumMMNo">No MAXMILLIONS draws this week</div>'
    return (f'<div class="pastWinNumDate">\n<h4>\n{day_text}\n</h4></div><ul class="pastWinNumbers">{main}'
            f'<li class="pastWinNumberBonus"><span class="pastWinNumberBonusText">Bonus</span>30</li></ul>'
            f'<!-- MAXPLUS-->{plus}<!-- MAXMILLIONS-->{mm}')


def page_max(*draws):
    return "<html>" + "".join(result_max(*d) for d in draws) + "</html>"


PAGE_649 = page_649(("Saturday, November 07, 2026", "White"), ("Wednesday, November 04, 2026", "White"))
PAGE_MAX = page_max(("Friday, November 06, 2026", 50, 2), ("Tuesday, November 03, 2026", 40, 0))


def by_key(snapshot):
    return {(d["game"], d["date"]): d for d in snapshot["draws"]}


class ParserTests(unittest.TestCase):
    def test_upcoming_box(self):
        box = parse_upcoming(play_now())
        self.assertEqual(box["649"], {"date": date(2026, 11, 7), "gold_jackpot_cad": 36_000_000, "gold_balls": 17})
        self.assertEqual(box["max"]["jackpot_cad"], 10_000_000)
        self.assertEqual((box["max"]["maxplus"], box["max"]["maxmillions"]), (10, None))
        big = parse_upcoming(play_now(max_m=60, extras="4 x $1 Million 60 x $100,000"))["max"]
        self.assertEqual((big["maxplus"], big["maxmillions"]), (60, 4))

    def test_max_results_counts(self):
        results = parse_max_results(PAGE_MAX)
        self.assertEqual([(r.day, r.maxplus, r.maxmillions) for r in results],
                         [(date(2026, 11, 6), 50, 2), (date(2026, 11, 3), 40, 0)])

    def test_layout_changes_fail_loudly(self):
        for bad in ("<html>nothing</html>", play_now().replace("Balls Remaining", "Balls")):
            with self.subTest(page=bad[:30]), self.assertRaises(ValueError):
                parse_upcoming(bad)
        with self.assertRaises(ValueError):
            parse_max_results("<html></html>")
        with self.assertRaises(ValueError):
            build_snapshot(NOW, PAGE_649, PAGE_MAX, "<html>changed</html>")

    def test_rules_helpers_and_bc_time(self):
        self.assertEqual(gold_jackpot_for(17), 36_000_000)
        self.assertEqual(gold_jackpot_for(30), 10_000_000)
        self.assertEqual(gold_jackpot_for(1), 68_000_000)
        self.assertEqual(next_draw_after(date(2026, 11, 7), "649"), date(2026, 11, 11))
        self.assertEqual(next_draw_after(date(2026, 11, 6), "max"), date(2026, 11, 10))
        self.assertEqual(bc_now(NOW).date(), date(2026, 11, 7))
        self.assertEqual(build_snapshot(NOW, PAGE_649, PAGE_MAX, play_now())["fetched_at"], "2026-11-07 22:00 BC")


class SnapshotTests(unittest.TestCase):
    def test_stale_gold_box_after_white_ball(self):
        snap = by_key(build_snapshot(NOW, PAGE_649, PAGE_MAX, play_now()))
        nxt = snap[("649", "2026-11-11")]
        self.assertEqual((nxt["gold_balls"], nxt["gold_jackpot_cad"]), (16, 38_000_000))
        self.assertIn("white ball", nxt["basis"])
        self.assertFalse(nxt["drawn"])
        self.assertTrue(snap[("649", "2026-11-07")]["drawn"])
        self.assertTrue(snap[("max", "2026-11-06")]["drawn"])
        self.assertFalse(snap[("max", "2026-11-10")]["drawn"])
        self.assertEqual(snap[("649", "2026-11-07")]["gold_balls"], 17)
        self.assertEqual(snap[("649", "2026-11-04")]["gold_balls"], 18)

    def test_stale_gold_box_after_gold_ball_resets(self):
        page = page_649(("Saturday, November 07, 2026", "Gold"), ("Wednesday, November 04, 2026", "White"))
        nxt = by_key(build_snapshot(NOW, page, PAGE_MAX, play_now()))[("649", "2026-11-11")]
        self.assertEqual((nxt["gold_balls"], nxt["gold_jackpot_cad"]), (30, 10_000_000))

    def test_fresh_boxes_and_past_max_jackpots(self):
        snap = build_snapshot(NOW, PAGE_649, PAGE_MAX,
                              play_now(day649="Wednesday, November 11, 2026", gold_m=38, balls=16))
        draws = by_key(snap)
        self.assertEqual(draws[("649", "2026-11-11")]["basis"], "published by WCLC")
        self.assertEqual(draws[("649", "2026-11-07")]["gold_balls"], 17)
        self.assertEqual((draws[("max", "2026-11-06")]["jackpot_cad"], draws[("max", "2026-11-06")]["maxmillions"]),
                         (50_000_000, 2))
        self.assertEqual(draws[("max", "2026-11-10")]["maxplus"], 10)
        self.assertEqual(snap["as_of"], "2026-11-07")
        self.assertEqual(snap["mode"], "live")
        self.assertEqual(snap["notes"], [])

    def test_unknown_values_are_left_out_not_guessed(self):
        stale_max = build_snapshot(NOW, PAGE_649, PAGE_MAX, play_now(day_max="Friday, November 6, 2026", max_m=50,
                                                                      extras="2 x $1 Million 50 x $100,000"))
        self.assertNotIn(("max", "2026-11-10"), by_key(stale_max))
        self.assertTrue(any("not been announced" in n for n in stale_max["notes"]))
        no_count = build_snapshot(NOW, PAGE_649, PAGE_MAX, play_now(max_m=60, extras="60 x $100,000"))
        self.assertNotIn(("max", "2026-11-10"), by_key(no_count))
        self.assertTrue(any("MAXMILLIONS prizes is not shown" in n for n in no_count["notes"]))
        with_count = build_snapshot(NOW, PAGE_649, PAGE_MAX, play_now(max_m=60, extras="4 x $1 Million 60 x $100,000"))
        self.assertEqual(by_key(with_count)[("max", "2026-11-10")]["maxmillions"], 4)

    def test_inconsistent_extra_draws_are_rejected(self):
        page = page_max(("Friday, November 06, 2026", 40, 2))   # MAXMILLIONS below a $50M jackpot is impossible
        snap = build_snapshot(NOW, PAGE_649, page, play_now())
        self.assertNotIn(("max", "2026-11-06"), by_key(snap))
        self.assertTrue(any("2026-11-06 left out" in n for n in snap["notes"]))

    def test_comparison_uses_live_note_and_few_thresholds(self):
        many = page_max(*[(f"{day}, {month} {num:02d}, 2026", 20, 0) for day, month, num in (
            ("Friday", "November", 6), ("Tuesday", "November", 3), ("Friday", "October", 30),
            ("Tuesday", "October", 27), ("Friday", "October", 23))])
        snap = build_snapshot(NOW, PAGE_649, many, play_now())
        report = compare_week(snap, 3000, date(2026, 11, 2))
        # In-week Max draws plus the next one; the three October draws are left out.
        self.assertEqual([t["max_draw_date"] for t in report["thresholds"]], ["2026-11-03", "2026-11-06", "2026-11-10"])
        self.assertIn("Read automatically from WCLC", report["limitations"][0])
        timing = {r["date"]: r["timing_relative_to_snapshot"] for r in report["draws"]}
        self.assertEqual(timing["2026-11-07"], "Drawn: results already posted")
        self.assertEqual(sorted(r["date"] for r in report["draws"]),
                         ["2026-11-03", "2026-11-04", "2026-11-06", "2026-11-07"])


if __name__ == "__main__":
    unittest.main()
