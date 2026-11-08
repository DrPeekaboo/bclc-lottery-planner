"""Dated draw inputs, million-dollar category comparisons, and unknown sales."""
from datetime import date, timedelta
from fractions import Fraction
from math import comb
from .games import GAMES, cad
from .odds import probability_record, iid_quick_pick_series

MAX_JACKPOT_CAP = 90_000_000

# Sharing rules from the PlayNow prize tables (checked 2026-11-07).
SHARING_649 = {
    "gold_ball": "Never shared. A Gold Ball award is an actual $1,000,000 (white ball) or the whole "
                 "Gold Ball jackpot (gold ball), so its probability is a probability of receiving it.",
    "classic_jackpot": "Win or share: $5,000,000 is split among all 6/6 winning selections, so a hit "
                       "does not guarantee receiving $1M or more.",
}
SHARING_MAX = {
    "main_jackpot": "Win or share among all 7/7 winning selections.",
    "maxmillions": "Win or share: each $1,000,000 MAXMILLIONS prize is split among exact-match winners.",
    "maxplus": "Win or share: $100,000 each, below the $1M threshold.",
}


def validate_draw(draw: dict) -> None:
    game = GAMES[draw["game"]]
    day = date.fromisoformat(draw["date"])
    if day.weekday() not in ((1, 4) if game.key == "max" else (2, 5)):
        raise ValueError("Draw date is not on this game's regular draw schedule.")
    if game.key == "max":
        if day < date(2026, 4, 14):
            raise ValueError("Comparison supports Lotto Max's current rules from 2026-04-14 onward.")
        jackpot = draw["jackpot_cad"]
        if not isinstance(jackpot, int) or not 1_000_000 <= jackpot <= MAX_JACKPOT_CAP:
            raise ValueError("Supply a whole-dollar Lotto Max jackpot from $1M to the $90M cap.")
        for key in ("maxmillions", "maxplus"):
            if not isinstance(draw[key], int) or not 0 <= draw[key] <= 10000:
                raise ValueError(f"{key} must be a nonnegative integer (maximum 10000).")
        if jackpot < 50_000_000 and draw["maxmillions"]:
            raise ValueError("MAXMILLIONS cannot be offered below a $50M jackpot.")
        # PlayNow (checked 2026-11-07): one $100,000 MAXPLUS prize per $1M of jackpot, up to 90.
        expected_maxplus = min(90, jackpot // 1_000_000)
        if draw["maxplus"] != expected_maxplus:
            raise ValueError(f"A ${jackpot:,} jackpot carries {expected_maxplus} MAXPLUS prizes "
                             f"(one per $1M, maximum 90), not {draw['maxplus']}.")
    else:
        balls, issued = draw["gold_balls"], draw.get("gold_issued")
        if not isinstance(balls, int) or not 1 <= balls <= 30:
            raise ValueError("gold_balls must include the gold ball and be between 1 and 30.")
        if issued is not None and (not isinstance(issued, int) or issued < 1):
            raise ValueError("gold_issued must be the total national entries, or null if unknown.")


def draw_comparison(draw: dict, budget_cents: int, gold_issued=None) -> dict:
    validate_draw(draw)
    game = GAMES[draw["game"]]
    plays = budget_cents // game.price_cents
    cost = plays * game.price_cents
    result = {"game": game.name, "date": draw["date"], "plays": plays,
              "spent_cad": cad(cost), "unspent_cad": cad(budget_cents - cost),
              "basis": "One draw, entire budget allocated to this game; not a spending recommendation."}
    if game.key == "649":
        issued = gold_issued if gold_issued is not None else draw.get("gold_issued")
        if issued is not None and (issued < max(1, plays) or not isinstance(issued, int)):
            raise ValueError("National Gold Ball entries must be a positive integer >= your plays.")
        classic = Fraction(plays, comb(49, 6))
        result["sharing"] = SHARING_649
        result["classic_jackpot_category"] = probability_record(classic)
        result["gold_balls"] = draw["gold_balls"]
        result["gold_issued"] = issued
        if issued is None:
            result["gold_any_million_category"] = None
            result["unknown_reason"] = "Final national Gold Ball entry count is unknown before sales close."
            result["gold_any_formula"] = f"{plays}/G, with G = final national Gold Ball entries"
            result["gold_exact_1m_formula"] = f"({plays}/G)*({draw['gold_balls'] - 1}/{draw['gold_balls']})"
        else:
            gold = Fraction(plays, issued)
            result["gold_any_million_category"] = probability_record(gold)
            result["gold_exact_1m"] = probability_record(gold * Fraction(draw["gold_balls"] - 1, draw["gold_balls"]))
            result["gold_jackpot"] = probability_record(gold / draw["gold_balls"])
            # Gold Ball prizes are never shared, so the category hit is an actual payout.
            result["gold_receive_at_least_1m"] = result["gold_any_million_category"]
            all_million = 1 - (1 - gold) * (1 - classic)
            result["any_million_category"] = probability_record(all_million)
            result["million_category_probability_per_cad_spent"] = float(all_million) / (cost / 100) if cost else 0
            result["sales_basis"] = "Conditional on supplied G; an estimate of G gives only a scenario."
    else:
        outcomes, mm = comb(52, 7), draw["maxmillions"]
        result["sharing"] = SHARING_MAX
        result["known_manual_lines"] = plays
        result["pending_quick_picks"] = 3 * plays
        result["quick_pick_assumption"] = "Independent uniform lines with replacement; pre-issue MODEL."
        for key, draws in (("main_jackpot_category", 1), ("maxmillions_category", mm),
                           ("any_million_category", mm + 1), ("maxplus_category", draw["maxplus"])):
            result[key] = probability_record(iid_quick_pick_series(plays, 3 * plays, outcomes, draws))
        result["million_category_probability_per_cad_spent"] = (
            result["any_million_category"]["decimal"] / (cost / 100) if cost else 0)
    return result


def compare_week(snapshot: dict, budget_cents: int, week_of: date, gold_issued=None) -> dict:
    monday = week_of - timedelta(days=week_of.weekday())
    sunday = monday + timedelta(days=6)
    rows = []
    for draw in snapshot["draws"]:
        if monday <= date.fromisoformat(draw["date"]) <= sunday:
            row = draw_comparison(draw, budget_cents, gold_issued)
            row["timing_relative_to_snapshot"] = ("Past draw: sales closed" if draw["date"] < snapshot["as_of"] else
                                                   "On/after snapshot date: verify current sales cutoff")
            rows.append(row)
    complete = all("million_category_probability_per_cad_spent" in row for row in rows)
    winner = max(rows, key=lambda r: r.get("million_category_probability_per_cad_spent", -1)) if rows and complete else None
    # These thresholds compare Gold Ball alone with ONE $6 Max play; they do
    # not need a guessed national sales count or confuse current/prior draws.
    thresholds = []
    for draw in snapshot["draws"]:
        if draw["game"] == "max":
            validate_draw(draw)
            p = iid_quick_pick_series(1, 3, comb(52, 7), draw["maxmillions"] + 1)
            thresholds.append({"max_draw_date": draw["date"],
                               "gold_ball_beats_max_main_plus_mm_per_dollar_if_G_less_than": float(2 / p),
                               "basis": "Gold Ball any $1M+ award vs one Max play main/MM category; IID QP model."})
    return {"snapshot_as_of": snapshot["as_of"], "week_monday": monday.isoformat(),
            "week_sunday": sunday.isoformat(), "draws": rows, "thresholds": thresholds,
            "ranking": (f"Highest historical/model/conditional category probability per dollar: {winner['game']} "
                        f"on {winner['date']}" if winner and len(rows) > 1 else
                        "No cross-game ranking: missing same-week draw inputs or national Gold Ball sales."),
            "limitations": [
                "Snapshot is offline, dated data, not a live jackpot feed. Add each draw in the requested week.",
                "A jackpot/MAXMILLIONS category hit can be shared. Actual receipt of >= $1M cannot be computed "
                "without other players' winning selections. Variable lower-tier pools are not counted here.",
                "Gold Ball awards either $1M or the gold jackpot, not both, and are never shared; given the "
                "final national entry count, its probability is a probability of actually receiving >= $1M. "
                "Extra and promotional draws are excluded.",
                "Each row independently allocates the same budget; do not buy every row using that single budget.",
            ], "sources": snapshot.get("sources", [])}
