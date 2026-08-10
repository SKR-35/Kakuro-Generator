import random

from kakuro_generator.generator import generate_kakuro
from kakuro_generator.geometry import connected_white_cells
from kakuro_generator.solver import solve_unique


def test_generate_easy_unique_and_connected():
    p = generate_kakuro(random.Random(12345), "easy", size=7, max_attempts=8)
    assert p.rows == 7
    assert p.cols == 7
    assert p.white_count > 0
    assert connected_white_cells(p.white)
    unique, _ = solve_unique(p)
    assert unique


def test_runs_are_legal_connected_and_not_tiled():
    p = generate_kakuro(random.Random(54321), "medium", size=8, max_attempts=8)
    assert connected_white_cells(p.white)

    lengths = []
    for run in p.runs:
        assert 2 <= len(run.cells) <= 9
        lengths.append(len(run.cells))
        vals = [p.solution[r][c] for r, c in run.cells]
        assert len(vals) == len(set(vals))
        assert sum(vals) == run.target

    assert max(lengths) >= 3
    assert sum(1 for n in lengths if n == 2) / len(lengths) <= 0.72
