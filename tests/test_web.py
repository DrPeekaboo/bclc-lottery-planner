"""Web app tests (FastAPI TestClient). Skipped when the web packages are not installed."""
from datetime import date, timedelta
import os
import random
import time
import unittest

# Tests never reach the network: the saved snapshot is used unless a test injects a fetcher.
os.environ["BCLC_LIVE_DRAWS"] = "0"

try:
    from fastapi.testclient import TestClient
except ImportError:  # the command-line tool needs no packages; only the web app does
    raise unittest.SkipTest("fastapi/httpx not installed; run with .venv\\Scripts\\python")

from bclc.analysis import analyze
from bclc.control import Cancelled
from bclc.games import GAMES, Draw
from bclc.generate import generate_coverage
from bclc.odds import UnionCounter, WorkLimit
from webapp.server import DrawInfo, app, load_snapshot

FAVS = [2, 4, 8, 9, 22, 24, 30, 31, 33, 34, 43, 45, 48]
RECEIPT_MAX = ('play,line,numbers\n1,1,"8 23 32 39 43 44 50"\n1,2,"1 5 10 20 30 40 52"\n'
               '1,3,"2 9 16 24 31 38 46"\n1,4,"3 12 19 28 35 42 49"\n')


def history_csv(rows: int, seed: int = 7) -> str:
    rng = random.Random(seed)
    day, lines = date(1985, 1, 1), ["date,numbers,bonus"]  # 3,000 rows stay in the past
    for _ in range(rows):
        numbers = rng.sample(range(1, 50), 7)
        lines.append(f'{day.isoformat()},"{" ".join(map(str, sorted(numbers[:6])))}",{numbers[6]}')
        day += timedelta(days=3)
    return "\n".join(lines) + "\n"


def wait(client, job, timeout=120):
    deadline = time.time() + timeout
    while job["status"] in ("queued", "running"):
        if time.time() > deadline:
            raise AssertionError(f"job {job['kind']} did not finish")
        time.sleep(0.05)
        job = client.get(f"/api/jobs/{job['id']}").json()
    return job


class WebTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_pages_rules_and_security_headers(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("BCLC Lottery Planner", page.text)
        self.assertIn("default-src 'self'", page.headers["content-security-policy"])
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/healthz").json()["ok"], True)
        rules = self.client.get("/api/rules").json()
        self.assertEqual(rules["verified_on"], "2026-11-07")
        self.assertEqual(rules["games"]["649"]["price"], "$3.00")
        self.assertEqual(len(rules["games"]["max"]["tiers"]), 9)
        self.assertEqual(rules["games"]["max"]["tiers"][0]["published_one_in_per_play"], 33_446_140)
        draws = self.client.get("/api/draws").json()
        self.assertEqual(draws["mode"], "saved")
        self.assertIn("switched off", draws["status"])
        self.assertTrue(draws["draws"])
        self.assertRegex(draws["today"], r"^\d{4}-\d{2}-\d{2}$")

    def test_history_upload_validation_and_visitor_isolation(self):
        bad = self.client.post("/api/history/649", files={"file": ("h.csv", 'date,numbers,bonus\n2026-11-04,"1 2 3 4 5",9\n')})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("CSV row 2", bad.json()["detail"])
        self.assertEqual(self.client.post("/api/history/649", files={"file": ("e.csv", "  ")}).status_code, 400)
        self.assertEqual(self.client.post("/api/history/649", files={"file": ("big.csv", "x" * 1_000_001)}).status_code, 413)
        good = self.client.post("/api/history/649", files={"file": ("mine.csv", history_csv(30))})
        self.assertEqual(good.status_code, 200, good.text)
        self.assertEqual(good.json()["draws"], 30)
        self.assertIn("649", self.client.get("/api/session").json()["history"])
        other = TestClient(app)
        self.assertEqual(other.get("/api/session").json()["history"], {})
        job = self.client.post("/api/jobs/generate", json={"game": "649", "budget": "3"}).json()
        self.assertEqual(other.get(f"/api/jobs/{job['id']}").status_code, 404)
        self.assertEqual(other.get(f"/api/jobs/{job['id']}/tickets.csv").status_code, 404)
        wait(self.client, job)
        self.client.delete("/api/session")
        self.assertEqual(self.client.get("/api/session").json()["history"], {})

    def test_generate_plans_respect_budget_and_export_checklist(self):
        job = wait(self.client, self.client.post("/api/jobs/generate", json={"game": "649", "budget": "30.00"}).json())
        self.assertEqual(job["status"], "done", job)
        plan = job["result"]
        self.assertEqual((plan["paid_plays"], plan["cost"], plan["unspent"]), (10, "$30.00", "$0.00"))
        self.assertLessEqual(plan["coverage"]["maximum_pairwise_overlap"], 2)
        self.assertTrue(plan["odds"]["exact_all_main_tiers_complete"])
        csv_text = self.client.get(f"/api/jobs/{job['id']}/tickets.csv").text
        self.assertEqual(len(csv_text.strip().splitlines()), 11)
        maxjob = wait(self.client, self.client.post("/api/jobs/generate", json={"game": "max", "budget": "$17.99"}).json())
        self.assertEqual((maxjob["result"]["paid_plays"], maxjob["result"]["unspent"]), (2, "$5.99"))
        self.assertEqual(maxjob["result"]["pending_quick_picks"], 6)
        for budget in ("30.001", "-3", "abc"):
            self.assertEqual(self.client.post("/api/jobs/generate", json={"game": "649", "budget": budget}).status_code, 400)
        self.assertEqual(self.client.post("/api/jobs/generate", json={"game": "649", "budget": "3",
                                                                      "use_history": True}).status_code, 400)

    def test_wheel_precheck_overlap_rule_and_verified_guarantee(self):
        check = self.client.post("/api/wheel/precheck", json={"game": "649", "favourites": FAVS}).json()
        self.assertTrue(check["needs_overlap_relaxation"])
        self.assertEqual(check["total_triples"], 286)
        refused = self.client.post("/api/jobs/wheel", json={"game": "649", "budget": "180", "favourites": FAVS})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("Relax the overlap limit", refused.json()["detail"])
        full = wait(self.client, self.client.post("/api/jobs/wheel", json={
            "game": "649", "budget": "180", "favourites": FAVS, "allow_overlap": True}).json())
        self.assertTrue(full["result"]["wheel"]["guaranteed"])
        self.assertEqual(full["result"]["wheel"]["covered_triples"], 286)
        partial = wait(self.client, self.client.post("/api/jobs/wheel", json={
            "game": "649", "budget": "6", "favourites": FAVS, "allow_overlap": True}).json())
        self.assertFalse(partial["result"]["wheel"]["guaranteed"])
        self.assertTrue(partial["result"]["wheel"]["uncovered_triples"])
        self.assertLessEqual(partial["result"]["cost_cents"], 600)
        impossible = self.client.post("/api/wheel/precheck", json={"game": "649", "favourites": list(range(1, 11)),
                                                                  "allow_overlap": True}).json()
        self.assertFalse(impossible["guarantee_possible"])
        self.assertTrue(impossible["uncoverable_triples"])
        self.assertEqual(self.client.post("/api/wheel/precheck", json={"game": "649", "favourites": [1, 2, 3]}).status_code, 400)

    def test_receipt_odds_with_dated_extra_draws(self):
        incomplete = self.client.post("/api/receipt/max", files={"file": ("r.csv", 'play,line,numbers\n1,1,"1 2 3 4 5 6 7"\n')})
        self.assertEqual(incomplete.status_code, 400)
        self.assertIn("incomplete", incomplete.json()["detail"])
        self.assertEqual(self.client.post("/api/receipt/max", files={"file": ("r.csv", RECEIPT_MAX)}).json()["plays"], 1)
        job = wait(self.client, self.client.post("/api/jobs/odds", json={
            "game": "max", "source": "receipt", "budget": "6", "draw_date": "2026-11-10"}).json())
        self.assertEqual(job["status"], "done", job)
        jackpot = job["result"]["tiers"][0]["at_least_one"]
        self.assertEqual((jackpot["numerator"], jackpot["denominator"]), ("1", "33446140"))
        self.assertEqual(job["result"]["additional_draws"]["maxmillions_at_least_one"]["numerator"], "0")
        over = self.client.post("/api/jobs/odds", json={"game": "max", "source": "receipt", "budget": "5.99"})
        self.assertEqual(over.status_code, 400)

    def test_plan_odds_gold_ball_unknown_and_conditional(self):
        plan = wait(self.client, self.client.post("/api/jobs/generate", json={"game": "649", "budget": "3"}).json())
        body = {"game": "649", "source": "plan", "plan_job_id": plan["id"], "draw_date": "2026-11-07"}
        unknown = wait(self.client, self.client.post("/api/jobs/odds", json=body).json())
        self.assertEqual(unknown["result"]["gold_ball"]["status"], "unknown")
        known = wait(self.client, self.client.post("/api/jobs/odds", json={**body, "gold_issued": 5_000_000}).json())
        self.assertEqual(known["result"]["gold_ball"]["any_award"]["denominator"], "5000000")
        self.assertEqual(self.client.post("/api/jobs/odds", json={"game": "max", "source": "plan",
                                                                  "plan_job_id": plan["id"]}).status_code, 400)

    def test_compare_week(self):
        report = self.client.post("/api/compare", json={"budget": "30", "week_of": "2026-11-02"}).json()
        self.assertEqual(sorted(row["date"] for row in report["draws"]), ["2026-11-06", "2026-11-07"])
        self.assertIn("No cross-game ranking", report["ranking"])
        self.assertEqual(self.client.post("/api/compare", json={"budget": "3.333"}).status_code, 400)
        self.assertEqual(self.client.post("/api/compare", json={"budget": ""}).status_code, 422)

    def test_long_analysis_can_be_cancelled_and_reports_progress(self):
        self.client.post("/api/history/649", files={"file": ("big.csv", history_csv(3000))})
        job = self.client.post("/api/jobs/analyze", json={"game": "649", "simulations": 4999}).json()
        time.sleep(0.3)
        self.client.post(f"/api/jobs/{job['id']}/cancel")
        job = wait(self.client, job, timeout=60)
        self.assertEqual(job["status"], "cancelled")
        self.assertIsNone(job["result"])
        done = wait(self.client, self.client.post("/api/jobs/analyze", json={"game": "649", "simulations": 99}).json())
        regime = done["result"]["regimes"][0]
        self.assertEqual(len(regime["frequencies"]), 49)
        self.assertEqual(len(regime["expected"]["odd"]), 7)


class DrawInfoTests(unittest.TestCase):
    LIVE = {**load_snapshot(), "mode": "live", "fetched_at": "2026-11-07 22:00 BC", "notes": []}

    def test_live_result_is_cached(self):
        calls = []

        def fetch():
            calls.append(1)
            return dict(self.LIVE)
        info = DrawInfo(True, 1800, fetch)
        first, second = info.get(), info.get()
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["mode"], "live")
        self.assertIn("Live from WCLC", second["status"])

    def test_failure_falls_back_to_saved_then_stale_live(self):
        def broken():
            raise OSError("network down")
        fallback = DrawInfo(True, 1800, broken).get()
        self.assertEqual(fallback["mode"], "saved")
        self.assertIn("Live update unavailable (network down)", fallback["status"])
        results = [dict(self.LIVE), OSError("timed out")]

        def flaky():
            item = results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        info = DrawInfo(True, 0, flaky, retry_seconds=0)   # every call refreshes
        self.assertEqual(info.get()["mode"], "live")
        stale = info.get()
        self.assertEqual(stale["mode"], "live-stale")
        self.assertIn("latest refresh failed (timed out)", stale["status"])


class EngineHookTests(unittest.TestCase):
    def test_cancel_and_deadline_hooks(self):
        events = [(0b111111111, 2, 2), (0b111111110000, 2, 2), (0b111111111000000000, 2, 2),
                  (0b111000000000111000, 2, 2)]
        with self.assertRaises(Cancelled):
            UnionCounter(0, should_stop=lambda: True).count(24, 6, events)
        with self.assertRaises(WorkLimit):
            UnionCounter(0, deadline=0).count(24, 6, events)
        with self.assertRaises(Cancelled):
            generate_coverage(GAMES["649"], 5, should_stop=lambda: True)
        seen = []
        draws = [Draw(date(2020, 1, 1) + timedelta(days=i), tuple(range(1 + i % 40, 7 + i % 40)), 49, 49)
                 for i in range(20)]
        analyze(draws, GAMES["649"], simulations=99, progress=seen.append)
        self.assertTrue(seen and seen[0] == 0)


if __name__ == "__main__":
    unittest.main()
