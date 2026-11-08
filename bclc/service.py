"""Plan building shared by the command line and the web app, including the final audits."""
from .games import Game
from .generate import build_wheel, coverage, coverage_bounds, filter_reasons, generate_coverage

MAX_PLAYS = 500


def column_layout(game: Game, slip_columns: int = 7, slip_map: dict | None = None):
    if slip_map is not None:
        mapping = {int(k): str(v) for k, v in slip_map.items()}
        if set(mapping) != set(range(1, game.n + 1)):
            raise ValueError("Slip map must map every game number to its actual column label.")
        return mapping, "Custom column map supplied by the user."
    if slip_columns < 1:
        raise ValueError("Slip columns must be positive.")
    return ({x: (x - 1) % slip_columns for x in range(1, game.n + 1)},
            f"Slip columns are assumed: numbers {slip_columns} apart (like 1, {1 + slip_columns}, "
            f"{1 + 2 * slip_columns}) count as one column. PlayNow's real number pad was not checked; "
            "change the column count if yours differs.")


def make_plan(game: Game, budget_cents: int, past, history: dict, columns, column_description: str, *,
              wheel=False, favourites=None, allow_overlap=False, seed=2026, restarts=None,
              candidates=500, should_stop=None, progress=None) -> dict:
    """Build a budgeted plan and audit the final lines. Odds are added separately by the caller."""
    plays = budget_cents // game.price_cents
    if plays > MAX_PLAYS:
        raise ValueError(f"This personal-use search supports at most {MAX_PLAYS} plays per run.")
    if candidates < 1:
        raise ValueError("Search candidates must be positive.")
    restarts = restarts if restarts is not None else (20 if wheel else 6)
    if restarts < 1:
        raise ValueError("Restarts must be positive.")
    result = None
    if wheel:
        result = build_wheel(game, tuple(favourites), plays, past, seed, restarts, allow_overlap, columns,
                             should_stop=should_stop, progress=progress)
        lines = result.pop("lines")
    else:
        lines = generate_coverage(game, plays, past, seed, candidates, restarts, columns,
                                  should_stop=should_stop, progress=progress)
    # Audit the final export rather than trusting the search implementation.
    lines = [tuple(line) for line in lines]
    if any(filter_reasons(line, past, columns) for line in lines) or len(set(lines)) != len(lines):
        raise RuntimeError("Internal final filter/uniqueness audit failed.")
    audit = coverage(lines)
    if not wheel and audit["maximum_pairwise_overlap"] > 2:
        raise RuntimeError("Internal overlap audit failed.")
    cost = len(lines) * game.price_cents
    if cost > budget_cents:
        raise RuntimeError("Internal budget audit failed.")
    report = {"format_version": 1, "game": game.key, "mode": "wheel" if wheel else "coverage",
              "budget_cents": budget_cents, "cost_cents": cost, "unspent_cents": budget_cents - cost,
              "affordable_plays": plays, "paid_plays": len(lines),
              "total_issued_lines": len(lines) * game.lines_per_play,
              "pending_quick_picks": (game.lines_per_play - 1) * len(lines),
              "lines": lines, "seed": seed, "history": history, "coverage": audit,
              "coverage_bounds": coverage_bounds(game, len(lines), audit),
              "column_layout": column_description, "odds": {"status": "not_counted_yet"},
              "search": "Randomized search. Global maximum coverage is certified at this line count "
                        "only when both reported coverage upper bounds are attained.",
              "sharing": "The number rules do not change your chance of winning. They avoid commonly picked "
                         "patterns so a prize is less likely to be shared, but that benefit is unproven "
                         "without data on what other players pick.",
              "scope": "One draw, no Extra, subscriptions, promotions or future free-play winnings."}
    if wheel:
        report["wheel"] = result
        report["favourites"] = tuple(sorted(favourites))
    return report


def ticket_rows(report: dict, game: Game) -> list[list[str]]:
    """Manual-entry checklist: one chosen line per paid play plus its pending Quick Pick slots."""
    rows = [["play", "line", "kind", "numbers", "cost_cad"]]
    for play, numbers in enumerate(report["lines"], 1):
        rows.append([play, 1, "enter_these_numbers", " ".join(map(str, numbers)), f"{game.price_cents / 100:.2f}"])
        for index in range(2, game.lines_per_play + 1):
            rows.append([play, index, "issued_quick_pick_pending", "", "0.00"])
    return rows
