from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

Cell = Tuple[int, int]


@dataclass(frozen=True)
class Run:
    id: int
    direction: str          # "across" or "down"
    cells: Tuple[Cell, ...]
    target: int
    clue_cell: Cell


@dataclass
class KakuroPuzzle:
    rows: int
    cols: int
    white: List[List[bool]]
    runs: List[Run]
    solution: List[List[Optional[int]]]
    difficulty: str = "medium"
    score: float = 0.0

    def run_map(self) -> Dict[Cell, Tuple[Optional[int], Optional[int]]]:
        """cell -> (across_run_id, down_run_id)."""
        out: Dict[Cell, List[Optional[int]]] = {}
        for run in self.runs:
            pos = 0 if run.direction == "across" else 1
            for cell in run.cells:
                if cell not in out:
                    out[cell] = [None, None]
                out[cell][pos] = run.id
        return {k: (v[0], v[1]) for k, v in out.items()}

    def clue_map(self) -> Dict[Cell, Dict[str, int]]:
        """
        Black clue cell -> {"across": sum?, "down": sum?}.
        A clue cell may contain one or both clues.
        """
        out: Dict[Cell, Dict[str, int]] = {}
        for run in self.runs:
            d = out.setdefault(run.clue_cell, {})
            d[run.direction] = run.target
        return out

    @property
    def white_count(self) -> int:
        return sum(1 for row in self.white for v in row if v)
