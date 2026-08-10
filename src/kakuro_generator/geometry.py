from __future__ import annotations

from typing import List, Tuple

Cell = Tuple[int, int]


def new_mask(rows: int, cols: int) -> List[List[bool]]:
    """
    Start with a black top row and left column.
    Interior cells are filled by the generator.
    """
    return [[False for _ in range(cols)] for _ in range(rows)]


def collect_runs(white: List[List[bool]]) -> Tuple[List[Tuple[str, Tuple[Cell, ...], Cell]], bool]:
    """
    Derive Kakuro runs from a white/black mask.

    Returns (runs, valid). Each run is:
      (direction, cells, clue_cell)

    Structural validity:
    - every run has length 2..9
    - every white cell belongs to one across and one down run
    """
    rows, cols = len(white), len(white[0])
    runs = []
    across_count = {}
    down_count = {}

    # Across
    for r in range(rows):
        c = 0
        while c < cols:
            if white[r][c] and (c == 0 or not white[r][c - 1]):
                cells = []
                cc = c
                while cc < cols and white[r][cc]:
                    cells.append((r, cc))
                    cc += 1
                if not (2 <= len(cells) <= 9):
                    return [], False
                clue = (r, c - 1)
                if clue[1] < 0:
                    return [], False
                runs.append(("across", tuple(cells), clue))
                for x in cells:
                    across_count[x] = across_count.get(x, 0) + 1
                c = cc
            else:
                c += 1

    # Down
    for c in range(cols):
        r = 0
        while r < rows:
            if white[r][c] and (r == 0 or not white[r - 1][c]):
                cells = []
                rr = r
                while rr < rows and white[rr][c]:
                    cells.append((rr, c))
                    rr += 1
                if not (2 <= len(cells) <= 9):
                    return [], False
                clue = (r - 1, c)
                if clue[0] < 0:
                    return [], False
                runs.append(("down", tuple(cells), clue))
                for x in cells:
                    down_count[x] = down_count.get(x, 0) + 1
                r = rr
            else:
                r += 1

    whites = [(r, c) for r in range(rows) for c in range(cols) if white[r][c]]
    if not whites:
        return [], False

    for cell in whites:
        if across_count.get(cell, 0) != 1 or down_count.get(cell, 0) != 1:
            return [], False

    return runs, True



def white_component_count(white: List[List[bool]]) -> int:
    """Return the number of orthogonally connected white-cell components."""
    rows, cols = len(white), len(white[0])
    remaining = {(r, c) for r in range(rows) for c in range(cols) if white[r][c]}
    components = 0
    while remaining:
        components += 1
        start = next(iter(remaining))
        remaining.remove(start)
        stack = [start]
        while stack:
            r, c = stack.pop()
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (r + dr, c + dc)
                if nxt in remaining:
                    remaining.remove(nxt)
                    stack.append(nxt)
    return components


def connected_white_cells(white: List[List[bool]]) -> bool:
    """A publishable Kakuro has one non-empty orthogonally connected white area."""
    return white_component_count(white) == 1
