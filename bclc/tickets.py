"""Check ticket lines against official results. Nothing entered here is stored.

Prize tiers come from bclc.odds.TIERS, the same verified definitions used for the odds, so the
checker and the odds calculator always agree. Prize wording follows PlayNow's prize tables
(checked 2026-11-07). Official BCLC results prevail.
"""
import re

from .games import GAMES
from .odds import TIERS
from .results import gold_digits, show_gold

PRIZE_TEXT = {
    "649": {"6/6": "$5,000,000 jackpot (win or share)", "5/6+bonus": "32.15% of the Pools Fund (shared)",
            "5/6": "13.5% of the Pools Fund (shared)", "4/6": "54.35% of the Pools Fund (shared)",
            "3/6": "$10", "2/6+bonus": "$5", "2/6": "$3 Free Play"},
    "max": {"7/7": "Jackpot, up to $90M (win or share)", "6/7+bonus": "18.50% of the Pool's Fund (shared)",
            "6/7": "18.85% of the Pool's Fund (shared)", "5/7+bonus": "12.25% of the Pool's Fund (shared)",
            "5/7": "27.50% of the Pool's Fund (shared)", "4/7+bonus": "22.90% of the Pool's Fund (shared)",
            "4/7": "$20", "3/7+bonus": "$20", "3/7": "$6 Free Play"},
}
EXTRA_PRIZES = {"maxmillions_sets": ("MAXMILLIONS", "$1,000,000 (win or share)"),
                "maxplus_sets": ("MAXPLUS", "$100,000 (win or share)")}
MAX_LINES = 100


def parse_lines(text: str, game_key: str) -> list[tuple[int, ...]]:
    """One line of numbers per row; blank rows are ignored. Errors name the row."""
    game = GAMES[game_key]
    lines = []
    for row, raw in enumerate((text or "").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        if not re.fullmatch(r"[\d\s,;|/-]+", raw):
            raise ValueError(f"Line {row}: use only numbers separated by spaces or commas.")
        values = [int(x) for x in re.findall(r"\d+", raw)]
        if len(values) != game.k or len(set(values)) != game.k or not all(1 <= x <= game.n for x in values):
            raise ValueError(f"Line {row}: enter {game.k} different numbers from 1 to {game.n} "
                             f"(found {len(values)} number{'s' if len(values) != 1 else ''}).")
        lines.append(tuple(sorted(values)))
    if not lines:
        raise ValueError("Enter at least one line of numbers.")
    if len(lines) > MAX_LINES:
        raise ValueError(f"Check at most {MAX_LINES} lines at a time.")
    return lines


def tier_for(game_key: str, line, numbers, bonus) -> str | None:
    hits = len(set(line) & set(numbers))
    on_line = bonus in line
    for tier in TIERS[game_key]:
        if hits == tier.matches and (tier.bonus is None or tier.bonus == on_line):
            return tier.label
    return None


def check_ticket(game_key: str, result: dict, lines, gold_ball: str = "") -> dict:
    """Compare lines (and a 6/49 Gold Ball number) with one official draw result."""
    game = GAMES[game_key]
    numbers, bonus = result["numbers"], result["bonus"]
    rows, wins = [], []
    for index, line in enumerate(lines, 1):
        label = tier_for(game_key, line, numbers, bonus)
        matched = sorted(set(line) & set(numbers))
        extras = []
        for key, (name, prize) in EXTRA_PRIZES.items():
            for drawn in result.get(key, []):
                if sorted(drawn) == list(line):
                    extras.append({"draw": name, "numbers": sorted(drawn), "prize": prize})
        row = {"line": list(line), "matched": matched, "matched_count": len(matched),
               "bonus_on_line": bonus in line, "tier": label,
               "prize": PRIZE_TEXT[game_key][label] if label else None, "extras": extras}
        rows.append(row)
        if label:
            wins.append(f"Line {index}: {label} ({len(matched)} matched) - {row['prize']}")
        for extra in extras:
            wins.append(f"Line {index}: {extra['draw']} exact match - {extra['prize']}")

    gold = None
    if game_key == "649":
        digits = gold_digits(gold_ball or "")
        if digits:
            gold_hits = [prize for number, prize in result.get("gold_ball", []) if gold_digits(number) == digits]
            super_hits = [prize for number, prize in result.get("super_draws", []) if gold_digits(number) == digits]
            kind = "Gold Ball jackpot" if str(result.get("ball_drawn", "")).lower() == "gold" else "Gold Ball $1,000,000"
            gold = {"checked": True, "number": show_gold(digits), "won": bool(gold_hits or super_hits),
                    "gold_ball_prize": gold_hits[0] if gold_hits else None, "kind": kind if gold_hits else None,
                    "super_draw_prizes": super_hits,
                    "winning_numbers": [show_gold(gold_digits(n)) for n, _ in result.get("gold_ball", [])]}
            if gold_hits:
                wins.append(f"{kind}: exact match on {show_gold(digits)} - {gold_hits[0]} (never shared)")
            for prize in super_hits:
                wins.append(f"Super Draw: exact match on {show_gold(digits)} - {prize}")
        else:
            gold = {"checked": False, "won": False,
                    "winning_numbers": [show_gold(gold_digits(n)) for n, _ in result.get("gold_ball", [])]}
    elif gold_ball and gold_ball.strip():
        raise ValueError("Gold Ball numbers belong to Lotto 6/49 tickets, not Lotto Max.")

    notes = ["Confirm any win with PlayNow or a BCLC retailer before claiming or throwing a ticket away; "
             "official BCLC results prevail.",
             "Your numbers are checked once and are not saved."]
    if any(row["tier"] and "Pool" in (row["prize"] or "") for row in rows):
        notes.append("Shared-pool prize amounts are on the official prize breakdown: " + result.get("details_url", ""))
    if any(row["tier"] in ("2/6", "3/7") for row in rows):
        notes.append("A Free Play is a ticket for the next draw.")
    if game_key == "max":
        notes.append("Enter every line printed on the ticket, including the three Quick Pick lines of each $6 play.")
    if game_key == "649" and gold and not gold["checked"]:
        notes.append("Add the 10-digit Gold Ball number from the ticket to check the $1M Gold Ball and Super Draws.")
    return {"game": game.name, "date": result["date"], "winning_numbers": numbers, "bonus": bonus,
            "lines": rows, "gold_ball": gold, "any_win": bool(wins), "wins": wins, "notes": notes,
            "details_url": result.get("details_url")}
