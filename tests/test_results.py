"""Results checker tests on synthetic pages that mirror the WCLC layout (no network)."""
import csv
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from bclc.results import (OfficialDraw, Ticket, build_log, check_ticket, classify, gold_digits,
                          load_tickets, parse_results, run_check)


def block(day_text, numbers, bonus, gold=(("05426542-01", "$1,000,000.00"),), ball="White", supers=()):
    balls = "".join(f'<li class="pastWinNumber">{n}</li>\n' for n in numbers)
    rows = "".join(f'<tr><td><div class="pastWinNumGPDNumber">{n}</div></td>\n'
                   f'<td><div class="pastWinNumGPDPrize">\n\t{p}\n</div></td></tr>' for n, p in gold)
    extra = ""
    if supers:
        extra = " SUPER DRAWS <table>" + "".join(
            f'<tr><td><div class="pastWinNumGPDNumber">{n}</div></td>\n'
            f'<td><div class="pastWinNumGPDPrize">\n{p}\n</div></td></tr>' for n, p in supers) + "</table>"
    return (f'<div class="pastWinNum"><div class="pastWinNumSidebar">'
            f'<div class="pastWinNumPrizeBreakdown" rel="/lotto-649-prize-details.htm?drawNumber=1">x</div></div>'
            f'<div class="pastWinNumDate">\n<h4>\n\t\t{day_text}\n</h4></div>'
            f'<ul class="pastWinNumbers">{balls}<li class="pastWinNumberBonus">'
            f'<span class="pastWinNumberBonusText">Bonus</span>{bonus}</li></ul>'
            f'<table>{rows}</table><div class="ballpos">{ball}</div>{extra}')


PAGE = "<html>" + block("Wednesday, November 04, 2026", (1, 3, 22, 28, 36, 41), 48) + block(
    "Saturday, October 31, 2026", (1, 3, 4, 15, 16, 37), 26, (("26063169-01", "$1,000,000.00"),),
    supers=(("04569329-01", "$10,000.00"), ("04773250-02", "$10,000.00"))) + "</html>"


def draw(numbers=(1, 3, 22, 28, 36, 41), bonus=48, gold=(("0542654201", "$1,000,000.00"),), ball="White",
         supers=(), day=date(2026, 11, 4)):
    return OfficialDraw(day, tuple(numbers), bonus, gold, ball, supers, "url")


class ParseTests(unittest.TestCase):
    def test_parses_numbers_bonus_gold_ball_and_super_draws(self):
        draws = parse_results(PAGE)
        self.assertEqual([d.day for d in draws], [date(2026, 11, 4), date(2026, 10, 31)])
        self.assertEqual(draws[0].numbers, (1, 3, 22, 28, 36, 41))
        self.assertEqual(draws[0].bonus, 48)
        self.assertEqual(draws[0].gold_ball, (("0542654201", "$1,000,000.00"),))
        self.assertEqual(draws[0].ball_drawn, "White")
        self.assertEqual(draws[0].super_draws, ())
        self.assertEqual(draws[1].gold_ball, (("2606316901", "$1,000,000.00"),))
        self.assertEqual([n for n, _ in draws[1].super_draws], ["0456932901", "0477325002"])
        self.assertTrue(draws[0].details_url.startswith("https://www.wclc.com/lotto-649-prize-details.htm"))

    def test_layout_change_fails_loudly(self):
        for broken in ("<html>nothing</html>",
                       block("Wednesday, November 04, 2026", (1, 3, 22, 28, 36), 48),
                       block("Wednesday, November 04, 2026", (1, 3, 22, 28, 36, 41), 41)):
            with self.subTest(page=broken[:40]), self.assertRaises(ValueError):
                parse_results(broken)


class CheckTests(unittest.TestCase):
    def test_every_prize_tier(self):
        d = draw()
        cases = {(1, 3, 22, 28, 36, 41): "6/6", (1, 3, 22, 28, 36, 48): "5/6+bonus",
                 (1, 3, 22, 28, 36, 49): "5/6", (1, 3, 22, 28, 48, 49): "4/6",
                 (1, 3, 22, 47, 48, 49): "3/6", (1, 3, 45, 47, 48, 49): "2/6+bonus",
                 (1, 3, 44, 45, 46, 47): "2/6", (1, 44, 45, 46, 47, 48): "", (2, 4, 5, 6, 7, 8): ""}
        for line, tier in cases.items():
            with self.subTest(line=line):
                self.assertEqual(classify(line, d)[0], tier)

    def test_gold_ball_and_super_draw_exact_match(self):
        d = draw(supers=(("0456932901", "$10,000.00"),))
        loser = Ticket(d.day, (2, 4, 5, 6, 7, 8), "0542654202", "yes")
        self.assertFalse(check_ticket(loser, d)[2])
        gold = Ticket(d.day, (2, 4, 5, 6, 7, 8), gold_digits("05426542-01"), "yes")
        text, prize, won = check_ticket(gold, d)
        self.assertTrue(won)
        self.assertIn("Gold Ball $1M", text)
        self.assertEqual(prize, "$1,000,000.00")
        jackpot = check_ticket(gold, draw(ball="Gold"))[0]
        self.assertIn("Gold Ball JACKPOT", jackpot)
        sup = Ticket(d.day, (2, 4, 5, 6, 7, 8), "0456932901", "yes")
        self.assertIn("Super Draw", check_ticket(sup, d)[0])

    def test_unconfirmed_and_missing_gold_ball_are_labelled(self):
        text, _, won = check_ticket(Ticket(date(2026, 11, 4), (1, 3, 22, 47, 48, 49), "", "unconfirmed"), draw())
        self.assertTrue(won)
        self.assertTrue(text.startswith("(only if this ticket was bought)"))
        self.assertIn("not checked", text)


class LogTests(unittest.TestCase):
    def test_one_row_per_day_with_pending_and_missing_draws(self):
        draws = {date(2026, 11, 4): draw()}
        tickets = [Ticket(date(2026, 11, 4), (1, 3, 22, 47, 48, 49), "", "yes"),
                   Ticket(date(2026, 11, 7), (1, 14, 30, 32, 43, 45), "", "unconfirmed")]
        rows, wins = build_log(tickets, draws, date(2026, 10, 31), date(2026, 11, 7), "now")
        self.assertEqual([r[0] for r in rows], [f"2026-{m}" for m in
                         ("10-31", "11-01", "11-02", "11-03", "11-04", "11-05", "11-06", "11-07")])
        self.assertIn("will recheck", rows[0][9])          # Oct 31 draw missing from the data
        self.assertIn("No Lotto 6/49 draw", rows[1][9])
        self.assertIn("3/6", rows[4][9])
        self.assertIn("results not posted", rows[7][9])     # today's draw
        self.assertEqual(len(wins), 1)

    def test_ticket_file_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "t.csv"
            for bad in ('2026-11-06,1 14 30 32 43 45,,yes',          # Friday
                        '2026-11-07,1 14 30 32 43,,yes',              # five numbers
                        '2026-11-07,1 14 30 32 43 50,,yes',           # out of range
                        '2026-11-07,1 14 30 32 43 45,12345,yes',      # short Gold Ball
                        '2026-11-07,1 14 30 32 43 45,,maybe',         # bad status
                        '11/07/2026,1 14 30 32 43 45,,yes'):          # date format
                path.write_text("draw_date,numbers,gold_ball,purchased\n" + bad + "\n", encoding="utf-8")
                with self.subTest(row=bad), self.assertRaises(ValueError):
                    load_tickets(path)
            path.write_text("draw_date,numbers,gold_ball,purchased\n2026-11-07,45 1 14 30 32 43,05426542-01,yes\n",
                            encoding="utf-8")
            self.assertEqual(load_tickets(path)[0], Ticket(date(2026, 11, 7), (1, 14, 30, 32, 43, 45),
                                                           "0542654201", "yes"))

    def test_run_is_repeatable_and_keeps_draws_that_leave_the_page(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tickets = root / "my_tickets.csv"
            tickets.write_text("draw_date,numbers,gold_ball,purchased\n"
                               "2026-10-31,1 3 4 40 41 42,04569329-01,yes\n", encoding="utf-8")
            first = run_check(tickets, root / "cache.csv", root / "log.csv", date(2026, 11, 5), PAGE)
            self.assertEqual(len(first["rows"]), 6)
            self.assertEqual(len(first["wins"]), 1)          # 3/6 plus a $10,000 Super Draw match
            self.assertIn("$10 + $10,000.00", first["wins"][0])
            # Later the page no longer lists Oct 31; the saved copy still answers for it.
            later = block("Saturday, November 07, 2026", (5, 6, 7, 8, 9, 10), 11)
            second = run_check(tickets, root / "cache.csv", root / "log.csv", date(2026, 11, 8), later)
            self.assertEqual(len(second["rows"]), 9)
            self.assertEqual(second["wins"], first["wins"])
            with (root / "log.csv").open(encoding="utf-8-sig") as stream:
                self.assertEqual(len(list(csv.reader(stream))), 10)   # header + 9 days

    def test_log_locked_by_another_program_gives_a_clear_message(self):
        from unittest import mock
        from bclc import results
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(Path, "open", side_effect=PermissionError(13, "denied")):
                with self.assertRaisesRegex(ValueError, "open in another program"):
                    results._write_csv(Path(folder) / "log.csv", ["a"], [["1"]])

    def test_cli_check_with_saved_page(self):
        from bclc.cli import main
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "page.html").write_text(PAGE, encoding="utf-8")
            (root / "t.csv").write_text("draw_date,numbers,gold_ball,purchased\n"
                                        "2026-11-07,1 14 30 32 43 45,,unconfirmed\n", encoding="utf-8")
            out = StringIO()
            with redirect_stdout(out):
                code = main(["check", "--tickets", str(root / "t.csv"), "--cache", str(root / "c.csv"),
                             "--log", str(root / "l.csv"), "--html-file", str(root / "page.html"),
                             "--today", "2026-11-07"])
            text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("results not posted yet", text)
        self.assertIn("WINS: none found", text)
        self.assertIn("Gold Ball number", text)


if __name__ == "__main__":
    unittest.main()
