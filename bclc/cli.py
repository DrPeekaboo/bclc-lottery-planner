"""Command-line workflows and auditable CSV/JSON/text exports."""
import argparse
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

from .analysis import analyze
from .compare import compare_week, validate_draw
from .games import GAMES, cad, load_history, load_receipt, money_cents, parse_numbers
from .generate import coverage
from .odds import portfolio_odds, series_probability, probability_record
from .service import MAX_PLAYS, column_layout, make_plan, ticket_rows


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def output_directory(value):
    directory = Path(value)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def format_probability(record):
    if record is None:
        return "unknown"
    if record["numerator"] == "0":
        return "0 (impossible)"
    exact = f"{record['numerator']}/{record['denominator']}"
    if len(exact) > 70:
        exact = "fraction stored in JSON"
    return (f"{record['percent']:.9g}% | 1 in {record['one_in']:,.8g} | exact {exact}")


def odds_text(report):
    parts = [report["definition"],
             f"Known lines: {report['known_lines']}; distinct: {report['distinct_known_lines']}; "
             f"pending Quick Picks: {report['pending_quick_picks']}"]
    for row in report["tiers"]:
        if row["status"] == "exact":
            parts.append(f"{row['tier']:14} {row['prize']:22} {format_probability(row['at_least_one'])}")
        else:
            parts.append(f"{row['tier']:14} EXACT COUNT NOT FINISHED; rigorous bounds "
                         f"[{row['lower_bound']['decimal']:.9g}, {row['upper_bound']['decimal']:.9g}]. "
                         "Rerun with --exact-work-limit 0.")
        if "including_pending_iid_quick_pick_model" in row:
            parts.append("  Including pending Quick Picks, IID MODEL: " +
                         format_probability(row["including_pending_iid_quick_pick_model"]))
    if report["pending_quick_picks"]:
        parts.append(report["quick_pick_model"])
    return "\n".join(parts)


def analysis_text(report):
    lines = [f"{report['game']} HISTORY ANALYSIS", report["p_value_method"], report["gap_note"]]
    for regime in report["regimes"]:
        lines.extend(["", f"1-{regime['pool']} regime: {regime['draws']} supplied draws; "
                      f"{regime['first_date']} to {regime['last_date']}",
                      "Number Main Bonus Last drawn  Gap(draws) Gap(days)"])
        for row in regime["frequencies"]:
            gap = (">=" if row["gap_is_lower_bound"] else "") + str(row["gap_draws"])
            lines.append(f"{row['number']:6} {row['main_count']:4} {row['bonus_count']:5} "
                         f"{row['last_drawn'] or 'never':11} {gap:10} {str(row['gap_calendar_days']):9}")
        for name, values in regime["splits"].items():
            lines.append(f"{name} counts per draw (0..k): {values}")
        lines.append("Low/high definition: " + regime["low_definition"])
        lines.append(f"Sum min / mean / max: {regime['sum_min']} / {regime['sum_mean']:.2f} / {regime['sum_max']}")
        lines.append("Sum bands: " + "; ".join(f"{x['from']}-{x['to']}: {x['draws']}" for x in regime["sum_ranges"]))
        for test in regime["tests"]:
            lines.append(f"{test['name']}: chi-square={test['chi_square']:.5f}; "
                         f"MC p={test['mc_p']:.5f} (SE about {test['mc_standard_error']:.5f}); "
                         f"Holm p={test['holm_adjusted_p']:.5f}. {test['conclusion']}")
    lines.append("Frequencies and gaps do not predict the next draw. No player-choice dataset is supplied.")
    return "\n".join(lines)


def layout(args, game):
    if args.slip_map:
        values = json.loads(Path(args.slip_map).read_text(encoding="utf-8-sig"))
        mapping, _ = column_layout(game, slip_map=values)
        return mapping, f"Custom column map: {args.slip_map}"
    mapping, description = column_layout(game, args.slip_columns)
    return mapping, description.replace("your own column map", "--slip-map")


def history_for_generation(args, game):
    if not args.history:
        if not args.allow_missing_history:
            raise ValueError("Supply --history your_results.csv. Until you have it, explicitly use "
                             "--allow-missing-history; past-combination exclusion will be unavailable.")
        return frozenset(), {"path": None, "warning": "NO HISTORY: past-combination exclusion is unavailable."}
    draws = load_history(args.history, game)
    return frozenset(d.numbers for d in draws), {
        "path": str(Path(args.history).resolve()), "draws": len(draws),
        "first_date": draws[0].day.isoformat(), "last_date": draws[-1].day.isoformat(),
        "sha256": hashlib.sha256(Path(args.history).read_bytes()).hexdigest(),
        "warning": "Only main combinations present in this supplied CSV can be excluded."}


def write_plan(directory, report, game):
    with (directory / "tickets.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        csv.writer(stream).writerows(ticket_rows(report, game))
    # The blank receipt must be completed with actual issued numbers before
    # exact full-portfolio analysis. It is not a PlayNow upload format.
    with (directory / "receipt_template.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["play", "line", "numbers"])
        for play, numbers in enumerate(report["lines"], 1):
            writer.writerow([play, 1, " ".join(map(str, numbers))])
            for index in range(2, game.lines_per_play + 1):
                writer.writerow([play, index, ""])
    save_json(directory / "report.json", report)


def generate_command(args, wheel=False):
    game = GAMES[args.game]
    budget = money_cents(args.budget)
    plays = budget // game.price_cents
    if plays > MAX_PLAYS:
        raise ValueError(f"This personal-use search supports at most {MAX_PLAYS} plays per run.")
    past, history = history_for_generation(args, game)
    columns, column_description = layout(args, game)
    report = make_plan(game, budget, past, history, columns, column_description, wheel=wheel,
                       favourites=parse_numbers(args.favourites) if wheel else None,
                       allow_overlap=wheel and args.allow_wheel_overlap, seed=args.seed,
                       restarts=args.restarts, candidates=getattr(args, "candidates", 500))
    result = report.get("wheel")
    lines, audit, bounds = report["lines"], report["coverage"], report["coverage_bounds"]
    cost = report["cost_cents"]
    directory = output_directory(args.out)
    write_plan(directory, report, game)
    print(f"Chosen lines saved to {directory.resolve()}; calculating known-line odds...", flush=True)
    odds = portfolio_odds(lines, game, args.exact_work_limit,
                          3 * len(lines) if game.key == "max" else 0,
                          lambda label: print(f"  {label}", flush=True))
    report["odds"] = odds
    save_json(directory / "report.json", report)
    text = [game.name + (" WHEEL" if wheel else " COVERAGE PLAN")]
    if wheel:
        text.append("VERIFIED MATCH-3-OR-BETTER COVER" if result["guaranteed"] else "PARTIAL/IMPOSSIBLE WHEEL: NO GUARANTEE")
        text.append(result["reason"])
        text.append(f"Covered favourite triples: {result['covered_triples']}/{result['total_triples']}")
        for key, label in (("uncovered_triples", "Uncovered triples"),
                           ("uncoverable_triples", "Triples no filtered line can cover")):
            missing = result.get(key) or []
            if missing:
                shown = "; ".join(" ".join(map(str, t)) for t in missing[:40])
                more = f" (+{len(missing) - 40} more in report.json)" if len(missing) > 40 else ""
                text.append(f"{label} ({len(missing)}): {shown}{more}")
        if result.get("full_cover_found_plays"):
            text.append(f"Full cover found: {result['full_cover_found_plays']} paid plays, "
                        f"{cad(result['full_cover_found_plays'] * game.price_cents)}; not a proven minimum.")
    text.extend([f"Budget {cad(budget)} | Cost {cad(cost)} | Unspent {cad(budget - cost)}",
                 "Play | Numbers to enter | Included Quick Picks"])
    for play, line in enumerate(lines, 1):
        text.append(f"{play:4} | {' '.join(f'{x:02}' for x in line)} | "
                    f"{game.lines_per_play - 1} {'pending' if game.key == 'max' else ''}")
    text.extend([f"Coverage: {audit['distinct_numbers']} numbers, {audit['distinct_pairs']} pairs; "
                 f"maximum known-line overlap {audit['maximum_pairwise_overlap']}.",
                 column_description, history["warning"], report["search"], report["sharing"]])
    text.append(f"Coverage upper bounds at this line count: {bounds['distinct_numbers_upper_bound']} numbers, "
                f"{bounds['distinct_pairs_upper_bound']} pairs; both attained: {bounds['both_upper_bounds_attained']}.")
    if len(lines) < plays and not wheel:
        text.append("Search left budget unspent to preserve the hard constraints. More restarts may help.")
    if game.key == "max":
        text.append("Enter ONE chosen line per $6 play. Three lines are issued by Quick Pick. "
                    "Their patterns, overlap and duplicates cannot be controlled before issue.")
    if game.key == "649":
        text.append("Gold Ball: one assigned entry per paid play. Probability of its award = plays/G; "
                    "exact $1M = (plays/G)*(B-1)/B, gold jackpot = plays/(G*B). "
                    "G is national issued entries and B is balls remaining. Use compare with dated draw inputs.")
    text.extend(["", odds_text(odds), "", "CSV is a manual-entry checklist, not an automated purchase/upload."])
    final = "\n".join(text)
    (directory / "report.txt").write_text(final + "\n", encoding="utf-8")
    print(final)
    print(f"\nSaved to {directory.resolve()}")
    if wheel and not result["guaranteed"]:
        return 3
    return 0 if odds["exact_all_main_tiers_complete"] else 4


def odds_command(args):
    game = GAMES[args.game]
    if bool(args.snapshot) != bool(args.draw_date):
        raise ValueError("Use --snapshot and --draw-date together.")
    if args.receipt:
        lines = load_receipt(args.receipt, game)
        pending = 0
        actual_cost = len(lines) // game.lines_per_play * game.price_cents
        if args.budget is not None and actual_cost > money_cents(args.budget):
            raise ValueError("The supplied receipt exceeds the stated budget.")
        scope = "All actual issued receipt lines; duplicates are retained for expected winner counts."
    else:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8-sig"))
        if plan["game"] != args.game:
            raise ValueError("Plan game does not match --game.")
        lines = [tuple(line) for line in plan["lines"]]
        from .games import validate_line
        for line in lines:
            validate_line(line, game)
        if args.budget is not None and len(lines) * game.price_cents > money_cents(args.budget):
            raise ValueError("The saved plan exceeds the stated budget.")
        pending = (game.lines_per_play - 1) * len(lines)
        scope = ("Saved chosen lines; each play's three Quick Pick lines are still pending." if pending else
                 "Saved chosen lines; every line in the plan is known.")
    report = portfolio_odds(lines, game, args.exact_work_limit, pending,
                            lambda label: print(f"Counting {label}...", flush=True))
    report["scope"] = scope
    report["coverage_audit"] = coverage(lines)
    if args.history:
        past = {draw.numbers for draw in load_history(args.history, game)}
        report["receipt_lines_in_supplied_history"] = [i + 1 for i, line in enumerate(lines) if tuple(line) in past]
    if args.snapshot:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8-sig"))
        matching = [draw for draw in snapshot["draws"] if draw["game"] == game.key and draw["date"] == args.draw_date]
        if len(matching) != 1:
            raise ValueError("Snapshot needs exactly one matching --draw-date and game.")
        draw = matching[0]
        validate_draw(draw)
        if game.key == "max":
            if pending:
                raise ValueError("Additional-draw receipt odds require --receipt with all actual issued lines.")
            unique = len(set(lines))
            report["additional_draws"] = {
                "date": args.draw_date,
                "maxmillions_at_least_one": probability_record(series_probability(unique, 52, 7, draw["maxmillions"])),
                "maxplus_at_least_one": probability_record(series_probability(unique, 52, 7, draw["maxplus"])),
                "main_or_maxmillions": probability_record(series_probability(unique, 52, 7, draw["maxmillions"] + 1)),
                "assumption": "Independent uniform separate draws; prizes can be shared."}
        else:
            from fractions import Fraction
            issued = draw.get("gold_issued")
            if issued is None or issued < len(lines):
                raise ValueError("Supply gold_issued national sales >= your paid plays in the snapshot.")
            gold = Fraction(len(lines), issued)
            report["gold_ball"] = {"date": args.draw_date,
                                   "any_award": probability_record(gold),
                                   "exact_1m": probability_record(gold * Fraction(draw["gold_balls"] - 1, draw["gold_balls"])),
                                   "jackpot": probability_record(gold / draw["gold_balls"])}
    directory = output_directory(args.out)
    save_json(directory / "odds.json", report)
    final = scope + "\n" + odds_text(report)
    if "additional_draws" in report:
        final += "\nAdditional draw probabilities: " + json.dumps(report["additional_draws"], indent=2)
    if "gold_ball" in report:
        final += "\nGold Ball: " + json.dumps(report["gold_ball"], indent=2)
    (directory / "odds.txt").write_text(final + "\n", encoding="utf-8")
    print(final)
    print(f"Saved to {directory.resolve()}")
    return 0 if report["exact_all_main_tiers_complete"] else 4


def make_parser():
    parser = argparse.ArgumentParser(description="Offline BCLC analysis, coverage and exact odds. No purchases.")
    commands = parser.add_subparsers(dest="command", required=True)
    history = commands.add_parser("analyze", help="Analyze a history CSV using calibrated chi-square tests.")
    history.add_argument("--game", choices=GAMES, required=True)
    history.add_argument("--history", required=True)
    history.add_argument("--simulations", type=int, default=1999)
    history.add_argument("--seed", type=int, default=2026)
    history.add_argument("--alpha", type=float, default=0.05)
    history.add_argument("--out", default="output/history")
    helps = {"generate": "Budgeted coverage plan using the popularity-avoidance filters.",
             "wheel": "Favourite-number wheel with an exhaustively verified match-3 cover check."}
    for command in ("generate", "wheel"):
        child = commands.add_parser(command, help=helps[command])
        child.add_argument("--game", choices=GAMES, required=True)
        child.add_argument("--budget", required=True, help="Total CAD for ONE draw, e.g. 30.00")
        child.add_argument("--history")
        child.add_argument("--allow-missing-history", action="store_true")
        child.add_argument("--seed", type=int, default=2026)
        child.add_argument("--restarts", type=int, default=20 if command == "wheel" else 6)
        child.add_argument("--slip-columns", type=int, default=7)
        child.add_argument("--slip-map", help="JSON number-to-column mapping for your actual layout.")
        child.add_argument("--exact-work-limit", type=int, default=200_000, help="Per tier. 0 = unlimited exact search.")
        child.add_argument("--out", default=f"output/{command}")
        if command == "generate":
            child.add_argument("--candidates", type=int, default=500)
        else:
            child.add_argument("--favourites", required=True, help='10-15 numbers, quoted, e.g. "1 4 ..."')
            child.add_argument("--allow-wheel-overlap", action="store_true",
                               help="Explicitly relax the <=2 overlap constraint for a guaranteed cover.")
    odds = commands.add_parser("odds", help="Recount a saved plan or all actual issued receipt lines.")
    odds.add_argument("--game", choices=GAMES, required=True)
    source = odds.add_mutually_exclusive_group(required=True)
    source.add_argument("--receipt")
    source.add_argument("--plan", help="report.json from generate/wheel")
    odds.add_argument("--budget", help="Optional receipt budget audit.")
    odds.add_argument("--history", help="Optional audit of actual receipt against past main combinations.")
    odds.add_argument("--snapshot", help="Dated draw JSON for additional prize draws.")
    odds.add_argument("--draw-date", help="ISO draw date required with --snapshot.")
    odds.add_argument("--exact-work-limit", type=int, default=200_000)
    odds.add_argument("--out", default="output/odds")
    compare = commands.add_parser("compare", help="Compare dated draw inputs in one calendar week.")
    compare.add_argument("--snapshot", required=True)
    compare.add_argument("--budget", required=True)
    compare.add_argument("--week-of", type=date.fromisoformat, default=date.today())
    compare.add_argument("--gold-issued", type=int, help="Final national entries, or explicitly a sales scenario.")
    compare.add_argument("--out", default="output/comparison")
    check = commands.add_parser("check", help="Check your recorded 6/49 tickets against official results "
                                              "and rebuild the one-row-per-day results log.")
    check.add_argument("--tickets", help="Ticket CSV (default results/my_tickets.csv in the tool folder).")
    check.add_argument("--cache", help="Saved official draws (default results/official_draws.csv).")
    check.add_argument("--log", help="Daily log CSV (default results/results_log.csv).")
    check.add_argument("--html-file", help="Parse a saved results page instead of fetching it.")
    check.add_argument("--today", type=date.fromisoformat, help="Override today's date (testing).")
    check.add_argument("--start", type=date.fromisoformat, help="First log date (default: first ticket).")
    return parser


def check_command(args):
    from .results import run_check, show_gold
    folder = Path(__file__).resolve().parent.parent / "results"
    tickets = Path(args.tickets) if args.tickets else folder / "my_tickets.csv"
    cache = Path(args.cache) if args.cache else folder / "official_draws.csv"
    log = Path(args.log) if args.log else folder / "results_log.csv"
    page = Path(args.html_file).read_text(encoding="utf-8", errors="replace") if args.html_file else None
    summary = run_check(tickets, cache, log, args.today or date.today(), page, args.start)
    text = [f"Lotto 6/49 results check for {summary['today'].isoformat()} "
            f"(log covers {summary['start'].isoformat()} onward)"]
    if summary["fetch_error"]:
        text.append("WARNING: " + summary["fetch_error"] + " Used saved results only.")
    latest = summary["latest"]
    if latest:
        gold = ", ".join(f"{show_gold(n)} ({p})" for n, p in latest.gold_ball) or "not shown"
        text.append(f"Latest official draw {latest.day.strftime('%a %Y-%m-%d')}: "
                    f"{' '.join(map(str, latest.numbers))}, bonus {latest.bonus}; "
                    f"Gold Ball winner {gold}, {latest.ball_drawn or 'unknown'} ball")
    text.append("Ticket results:")
    ticket_rows = [row for row in summary["rows"] if row[6]]
    for row in ticket_rows:
        prize = f" | prize: {row[10]}" if row[10] and row[10] != "none" else ""
        text.append(f"  {row[0]} | {row[6]} | {row[9]}{prize}")
    if not ticket_rows:
        text.append("  No tickets recorded yet. Add them to " + str(tickets))
    today_row = summary["rows"][-1] if summary["rows"] else None
    if today_row and not today_row[6]:
        text.append(f"Today ({today_row[0]}): {today_row[9]}")
    text.append("WINS: " + ("; ".join(summary["wins"]) if summary["wins"] else "none found"))
    if summary["wins"]:
        text.append("Confirm any win with PlayNow or a BCLC retailer; official BCLC results prevail.")
    for ticket in summary["unconfirmed"]:
        text.append(f"Reminder: ticket for {ticket.day.isoformat()} is marked unconfirmed. "
                    "Set purchased to yes (bought) or no (not bought) in the ticket file.")
    for ticket in summary["missing_gold_ball"]:
        if ticket.purchased != "no":
            text.append(f"Reminder: add the 10-digit Gold Ball number for {ticket.day.isoformat()} "
                        "so the $1M Gold Ball can be checked.")
    text.append(f"Daily log: {log} ({len(summary['rows'])} rows); tickets: {tickets}")
    print("\n".join(text))
    return 5 if summary["fetch_error"] else 0


def comparison_text(report):
    text = [f"Million-dollar CATEGORY comparison: {report['week_monday']} to {report['week_sunday']}",
            f"Offline snapshot: {report['snapshot_as_of']}",
            "Date       | Game        | Plays | Cost       | At least one $1M+ category"]
    for row in report["draws"]:
        p = row.get("any_million_category")
        odds = f"1 in {p['one_in']:,.8g}" if p and p["one_in"] else "unknown" if p is None else "0"
        text.append(f"{row['date']} | {row['game']:11} | {row['plays']:5} | {row['spent_cad']:10} | {odds}")
        text.append("  " + row["timing_relative_to_snapshot"])
        for key in ("gold_any_million_category", "gold_exact_1m", "gold_jackpot", "classic_jackpot_category",
                    "main_jackpot_category", "maxmillions_category", "maxplus_category"):
            if key in row:
                text.append(f"  {key}: {format_probability(row[key])}")
        if "unknown_reason" in row:
            text.extend(["  " + row["unknown_reason"], "  Gold Ball: " + row["gold_any_formula"]])
        if "million_category_probability_per_cad_spent" in row:
            text.append(f"  Category probability per CAD spent: {row['million_category_probability_per_cad_spent']:.9g}")
        if "quick_pick_assumption" in row:
            text.append("  " + row["quick_pick_assumption"])
        for name, note in row.get("sharing", {}).items():
            text.append(f"  Sharing, {name}: {note}")
    text.append(report["ranking"])
    for row in report["thresholds"]:
        text.append(f"Gold Ball alone beats Max main+MM per CAD for the {row['max_draw_date']} draw "
                    "if national Gold Ball entries G < "
                    f"{row['gold_ball_beats_max_main_plus_mm_per_dollar_if_G_less_than']:,.2f} (IID QP model).")
    text.extend(report["limitations"])
    text.append("Exact fractions and source URLs are in comparison.json.")
    return "\n".join(text)


def wizard():
    print("BCLC number planner: one draw, no Extra. Press Ctrl+C to cancel.")
    game = input("Game [649/max]: ").strip().lower()
    budget = input("Total budget in CAD: ").strip()
    history = input("History CSV path (blank only for a history-free demo): ").strip().strip('"')
    args = ["generate", "--game", game, "--budget", budget]
    args.extend(["--history", history] if history else ["--allow-missing-history"])
    return args


def main(argv=None):
    try:
        supplied = sys.argv[1:] if argv is None else argv
        args = make_parser().parse_args(supplied if supplied else wizard())
        if hasattr(args, "exact_work_limit") and args.exact_work_limit < 0:
            raise ValueError("Exact work limit must be nonnegative.")
        if hasattr(args, "restarts") and args.restarts < 1:
            raise ValueError("Restarts must be positive.")
        if args.command == "analyze":
            game = GAMES[args.game]
            report = analyze(load_history(args.history, game), game, args.simulations, args.seed, args.alpha)
            report["source_csv"] = {"path": str(Path(args.history).resolve()),
                                    "sha256": hashlib.sha256(Path(args.history).read_bytes()).hexdigest()}
            directory = output_directory(args.out)
            save_json(directory / "analysis.json", report)
            final = analysis_text(report)
            (directory / "analysis.txt").write_text(final + "\n", encoding="utf-8")
            print(final)
            print(f"Saved to {directory.resolve()}")
            return 0
        if args.command in ("generate", "wheel"):
            return generate_command(args, args.command == "wheel")
        if args.command == "odds":
            return odds_command(args)
        if args.command == "check":
            return check_command(args)
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8-sig"))
        report = compare_week(snapshot, money_cents(args.budget), args.week_of, args.gold_issued)
        directory = output_directory(args.out)
        save_json(directory / "comparison.json", report)
        final = comparison_text(report)
        (directory / "comparison.txt").write_text(final + "\n", encoding="utf-8")
        print(final)
        print(f"Saved to {directory.resolve()}")
        return 0
    except KeyboardInterrupt:
        print("\nCancelled. No tickets were purchased.", file=sys.stderr)
        return 130
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
