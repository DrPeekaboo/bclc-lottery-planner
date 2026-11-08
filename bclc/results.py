"""Check recorded Lotto 6/49 tickets against official WCLC results and keep a one-row-per-day log.

Nothing here buys tickets or signs in anywhere. It reads the public WCLC results page,
keeps a local copy of every draw it has seen (the page only lists about four weeks),
and rebuilds the daily log from that copy plus your ticket file on every run.
"""
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import re
import urllib.request

RESULTS_URL = "https://www.wclc.com/winning-numbers/lotto-649-extra.htm"
SITE = "https://www.wclc.com"
USER_AGENT = "bclc-lottery-tool/1.1 (personal results checker)"
DRAW_WEEKDAYS = (2, 5)  # Lotto 6/49 draws: Wednesday and Saturday
PURCHASE_STATES = ("yes", "unconfirmed", "no")

TICKET_HEADER = ["draw_date", "numbers", "gold_ball", "purchased"]
CACHE_HEADER = ["date", "numbers", "bonus", "gold_ball", "ball_drawn", "super_draws", "details_url"]
LOG_HEADER = ["date", "draw", "winning_numbers", "bonus", "gold_ball_winner", "ball_drawn",
              "your_numbers", "your_gold_ball", "purchased", "result", "prize", "last_checked"]


@dataclass(frozen=True)
class OfficialDraw:
    day: date
    numbers: tuple[int, ...]
    bonus: int
    gold_ball: tuple[tuple[str, str], ...]     # (10 digits, prize text)
    ball_drawn: str
    super_draws: tuple[tuple[str, str], ...]   # promotional exact-match prizes
    details_url: str


@dataclass(frozen=True)
class Ticket:
    day: date
    numbers: tuple[int, ...]
    gold_ball: str          # 10 digits, or "" if not recorded yet
    purchased: str


def gold_digits(value: str) -> str:
    digits = re.sub(r"[\s-]", "", value or "")
    if digits and not re.fullmatch(r"\d{10}", digits):
        raise ValueError(f"Gold Ball number must have 10 digits, e.g. 05426542-01; got {value!r}.")
    return digits


def show_gold(digits: str) -> str:
    return f"{digits[:8]}-{digits[8:]}" if len(digits) == 10 else digits


def _valid_line(numbers, where: str) -> tuple[int, ...]:
    line = tuple(sorted(numbers))
    if len(line) != 6 or len(set(line)) != 6 or not all(1 <= x <= 49 for x in line):
        raise ValueError(f"{where}: expected 6 different numbers from 1 to 49, got {line}.")
    return line


# ---------- official results ----------

_PAIR = re.compile(r'pastWinNumGPDNumber">\s*([\d-]+)\s*</div>\s*</td>\s*<td>\s*'
                   r'<div class="pastWinNumGPDPrize">\s*([^<]+?)\s*</div>')


def parse_results(page: str) -> list[OfficialDraw]:
    """Parse every Lotto 6/49 draw block on the WCLC results page. Fails loudly on layout changes."""
    draws = []
    for block in page.split('<div class="pastWinNum">')[1:]:
        heading = re.search(r'class="pastWinNumDate">\s*<h4>\s*([^<]+?)\s*</h4>', block)
        if not heading:
            continue
        day = datetime.strptime(" ".join(heading.group(1).split()), "%A, %B %d, %Y").date()
        where = f"WCLC results for {day.isoformat()}"
        numbers = _valid_line((int(x) for x in re.findall(r'<li class="pastWinNumber">\s*(\d+)\s*</li>', block)),
                              where)
        bonus_match = re.search(r'pastWinNumberBonusText">\s*Bonus\s*</span>\s*(\d+)', block)
        if not bonus_match:
            raise ValueError(f"{where}: bonus number not found; the page layout may have changed.")
        bonus = int(bonus_match.group(1))
        if bonus in numbers or not 1 <= bonus <= 49:
            raise ValueError(f"{where}: invalid bonus number {bonus}.")
        gold_part, _, super_part = block.partition("SUPER DRAWS")
        gold = tuple((gold_digits(n), " ".join(p.split())) for n, p in _PAIR.findall(gold_part))
        supers = tuple((gold_digits(n), " ".join(p.split())) for n, p in _PAIR.findall(super_part))
        ball = re.search(r'class="ballpos">\s*(\w+)\s*<', block)
        link = re.search(r'pastWinNumPrizeBreakdown"\s+rel="([^"]+)"', block)
        draws.append(OfficialDraw(day, numbers, bonus, gold, ball.group(1) if ball else "",
                                  supers, SITE + link.group(1) if link else RESULTS_URL))
    if not draws:
        raise ValueError("No Lotto 6/49 draws found on the results page; its layout may have changed.")
    return draws


def fetch_page(url: str = RESULTS_URL, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _write_csv(path: Path, header: list[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
    except PermissionError:
        raise ValueError(f"Cannot save {path}: it is open in another program (often Excel). "
                         "Close it and run the check again.") from None


def _pairs_text(pairs) -> str:
    return ";".join(f"{show_gold(n)}={p}" for n, p in pairs)


def _pairs_parse(text: str):
    pairs = []
    for item in filter(None, (text or "").split(";")):
        number, _, prize = item.partition("=")
        pairs.append((gold_digits(number), prize))
    return tuple(pairs)


def load_cache(path: Path) -> dict[date, OfficialDraw]:
    if not path.exists():
        return {}
    draws = {}
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            day = date.fromisoformat(row["date"])
            draws[day] = OfficialDraw(day, _valid_line(map(int, row["numbers"].split()), f"cache {day}"),
                                      int(row["bonus"]), _pairs_parse(row["gold_ball"]), row["ball_drawn"],
                                      _pairs_parse(row["super_draws"]), row["details_url"])
    return draws


def save_cache(path: Path, draws: dict[date, OfficialDraw]) -> None:
    _write_csv(path, CACHE_HEADER, (
        [day.isoformat(), " ".join(map(str, d.numbers)), d.bonus, _pairs_text(d.gold_ball),
         d.ball_drawn, _pairs_text(d.super_draws), d.details_url]
        for day, d in sorted(draws.items())))


# ---------- tickets ----------

def write_ticket_template(path: Path) -> None:
    _write_csv(path, TICKET_HEADER, [])


def load_tickets(path: Path) -> list[Ticket]:
    tickets = []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"draw_date", "numbers"} <= set(reader.fieldnames):
            raise ValueError(f"{path} needs the header: {','.join(TICKET_HEADER)}")
        for row_number, row in enumerate(reader, 2):
            where = f"{path.name}, row {row_number}"
            raw_day = (row.get("draw_date") or "").strip()
            if not raw_day and not (row.get("numbers") or "").strip():
                continue  # blank line
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_day):
                raise ValueError(f"{where}: draw_date must be YYYY-MM-DD.")
            day = date.fromisoformat(raw_day)
            if day.weekday() not in DRAW_WEEKDAYS:
                raise ValueError(f"{where}: {day.isoformat()} is not a Wednesday or Saturday Lotto 6/49 draw.")
            numbers_text = row.get("numbers") or ""
            if not re.fullmatch(r"[\d\s,;|]+", numbers_text.strip() or "x"):
                raise ValueError(f"{where}: numbers must be six numbers separated by spaces.")
            numbers = _valid_line((int(x) for x in re.findall(r"\d+", numbers_text)), where)
            purchased = (row.get("purchased") or "unconfirmed").strip().lower()
            if purchased not in PURCHASE_STATES:
                raise ValueError(f"{where}: purchased must be yes, unconfirmed or no.")
            try:
                gold = gold_digits(row.get("gold_ball") or "")
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}") from None
            tickets.append(Ticket(day, numbers, gold, purchased))
    return tickets


# ---------- checking ----------

def classify(numbers, draw: OfficialDraw) -> tuple[str, str]:
    hits = len(set(numbers) & set(draw.numbers))
    bonus = draw.bonus in numbers
    if hits == 6:
        return "6/6", "Classic jackpot: $5,000,000 (win or share)"
    if hits == 5 and bonus:
        return "5/6+bonus", "share of the 5/6+bonus pool; see the prize breakdown"
    if hits == 5:
        return "5/6", "share of the 5/6 pool; see the prize breakdown"
    if hits == 4:
        return "4/6", "share of the 4/6 pool; see the prize breakdown"
    if hits == 3:
        return "3/6", "$10"
    if hits == 2 and bonus:
        return "2/6+bonus", "$5"
    if hits == 2:
        return "2/6", "$3 Free Play"
    return "", ""


def check_ticket(ticket: Ticket, draw: OfficialDraw) -> tuple[str, str, bool]:
    """Return (result text, prize text, won anything)."""
    results, prizes = [], []
    tier, prize = classify(ticket.numbers, draw)
    hits = len(set(ticket.numbers) & set(draw.numbers))
    if tier:
        results.append(f"Classic {tier} ({hits} matched)")
        prizes.append(prize)
    else:
        results.append(f"Classic: {hits} matched, no prize")
    if ticket.gold_ball:
        gold_wins = [p for n, p in draw.gold_ball if n == ticket.gold_ball]
        super_wins = [p for n, p in draw.super_draws if n == ticket.gold_ball]
        if gold_wins:
            kind = "Gold Ball JACKPOT" if draw.ball_drawn.lower() == "gold" else "Gold Ball $1M"
            results.append(f"{kind}: exact match")
            prizes.extend(gold_wins)
        else:
            results.append("Gold Ball: no match")
        if super_wins:
            results.append("Super Draw: exact match")
            prizes.extend(super_wins)
    else:
        results.append("Gold Ball: not checked (number not recorded)")
    won = bool(prizes)
    text = "; ".join(results)
    if won:
        text = "WIN - " + text
    if ticket.purchased != "yes":
        text = f"(only if this ticket was bought) {text}"
    return text, " + ".join(prizes) if prizes else "none", won


def build_log(tickets: list[Ticket], draws: dict[date, OfficialDraw], start: date, today: date,
              checked: str) -> tuple[list[list[str]], list[str]]:
    """One row per calendar day from start to today (one per ticket on draw days with several)."""
    rows, wins = [], []
    by_day = {}
    for ticket in tickets:
        by_day.setdefault(ticket.day, []).append(ticket)
    day = start
    while day <= today:
        draw = draws.get(day)
        day_tickets = by_day.get(day, [])
        if draw:
            base = [day.isoformat(), "yes", " ".join(map(str, draw.numbers)), str(draw.bonus),
                    ", ".join(show_gold(n) for n, _ in draw.gold_ball), draw.ball_drawn]
            if not day_tickets:
                rows.append(base + ["", "", "", "No ticket recorded for this draw", "none", checked])
            for ticket in day_tickets:
                text, prize, won = check_ticket(ticket, draw)
                rows.append(base + [" ".join(map(str, ticket.numbers)), show_gold(ticket.gold_ball),
                                    ticket.purchased, text, prize, checked])
                if won:
                    wins.append(f"{day.isoformat()}: {' '.join(map(str, ticket.numbers))} -> {text} | {prize}")
        elif day.weekday() in DRAW_WEEKDAYS:
            status = ("Draw tonight; results not posted yet" if day == today else
                      "Results not found yet; will recheck on the next run")
            if day_tickets:
                for ticket in day_tickets:
                    rows.append([day.isoformat(), "yes", "", "", "", "", " ".join(map(str, ticket.numbers)),
                                 show_gold(ticket.gold_ball), ticket.purchased, status, "", checked])
            else:
                rows.append([day.isoformat(), "yes", "", "", "", "", "", "", "", status, "", checked])
        else:
            rows.append([day.isoformat(), "no", "", "", "", "", "", "", "",
                         "No Lotto 6/49 draw today (draws are Wednesday and Saturday)", "", checked])
        day += timedelta(days=1)
    return rows, wins


def run_check(tickets_path: Path, cache_path: Path, log_path: Path, today: date,
              page: str | None = None, start: date | None = None) -> dict:
    """Fetch (or use the supplied page), merge into the cache, rebuild the log. Returns a summary."""
    if not tickets_path.exists():
        write_ticket_template(tickets_path)
    tickets = load_tickets(tickets_path)
    cache = load_cache(cache_path)
    fetch_error = None
    if page is None:
        try:
            page = fetch_page()
        except OSError as exc:  # network problems: keep going with what is cached
            fetch_error = f"Could not reach {RESULTS_URL}: {exc}"
    if page is not None:
        for draw in parse_results(page):
            cache[draw.day] = draw
        save_cache(cache_path, cache)
    if start is None:
        start = min([t.day for t in tickets], default=today)
    checked = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows, wins = build_log(tickets, cache, start, today, checked)
    _write_csv(log_path, LOG_HEADER, rows)
    latest = max((d for d in cache if d <= today), default=None)
    return {"rows": rows, "wins": wins, "latest": cache.get(latest), "fetch_error": fetch_error,
            "missing_gold_ball": [t for t in tickets if not t.gold_ball],
            "unconfirmed": [t for t in tickets if t.purchased == "unconfirmed"],
            "start": start, "today": today}
