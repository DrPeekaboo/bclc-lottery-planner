"""FastAPI web app. All validation and lottery calculations run here, on the server.

Start locally:  .venv\\Scripts\\python -m uvicorn webapp.server:app --port 8000
"""
from contextlib import asynccontextmanager
from datetime import date
from fractions import Fraction
import csv
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bclc import __version__
from bclc.analysis import analyze, split_probabilities, sum_probabilities
from bclc.compare import compare_week, validate_draw
from bclc.draws import bc_now, fetch_live_snapshot
from bclc.games import GAMES, cad, money_cents, read_history, read_receipt
from bclc.generate import coverage, wheel_precheck
from bclc.odds import TIERS, portfolio_odds, probability_record, series_probability, single_probability
from bclc.service import MAX_PLAYS, column_layout, make_plan, ticket_rows
from bclc.tickets import check_ticket, parse_lines

from .jobs import JobManager
from .sessions import COOKIE, Session, SessionStore

log = logging.getLogger("bclc.web")
ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
SNAPSHOT_PATH = ROOT / "examples" / "current_draw_snapshot.json"
RULES_CHECKED = "2026-11-07"
MAX_UPLOAD_BYTES = 1_000_000
MAX_HISTORY_ROWS = 10_000
WORK_LIMIT = int(os.environ.get("BCLC_WORK_LIMIT", "200000"))
TIER_SECONDS = float(os.environ.get("BCLC_TIER_SECONDS", "20"))
IDLE_SECONDS = int(os.environ.get("BCLC_SESSION_IDLE_SECONDS", "3600"))
WORKERS = int(os.environ.get("BCLC_WORKERS", "2"))
# Live draw information from WCLC (on by default); refreshed at most every 30 minutes.
LIVE_DRAWS = os.environ.get("BCLC_LIVE_DRAWS", "1").strip().lower() not in ("0", "false", "no", "off")
DRAW_REFRESH_SECONDS = int(os.environ.get("BCLC_DRAW_REFRESH_SECONDS", "1800"))

jobs = JobManager(workers=WORKERS, keep_seconds=IDLE_SECONDS)
store = SessionStore(idle_seconds=IDLE_SECONDS, on_expire=jobs.drop_owner)

# ---------- verified rules (rendered PlayNow pages, checked 2026-11-07) ----------

PRIZES = {
    "649": ["$5,000,000 (win or share)", "32.15% of the Pools Fund (shared)", "13.5% of the Pools Fund (shared)",
            "54.35% of the Pools Fund (shared)", "$10", "$5", "$3 Free Play"],
    "max": ["Jackpot, up to $90M (win or share)", "18.50% of the Pool's Fund (shared)",
            "18.85% of the Pool's Fund (shared)", "12.25% of the Pool's Fund (shared)",
            "27.50% of the Pool's Fund (shared)", "22.90% of the Pool's Fund (shared)", "$20", "$20",
            "$6 Free Play"],
}
PUBLISHED_ODDS = {"649": [13_983_816, 2_330_636, 55_492, 1_033, 56.7, 81.2, 8.3],
                  "max": [33_446_140, 4_778_020, 108_591, 36_197, 1_684, 1_010, 72.2, 72.2, 7.0]}
GAME_NOTES = {
    "649": {"draws": "Wednesday and Saturday, 8:30 PM BC time",
            "lines": "$3 buys one 6-number line from 1-49 that you can choose, plus one 10-digit Gold Ball number.",
            "extras": ["Gold Ball draw: one Gold Ball number per $3 play; exact 10-digit match only; never shared. "
                       "The drum starts with 29 white balls ($1,000,000 each) and 1 gold ball (jackpot from $10M, "
                       "+$2M per draw, up to $68M), then resets. Your odds are your plays out of all Gold Ball "
                       "numbers issued nationally for that draw."],
            "any_prize": "1 in 6.6 per $3 play (published)"},
    "max": {"draws": "Tuesday and Friday, 8:30 PM BC time",
            "lines": "$6 buys four 7-number lines from 1-52. You choose the first line; the other three are "
                     "Quick Picks issued at purchase.",
            "extras": ["MAXMILLIONS: $1,000,000 prizes (win or share), exact 7/7 match, offered when the "
                       "jackpot is $50M or more; each draws its own numbers.",
                       "MAXPLUS: one $100,000 prize (win or share) per $1M of jackpot, up to 90; exact 7/7 match."],
            "any_prize": "1 in 5.8 per $6 play (published)"},
}
SOURCES = ["https://www.playnow.com/lottery/lotto-649/", "https://www.playnow.com/lottery/lotto-max/",
           "https://www.wclc.com/winning-numbers/lotto-649-extra.htm",
           "https://www.wclc.com/winning-numbers/lotto-max-extra.htm"]


def load_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8-sig"))


class DrawInfo:
    """Live draw information from WCLC, cached; falls back to the saved snapshot, always labelled."""

    def __init__(self, live: bool, refresh_seconds: int, fetcher=fetch_live_snapshot, retry_seconds: int = 60):
        self.live, self.refresh, self.fetcher, self.retry = live, refresh_seconds, fetcher, retry_seconds
        self.lock = threading.Lock()
        self.cached, self.cached_at, self.last_attempt, self.error = None, 0.0, 0.0, None

    def get(self) -> dict:
        saved = load_snapshot()
        if not self.live:
            return {**saved, "mode": "saved",
                    "status": f"Saved draw information from {saved['as_of']} (live updates are switched off)."}
        with self.lock:
            now = time.time()
            fresh = self.cached is not None and now - self.cached_at < self.refresh
            if not fresh and now - self.last_attempt >= self.retry:
                self.last_attempt = now
                try:
                    self.cached, self.cached_at, self.error = self.fetcher(), now, None
                    fresh = True
                except Exception as exc:  # network, layout change or validation: never break the site
                    self.error = (str(exc) or type(exc).__name__)[:200]
                    log.warning("Live draw refresh failed: %s", self.error)
            if fresh:
                return {**self.cached, "status": f"Live from WCLC's public pages, updated {self.cached['fetched_at']}."}
            if self.cached is not None:
                return {**self.cached, "mode": "live-stale",
                        "status": f"Last live update {self.cached['fetched_at']}; the latest refresh failed "
                                  f"({self.error}). Figures may be out of date."}
            return {**saved, "mode": "saved",
                    "status": f"Live update unavailable ({self.error}); showing saved draw information "
                              f"from {saved['as_of']}."}


draw_info = DrawInfo(LIVE_DRAWS, DRAW_REFRESH_SECONDS)


def rules_payload() -> dict:
    games = {}
    for key, game in GAMES.items():
        tiers = []
        for tier, prize, published in zip(TIERS[key], PRIZES[key], PUBLISHED_ODDS[key]):
            single = single_probability(game, tier)
            tiers.append({"tier": tier.label, "prize": prize, "published_one_in_per_play": published,
                          "single_line_one_in": float(1 / single)})
        games[key] = {"key": key, "name": game.name, "price_cents": game.price_cents, "price": cad(game.price_cents),
                      "pool": game.n, "pick": game.k, "lines_per_play": game.lines_per_play,
                      "chosen_lines_per_play": 1, "tiers": tiers, **GAME_NOTES[key]}
    return {"verified_on": RULES_CHECKED, "version": __version__, "games": games, "sources": SOURCES,
            "verification_note": "Rules were checked against the rendered PlayNow Lotto 6/49 and Lotto Max pages "
                                 "and the WCLC draw pages on 2026-11-07. The official BCLC game-conditions PDFs "
                                 "could not be opened by that automated check, and official BCLC records prevail.",
            "honesty": "Every valid combination has exactly the same chance of being drawn. Nothing here predicts "
                       "numbers or raises the chance of a draw; more plays raise it only in proportion to cost."}


# ---------- app, sessions, errors ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    jobs.shutdown()


app = FastAPI(title="BCLC Lottery Planner", version=__version__, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def friendly_validation(request: Request, exc: RequestValidationError):
    parts = []
    for error in exc.errors():
        field = ".".join(str(x) for x in error.get("loc", ()) if x not in ("body", "query", "path"))
        parts.append(f"{field or 'input'}: {error.get('msg', 'invalid value')}")
    return JSONResponse({"detail": "Please check your input. " + "; ".join(parts)}, status_code=422)


def https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


def visitor(request: Request, response: Response) -> Session:
    session, _ = store.get_or_create(request.cookies.get(COOKIE))
    response.set_cookie(COOKIE, session.sid, max_age=IDLE_SECONDS, httponly=True, samesite="strict",
                        secure=https(request), path="/")
    return session


def game_of(key: str):
    if key not in GAMES:
        raise HTTPException(404, "Unknown game. Use 649 or max.")
    return GAMES[key]


def bad_request(exc: Exception):
    raise HTTPException(400, str(exc))


async def read_upload(upload: UploadFile) -> tuple[str, str]:
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 1 MB. Split it or remove unneeded columns.")
    if not data.strip():
        raise HTTPException(400, "The uploaded file is empty.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return text, hashlib.sha256(data).hexdigest()


def safe_name(upload: UploadFile) -> str:
    return "".join(c for c in (upload.filename or "upload.csv") if c.isalnum() or c in "._- ")[:80] or "upload.csv"


# ---------- pages and simple endpoints ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": __version__}


@app.get("/api/rules")
def rules():
    return rules_payload()


def official_results(game_key: str) -> tuple[dict, list]:
    info = draw_info.get()
    results = (info.get("results") or {}).get(game_key)
    if not results:
        raise HTTPException(503, "Official results can't be read from WCLC right now. Please try again later, "
                                 "or check your ticket on PlayNow.")
    return info, results


@app.get("/api/results/{game_key}")
def results_list(game_key: str):
    """Draws whose official winning numbers are available for checking (newest first)."""
    game = game_of(game_key)
    info, results = official_results(game.key)
    draws = []
    for result in results:
        entry = {"date": result["date"], "numbers": result["numbers"], "bonus": result["bonus"],
                 "details_url": result.get("details_url")}
        if game.key == "649":
            entry.update(ball_drawn=result.get("ball_drawn"), super_draws=len(result.get("super_draws", [])),
                         gold_ball_winners=[n[:8] + "-" + n[8:] for n, _ in result.get("gold_ball", [])])
        else:
            entry.update(maxmillions=len(result.get("maxmillions_sets", [])),
                         maxplus=len(result.get("maxplus_sets", [])))
        draws.append(entry)
    return {"game": game.name, "mode": info["mode"], "status": info["status"], "draws": draws}


class TicketRequest(BaseModel):
    game: Literal["649", "max"]
    draw_date: date
    lines: str = Field(..., min_length=1, max_length=6000)
    gold_ball: str | None = Field(None, max_length=20)


@app.post("/api/check-ticket")
def check_ticket_endpoint(body: TicketRequest):
    """Checks once against official results; the numbers entered are not stored or logged."""
    info, results = official_results(body.game)
    match = next((r for r in results if r["date"] == body.draw_date.isoformat()), None)
    if match is None:
        raise HTTPException(404, f"No official results for {body.draw_date.isoformat()} are available here. "
                                 "Results appear after the draw is posted; the page lists the last few weeks.")
    try:
        report = check_ticket(body.game, match, parse_lines(body.lines, body.game), body.gold_ball or "")
    except ValueError as exc:
        bad_request(exc)
    report["source"] = info["status"]
    return report


@app.get("/api/draws")
def draws():
    """Dated draw information: live from WCLC when available, otherwise the saved snapshot (labelled)."""
    info = draw_info.get()
    return {"as_of": info["as_of"], "mode": info["mode"], "status": info["status"], "draws": info["draws"],
            "notes": info.get("notes", []), "sources": info.get("sources", []),
            "today": bc_now().date().isoformat()}


@app.get("/api/templates/{name}")
def template(name: Literal["history_649", "history_max", "receipt_max"]):
    files = {"history_649": ("history_template_649.csv", "date,numbers,bonus\n"),
             "history_max": ("history_template_max.csv", "date,numbers,bonus\n"),
             "receipt_max": ("receipt_template_max.csv", "play,line,numbers\n1,1,\n1,2,\n1,3,\n1,4,\n")}
    filename, body = files[name]
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/session")
def session_info(session: Session = Depends(visitor)):
    return {"history": {k: v["summary"] for k, v in session.history.items()},
            "receipts": {k: v["summary"] for k, v in session.receipts.items()},
            "idle_minutes": IDLE_SECONDS // 60,
            "storage": "Uploads are kept in server memory only for this browser session and deleted after "
                       f"{IDLE_SECONDS // 60} idle minutes, on server restart, or when you press Delete my data."}


@app.delete("/api/session")
def delete_session(request: Request, response: Response):
    sid = request.cookies.get(COOKIE)
    if sid:
        store.delete(sid)
        jobs.drop_owner(sid)
    response.delete_cookie(COOKIE, path="/")
    return {"deleted": True}


@app.post("/api/history/{game_key}")
async def upload_history(game_key: str, file: UploadFile = File(...), session: Session = Depends(visitor)):
    game = game_of(game_key)
    text, digest = await read_upload(file)
    if text.count("\n") > MAX_HISTORY_ROWS + 1:
        raise HTTPException(400, f"History is limited to {MAX_HISTORY_ROWS:,} rows on the website.")
    name = safe_name(file)
    try:
        draws = read_history(io.StringIO(text, newline=""), game, name)
    except ValueError as exc:
        bad_request(exc)
    regimes = {}
    for draw in draws:
        info = regimes.setdefault(draw.pool, {"pool": draw.pool, "draws": 0, "first": draw.day.isoformat()})
        info["draws"] += 1
        info["last"] = draw.day.isoformat()
    summary = {"name": name, "game": game.name, "draws": len(draws), "first": draws[0].day.isoformat(),
               "last": draws[-1].day.isoformat(), "regimes": list(regimes.values()), "sha256": digest}
    session.history[game.key] = {"draws": draws, "summary": summary}
    return summary


@app.delete("/api/history/{game_key}")
def delete_history(game_key: str, session: Session = Depends(visitor)):
    session.history.pop(game_of(game_key).key, None)
    return {"deleted": True}


@app.post("/api/receipt/{game_key}")
async def upload_receipt(game_key: str, file: UploadFile = File(...), session: Session = Depends(visitor)):
    game = game_of(game_key)
    text, digest = await read_upload(file)
    name = safe_name(file)
    try:
        lines = read_receipt(io.StringIO(text, newline=""), game, name)
    except ValueError as exc:
        bad_request(exc)
    plays = len(lines) // game.lines_per_play
    if plays > MAX_PLAYS:
        raise HTTPException(400, f"Receipts are limited to {MAX_PLAYS} plays.")
    summary = {"name": name, "game": game.name, "plays": plays, "lines": len(lines),
               "distinct_lines": len(set(lines)), "cost": cad(plays * game.price_cents), "sha256": digest}
    session.receipts[game.key] = {"lines": lines, "summary": summary}
    return summary


@app.delete("/api/receipt/{game_key}")
def delete_receipt(game_key: str, session: Session = Depends(visitor)):
    session.receipts.pop(game_of(game_key).key, None)
    return {"deleted": True}


# ---------- background calculations ----------

class AnalyzeRequest(BaseModel):
    game: Literal["649", "max"]
    simulations: int = Field(999, ge=99, le=4999)
    alpha: float = Field(0.05, gt=0, lt=1)
    seed: int = Field(2026, ge=0, le=2**31)


class PlanRequest(BaseModel):
    game: Literal["649", "max"]
    budget: str = Field(..., min_length=1, max_length=12)
    use_history: bool = False
    seed: int = Field(2026, ge=0, le=2**31)
    slip_columns: int = Field(7, ge=1, le=20)
    candidates: int = Field(500, ge=50, le=2000)
    restarts: int = Field(6, ge=1, le=40)


class WheelRequest(BaseModel):
    game: Literal["649", "max"]
    budget: str = Field("0", min_length=1, max_length=12)
    favourites: list[int] = Field(..., min_length=1, max_length=20)
    allow_overlap: bool = False
    use_history: bool = False
    seed: int = Field(2026, ge=0, le=2**31)
    slip_columns: int = Field(7, ge=1, le=20)
    restarts: int = Field(20, ge=1, le=40)


class OddsRequest(BaseModel):
    game: Literal["649", "max"]
    source: Literal["receipt", "plan"]
    plan_job_id: str | None = Field(None, max_length=64)
    budget: str | None = Field(None, max_length=12)
    draw_date: date | None = None
    gold_issued: int | None = Field(None, ge=1, le=10**9)


class CompareRequest(BaseModel):
    budget: str = Field(..., min_length=1, max_length=12)
    week_of: date | None = None
    gold_issued: int | None = Field(None, ge=1, le=10**9)


def submit(session: Session, kind: str, work):
    try:
        return jobs.submit(session.sid, kind, work).public()
    except OverflowError as exc:
        raise HTTPException(429, str(exc))


def history_for(session: Session, game, use_history: bool):
    if not use_history:
        return frozenset(), {"path": None, "warning": "NO HISTORY: past-combination exclusion is unavailable."}
    stored = session.history.get(game.key)
    if not stored:
        raise ValueError("Upload a history CSV for this game first, or untick 'Exclude past jackpots'.")
    summary = stored["summary"]
    return frozenset(d.numbers for d in stored["draws"]), {
        "path": summary["name"], "draws": summary["draws"], "first_date": summary["first"],
        "last_date": summary["last"], "sha256": summary["sha256"],
        "warning": "Only main combinations present in your uploaded CSV are excluded."}


def odds_with_progress(job, lines, game, pending, start=0.5):
    tiers = TIERS[game.key]
    seen = []

    def tier_progress(label):
        seen.append(label)
        job.update(start + (1 - start) * (len(seen) - 1) / len(tiers), f"Exact odds: tier {label}")
    return portfolio_odds(lines, game, WORK_LIMIT, pending, tier_progress, job.should_stop, TIER_SECONDS)


def expected_series(regime: dict, game) -> dict:
    n, size, k = regime["pool"], regime["draws"], game.k
    odd = split_probabilities(n, k, (n + 1) // 2)
    low = split_probabilities(n, k, n // 2)
    sums = sum_probabilities(n, k)
    return {"number": size * k / n,
            "odd": [size * odd.get(i, 0) for i in range(k + 1)],
            "low": [size * low.get(i, 0) for i in range(k + 1)],
            "sum_bands": [size * sums[band] for band in sums]}


@app.post("/api/jobs/analyze")
def start_analysis(body: AnalyzeRequest, session: Session = Depends(visitor)):
    game = GAMES[body.game]
    stored = session.history.get(game.key)
    if not stored:
        raise HTTPException(400, "Upload a history CSV for this game first.")
    draws = stored["draws"]

    def work(job):
        report = analyze(draws, game, body.simulations, body.seed, body.alpha,
                         progress=lambda f: job.update(f, f"Monte Carlo replicates: {int(f * 100)}%"),
                         should_stop=job.should_stop)
        for regime in report["regimes"]:
            regime["expected"] = expected_series(regime, game)
        report["source"] = stored["summary"]
        return report
    return submit(session, "analyze", work)


@app.post("/api/jobs/generate")
def start_generate(body: PlanRequest, session: Session = Depends(visitor)):
    game = GAMES[body.game]
    try:
        budget = money_cents(body.budget.strip().lstrip("$"))
        past, history = history_for(session, game, body.use_history)
        columns, layout_text = column_layout(game, body.slip_columns)
    except ValueError as exc:
        bad_request(exc)

    def work(job):
        job.update(0, "Searching for filtered lines")
        report = make_plan(game, budget, past, history, columns, layout_text, seed=body.seed,
                           restarts=body.restarts, candidates=body.candidates, should_stop=job.should_stop,
                           progress=lambda f: job.update(0.5 * f, "Searching for filtered lines"))
        report["odds"] = odds_with_progress(job, report["lines"], game, report["pending_quick_picks"])
        report["cost"], report["unspent"] = cad(report["cost_cents"]), cad(report["unspent_cents"])
        return report
    return submit(session, "generate", work)


@app.post("/api/wheel/precheck")
def precheck_wheel(body: WheelRequest, session: Session = Depends(visitor)):
    game = GAMES[body.game]
    try:
        past, _ = history_for(session, game, body.use_history)
        columns, _ = column_layout(game, body.slip_columns)
        result = wheel_precheck(game, tuple(body.favourites), body.allow_overlap, past, columns)
    except ValueError as exc:
        bad_request(exc)
    result["lower_bound_cost"] = cad(result["lower_bound_plays"] * game.price_cents)
    return result


@app.post("/api/jobs/wheel")
def start_wheel(body: WheelRequest, session: Session = Depends(visitor)):
    game = GAMES[body.game]
    try:
        budget = money_cents(body.budget.strip().lstrip("$"))
        past, history = history_for(session, game, body.use_history)
        columns, layout_text = column_layout(game, body.slip_columns)
        check = wheel_precheck(game, tuple(body.favourites), body.allow_overlap, past, columns)
        if check["needs_overlap_relaxation"] and not body.allow_overlap:
            raise ValueError(check["overlap_obstruction"] +
                             " Tick 'Relax the overlap limit for this wheel' to build a cover anyway.")
    except ValueError as exc:
        bad_request(exc)

    def work(job):
        job.update(0, "Building the triple cover")
        report = make_plan(game, budget, past, history, columns, layout_text, wheel=True,
                           favourites=tuple(body.favourites), allow_overlap=body.allow_overlap, seed=body.seed,
                           restarts=body.restarts, should_stop=job.should_stop,
                           progress=lambda f: job.update(0.5 * f, "Building the triple cover"))
        report["odds"] = odds_with_progress(job, report["lines"], game, report["pending_quick_picks"])
        report["cost"], report["unspent"] = cad(report["cost_cents"]), cad(report["unspent_cents"])
        wheel = report["wheel"]
        if wheel.get("full_cover_found_plays"):
            wheel["full_cover_found_cost"] = cad(wheel["full_cover_found_plays"] * game.price_cents)
        return report
    return submit(session, "wheel", work)


@app.post("/api/jobs/odds")
def start_odds(body: OddsRequest, session: Session = Depends(visitor)):
    game = GAMES[body.game]
    snapshot_draw = None
    try:
        if body.source == "receipt":
            stored = session.receipts.get(game.key)
            if not stored:
                raise ValueError("Upload your actual issued ticket lines for this game first.")
            lines, pending = list(stored["lines"]), 0
            scope = "All actual issued lines from your uploaded receipt."
        else:
            plan_job = jobs.get(session.sid, body.plan_job_id or "")
            if not plan_job or plan_job.status != "done" or plan_job.kind not in ("generate", "wheel"):
                raise ValueError("Generate a plan first (or pick a finished one).")
            if plan_job.result["game"] != game.key:
                raise ValueError("That plan is for a different game.")
            lines = [tuple(x) for x in plan_job.result["lines"]]
            pending = (game.lines_per_play - 1) * len(lines)
            scope = ("Your chosen lines; each play's three Quick Picks are still pending." if pending else
                     "Your chosen lines; every line is known.")
        plays = len(lines) // game.lines_per_play if body.source == "receipt" else len(lines)
        if body.budget and plays * game.price_cents > money_cents(body.budget.strip().lstrip("$")):
            raise ValueError("These tickets cost more than the budget you entered.")
        if body.draw_date:
            matches = [d for d in draw_info.get()["draws"]
                       if d["game"] == game.key and d["date"] == body.draw_date.isoformat()]
            if len(matches) != 1:
                raise ValueError("No dated draw information for that game and date. Reload the page to see "
                                 "the draws currently available.")
            snapshot_draw = matches[0]
            validate_draw(snapshot_draw)
            if game.key == "max" and pending:
                raise ValueError("MAXMILLIONS/MAXPLUS odds need your actual issued lines: upload your receipt.")
    except ValueError as exc:
        bad_request(exc)
    past = frozenset(d.numbers for d in session.history[game.key]["draws"]) if game.key in session.history else None

    def work(job):
        report = odds_with_progress(job, lines, game, pending, start=0.0)
        report["scope"] = scope
        report["coverage_audit"] = coverage(lines)
        if past is not None:
            report["lines_in_uploaded_history"] = [i + 1 for i, line in enumerate(lines) if tuple(line) in past]
        if snapshot_draw and game.key == "max":
            unique = len(set(lines))
            report["additional_draws"] = {
                "date": snapshot_draw["date"],
                "maxmillions_at_least_one": probability_record(series_probability(unique, 52, 7, snapshot_draw["maxmillions"])),
                "maxplus_at_least_one": probability_record(series_probability(unique, 52, 7, snapshot_draw["maxplus"])),
                "main_or_maxmillions": probability_record(series_probability(unique, 52, 7, snapshot_draw["maxmillions"] + 1)),
                "assumption": "Independent uniform separate draws; prizes can be shared."}
        if snapshot_draw and game.key == "649":
            if body.gold_issued is None:
                report["gold_ball"] = {"date": snapshot_draw["date"], "status": "unknown",
                                       "formula": f"{plays}/G, where G = final national Gold Ball numbers issued"}
            else:
                if body.gold_issued < plays:
                    raise ValueError("National Gold Ball entries must be at least your number of plays.")
                gold = Fraction(plays, body.gold_issued)
                balls = snapshot_draw["gold_balls"]
                report["gold_ball"] = {"date": snapshot_draw["date"], "status": "conditional on your G",
                                       "any_award": probability_record(gold),
                                       "exact_1m": probability_record(gold * Fraction(balls - 1, balls)),
                                       "jackpot": probability_record(gold / balls)}
        return report
    return submit(session, "odds", work)


@app.post("/api/compare")
def compare(body: CompareRequest, session: Session = Depends(visitor)):
    info = draw_info.get()
    try:
        report = compare_week(info, money_cents(body.budget.strip().lstrip("$")),
                              body.week_of or bc_now().date(), body.gold_issued)
    except ValueError as exc:
        bad_request(exc)
    report["draw_info"] = {"mode": info["mode"], "status": info["status"], "notes": info.get("notes", [])}
    return report


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, session: Session = Depends(visitor)):
    job = jobs.get(session.sid, job_id)
    if not job:
        raise HTTPException(404, "Calculation not found. It may have expired or belong to another visitor.")
    return job.public()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, session: Session = Depends(visitor)):
    job = jobs.cancel(session.sid, job_id)
    if not job:
        raise HTTPException(404, "Calculation not found.")
    return job.public()


@app.get("/api/jobs/{job_id}/tickets.csv")
def tickets_csv(job_id: str, session: Session = Depends(visitor)):
    job = jobs.get(session.sid, job_id)
    if not job or job.status != "done" or job.kind not in ("generate", "wheel"):
        raise HTTPException(404, "Finished plan not found.")
    game = GAMES[job.result["game"]]
    stream = io.StringIO()
    csv.writer(stream).writerows(ticket_rows(job.result, game))
    filename = f"tickets_{game.key}_{job.kind}.csv"
    return Response("﻿" + stream.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
