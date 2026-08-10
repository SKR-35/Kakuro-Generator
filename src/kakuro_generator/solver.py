from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from .model import KakuroPuzzle, Run

Cell = Tuple[int, int]

# Digits 1..9 use bits 0..8.
ALL_DIGITS_MASK = (1 << 9) - 1


def _bit(d: int) -> int:
    return 1 << (d - 1)


def _digits_from_mask(mask: int) -> List[int]:
    out = []
    while mask:
        lsb = mask & -mask
        out.append(lsb.bit_length())
        mask ^= lsb
    return out


@lru_cache(maxsize=None)
def digit_sets(length: int, total: int) -> Tuple[Tuple[int, ...], ...]:
    """
    All sorted distinct digit combinations of `length` digits from 1..9 summing to total.

    Kept for difficulty scoring / public compatibility.
    """
    out: List[Tuple[int, ...]] = []

    def rec(start: int, need: int, remain: int, acc: List[int]) -> None:
        if need == 0:
            if remain == 0:
                out.append(tuple(acc))
            return

        for d in range(start, 10):
            if d > remain:
                break

            left = need - 1
            rem2 = remain - d

            if left:
                # Tight arithmetic bounds.
                next_start = d + 1
                min_possible = sum(range(next_start, next_start + left))
                max_possible = sum(range(10 - left, 10))
                if rem2 < min_possible or rem2 > max_possible:
                    continue

            rec(d + 1, left, rem2, acc + [d])

    rec(1, length, total, [])
    return tuple(out)


@lru_cache(maxsize=None)
def digit_set_masks(length: int, total: int) -> Tuple[int, ...]:
    """
    Same combinations as digit_sets(), encoded as 9-bit masks.

    Example: {1,3,7} -> bits 0,2,6.
    """
    masks = []
    for combo in digit_sets(length, total):
        mask = 0
        for d in combo:
            mask |= _bit(d)
        masks.append(mask)
    return tuple(masks)


@lru_cache(maxsize=None)
def digit_set_count(length: int, total: int) -> int:
    """Number of distinct digit sets compatible with one Kakuro clue."""
    return len(digit_set_masks(length, total))


@lru_cache(maxsize=None)
def partial_run_ambiguity(
    length: int,
    used_mask: int,
    assigned_sum: int,
) -> Tuple[float, float, int]:
    """
    Cheap look-ahead score for a partially filled run.

    Returns:
      (minimum final clue ambiguity,
       mean final clue ambiguity,
       number of distinct remaining digit completions)

    Lower ambiguity means the eventual clue sum constrains the run more
    strongly, which tends to improve the probability of a unique puzzle.
    """
    assigned = used_mask.bit_count()
    remaining = length - assigned

    if remaining < 0:
        return (float("inf"), float("inf"), 0)

    if remaining == 0:
        n = digit_set_count(length, assigned_sum)
        if n == 0:
            return (float("inf"), float("inf"), 0)
        return (float(n), float(n), 1)

    available = [d for d in range(1, 10) if not (used_mask & _bit(d))]
    if len(available) < remaining:
        return (float("inf"), float("inf"), 0)

    # Enumerate remaining digit combinations. At most C(9,4/5)=126 states,
    # and this whole function is cached by (length, used_mask, assigned_sum).
    from itertools import combinations

    ambiguities = []
    for combo in combinations(available, remaining):
        total = assigned_sum + sum(combo)
        n = digit_set_count(length, total)
        if n > 0:
            ambiguities.append(n)

    if not ambiguities:
        return (float("inf"), float("inf"), 0)

    return (
        float(min(ambiguities)),
        float(sum(ambiguities) / len(ambiguities)),
        len(ambiguities),
    )


class _CompiledPuzzle:
    """
    Compact immutable representation used by the solver.

    Cells are integer indices 0..N-1. Each white cell belongs to exactly
    two runs: across and down.
    """

    __slots__ = (
        "cells",
        "cell_index",
        "cell_runs",
        "run_cells",
        "run_domains",
        "rows",
        "cols",
    )

    def __init__(self, puzzle: KakuroPuzzle):
        run_map = puzzle.run_map()
        self.cells = tuple(run_map.keys())
        self.cell_index = {cell: i for i, cell in enumerate(self.cells)}
        self.rows = puzzle.rows
        self.cols = puzzle.cols

        runs_by_id = {run.id: run for run in puzzle.runs}
        max_run_id = max(runs_by_id) if runs_by_id else -1

        run_cells: List[Tuple[int, ...]] = [tuple() for _ in range(max_run_id + 1)]
        run_domains: List[Tuple[int, ...]] = [tuple() for _ in range(max_run_id + 1)]

        for run_id, run in runs_by_id.items():
            run_cells[run_id] = tuple(self.cell_index[c] for c in run.cells)
            run_domains[run_id] = digit_set_masks(len(run.cells), run.target)

        self.run_cells = tuple(run_cells)
        self.run_domains = tuple(run_domains)

        cell_runs = []
        for cell in self.cells:
            a, d = run_map[cell]
            if a is None or d is None:
                raise ValueError(f"White cell {cell} does not belong to two runs.")
            cell_runs.append((a, d))
        self.cell_runs = tuple(cell_runs)


def _run_state(
    compiled: _CompiledPuzzle,
    values: List[int],
    run_id: int,
) -> Tuple[Tuple[int, ...], int]:
    """
    Return (feasible_combo_masks, union_of_digits_available_to_unassigned_cells).

    A run-domain is filtered only by already assigned digits. Since each domain
    mask contains exactly `len(run)` distinct digits, an assignment is feasible
    iff all assigned digits are a subset of that mask and no digit repeats.
    """
    used_mask = 0
    assigned_count = 0

    for ci in compiled.run_cells[run_id]:
        v = values[ci]
        if not v:
            continue
        b = _bit(v)
        if used_mask & b:
            return tuple(), 0
        used_mask |= b
        assigned_count += 1

    feasible = tuple(
        combo_mask
        for combo_mask in compiled.run_domains[run_id]
        if (combo_mask & used_mask) == used_mask
    )

    if not feasible:
        return tuple(), 0

    available_union = 0
    for combo_mask in feasible:
        available_union |= combo_mask & ~used_mask

    # Completed run: its exact digit set must be one valid domain.
    if assigned_count == len(compiled.run_cells[run_id]):
        if used_mask not in feasible:
            return tuple(), 0
        available_union = 0

    return feasible, available_union


def _candidate_mask(
    compiled: _CompiledPuzzle,
    values: List[int],
    cell_idx: int,
    cached_run_unions: Dict[int, int],
) -> int:
    if values[cell_idx]:
        return _bit(values[cell_idx])

    a_id, d_id = compiled.cell_runs[cell_idx]

    if a_id not in cached_run_unions:
        _, cached_run_unions[a_id] = _run_state(compiled, values, a_id)
    if d_id not in cached_run_unions:
        _, cached_run_unions[d_id] = _run_state(compiled, values, d_id)

    return cached_run_unions[a_id] & cached_run_unions[d_id]


def _propagate(
    compiled: _CompiledPuzzle,
    values: List[int],
) -> bool:
    """
    Constraint propagation to a fixed point.

    Rules:
    1. every run must retain at least one feasible digit-set domain;
    2. every unassigned cell gets the intersection of its across/down domains;
    3. singleton cell domains are assigned immediately.

    Returns False on contradiction.
    """
    while True:
        run_unions: Dict[int, int] = {}

        # First ensure all runs still have a feasible domain.
        for run_id in range(len(compiled.run_cells)):
            feasible, union_mask = _run_state(compiled, values, run_id)
            if not feasible:
                return False
            run_unions[run_id] = union_mask

        changed = False

        for ci, v in enumerate(values):
            if v:
                continue

            mask = _candidate_mask(compiled, values, ci, run_unions)
            if mask == 0:
                return False

            # Singleton propagation.
            if mask & (mask - 1) == 0:
                values[ci] = mask.bit_length()
                changed = True

        if not changed:
            return True


def _is_complete_and_valid(
    compiled: _CompiledPuzzle,
    values: List[int],
) -> bool:
    if any(v == 0 for v in values):
        return False

    for run_id in range(len(compiled.run_cells)):
        used_mask = 0
        for ci in compiled.run_cells[run_id]:
            b = _bit(values[ci])
            if used_mask & b:
                return False
            used_mask |= b

        if used_mask not in compiled.run_domains[run_id]:
            return False

    return True


def find_solutions(
    puzzle: KakuroPuzzle,
    limit: int = 2,
    *,
    node_limit: Optional[int] = None,
) -> Tuple[List[List[List[Optional[int]]]], int, bool]:
    """
    Return up to `limit` concrete solutions.

    Returns:
      (solutions, search_nodes, aborted)

    `aborted=True` means node_limit was reached before the search space was
    exhausted. This API is used by the Hard/Evil generator to obtain an actual
    counterexample solution instead of learning only "not unique".
    """
    compiled = _CompiledPuzzle(puzzle)

    if any(not domain for domain in compiled.run_domains):
        return [], 0, False

    solutions_values: List[List[int]] = []
    nodes = 0
    aborted = False

    def search(values: List[int]) -> None:
        nonlocal nodes, aborted

        if aborted or len(solutions_values) >= limit:
            return

        if node_limit is not None and nodes >= node_limit:
            aborted = True
            return

        values = values[:]

        if not _propagate(compiled, values):
            return

        if _is_complete_and_valid(compiled, values):
            solutions_values.append(values[:])
            return

        run_unions: Dict[int, int] = {}
        best_ci = -1
        best_mask = 0
        best_n = 10

        for ci, v in enumerate(values):
            if v:
                continue

            mask = _candidate_mask(
                compiled,
                values,
                ci,
                run_unions,
            )
            n = mask.bit_count()

            if n == 0:
                return

            if n < best_n:
                best_ci = ci
                best_mask = mask
                best_n = n

                if n == 2:
                    break

        if best_ci < 0:
            return

        mask = best_mask

        while mask and len(solutions_values) < limit and not aborted:
            lsb = mask & -mask
            mask ^= lsb
            digit = lsb.bit_length()

            nodes += 1

            if node_limit is not None and nodes > node_limit:
                aborted = True
                return

            child = values[:]
            child[best_ci] = digit
            search(child)

    search([0] * len(compiled.cells))

    grids: List[List[List[Optional[int]]]] = []

    for values in solutions_values:
        grid: List[List[Optional[int]]] = [
            [None for _ in range(puzzle.cols)]
            for _ in range(puzzle.rows)
        ]

        for cell, value in zip(compiled.cells, values):
            r, c = cell
            grid[r][c] = value

        grids.append(grid)

    return grids, nodes, aborted


def count_solutions(
    puzzle: KakuroPuzzle,
    limit: int = 2,
    *,
    return_first: bool = False,
    node_limit: Optional[int] = None,
) -> Tuple[int, Optional[List[List[Optional[int]]]], int]:
    """
    Backward-compatible solution-count API.

    If the search aborts before uniqueness can be proven and fewer than two
    solutions were found, count is forced to zero so callers never mistake an
    incomplete search for proof of uniqueness.
    """
    solutions, nodes, aborted = find_solutions(
        puzzle,
        limit=limit,
        node_limit=node_limit,
    )

    count = len(solutions)

    if aborted and count < 2:
        count = 0

    first = solutions[0] if (return_first and solutions) else None
    return count, first, nodes


def solve_unique(
    puzzle: KakuroPuzzle,
    node_limit: Optional[int] = None,
) -> Tuple[bool, int]:
    solutions, nodes, aborted = find_solutions(
        puzzle,
        limit=2,
        node_limit=node_limit,
    )

    return (not aborted and len(solutions) == 1), nodes
