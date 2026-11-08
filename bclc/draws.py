"""Dated draw information built from WCLC's public pages.

Reads three public pages: the "next jackpot" box (play-now), the Lotto 6/49 results and the
Lotto Max results. Builds the same snapshot shape as examples/current_draw_snapshot.json.

Nothing is guessed:
- Lotto Max past jackpots come from each draw's MAXPLUS count (one $100,000 prize per $1M of
  jackpot, up to 90, per PlayNow's rules checked 2026-11-07).
- Gold Ball ball counts follow the official rules: each white ball drawn removes that ball and adds
  $2M; drawing the gold ball resets to 30 balls and $10M. Values are derived only along an
  unbroken chain of consecutive draws.
- Anything that cannot be established (for example a $50M+ jackpot whose MAXMILLIONS count is not
  shown) is left out and listed in "notes".
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re

from .compare import validate_draw
from .results import fetch_page, parse_results

PLAY_NOW_URL = "https://www.wclc.com/play-now.htm"
RESULTS_649_URL = "https://www.wclc.com/winning-numbers/lotto-649-extra.htm"
RESULTS_MAX_URL = "https://www.wclc.com/winning-numbers/lotto-max-extra.htm"

# BC keeps UTC-7 year-round from November 2026: PlayNow lists draws at 8:30 PM BC (10:30 PM ET),
# and Windows on Ken's PC reports "British Columbia Standard Time" at UTC-7 in November.
BC_TIME = timezone(timedelta(hours=-7), "BC")
SCHEDULE = {"649": (2, 5), "max": (1, 4)}   # Wednesday/Saturday and Tuesday/Friday
GOLD_START_BALLS, GOLD_START_CAD, GOLD_STEP_CAD = 30, 10_000_000, 2_000_000
CLASSIC_JACKPOT_CAD = 5_000_000
MAX_JACKPOT_CAP = 90_000_000


@dataclass(frozen=True)
class MaxResult:
    day: date
    numbers: tuple[int, ...]
    bonus: int
    maxmillions: int
    maxplus: int


def bc_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(BC_TIME)


def next_draw_after(day: date, game: str) -> date:
    following = day + timedelta(days=1)
    while following.weekday() not in SCHEDULE[game]:
        following += timedelta(days=1)
    return following


def previous_draw_before(day: date, game: str) -> date:
    earlier = day - timedelta(days=1)
    while earlier.weekday() not in SCHEDULE[game]:
        earlier -= timedelta(days=1)
    return earlier


def gold_jackpot_for(balls: int) -> int:
    return GOLD_START_CAD + GOLD_STEP_CAD * (GOLD_START_BALLS - balls)


def _date(text: str) -> date:
    return datetime.strptime(" ".join(text.split()), "%A, %B %d, %Y").date()


def _millions(text: str) -> int:
    amount = float(text.replace(",", ""))
    cad = int(round(amount * 1_000_000))
    if cad < 1_000_000:
        raise ValueError(f"Unexpected jackpot amount {text!r}.")
    return cad


def _section(page: str, start: str, end: str) -> str:
    begin = page.find(start)
    finish = page.find(end, begin + 1)
    if begin < 0 or finish < 0:
        raise ValueError(f"Next-jackpot box not found ({start!r}); the WCLC page layout may have changed.")
    return page[begin:finish]


def parse_upcoming(page: str) -> dict:
    """The advertised next draws. A box can be stale for hours after a draw; callers check dates."""
    upcoming = {}
    block = _section(page, "start next jackpot details l649", "end next jackpot details l649")
    amount = re.search(r'nextJackpotPrizeAmount">\s*([\d.,]+)\s*<', block)
    balls = re.search(r"<b>\s*(\d+)\s*</b>\s*Balls Remaining", block)
    when = re.search(r'nextJackpotDateDate">\s*([^<]+?)\s*<', block)
    if not (amount and balls and when) or "Million" not in block:
        raise ValueError("Lotto 6/49 next-jackpot details not found; the WCLC page layout may have changed.")
    upcoming["649"] = {"date": _date(when.group(1)), "gold_jackpot_cad": _millions(amount.group(1)),
                       "gold_balls": int(balls.group(1))}
    block = _section(page, "start next jackpot details lmax", "end next jackpot details lmax")
    amount = re.search(r'nextJackpotPrizeAmount">\s*([\d.,]+)\s*<', block)
    when = re.search(r'nextJackpotDateDate">\s*([^<]+?)\s*<', block)
    if not (amount and when) or "Million" not in block:
        raise ValueError("Lotto Max next-jackpot details not found; the WCLC page layout may have changed.")
    text = " ".join(re.sub(r"<[^>]+>", " ", block).split())
    maxplus = re.search(r"(\d+)\s*x\s*\$100,000", text)
    maxmillions = re.search(r"(\d+)\s*x\s*\$1(?:,000,000|\s*Million)", text, re.I)
    upcoming["max"] = {"date": _date(when.group(1)), "jackpot_cad": _millions(amount.group(1)),
                       "maxplus": int(maxplus.group(1)) if maxplus else None,
                       "maxmillions": int(maxmillions.group(1)) if maxmillions else None}
    return upcoming


def parse_max_results(page: str) -> list[MaxResult]:
    headings = list(re.finditer(r'class="pastWinNumDate">\s*<h4>\s*([^<]+?)\s*</h4>', page))
    results = []
    for index, heading in enumerate(headings):
        block = page[heading.end(): headings[index + 1].start() if index + 1 < len(headings) else len(page)]
        day = _date(heading.group(1))
        numbers = tuple(sorted(int(x) for x in re.findall(r'<li class="pastWinNumber">\s*(\d+)\s*</li>', block)))
        bonus = re.search(r'pastWinNumberBonusText">\s*Bonus\s*</span>\s*(\d+)', block)
        if len(numbers) != 7 or len(set(numbers)) != 7 or not all(1 <= x <= 52 for x in numbers) or not bonus:
            raise ValueError(f"Lotto Max results for {day.isoformat()} could not be read; the layout may have changed.")
        plus_numbers = len(re.findall(r'class="winNumHomeNumberMaxPlus">', block))
        mm_numbers = len(re.findall(r'class="pastWinNumMM">', block))
        if plus_numbers % 7 or mm_numbers % 7:
            raise ValueError(f"Lotto Max extra draws for {day.isoformat()} are incomplete on the page.")
        if mm_numbers == 0 and "No MAXMILLIONS" not in block and "pastWinNumMMGroup" in block:
            raise ValueError(f"Lotto Max MAXMILLIONS for {day.isoformat()} could not be read.")
        results.append(MaxResult(day, numbers, int(bonus.group(1)), mm_numbers // 7, plus_numbers // 7))
    if not results:
        raise ValueError("No Lotto Max draws found on the results page; its layout may have changed.")
    return results


def _gold_entry(day: date, balls: int, basis: str) -> dict:
    return {"game": "649", "date": day.isoformat(), "classic_jackpot_cad": CLASSIC_JACKPOT_CAD,
            "gold_jackpot_cad": gold_jackpot_for(balls), "gold_balls": balls, "gold_issued": None,
            "basis": basis}


def build_snapshot(now: datetime, page_649: str, page_max: str, page_next: str) -> dict:
    """Combine the three pages into a validated, dated snapshot. Raises ValueError on layout changes."""
    local = bc_now(now)
    results_649 = sorted(parse_results(page_649), key=lambda d: d.day)
    results_max = sorted(parse_max_results(page_max), key=lambda d: d.day)
    upcoming = parse_upcoming(page_next)
    draws, notes = [], []

    # ---- Lotto 6/49 Gold Ball: anchor on the advertised box, then follow the official ball rules.
    ball_by_day = {d.day: d.ball_drawn.lower() for d in results_649}
    latest = results_649[-1].day
    advert = upcoming["649"]
    if advert["gold_jackpot_cad"] != gold_jackpot_for(advert["gold_balls"]):
        notes.append("WCLC's advertised Gold Ball jackpot does not match its ball count under the published "
                     "rules; ball counts were used.")
    known = {}
    if advert["date"] > latest:
        if advert["date"] == next_draw_after(latest, "649"):
            known[advert["date"]] = (advert["gold_balls"], "published by WCLC")
        else:
            notes.append("Lotto 6/49 results are missing a draw before the advertised one; past Gold Ball "
                         "counts were not derived.")
            known[advert["date"]] = (advert["gold_balls"], "published by WCLC")
    elif advert["date"] in ball_by_day:
        known[advert["date"]] = (advert["gold_balls"], "published by WCLC before this draw")
        following = next_draw_after(advert["date"], "649")
        colour = ball_by_day[advert["date"]]
        if colour == "white":
            known[following] = (advert["gold_balls"] - 1,
                                f"derived: the {advert['date'].isoformat()} draw drew a white ball, which is removed "
                                "and adds $2M (official rules); WCLC had not updated its box yet")
        elif colour == "gold":
            known[following] = (GOLD_START_BALLS, f"derived: the gold ball was drawn on "
                                f"{advert['date'].isoformat()}, so the drum resets to 30 balls and $10M")
        else:
            notes.append(f"Ball colour for {advert['date'].isoformat()} not shown; next Gold Ball count unknown.")
    else:
        notes.append("WCLC's Lotto 6/49 next-jackpot box is out of date; Gold Ball counts were not derived.")
    # Walk backwards through consecutive results: a white ball at a draw means one more ball before it.
    if known:
        day = min(known)
        balls = known[day][0]
        while True:
            earlier = previous_draw_before(day, "649")
            colour = ball_by_day.get(earlier)
            if colour != "white" or balls + 1 > GOLD_START_BALLS:
                break
            balls += 1
            known[earlier] = (balls, "derived from the next draw's ball count and this draw's white ball")
            day = earlier
    for day in sorted(known):
        balls, basis = known[day]
        if 1 <= balls <= GOLD_START_BALLS:
            draws.append({**_gold_entry(day, balls, basis), "drawn": day in ball_by_day})

    # ---- Lotto Max: past draws from MAXPLUS/MAXMILLIONS counts; next draw from the advertised box.
    for result in results_max:
        if not 1 <= result.maxplus <= 90:
            notes.append(f"Lotto Max {result.day.isoformat()}: MAXPLUS count not shown; jackpot not derived.")
            continue
        jackpot = result.maxplus * 1_000_000
        draws.append({"game": "max", "date": result.day.isoformat(), "jackpot_cad": jackpot,
                      "maxmillions": result.maxmillions, "maxplus": result.maxplus, "drawn": True,
                      "basis": "derived from this draw's published MAXPLUS and MAXMILLIONS results"})
    advert = upcoming["max"]
    latest_max = results_max[-1].day
    if advert["date"] <= latest_max:
        notes.append(f"The next Lotto Max jackpot after {latest_max.isoformat()} has not been announced yet "
                     "(WCLC's box still shows the previous draw), so that draw is not listed.")
    else:
        jackpot = min(advert["jackpot_cad"], MAX_JACKPOT_CAP)
        maxplus = advert["maxplus"] if advert["maxplus"] is not None else min(90, jackpot // 1_000_000)
        maxmillions = advert["maxmillions"] if advert["maxmillions"] is not None else (
            0 if jackpot < 50_000_000 else None)
        if maxmillions is None:
            notes.append(f"Lotto Max {advert['date'].isoformat()}: the number of MAXMILLIONS prizes is not shown, "
                         "so that draw is not listed rather than guessed.")
        else:
            draws.append({"game": "max", "date": advert["date"].isoformat(), "jackpot_cad": jackpot,
                          "maxmillions": maxmillions, "maxplus": maxplus, "drawn": False,
                          "basis": "published by WCLC"})

    valid = []
    for draw in draws:
        try:
            validate_draw(draw)
        except (ValueError, KeyError, TypeError) as exc:
            notes.append(f"{draw['game']} {draw['date']} left out: {exc}")
            continue
        valid.append(draw)
    if not valid:
        raise ValueError("No usable draw information could be built from the WCLC pages.")
    return {"as_of": local.date().isoformat(), "fetched_at": local.strftime("%Y-%m-%d %H:%M") + " BC",
            "mode": "live",
            "feed_note": "Read automatically from WCLC's public pages at " + local.strftime("%Y-%m-%d %H:%M") +
                         " BC. Pages can lag official announcements; official BCLC information prevails.",
            "draws": sorted(valid, key=lambda d: (d["date"], d["game"])), "notes": notes,
            "sources": [PLAY_NOW_URL, RESULTS_649_URL, RESULTS_MAX_URL]}


def fetch_live_snapshot(now: datetime | None = None, fetch=fetch_page) -> dict:
    pages = [fetch(url) for url in (RESULTS_649_URL, RESULTS_MAX_URL, PLAY_NOW_URL)]
    return build_snapshot(now or datetime.now(timezone.utc), *pages)
