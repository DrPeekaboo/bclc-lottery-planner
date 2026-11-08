"""Explicit game rules and strict CSV/money parsing; no third-party packages."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import csv
import re


@dataclass(frozen=True)
class Game:
    key: str
    name: str
    n: int
    k: int
    price_cents: int
    lines_per_play: int


GAMES = {
    "649": Game("649", "Lotto 6/49", 49, 6, 300, 1),
    "max": Game("max", "Lotto Max", 52, 7, 600, 4),
}


@dataclass(frozen=True)
class Draw:
    day: date
    numbers: tuple[int, ...]
    bonus: int
    pool: int


def money_cents(value: str) -> int:
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount * 100 != (amount * 100).to_integral_value():
            raise ValueError
        return int(amount * 100)
    except (ValueError, InvalidOperation, OverflowError):
        raise ValueError("Budget must be a nonnegative CAD amount with at most two decimal places.") from None


def cad(cents: int) -> str:
    return f"${cents // 100:,}.{cents % 100:02d}"


def parse_numbers(value: str) -> tuple[int, ...]:
    # Accept spaces, commas, semicolons, pipes and JSON-style square brackets.
    if not value or not re.fullmatch(r"[\d\s,;|\[\]]+", value):
        raise ValueError("Numbers must be positive integers separated by spaces or commas.")
    return tuple(sorted(int(x) for x in re.findall(r"\d+", value)))


def validate_line(line: tuple[int, ...], game: Game, pool: int | None = None) -> None:
    limit = pool or game.n
    if (len(line) != game.k or len(set(line)) != game.k or
            not all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= limit for x in line)):
        raise ValueError(f"Expected {game.k} distinct integers from 1 to {limit}; got {line}.")


def history_pool(game: Game, day: date) -> int:
    if game.key == "649":
        return 49
    if day < date(2009, 9, 25):
        raise ValueError("Lotto Max history predates its first draw (2009-09-25).")
    if day < date(2019, 5, 14):
        return 49
    if day < date(2026, 4, 14):
        return 50
    return 52


def row_numbers(row: dict, game: Game) -> tuple[int, ...]:
    raw = row.get("numbers")
    if isinstance(raw, str) and raw.strip():
        return parse_numbers(raw)
    try:
        return tuple(sorted(int(row[f"n{i}"]) for i in range(1, game.k + 1)))
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Use a numbers column, or n1 through n{game.k} columns.") from None


def load_history(path: str, game: Game) -> list[Draw]:
    with open(path, newline="", encoding="utf-8-sig") as stream:
        return read_history(stream, game, path)


def read_history(stream, game: Game, path: str = "upload") -> list[Draw]:
    """Validate history rows from any text stream (a file or an in-memory upload)."""
    draws, seen = [], set()
    reader = csv.DictReader(stream)
    if not reader.fieldnames or not {"date", "bonus"} <= set(reader.fieldnames):
        raise ValueError("History CSV needs date,numbers,bonus (or n1..nk) headers.")
    for row_number, row in enumerate(reader, 2):
        try:
            # Python 3.11+ fromisoformat also accepts forms like 20261104; require one format.
            raw_day = (row.get("date") or "").strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_day):
                raise ValueError("Date must be written YYYY-MM-DD.")
            day = date.fromisoformat(raw_day)
            if day > date.today():
                raise ValueError("Draw date is in the future; history must contain past results only.")
            if day in seen:
                raise ValueError("Duplicate draw date; one MAIN draw per date is allowed.")
            pool = history_pool(game, day)
            numbers = row_numbers(row, game)
            validate_line(numbers, game, pool)
            bonus = int(row["bonus"])
            if bonus in numbers or not 1 <= bonus <= pool:
                raise ValueError("Bonus must be in range and different from all main numbers.")
            draws.append(Draw(day, numbers, bonus, pool))
            seen.add(day)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"{path}, CSV row {row_number}: {exc}") from None
    if not draws:
        raise ValueError("History CSV has no draws. Fill the template with real results first.")
    return sorted(draws, key=lambda d: d.day)


def load_receipt(path: str, game: Game) -> list[tuple[int, ...]]:
    with open(path, newline="", encoding="utf-8-sig") as stream:
        return read_receipt(stream, game, path)


def read_receipt(stream, game: Game, path: str = "upload") -> list[tuple[int, ...]]:
    """Require all issued lines for each paid play; duplicates remain for auditing."""
    groups = {}
    reader = csv.DictReader(stream)
    if not reader.fieldnames or not {"play", "line", "numbers"} <= set(reader.fieldnames):
        raise ValueError("Receipt CSV needs play,line,numbers headers.")
    for row_number, row in enumerate(reader, 2):
        try:
            play, index = int(row["play"]), int(row["line"])
            if play < 1 or not 1 <= index <= game.lines_per_play:
                raise ValueError("Invalid play or line index.")
            numbers = row_numbers(row, game)
            validate_line(numbers, game)
            if index in groups.setdefault(play, {}):
                raise ValueError("Duplicate play/line position.")
            groups[play][index] = numbers
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{path}, CSV row {row_number}: {exc}") from None
    if not groups:
        raise ValueError("Receipt is empty.")
    for play, lines in groups.items():
        if set(lines) != set(range(1, game.lines_per_play + 1)):
            raise ValueError(f"Play {play} is incomplete; include ALL {game.lines_per_play} issued lines.")
    return [groups[p][i] for p in sorted(groups) for i in sorted(groups[p])]
