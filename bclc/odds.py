"""Exact finite-space union probabilities, never independence across fixed lines.

The counter branches on groups of numbers with identical ticket memberships.
Binomial multiplicities count all concrete draws in each branch. Impossible or
certain events are removed early; disjoint residual events use polynomials.
Large portfolios can be expensive. A work limit produces explicit bounds, not
an estimate pretending to be exact. Set the limit to zero to finish unbounded.
"""
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
from math import comb
import time
from .control import check_stop
from .games import Game


@dataclass(frozen=True)
class Tier:
    label: str
    matches: int
    bonus: bool | None
    prize: str


TIERS = {
    "649": (
        Tier("6/6", 6, None, "share of $5M"),
        Tier("5/6+bonus", 5, True, "variable pool share"),
        Tier("5/6", 5, False, "variable pool share"),
        Tier("4/6", 4, None, "variable pool share"),
        Tier("3/6", 3, None, "$10"),
        Tier("2/6+bonus", 2, True, "$5"),
        Tier("2/6", 2, False, "free play"),
    ),
    "max": (
        Tier("7/7", 7, None, "jackpot share"),
        Tier("6/7+bonus", 6, True, "variable pool share"),
        Tier("6/7", 6, False, "variable pool share"),
        Tier("5/7+bonus", 5, True, "variable pool share"),
        Tier("5/7", 5, False, "variable pool share"),
        Tier("4/7+bonus", 4, True, "variable pool share"),
        Tier("4/7", 4, False, "$20"),
        Tier("3/7+bonus", 3, True, "$20"),
        Tier("3/7", 3, False, "free play"),
    ),
}


def number_mask(line) -> int:
    return sum(1 << (x - 1) for x in line)


def integer_string(value: int) -> str:
    """Serialize huge exact fractions without changing Python's safety limit."""
    try:
        return str(value)
    except ValueError:
        if value < 0:
            return "-" + integer_string(-value)
        parts = []
        base = 10 ** 1000
        while value:
            value, remainder = divmod(value, base)
            parts.append(str(remainder))
        return parts[-1] + "".join(part.zfill(1000) for part in reversed(parts[:-1]))


def probability_record(p: Fraction) -> dict:
    return {"numerator": integer_string(p.numerator), "denominator": integer_string(p.denominator),
            "decimal": float(p), "percent": float(p) * 100,
            "one_in": float(1 / p) if p else None}


def single_probability(game: Game, tier: Tier) -> Fraction:
    n, k, r = game.n, game.k, tier.matches
    base = Fraction(comb(k, r) * comb(n - k, k - r), comb(n, k))
    if tier.bonus is True:
        return base * Fraction(k - r, n - k)
    if tier.bonus is False:
        return base * Fraction(n - 2 * k + r, n - k)
    return base


class WorkLimit(Exception):
    pass


class UnionCounter:
    def __init__(self, work_limit=200_000, should_stop=None, deadline=None):
        self.limit = work_limit
        self.visits = 0
        self.cache = {}
        self.cache_weight = 0
        # Optional: should_stop() -> True cancels; a time.monotonic() deadline acts like the work limit.
        self.should_stop = should_stop
        self.deadline = deadline

    def count(self, n: int, k: int, events) -> int:
        """Events are (remaining-number mask, minimum hits, maximum hits)."""
        if k < 0 or k > n:
            return 0
        normalized = []
        for mask, low, high in events:
            size = mask.bit_count()
            least, most = max(0, k - (n - size)), min(k, size)
            if low > most or high < least:
                continue
            if low <= least and high >= most:
                return comb(n, k)
            normalized.append((mask, max(low, least), min(high, most)))
        if not normalized:
            return 0
        normalized = tuple(sorted(set(normalized)))
        key = (n, k, normalized)
        if key in self.cache:
            return self.cache[key]
        self.visits += 1
        if self.limit and self.visits > self.limit:
            raise WorkLimit
        if self.visits & 511 == 1:  # first visit, then every 512
            check_stop(self.should_stop)
            if self.deadline is not None and time.monotonic() > self.deadline:
                raise WorkLimit
        if len(normalized) == 1:
            mask, low, high = normalized[0]
            size = mask.bit_count()
            return sum(comb(size, hit) * comb(n - size, k - hit)
                       for hit in range(low, high + 1))
        union, disjoint = 0, True
        for mask, _, _ in normalized:
            if union & mask:
                disjoint = False
            union |= mask
        if disjoint:
            # Count draws satisfying none of the independent *number-set*
            # constraints, with a common total k; probabilities are dependent.
            outside = n - union.bit_count()
            polynomial = [comb(outside, i) if i <= outside else 0 for i in range(k + 1)]
            for mask, low, high in normalized:
                size = mask.bit_count()
                factor = [comb(size, i) if i <= size and not low <= i <= high else 0
                          for i in range(k + 1)]
                polynomial = [sum(polynomial[j] * factor[i - j] for j in range(i + 1))
                              for i in range(k + 1)]
            result = comb(n, k) - polynomial[k]
        else:
            # Equivalent numbers can be selected together in one weighted
            # branch, dramatically reducing states for small portfolios.
            groups = defaultdict(int)
            remaining = union
            while remaining:
                bit = remaining & -remaining
                signature = sum(1 << i for i, (mask, _, _) in enumerate(normalized) if bit & mask)
                groups[signature] |= bit
                remaining ^= bit
            signature, group_mask = max(groups.items(), key=lambda item: (item[0].bit_count(),
                                                                         item[1].bit_count()))
            size = group_mask.bit_count()
            result = 0
            for take in range(max(0, k - (n - size)), min(k, size) + 1):
                reduced = [(mask & ~group_mask,
                            low - (take if signature >> i & 1 else 0),
                            high - (take if signature >> i & 1 else 0))
                           for i, (mask, low, high) in enumerate(normalized)]
                result += comb(size, take) * self.count(n - size, k - take, reduced)
        # Limit cached event storage as well as visits. Large portfolios can
        # otherwise retain hundreds of line constraints in every state.
        self.cache_weight += len(normalized) + 1
        if self.cache_weight > 300_000:
            self.cache.clear()
            self.cache_weight = len(normalized) + 1
        self.cache[key] = result
        return result


def count_components(counter, n, k, events):
    """Sum disconnected groups of mutually exclusive possible winners."""
    if len(events) < 2:
        return counter.count(n, k, events)
    unseen, components = set(range(len(events))), []
    while unseen:
        component, frontier = [], [unseen.pop()]
        while frontier:
            index = frontier.pop()
            component.append(index)
            mask, low, _ = events[index]
            neighbours = []
            for other in unseen:
                other_mask, other_low, _ = events[other]
                overlap = (mask & other_mask).bit_count()
                # Necessary condition for both events: their minimum hit
                # requirements can fit inside the k drawn main numbers.
                if low + other_low - min(overlap, low, other_low) <= k:
                    neighbours.append(other)
            unseen.difference_update(neighbours)
            frontier.extend(neighbours)
        components.append(component)
    return sum(counter.count(n, k, [events[i] for i in component]) for component in components)


def exact_tier_probability(lines, game: Game, tier: Tier, work_limit=200_000,
                           should_stop=None, deadline=None) -> Fraction:
    masks = sorted(set(number_mask(line) for line in lines))
    if not masks:
        return Fraction(0)
    if tier.matches == game.k:
        return Fraction(len(masks), comb(game.n, game.k))
    maximum_overlap = max(((a & b).bit_count() for a, b in combinations(masks, 2)), default=0)
    if 2 * tier.matches > game.k + maximum_overlap:
        # It is impossible for two distinct lines to hit this tier together.
        return len(masks) * single_probability(game, tier)
    counter = UnionCounter(work_limit, should_stop, deadline)
    if tier.bonus is None:
        count = count_components(counter, game.n, game.k, [(mask, tier.matches, tier.matches) for mask in masks])
        return Fraction(count, comb(game.n, game.k))
    # Choose the bonus first. Bonus numbers with the same membership vector
    # are exchangeable; multiply a representative count by the class size.
    bonus_groups = defaultdict(list)
    for number in range(game.n):
        signature = tuple(bool(mask & (1 << number)) for mask in masks)
        bonus_groups[signature].append(number)
    count = 0
    for signature, numbers in bonus_groups.items():
        bonus = 1 << numbers[0]
        events = [(mask & ~bonus, tier.matches, tier.matches)
                  for mask, present in zip(masks, signature) if present == tier.bonus]
        count += len(numbers) * count_components(counter, game.n - 1, game.k, events)
    return Fraction(count, comb(game.n, game.k) * (game.n - game.k))


def portfolio_odds(lines, game: Game, work_limit=200_000, pending_quick_picks=0, progress=None,
                   should_stop=None, time_limit_per_tier=None) -> dict:
    """time_limit_per_tier (seconds) behaves like the work limit: bounds, never an estimate."""
    unique = sorted(set(map(tuple, lines)))
    records = []
    for tier in TIERS[game.key]:
        if progress:
            progress(tier.label)
        check_stop(should_stop)
        deadline = time.monotonic() + time_limit_per_tier if time_limit_per_tier else None
        marginal = single_probability(game, tier)
        expected = len(lines) * marginal
        record = {"tier": tier.label, "prize": tier.prize,
                  "single_line": probability_record(marginal),
                  "expected_winning_lines": {"numerator": integer_string(expected.numerator),
                                             "denominator": integer_string(expected.denominator),
                                             "decimal": float(expected)}}
        try:
            p = exact_tier_probability(unique, game, tier, work_limit, should_stop, deadline)
            record.update(status="exact", at_least_one=probability_record(p))
            if pending_quick_picks:
                model = 1 - (1 - p) * (1 - marginal) ** pending_quick_picks
                record["including_pending_iid_quick_pick_model"] = probability_record(model)
        except WorkLimit:
            record.update(status="work_limit_reached", at_least_one=None,
                          lower_bound=probability_record(marginal if unique else Fraction(0)),
                          upper_bound=probability_record(min(Fraction(1), len(unique) * marginal)),
                          next_step="Rerun odds on these saved lines with --exact-work-limit 0.")
        records.append(record)
    return {"game": game.name, "known_lines": len(lines), "distinct_known_lines": len(unique),
            "exact_all_main_tiers_complete": all(row["status"] == "exact" for row in records),
            "pending_quick_picks": pending_quick_picks,
            "definition": "Probability of at least one line in EACH specified tier in ONE main draw. "
                          "Different tiers can both occur; do not add these probabilities.",
            "quick_pick_model": "If shown, pre-issue model assumes independent uniform Quick Pick lines "
                                "with replacement. It is model-dependent, not a guarantee about issued tickets. "
                                "Import all issued numbers to obtain unconditional-on-Quick-Pick-model exact odds.",
            "work_limit_per_tier": work_limit, "time_limit_per_tier_seconds": time_limit_per_tier,
            "tiers": records}


def series_probability(distinct_lines: int, n: int, k: int, draws: int) -> Fraction:
    """Fixed actual portfolio, independent additional full-number draws."""
    return 1 - (1 - Fraction(distinct_lines, comb(n, k))) ** draws


def iid_quick_pick_series(manual: int, random_lines: int, outcomes: int, draws: int) -> Fraction:
    """Pre-issue exact MODEL, including possible duplicate Quick Picks.

    Condition on r distinct winning combinations across draws. S(d,r) counts
    partitions of d draw positions into r nonempty groups. This avoids falsely
    treating re-used Quick Picks as fresh selections for each additional draw.
    """
    if draws == 0:
        return Fraction(0)
    if not 0 <= manual <= outcomes or random_lines < 0 or draws < 0:
        raise ValueError("Invalid model counts.")
    stirling = [1] + [0] * draws
    for d in range(1, draws + 1):
        stirling = [0] + [stirling[r - 1] + r * stirling[r] for r in range(1, draws + 1)]
    miss_numerator, falling = 0, 1
    for distinct in range(draws + 1):
        if distinct:
            falling *= outcomes - manual - distinct + 1
        if distinct > outcomes - manual:
            break
        miss_numerator += stirling[distinct] * falling * (outcomes - distinct) ** random_lines
    return 1 - Fraction(miss_numerator, outcomes ** (draws + random_lines))
