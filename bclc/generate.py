"""Popularity filters, randomized coverage search, and exhaustively verified wheels."""
from collections import Counter
from itertools import combinations
from math import comb, ceil
import random
from .control import check_stop
from .games import Game


def filter_reasons(line, past=frozenset(), column_map=None) -> list[str]:
    values = sorted(line)
    reasons = []
    if sum(x > 31 for x in values) < 3:
        reasons.append("fewer than three numbers above 31")
    if any(values[i + 2] == values[i] + 2 and values[i + 1] == values[i] + 1
           for i in range(len(values) - 2)):
        reasons.append("run of at least three consecutive numbers")
    if any(b - a == c - b for a, b, c in combinations(values, 3)):
        reasons.append("evenly spaced three-number subsequence")
    if len({(x - 1) // 10 for x in values}) == 1:
        reasons.append("one decade (1-10, 11-20, etc.)")
    if column_map is not None and len({column_map[x] for x in values}) == 1:
        reasons.append("one column in the supplied slip layout")
    if sum(x % 7 == 0 for x in values) > 1:
        reasons.append("more than one multiple of seven")
    if sum("7" in str(x) for x in values) > 1:
        reasons.append("more than one number containing digit seven")
    if tuple(values) in past:
        reasons.append("past main jackpot combination in supplied history")
    return reasons


def coverage(lines) -> dict:
    counts = Counter(x for line in lines for x in line)
    pairs = {pair for line in lines for pair in combinations(line, 2)}
    overlaps = [len(set(a) & set(b)) for a, b in combinations(lines, 2)]
    return {"distinct_numbers": len(counts), "distinct_pairs": len(pairs),
            "maximum_pairwise_overlap": max(overlaps, default=0),
            "duplicate_lines": len(lines) - len(set(map(tuple, lines))),
            "number_uses": dict(sorted(counts.items()))}


def generate_coverage(game: Game, count: int, past=frozenset(), seed=2026,
                      candidates=500, restarts=6, column_map=None, should_stop=None,
                      progress=None) -> list[tuple[int, ...]]:
    """Lexicographic objective: distinct numbers, pairs, then balanced usage.

    This is a heuristic, not a certificate of a global optimum. Constraints
    are hard: leave money unspent if search cannot safely fill every play.
    """
    if count < 0 or candidates < 1 or restarts < 1:
        raise ValueError("Counts must be nonnegative; search settings must be positive.")
    rng = random.Random(seed)
    best, best_score = [], (-1, -1, -1, -1)
    for restart in range(restarts):
        chosen, chosen_sets = [], []
        uses, pairs = Counter(), set()
        while len(chosen) < count:
            check_stop(should_stop)
            if progress:
                progress((restart + len(chosen) / max(count, 1)) / restarts)
            winner, score = None, (-1, -1, -1, float("-inf"))
            for _ in range(candidates):
                # Alternate uniform proposals and proposals favouring unused
                # numbers. Always draw without replacement within each line.
                proposal = rng.random()
                if proposal < 0.34:
                    line = tuple(sorted(rng.sample(range(1, game.n + 1), game.k)))
                elif proposal < 0.67:
                    # Spending only the necessary three high-number slots
                    # preserves more room to cover the 31 low numbers.
                    high, low = list(range(32, game.n + 1)), list(range(1, 32))
                    rng.shuffle(high)
                    rng.shuffle(low)
                    high.sort(key=lambda x: uses[x] + rng.random() * 1.5)
                    low.sort(key=lambda x: uses[x] + rng.random() * 1.5)
                    line = tuple(sorted(high[:3] + low[:game.k - 3]))
                else:
                    pool = list(range(1, game.n + 1))
                    rng.shuffle(pool)
                    pool.sort(key=lambda x: uses[x] + rng.random() * 1.5)
                    line = tuple(sorted(pool[:game.k]))
                members = set(line)
                if filter_reasons(line, past, column_map) or any(len(members & s) > 2 for s in chosen_sets):
                    continue
                line_pairs = set(combinations(line, 2))
                objective = (sum(uses[x] == 0 for x in line), len(line_pairs - pairs),
                             -sum(x > 31 for x in line),
                             -sum(uses[x] ** 2 for x in line))
                if objective > score:
                    winner, score = line, objective
            if winner is None:
                break
            chosen.append(winner)
            chosen_sets.append(set(winner))
            uses.update(winner)
            pairs.update(combinations(winner, 2))
        objective = (len(uses), len(pairs), len(chosen), -sum(c * c for c in uses.values()))
        if objective > best_score:
            best, best_score = chosen, objective
    return best


def coverage_bounds(game: Game, count: int, audit: dict) -> dict:
    number_bound = min(game.n, game.k * count, (game.k - 3) * count + game.n - 31)
    pair_bound = min(comb(game.n, 2), comb(game.k, 2) * count)
    return {"distinct_numbers_upper_bound": number_bound, "distinct_pairs_upper_bound": pair_bound,
            "both_upper_bounds_attained": (audit["distinct_numbers"] == number_bound and
                                           audit["distinct_pairs"] == pair_bound),
            "note": "Attaining both proves maximal coverage at this line count. "
                    "Otherwise these are bounds, not a certificate of optimality."}


def strict_wheel_obstruction(v: int, k: int) -> str | None:
    """Necessary Steiner-system divisibility, since no triple can repeat."""
    for fixed in range(3):
        numerator, denominator = comb(v - fixed, 3 - fixed), comb(k - fixed, 3 - fixed)
        if numerator % denominator:
            what = ("the total number of lines", "the number of lines containing any one favourite",
                    "the number of lines containing any one pair of favourites")[fixed]
            return (f"If lines may share at most 2 numbers, each group of three favourites can appear in only "
                    f"one line, so {what} would have to be exactly {numerator}/{denominator} "
                    f"(that is, C({v - fixed},{3 - fixed})/C({k - fixed},{3 - fixed})). That is not a whole "
                    "number, so a full cover under that rule is mathematically impossible.")
    return None


def wheel_precheck(game: Game, favourites: tuple[int, ...], allow_overlap=False, past=frozenset(),
                   column_map=None) -> dict:
    """Fast checks shown before any search: incompatible constraints and uncoverable triples."""
    if not 10 <= len(favourites) <= 15 or len(set(favourites)) != len(favourites):
        raise ValueError("Choose 10-15 distinct favourite numbers.")
    if not all(1 <= x <= game.n for x in favourites):
        raise ValueError(f"Favourites must be between 1 and {game.n}.")
    obstruction = strict_wheel_obstruction(len(favourites), game.k)
    triples = list(combinations(sorted(favourites), 3))
    coverable = set()
    for line in combinations(sorted(favourites), game.k):
        if not filter_reasons(line, past, column_map):
            coverable.update(combinations(line, 3))
    uncoverable = [list(t) for t in triples if t not in coverable]
    return {"favourites": sorted(favourites), "total_triples": len(triples),
            "overlap_obstruction": obstruction,
            "needs_overlap_relaxation": obstruction is not None,
            "overlap_relaxed": allow_overlap,
            "uncoverable_triples": uncoverable,
            "guarantee_possible": not uncoverable and (allow_overlap or obstruction is None),
            "lower_bound_plays": ceil(len(triples) / comb(game.k, 3))}


def build_wheel(game: Game, favourites: tuple[int, ...], max_plays: int, past=frozenset(),
                seed=2026, restarts=20, allow_overlap=False, column_map=None,
                should_stop=None, progress=None) -> dict:
    if not 10 <= len(favourites) <= 15 or len(set(favourites)) != len(favourites):
        raise ValueError("Choose 10-15 distinct favourite numbers.")
    if not all(1 <= x <= game.n for x in favourites):
        raise ValueError(f"Favourites must be between 1 and {game.n}.")
    if not allow_overlap:
        reason = strict_wheel_obstruction(len(favourites), game.k)
        if reason:
            raise ValueError(reason + " Use --allow-wheel-overlap to explicitly relax overlap in wheel mode.")
    triples = list(combinations(favourites, 3))
    index = {triple: i for i, triple in enumerate(triples)}
    universe = (1 << len(triples)) - 1
    options = []
    for line in combinations(favourites, game.k):
        if not filter_reasons(line, past, column_map):
            mask = sum(1 << index[t] for t in combinations(line, 3))
            options.append((line, mask))
    possible = 0
    for _, mask in options:
        possible |= mask
    if possible != universe:
        missing = [list(t) for i, t in enumerate(triples) if not possible >> i & 1]
        return {"lines": [], "guaranteed": False, "reason": "Some groups of three favourites cannot appear in any line that passes the number "
                          "rules, so a full guarantee is impossible with these favourites.",
                "uncoverable_triples": missing, "covered_triples": 0, "total_triples": len(triples),
                "lower_bound_plays": ceil(len(triples) / comb(game.k, 3))}
    rng, best = random.Random(seed), None
    # Construct a complete cover first, then trim to the authorized budget.
    # A failed search is never called a proof of minimum required cost.
    for restart in range(restarts):
        if progress:
            progress(restart / restarts)
        remaining, selection = universe, []
        available = list(range(len(options)))
        while remaining:
            check_stop(should_stop)
            gains = [(options[i][1] & remaining).bit_count() for i in available]
            gain = max(gains, default=0)
            if not gain:
                break
            ties = [i for i, value in zip(available, gains) if value == gain]
            selected = rng.choice(ties)
            selection.append(selected)
            remaining &= ~options[selected][1]
            available.remove(selected)
        # Remove redundant blocks, preserving a full cover.
        for selected in selection[:][::-1]:
            covered = 0
            for other in selection:
                if other != selected:
                    covered |= options[other][1]
            if covered == universe:
                selection.remove(selected)
        if best is None or len(selection) < len(best):
            best = selection
    full = [options[i][0] for i in best]
    lines = full[:max_plays]
    certificate = verify_wheel(lines, favourites)
    return {"lines": lines, **certificate,
            "overlap_relaxed": allow_overlap,
            "full_cover_found_plays": len(full),
            "lower_bound_plays": ceil(len(triples) / comb(game.k, 3)),
            "reason": "Every group of three favourites is covered." if certificate["guaranteed"] else
                      "The full cover found needs more plays than this budget allows, so there is no "
                      "guarantee. The cover found is not proven to be the cheapest possible."}


def verify_wheel(lines, favourites) -> dict:
    covered = {t for line in lines for t in combinations(sorted(line), 3)}
    universe = set(combinations(sorted(favourites), 3))
    missing = sorted(universe - covered)
    return {"guaranteed": not missing, "covered_triples": len(universe & covered),
            "total_triples": len(universe), "uncovered_triples": [list(t) for t in missing]}
