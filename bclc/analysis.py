"""Descriptive reports and chi-square tests calibrated to draws without replacement."""
from collections import Counter
from itertools import groupby
from math import comb, sqrt
import random
from .control import check_stop
from .games import Draw, Game


def pearson(observed, expected):
    return sum((o - e) ** 2 / e for o, e in zip(observed, expected) if e > 0)


def split_probabilities(n: int, k: int, group_size: int) -> dict[int, float]:
    return {i: comb(group_size, i) * comb(n - group_size, k - i) / comb(n, k)
            for i in range(k + 1) if i <= group_size and k - i <= n - group_size}


def sum_probabilities(n: int, k: int, width: int = 25) -> dict[int, float]:
    # Coefficients of product(1 + y*x**number), keeping only powers y<=k.
    ways = [Counter() for _ in range(k + 1)]
    ways[0][0] = 1
    for number in range(1, n + 1):
        for count in range(min(k, number), 0, -1):
            for total, multiplicity in list(ways[count - 1].items()):
                ways[count][total + number] += multiplicity
    bands = Counter()
    for total, count in ways[k].items():
        bands[(total // width) * width] += count / comb(n, k)
    return dict(sorted(bands.items()))


def holm_adjust(pvalues: list[float]) -> list[float]:
    result = [0.0] * len(pvalues)
    floor = 0.0
    for rank, index in enumerate(sorted(range(len(pvalues)), key=pvalues.__getitem__)):
        floor = max(floor, (len(pvalues) - rank) * pvalues[index])
        result[index] = min(1.0, floor)
    return result


def statistics(lines, n, k, sum_keys):
    freq = [0] * n
    odd, low, sums = [0] * (k + 1), [0] * (k + 1), Counter()
    for line in lines:
        for number in line:
            freq[number - 1] += 1
        odd[sum(x % 2 for x in line)] += 1
        low[sum(x <= n // 2 for x in line)] += 1
        sums[sum(line) // 25 * 25] += 1
    return freq, odd, low, [sums[x] for x in sum_keys]


def analyze_regime(draws: list[Draw], game: Game, simulations: int, seed: int,
                   progress=None, should_stop=None) -> dict:
    n, k, size = draws[0].pool, game.k, len(draws)
    odd_p = split_probabilities(n, k, (n + 1) // 2)
    low_p = split_probabilities(n, k, n // 2)
    sum_p = sum_probabilities(n, k)
    expected = ([size * k / n] * n,
                [size * odd_p.get(i, 0) for i in range(k + 1)],
                [size * low_p.get(i, 0) for i in range(k + 1)],
                [size * sum_p[i] for i in sum_p])
    observed = statistics([d.numbers for d in draws], n, k, sum_p)
    observed_stats = [pearson(o, e) for o, e in zip(observed, expected)]
    exceedances = [0] * 4
    rng = random.Random(seed)
    # A multinomial chi-square reference is wrong for within-draw dependent
    # number indicators. Simulate the actual uniform k-of-n null for all tests.
    for replicate in range(simulations):
        if replicate % 50 == 0:
            check_stop(should_stop)
            if progress:
                progress(replicate / simulations)
        generated = (rng.sample(range(1, n + 1), k) for _ in range(size))
        simulated = statistics(generated, n, k, sum_p)
        for index, (values, exp) in enumerate(zip(simulated, expected)):
            if pearson(values, exp) >= observed_stats[index] - 1e-10:
                exceedances[index] += 1
    raw = [(count + 1) / (simulations + 1) for count in exceedances]
    bonuses = Counter(d.bonus for d in draws)
    frequencies = []
    for number in range(1, n + 1):
        hits = [i for i, d in enumerate(draws) if number in d.numbers]
        last = draws[hits[-1]].day if hits else None
        frequencies.append({
            "number": number, "main_count": observed[0][number - 1],
            "bonus_count": bonuses[number], "last_drawn": last.isoformat() if last else None,
            "gap_draws": size - 1 - hits[-1] if hits else size,
            "gap_is_lower_bound": not bool(hits),
            "gap_calendar_days": (draws[-1].day - last).days if last else None,
        })
    return {
        "pool": n, "draws": size, "first_date": draws[0].day.isoformat(),
        "last_date": draws[-1].day.isoformat(), "frequencies": frequencies,
        "low_definition": f"1-{n // 2}; high {n // 2 + 1}-{n}",
        "splits": {"odd": observed[1], "low": observed[2]},
        "sum_ranges": [{"from": band, "to": band + 24, "draws": count}
                       for band, count in zip(sum_p, observed[3])],
        "sum_min": min(sum(d.numbers) for d in draws),
        "sum_max": max(sum(d.numbers) for d in draws),
        "sum_mean": sum(sum(d.numbers) for d in draws) / size,
        "tests": [{"name": name, "chi_square": stat, "mc_p": p,
                   "mc_standard_error": sqrt(p * (1 - p) / (simulations + 1)),
                   "minimum_expected_cell_count": min(e for e in exp if e > 0)}
                  for name, stat, p, exp in zip(
                      ("number frequencies", "odd/even", "high/low", "sum bands"),
                      observed_stats, raw, expected)],
    }


def analyze(draws: list[Draw], game: Game, simulations=1999, seed=2026, alpha=0.05,
            progress=None, should_stop=None) -> dict:
    """progress(fraction 0..1) and should_stop() are optional hooks for interactive callers."""
    if simulations < 99:
        raise ValueError("Use at least 99 Monte Carlo replicates (1999+ recommended).")
    if not 0 < alpha < 1:
        raise ValueError("Alpha must be between zero and one.")
    groups = [(n, list(group)) for n, group in groupby(draws, key=lambda d: d.pool)]
    regimes = []
    for index, (n, group) in enumerate(groups):
        def regime_progress(fraction, index=index):
            if progress:
                progress((index + fraction) / len(groups))
        regimes.append(analyze_regime(group, game, simulations, seed + n, regime_progress, should_stop))
    tests = [test for regime in regimes for test in regime["tests"]]
    for test, adjusted in zip(tests, holm_adjust([t["mc_p"] for t in tests])):
        test["holm_adjusted_p"] = adjusted
        test["significant"] = adjusted <= alpha
        test["conclusion"] = (
            "Evidence against this uniform-draw model; check data quality and seek independent replication. "
            "This does not identify better future numbers."
            if test["significant"] else
            "No statistically significant departure detected; compatible with normal random variation. "
            "This is not proof that the draws are perfectly random.")
    return {"game": game.name, "simulations": simulations, "seed": seed, "alpha": alpha,
            "p_value_method": "Monte Carlo Pearson chi-square under uniform k-of-n draws; "
                              "Holm correction across all four tests in every rule regime.",
            "gap_note": "Gaps count supplied draws only, within each rule regime. Missing dates are not inferred.",
            "regimes": regimes}
