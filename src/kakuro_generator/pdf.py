from __future__ import annotations

from typing import Dict, List, Tuple

from reportlab.lib.pagesizes import A4, LETTER, LEGAL
from reportlab.lib.units import inch, mm
from reportlab.pdfgen.canvas import Canvas

from .model import KakuroPuzzle

PageSizeMap = {
    "A4": A4,
    "LETTER": LETTER,
    "LEGAL": LEGAL,
    "6X9": (6 * inch, 9 * inch),
    "8X10": (8 * inch, 10 * inch),
}


def _difficulty_to_stars_label(diff: str) -> Tuple[str, str]:
    return {
        "easy": ("★", "Easy"),
        "medium": ("★★", "Medium"),
        "hard": ("★★★", "Hard"),
        "evil": ("★★★★", "Evil"),
    }[diff]


def _draw_grid(
    c: Canvas,
    puzzle: KakuroPuzzle,
    x0: float,
    y0: float,
    cell: float,
    *,
    solution: bool,
    mini: bool,
    block_color: str = "gray",
) -> None:
    rows, cols = puzzle.rows, puzzle.cols
    clues = puzzle.clue_map()

    thin = max(0.3, cell * (0.035 if mini else 0.045))
    diag = max(0.4, cell * (0.045 if mini else 0.055))
    digit_fs = max(5.0, min(16.0, cell * (0.52 if mini else 0.58)))
    clue_fs = max(3.5, min(8.5, cell * (0.27 if mini else 0.30)))

    for r in range(rows):
        for col in range(cols):
            # PDF y grows upward; puzzle row 0 is visually top.
            x = x0 + col * cell
            y = y0 + (rows - 1 - r) * cell

            c.setLineWidth(thin)
            c.rect(x, y, cell, cell, stroke=1, fill=0)

            if not puzzle.white[r][col]:
                c.saveState()
                block_fill = 0.12 if block_color == "black" else 0.72
                c.setFillGray(block_fill)
                c.rect(x, y, cell, cell, stroke=0, fill=1)
                c.restoreState()

                info = clues.get((r, col))
                if info:
                    c.setLineWidth(diag)
                    clue_gray = 0.92 if block_color == "black" else 0.18
                    c.setStrokeGray(clue_gray)
                    c.line(x, y + cell, x + cell, y)
                    c.setFillGray(1.0 if block_color == "black" else 0.0)
                    c.setFont("Helvetica", clue_fs)

                    # Across clue: upper-right triangle.
                    if "across" in info:
                        c.drawRightString(
                            x + cell * 0.90,
                            y + cell * 0.62,
                            str(info["across"]),
                        )
                    # Down clue: lower-left triangle.
                    if "down" in info:
                        c.drawString(
                            x + cell * 0.10,
                            y + cell * 0.12,
                            str(info["down"]),
                        )
                    c.setFillGray(0.0)
                    c.setStrokeGray(0.0)
            elif solution:
                v = puzzle.solution[r][col]
                if v is not None:
                    c.setFont("Helvetica", digit_fs)
                    c.drawCentredString(
                        x + cell / 2.0,
                        y + cell * 0.5 - digit_fs * 0.30,
                        str(v),
                    )


def draw_puzzle_page(
    canvas: Canvas,
    puzzle: KakuroPuzzle,
    page_num: int,
    total_pages: int,
    pagesize,
    *,
    db_id: int | None = None,
    block_color: str = "gray",
) -> None:
    W, H = pagesize
    margin = 15 * mm

    canvas.setFont("Helvetica-Bold", 16)
    title = f"Kakuro — Puzzle {page_num}/{total_pages}"
    if db_id is not None:
        title += f"  [ID {db_id}]"
    canvas.drawString(margin, H - margin, title)

    stars, label = _difficulty_to_stars_label(puzzle.difficulty)
    canvas.setFont("Helvetica", 11)
    canvas.drawString(
        margin,
        H - margin - 16,
        f"Difficulty: {label} {stars}    Size: {puzzle.rows}×{puzzle.cols}",
    )

    usable_w = W - 2 * margin
    usable_h = H - 3 * margin - 24
    cell = min(usable_w / puzzle.cols, usable_h / puzzle.rows)
    x0 = (W - puzzle.cols * cell) / 2.0
    y0 = margin + 6

    _draw_grid(canvas, puzzle, x0, y0, cell, solution=False, mini=False, block_color=block_color)

    canvas.setFont("Helvetica-Oblique", 8)
    canvas.drawRightString(W - margin, margin * 0.65, f"Page {page_num}/{total_pages}")


def draw_solutions_pages(
    canvas: Canvas,
    puzzles: List[KakuroPuzzle],
    pagesize,
    *,
    db_ids: List[int] | None = None,
    per_page: int = 4,
    block_color: str = "gray",
) -> None:
    W, H = pagesize
    margin = 12 * mm
    db_ids = db_ids or [None] * len(puzzles)

    cols = 2
    rows = 2
    page_capacity = cols * rows

    for page_start in range(0, len(puzzles), page_capacity):
        canvas.showPage()
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(margin, H - margin, "Solutions")

        top = H - margin - 24
        area_h = top - margin
        area_w = W - 2 * margin
        box_w = area_w / cols
        box_h = area_h / rows

        chunk = puzzles[page_start: page_start + page_capacity]
        chunk_ids = db_ids[page_start: page_start + page_capacity]

        for idx, (puzzle, db_id) in enumerate(zip(chunk, chunk_ids)):
            rr = idx // cols
            cc = idx % cols

            max_w = box_w - 14
            max_h = box_h - 22
            cell = min(max_w / puzzle.cols, max_h / puzzle.rows)
            block_w = puzzle.cols * cell
            block_h = puzzle.rows * cell

            bx = margin + cc * box_w + (box_w - block_w) / 2.0
            by = margin + (rows - 1 - rr) * box_h + (box_h - block_h) / 2.0

            _draw_grid(canvas, puzzle, bx, by, cell, solution=True, mini=True, block_color=block_color)

            label = f"#{page_start + idx + 1}"
            if db_id is not None:
                label += f" / DB {db_id}"
            canvas.setFont("Helvetica", 8)
            canvas.drawCentredString(bx + block_w / 2.0, by - 10, label)
