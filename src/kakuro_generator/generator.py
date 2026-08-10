from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .geometry import collect_runs, connected_white_cells, new_mask, white_component_count
from .model import KakuroPuzzle, Run
from .solver import (
    digit_sets,
    digit_set_count,
    find_solutions,
    partial_run_ambiguity,
    solve_unique,
)

Cell = Tuple[int, int]


@dataclass(frozen=True)
class DifficultyProfile:
    min_density: float
    max_density: float
    symmetry_chance: float
    ambiguity_weight: float
    target_score_min: float
    target_score_max: float
    node_limit: int


PROFILES = {
    "easy": DifficultyProfile(0.42, 0.55, 0.85, 0.75, 0.0, 29.0, 80_000),
    "medium": DifficultyProfile(0.48, 0.62, 0.70, 1.00, 18.0, 48.0, 100_000),

    # Hard/Evil deliberately use denser clue structure (lower white-cell density)
    # than the earlier versions. On 12x12 boards, 0.54-0.72 white density created
    # long, weakly constrained runs and unique anchors became extremely rare.
    #
    # Difficulty is therefore driven primarily by clue ambiguity + solver search,
    # not simply by making the board more open.
    "hard": DifficultyProfile(0.46, 0.56, 0.55, 1.25, 34.0, 72.0, 250_000),
    "evil": DifficultyProfile(0.50, 0.60, 0.40, 1.50, 52.0, 10_000.0, 600_000),
}

DEFAULT_SIZE_RANGES = {
    "easy": (7, 8),
    "medium": (8, 10),
    "hard": (10, 12),
    "evil": (12, 12),
}


def _pattern_from_blocks(
    rng: random.Random,
    rows: int,
    cols: int,
    density: float,
    symmetry: bool,
) -> List[List[bool]]:
    """
    Fast constructive geometry generation.

    Start from one fully connected white interior, then carve black cells only
    when the resulting board remains:
      - a legal Kakuro geometry,
      - one orthogonally connected white component,
      - free of excessive 2-cell-run texture.

    This replaces the old simulated-annealing search, whose nested
    restart/flip loops could dominate runtime even before uniqueness solving.
    """
    w = new_mask(rows, cols)
    for r in range(1, rows):
        for c in range(1, cols):
            w[r][c] = True

    interior_cells = (rows - 1) * (cols - 1)
    target_white = max(4, round(interior_cells * density))

    positions = [(r, c) for r in range(1, rows) for c in range(1, cols)]
    rng.shuffle(positions)

    def white_count() -> int:
        return sum(
            1
            for r in range(1, rows)
            for c in range(1, cols)
            if w[r][c]
        )

    # Several cheap passes are enough; each accepted carve permanently keeps
    # the geometry legal and connected.
    for _pass in range(4):
        if white_count() <= target_white:
            break

        rng.shuffle(positions)
        changed = False

        for r, c in positions:
            if white_count() <= target_white:
                break
            if not w[r][c]:
                continue

            flips = {(r, c)}
            if symmetry:
                mr, mc = rows - r, cols - c
                if 0 < mr < rows and 0 < mc < cols and w[mr][mc]:
                    flips.add((mr, mc))

            # Do not overshoot the requested density too aggressively.
            if white_count() - len(flips) < target_white:
                continue

            old = [(rr, cc, w[rr][cc]) for rr, cc in flips]
            for rr, cc, _ in old:
                w[rr][cc] = False

            run_defs, ok = collect_runs(w)
            if (
                ok
                and connected_white_cells(w)
                and _geometry_quality_ok(w)
            ):
                changed = True
            else:
                for rr, cc, value in old:
                    w[rr][cc] = value

        if not changed:
            break

    return w



def _geometry_quality_ok(white: List[List[bool]]) -> bool:
    """Publication-oriented geometry gate beyond basic Kakuro legality."""
    run_defs, ok = collect_runs(white)
    if not ok or not connected_white_cells(white):
        return False
    lengths = [len(cells) for _, cells, _ in run_defs]
    if not lengths or max(lengths) < 3:
        return False
    ratio_two = sum(1 for L in lengths if L == 2) / len(lengths)
    return ratio_two <= 0.72

def _make_solution(
    rng: random.Random,
    white: List[List[bool]],
    run_defs,
    difficulty: str,
    *,
    guidance_override: Optional[float] = None,
    balance_override: Optional[float] = None,
) -> Optional[List[List[Optional[int]]]]:
    """
    Build a full valid digit grid with ambiguity-aware candidate ordering.

    Easy/Medium keep the successful guided-ambiguity strategy.

    Hard/Evil may call this with a stronger guidance_override to deliberately
    build a high-constraint "anchor" puzzle first. Difficulty is then tuned
    separately by local clue-preserving-valid mutations.

    A soft global digit-balance term discourages pathological solutions that
    overuse only high (or only low) digits. It is intentionally soft: run
    legality and uniqueness remain the primary constraints.
    """
    rows, cols = len(white), len(white[0])
    grid: List[List[Optional[int]]] = [[None] * cols for _ in range(rows)]

    run_cells: List[Tuple[Cell, ...]] = []
    runs_for_cell: Dict[Cell, List[int]] = {}

    for run_id, (_direction, cells, _clue) in enumerate(run_defs):
        cells = tuple(cells)
        run_cells.append(cells)
        for cell in cells:
            runs_for_cell.setdefault(cell, []).append(run_id)

    cells = [(r, c) for r in range(rows) for c in range(cols) if white[r][c]]

    default_guidance = {
        "easy": 1.35,
        "medium": 1.00,
        "hard": 0.72,
        "evil": 0.55,
    }[difficulty]

    # Keep Easy very close to the proven baseline. Medium gets a little more
    # balancing because the observed 10x10 examples were strongly 7/8/9-heavy.
    default_balance = {
        "easy": 0.12,
        "medium": 0.28,
        "hard": 0.34,
        "evil": 0.38,
    }[difficulty]

    guidance_strength = (
        default_guidance if guidance_override is None else guidance_override
    )
    balance_strength = (
        default_balance if balance_override is None else balance_override
    )

    digit_counts = [0] * 10

    def run_used_and_sum(
        run_id: int,
        extra_cell: Optional[Cell] = None,
        extra_digit: Optional[int] = None,
    ) -> Tuple[int, int]:
        used_mask = 0
        total = 0

        for rr, cc in run_cells[run_id]:
            if extra_cell == (rr, cc) and extra_digit is not None:
                v = extra_digit
            else:
                v = grid[rr][cc]

            if v is None:
                continue

            bit = 1 << (v - 1)
            if used_mask & bit:
                return -1, -1

            used_mask |= bit
            total += v

        return used_mask, total

    def candidates(cell: Cell) -> List[int]:
        used_mask = 0
        for run_id in runs_for_cell[cell]:
            mask, _ = run_used_and_sum(run_id)
            if mask < 0:
                return []
            used_mask |= mask

        return [
            d
            for d in range(1, 10)
            if not (used_mask & (1 << (d - 1)))
        ]

    def digit_balance_cost(digit: int, filled: int) -> float:
        """
        Prefer globally under-used digits without imposing a hard quota.
        """
        if filled <= 0:
            return 0.0

        target = filled / 9.0
        projected = digit_counts[digit] + 1

        # Positive only when this digit would become overrepresented.
        overuse = max(0.0, projected - target)
        return balance_strength * overuse

    def candidate_risk(cell: Cell, digit: int, filled: int) -> float:
        score = 0.0

        for run_id in runs_for_cell[cell]:
            mask, total = run_used_and_sum(
                run_id,
                extra_cell=cell,
                extra_digit=digit,
            )
            if mask < 0:
                return float("inf")

            length = len(run_cells[run_id])
            min_amb, mean_amb, completions = partial_run_ambiguity(
                length,
                mask,
                total,
            )

            if completions == 0:
                return float("inf")

            remaining = length - mask.bit_count()
            near_complete = 1.0 / (1.0 + remaining)

            score += guidance_strength * (
                min_amb * (1.25 + near_complete)
                + mean_amb * 0.35
                + completions * 0.012
            )

        score += digit_balance_cost(digit, filled)

        # Enough stochasticity for variety, but not enough to overwhelm
        # the constraint signal.
        score += rng.random() * (0.45 / max(0.20, guidance_strength))
        return score

    def rec(filled: int) -> bool:
        if filled == len(cells):
            return True

        best_cell = None
        best_candidates = None
        best_pressure = -1.0

        for cell in cells:
            r, c = cell
            if grid[r][c] is not None:
                continue

            cand = candidates(cell)
            if not cand:
                return False

            pressure = 0.0
            for run_id in runs_for_cell[cell]:
                assigned = sum(
                    1
                    for rr, cc in run_cells[run_id]
                    if grid[rr][cc] is not None
                )
                remaining = len(run_cells[run_id]) - assigned
                pressure += 1.0 / max(1, remaining)

            if (
                best_candidates is None
                or len(cand) < len(best_candidates)
                or (
                    len(cand) == len(best_candidates)
                    and pressure > best_pressure
                )
            ):
                best_cell = cell
                best_candidates = cand
                best_pressure = pressure

        assert best_cell is not None and best_candidates is not None

        scored = [
            (candidate_risk(best_cell, d, filled), d)
            for d in best_candidates
        ]
        scored.sort(key=lambda x: x[0])

        r, c = best_cell

        for risk, d in scored:
            if risk == float("inf"):
                continue

            grid[r][c] = d
            digit_counts[d] += 1

            if rec(filled + 1):
                return True

            digit_counts[d] -= 1
            grid[r][c] = None

        return False

    return grid if rec(0) else None


def _digit_balance_penalty(
    solution: List[List[Optional[int]]],
) -> float:
    """
    Soft global 1..9 imbalance measure.

    Lower is better. A perfectly uniform distribution is near zero.
    """
    counts = [0] * 10

    for row in solution:
        for v in row:
            if v is not None:
                counts[v] += 1

    total = sum(counts[1:])
    if total == 0:
        return 0.0

    target = total / 9.0
    variance = sum(
        (counts[d] - target) ** 2
        for d in range(1, 10)
    ) / 9.0

    return variance / max(1.0, target)


def _solution_ambiguity_risk(
    solution: List[List[Optional[int]]],
    run_defs,
    *,
    balance_weight: float = 0.65,
) -> float:
    """
    Cheap candidate ranking before uniqueness solving.

    Lower values favor:
      - smaller clue digit-set domains,
      - fewer highly ambiguous runs,
      - healthier global use of digits 1..9.
    """
    counts = []

    for _direction, cells, _clue in run_defs:
        total = sum(
            solution[r][c]
            for r, c in cells
            if solution[r][c] is not None
        )
        n = digit_set_count(len(cells), int(total))
        counts.append(max(1, n))

    if not counts:
        return float("inf")

    avg = sum(counts) / len(counts)
    max_count = max(counts)
    highly_ambiguous = sum(1 for n in counts if n >= 4) / len(counts)
    balance = _digit_balance_penalty(solution)

    return (
        avg
        + 0.22 * max_count
        + 1.75 * highly_ambiguous
        + balance_weight * balance
    )


def _build_puzzle(
    white: List[List[bool]],
    run_defs,
    solution: List[List[Optional[int]]],
    difficulty: str,
) -> KakuroPuzzle:
    runs: List[Run] = []
    for rid, (direction, cells, clue_cell) in enumerate(run_defs):
        target = sum(solution[r][c] for r, c in cells if solution[r][c] is not None)
        runs.append(Run(rid, direction, cells, int(target), clue_cell))
    return KakuroPuzzle(
        rows=len(white),
        cols=len(white[0]),
        white=white,
        runs=runs,
        solution=solution,
        difficulty=difficulty,
    )


def difficulty_score(puzzle: KakuroPuzzle, search_nodes: int = 0) -> float:
    """
    Structural score:
    - longer runs raise score
    - clue sums with many digit combinations raise score
    - denser white-cell geometry raises score
    - solver search contributes logarithmically
    """
    interior = max(1, (puzzle.rows - 1) * (puzzle.cols - 1))
    density = puzzle.white_count / interior

    run_lengths = [len(r.cells) for r in puzzle.runs]
    avg_run = sum(run_lengths) / max(1, len(run_lengths))

    ambiguity = 0.0
    for run in puzzle.runs:
        ambiguity += max(0, len(digit_sets(len(run.cells), run.target)) - 1)
    ambiguity /= max(1, len(puzzle.runs))

    import math
    node_term = math.log10(max(1, search_nodes) + 1)

    return (
        density * 28.0
        + max(0.0, avg_run - 2.0) * 6.0
        + ambiguity * 3.0
        + node_term * 4.0
    )


def _score_matches(diff: str, score: float) -> bool:
    p = PROFILES[diff]
    return p.target_score_min <= score <= p.target_score_max





def _solution_copy(
    solution: List[List[Optional[int]]],
) -> List[List[Optional[int]]]:
    return [row[:] for row in solution]


def _solution_cell_runs(run_defs) -> Dict[Cell, List[Tuple[Cell, ...]]]:
    out: Dict[Cell, List[Tuple[Cell, ...]]] = {}

    for _direction, cells, _clue in run_defs:
        rcells = tuple(cells)

        for cell in rcells:
            out.setdefault(cell, []).append(rcells)

    return out


def _replacement_digits(
    solution: List[List[Optional[int]]],
    cell: Cell,
    cell_runs: Dict[Cell, List[Tuple[Cell, ...]]],
) -> List[int]:
    """
    Digits that can replace one target-solution cell while preserving Kakuro's
    distinct-digit rule in both crossing runs.
    """
    r, c = cell
    old = solution[r][c]
    used = set()

    for run_cells in cell_runs[cell]:
        for rr, cc in run_cells:
            if (rr, cc) == cell:
                continue

            v = solution[rr][cc]

            if v is not None:
                used.add(v)

    return [
        d
        for d in range(1, 10)
        if d != old and d not in used
    ]


def _target_distance(diff: str, score: float) -> float:
    profile = PROFILES[diff]

    if profile.target_score_min <= score <= profile.target_score_max:
        return 0.0

    if score < profile.target_score_min:
        return profile.target_score_min - score

    return score - profile.target_score_max


def _solution_difference_cells(
    target: List[List[Optional[int]]],
    alternative: List[List[Optional[int]]],
    white: List[List[bool]],
) -> List[Cell]:
    """
    White cells where the counterexample solution differs from our target.
    """
    out = []

    for r in range(len(white)):
        for c in range(len(white[0])):
            if not white[r][c]:
                continue

            if target[r][c] != alternative[r][c]:
                out.append((r, c))

    return out


def _counterexample_break_score(
    target: List[List[Optional[int]]],
    alternative: List[List[Optional[int]]],
    candidate_solution: List[List[Optional[int]]],
    cell: Cell,
    run_defs,
    difficulty: str,
) -> float:
    """
    Cheap score for a mutation before paying for another full solver call.

    Lower is better.

    Priorities:
      1. alter clue sums on runs where target and counterexample differ;
      2. preserve useful clue ambiguity;
      3. preserve global digit balance.
    """
    r, c = cell

    affected_runs = []
    alt_break_strength = 0.0

    for _direction, cells, _clue in run_defs:
        if cell not in cells:
            continue

        target_sum = sum(target[rr][cc] for rr, cc in cells)
        alt_sum = sum(alternative[rr][cc] for rr, cc in cells)
        new_sum = sum(candidate_solution[rr][cc] for rr, cc in cells)

        affected_runs.append((cells, new_sum))

        # If the alternative matched the old clue but no longer matches the
        # new clue, this mutation directly kills the known counterexample on
        # that run.
        if alt_sum != new_sum:
            alt_break_strength += 1.0

        # Bigger clue-sum displacement from the alternative gets a tiny bonus.
        alt_break_strength += min(0.75, abs(new_sum - alt_sum) * 0.06)

    ambiguity_cost = 0.0

    for cells, new_sum in affected_runs:
        n = digit_set_count(len(cells), int(new_sum))

        if difficulty == "hard":
            # Hard should remain constrained enough to be solvable/unique,
            # but not collapse into trivial single-domain clues everywhere.
            ambiguity_cost += abs(n - 2.0) * 0.25
        else:
            # Evil tolerates / prefers a little more ambiguity.
            ambiguity_cost += abs(n - 3.0) * 0.20

    balance_cost = _digit_balance_penalty(candidate_solution)

    return (
        ambiguity_cost
        + balance_cost * (0.32 if difficulty == "hard" else 0.26)
        - alt_break_strength * 1.35
    )


def _choose_counterexample_mutations(
    rng: random.Random,
    target: List[List[Optional[int]]],
    alternative: List[List[Optional[int]]],
    white: List[List[bool]],
    run_defs,
    difficulty: str,
    cell_runs: Dict[Cell, List[Tuple[Cell, ...]]],
) -> List[Tuple[float, Cell, int]]:
    """
    Build and rank local mutations specifically aimed at destroying the known
    alternative solution.
    """
    diff_cells = _solution_difference_cells(
        target,
        alternative,
        white,
    )

    if not diff_cells:
        return []

    rng.shuffle(diff_cells)
    proposals = []

    # Counterexample differences are usually localized; sampling keeps each
    # CEGIS round bounded on 12x12 boards.
    max_cells = 18 if difficulty == "hard" else 24

    for cell in diff_cells[:max_cells]:
        replacements = _replacement_digits(
            target,
            cell,
            cell_runs,
        )

        if not replacements:
            continue

        # Global under-use preference preserves the nice 1..9 balance behavior.
        digit_counts = [0] * 10

        for row in target:
            for v in row:
                if v is not None:
                    digit_counts[v] += 1

        replacements.sort(
            key=lambda d: (
                digit_counts[d],
                rng.random(),
            )
        )

        r, c = cell
        old = target[r][c]

        for new_digit in replacements[:5]:
            candidate = _solution_copy(target)
            candidate[r][c] = new_digit

            score = _counterexample_break_score(
                target,
                alternative,
                candidate,
                cell,
                run_defs,
                difficulty,
            )

            # Small noise avoids converging to one deterministic local motif.
            score += rng.random() * 0.08
            proposals.append((score, cell, new_digit))

    proposals.sort(key=lambda x: x[0])
    return proposals


def _cegis_make_unique(
    rng: random.Random,
    white: List[List[bool]],
    run_defs,
    difficulty: str,
    initial_solution: List[List[Optional[int]]],
    node_limit: int,
    *,
    deadline: float,
) -> Optional[KakuroPuzzle]:
    """
    Counterexample-guided uniqueness synthesis.

    Instead of generating thousands of unrelated seeds, repeatedly ask the
    solver for a second concrete solution and mutate the target solution exactly
    where that counterexample differs.

    Each accepted mutation keeps the target itself legal because clue sums are
    always rebuilt from the mutated target.
    """
    target = _solution_copy(initial_solution)
    cell_runs = _solution_cell_runs(run_defs)

    max_rounds = 22 if difficulty == "hard" else 34
    solver_calls = 0
    solver_budget = 46 if difficulty == "hard" else 70

    best_unique: Optional[KakuroPuzzle] = None

    for _round in range(max_rounds):
        if time.monotonic() >= deadline:
            break

        if solver_calls >= solver_budget:
            break

        puzzle = _build_puzzle(
            white,
            run_defs,
            target,
            difficulty,
        )

        solutions, nodes, aborted = find_solutions(
            puzzle,
            limit=2,
            node_limit=node_limit,
        )
        solver_calls += 1

        if aborted:
            # This candidate is too expensive to prove; move to a new outer
            # anchor/geometry rather than getting stuck.
            return best_unique

        if len(solutions) == 1:
            puzzle.score = difficulty_score(
                puzzle,
                nodes,
            )

            best_unique = puzzle

            # Hard generally lands in-band naturally on 12x12 once unique.
            # For Evil, keep searching only if score is still below target.
            if _score_matches(difficulty, puzzle.score):
                return puzzle

            if difficulty == "hard":
                return puzzle

            # Evil unique but still too easy: use the found target as the new
            # base, then attempt bounded ambiguity-increasing mutations below.
            return _raise_unique_difficulty(
                rng,
                white,
                run_defs,
                difficulty,
                target,
                puzzle,
                node_limit,
                deadline=deadline,
            )

        if len(solutions) < 2:
            # No solution should not normally happen because the target built
            # the clues. Treat it as a rejected anchor.
            return best_unique

        # Ensure we use a counterexample different from our intended target.
        alt = None

        for s in solutions:
            if _solution_difference_cells(
                target,
                s,
                white,
            ):
                alt = s
                break

        if alt is None:
            return best_unique

        proposals = _choose_counterexample_mutations(
            rng,
            target,
            alt,
            white,
            run_defs,
            difficulty,
            cell_runs,
        )

        if not proposals:
            return best_unique

        accepted = False

        # Only a few expensive verification calls per counterexample.
        for _cheap_score, cell, new_digit in proposals[:5]:
            if time.monotonic() >= deadline:
                break

            if solver_calls >= solver_budget:
                break

            r, c = cell
            candidate_target = _solution_copy(target)
            candidate_target[r][c] = new_digit

            candidate_puzzle = _build_puzzle(
                white,
                run_defs,
                candidate_target,
                difficulty,
            )

            candidate_solutions, candidate_nodes, candidate_aborted = find_solutions(
                candidate_puzzle,
                limit=2,
                node_limit=node_limit,
            )
            solver_calls += 1

            if candidate_aborted or not candidate_solutions:
                continue

            # The candidate target must remain one of the puzzle's solutions.
            # It should by construction, but keep the check explicit.
            target_found = any(
                not _solution_difference_cells(
                    candidate_target,
                    s,
                    white,
                )
                for s in candidate_solutions
            )

            if not target_found:
                continue

            # Accept if unique immediately.
            if len(candidate_solutions) == 1:
                candidate_puzzle.score = difficulty_score(
                    candidate_puzzle,
                    candidate_nodes,
                )

                if _score_matches(
                    difficulty,
                    candidate_puzzle.score,
                ) or difficulty == "hard":
                    return candidate_puzzle

                best_unique = candidate_puzzle
                target = candidate_target
                accepted = True
                break

            # Otherwise accept only when the old counterexample is gone.
            old_alt_survives = any(
                not _solution_difference_cells(
                    alt,
                    s,
                    white,
                )
                for s in candidate_solutions
            )

            if not old_alt_survives:
                target = candidate_target
                accepted = True
                break

        if not accepted:
            return best_unique

    return best_unique


def _raise_unique_difficulty(
    rng: random.Random,
    white: List[List[bool]],
    run_defs,
    difficulty: str,
    target: List[List[Optional[int]]],
    initial_puzzle: KakuroPuzzle,
    node_limit: int,
    *,
    deadline: float,
) -> KakuroPuzzle:
    """
    Bounded post-pass used mainly by Evil.

    Starting from a proven unique puzzle, try local mutations that increase
    difficulty_score while preserving uniqueness. This never blocks Hard.
    """
    best_puzzle = initial_puzzle
    best_target = _solution_copy(target)
    cell_runs = _solution_cell_runs(run_defs)
    cells = list(cell_runs)

    max_checks = 18
    checks = 0

    while checks < max_checks and time.monotonic() < deadline:
        if _score_matches(difficulty, best_puzzle.score):
            break

        rng.shuffle(cells)
        improved = False

        for cell in cells[:12]:
            replacements = _replacement_digits(
                best_target,
                cell,
                cell_runs,
            )

            if not replacements:
                continue

            r, c = cell
            old = best_target[r][c]

            scored = []

            for new_digit in replacements:
                candidate_target = _solution_copy(best_target)
                candidate_target[r][c] = new_digit

                # Prefer mutations that increase local clue ambiguity but keep
                # global digit balance reasonable.
                local_risk = _solution_ambiguity_risk(
                    candidate_target,
                    run_defs,
                    balance_weight=0.45,
                )

                scored.append((
                    -local_risk + rng.random() * 0.05,
                    new_digit,
                ))

            scored.sort()

            for _, new_digit in scored[:3]:
                candidate_target = _solution_copy(best_target)
                candidate_target[r][c] = new_digit

                candidate = _build_puzzle(
                    white,
                    run_defs,
                    candidate_target,
                    difficulty,
                )

                solutions, nodes, aborted = find_solutions(
                    candidate,
                    limit=2,
                    node_limit=node_limit,
                )
                checks += 1

                if aborted or len(solutions) != 1:
                    continue

                candidate.score = difficulty_score(
                    candidate,
                    nodes,
                )

                if candidate.score > best_puzzle.score:
                    best_puzzle = candidate
                    best_target = candidate_target
                    improved = True
                    break

            if improved or checks >= max_checks:
                break

        if not improved:
            break

    return best_puzzle


def _generate_hard_or_evil(
    rng: random.Random,
    difficulty: str,
    size: int,
    max_attempts: int,
) -> KakuroPuzzle:
    """
    Dedicated Hard/Evil counterexample-guided generator.

    Easy/Medium continue to use the proven fast guided-ambiguity engine.

    Hard/Evil:
      geometry
        -> one balanced target solution
        -> ask solver for two concrete solutions
        -> mutate cells where the counterexample differs
        -> rebuild clues
        -> repeat until unique
    """
    profile = PROFILES[difficulty]
    best: Optional[Tuple[float, KakuroPuzzle]] = None

    # One task-retry should be cheap and bounded. More outer CLI retries are
    # therefore meaningful again, unlike the old 2560-seed lottery.
    time_budget = 24.0 if difficulty == "hard" else 38.0
    deadline = time.monotonic() + time_budget

    geometry_attempts = min(
        max(4, max_attempts),
        18 if difficulty == "hard" else 24,
    )

    for _geometry_try in range(geometry_attempts):
        if time.monotonic() >= deadline:
            break

        # Constraint-rich geometry remains useful, but uniqueness is no longer
        # expected to happen by luck.
        density = rng.uniform(
            profile.min_density,
            profile.max_density,
        )
        symmetry = rng.random() < profile.symmetry_chance

        white = _pattern_from_blocks(
            rng,
            size,
            size,
            density,
            symmetry,
        )

        run_defs, ok = collect_runs(white)

        if not ok or not _geometry_quality_ok(white):
            continue

        # Moderate guidance: strong enough for useful clue sums, but more
        # diverse than the old uniqueness-anchor search.
        guidance = 1.08 if difficulty == "hard" else 0.92

        # A few independent targets per geometry are much cheaper than hundreds
        # of blind complete puzzle attempts.
        target_tries = 3 if difficulty == "hard" else 4

        for _target_try in range(target_tries):
            if time.monotonic() >= deadline:
                break

            target = _make_solution(
                rng,
                white,
                run_defs,
                difficulty,
                guidance_override=guidance,
                balance_override=0.36,
            )

            if target is None:
                continue

            candidate = _cegis_make_unique(
                rng,
                white,
                run_defs,
                difficulty,
                target,
                profile.node_limit,
                deadline=deadline,
            )

            if candidate is None:
                continue

            distance = _target_distance(
                difficulty,
                candidate.score,
            )

            if _score_matches(
                difficulty,
                candidate.score,
            ):
                return candidate

            if best is None or distance < best[0]:
                best = (distance, candidate)

            # For Hard, a unique 12x12 puzzle near the band is already a strong
            # production result; don't throw it away solely because calibration
            # is a few score points off.
            if difficulty == "hard" and candidate.score >= 30.0:
                return candidate

    if best is not None:
        return best[1]

    raise RuntimeError(
        f"Could not synthesize a unique {difficulty} Kakuro within "
        f"{time_budget:.0f}s using counterexample-guided generation."
    )

def generate_kakuro(
    rng: random.Random,
    difficulty: str,
    size: Optional[int] = None,
    max_attempts: int = 250,
) -> KakuroPuzzle:
    difficulty = difficulty.lower()

    if difficulty not in PROFILES:
        raise ValueError(f"Unknown difficulty: {difficulty}")

    if size is None:
        lo, hi = DEFAULT_SIZE_RANGES[difficulty]
        size = rng.randint(lo, hi)

    if not 7 <= size <= 15:
        raise ValueError("size must be between 7 and 15")

    # --------------------------------------------------------------
    # Hard / Evil: separate hybrid strategy.
    # --------------------------------------------------------------
    if difficulty in ("hard", "evil"):
        return _generate_hard_or_evil(
            rng,
            difficulty,
            size,
            max_attempts,
        )

    # --------------------------------------------------------------
    # Easy / Medium: preserve the successful guided-ambiguity engine.
    # --------------------------------------------------------------
    profile = PROFILES[difficulty]
    best: Optional[Tuple[float, KakuroPuzzle]] = None

    for _ in range(max_attempts):
        density = rng.uniform(
            profile.min_density,
            profile.max_density,
        )
        symmetry = rng.random() < profile.symmetry_chance

        white = _pattern_from_blocks(
            rng,
            size,
            size,
            density,
            symmetry,
        )

        run_defs, ok = collect_runs(white)
        if not ok or not _geometry_quality_ok(white):
            continue

        fill_pool = []

        for _fill_try in range(6):
            solution = _make_solution(
                rng,
                white,
                run_defs,
                difficulty,
            )
            if solution is None:
                continue

            risk = _solution_ambiguity_risk(
                solution,
                run_defs,
            )
            fill_pool.append((risk, solution))

        if not fill_pool:
            continue

        fill_pool.sort(key=lambda x: x[0])

        checks_per_geometry = {
            "easy": 2,
            "medium": 3,
        }[difficulty]

        for _risk, solution in fill_pool[:checks_per_geometry]:
            puzzle = _build_puzzle(
                white,
                run_defs,
                solution,
                difficulty,
            )
            unique, nodes = solve_unique(
                puzzle,
                node_limit=profile.node_limit,
            )

            if not unique:
                continue

            score = difficulty_score(puzzle, nodes)
            puzzle.score = score

            if _score_matches(difficulty, score):
                return puzzle

            midpoint = (
                profile.target_score_min
                + min(profile.target_score_max, 100.0)
            ) / 2.0
            distance = abs(score - midpoint)

            if best is None or distance < best[0]:
                best = (distance, puzzle)

    if best is not None:
        return best[1]

    raise RuntimeError(
        f"Could not generate a connected, unique {difficulty} Kakuro after "
        f"{max_attempts} attempts. Try another seed, size, or a larger --max-attempts."
    )

