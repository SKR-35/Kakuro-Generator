from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

from .model import KakuroPuzzle, Run

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_utc     INTEGER NOT NULL,
    seed            INTEGER,
    pages           INTEGER NOT NULL,
    pagesize        TEXT NOT NULL,
    workers         INTEGER,
    fixed_size      INTEGER,
    mix_easy        INTEGER NOT NULL,
    mix_medium      INTEGER NOT NULL,
    mix_hard        INTEGER NOT NULL,
    mix_evil        INTEGER NOT NULL,
    args_json       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS puzzles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    idx_in_run      INTEGER NOT NULL,
    difficulty      TEXT NOT NULL,
    seed            INTEGER NOT NULL,
    rows            INTEGER NOT NULL,
    cols            INTEGER NOT NULL,
    white_cells     INTEGER NOT NULL,
    score           REAL NOT NULL,
    seconds         REAL NOT NULL,
    puzzle_json     TEXT NOT NULL,
    puzzle_hash     TEXT,
    UNIQUE(run_id, idx_in_run)
);

CREATE INDEX IF NOT EXISTS puzzles_by_run ON puzzles(run_id);
CREATE INDEX IF NOT EXISTS puzzles_by_diff ON puzzles(difficulty);
CREATE INDEX IF NOT EXISTS puzzles_by_score ON puzzles(score);
"""


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _canonical_puzzle_payload(p: KakuroPuzzle) -> Dict[str, Any]:
    canonical_runs = sorted(
        (
            r.direction,
            tuple(tuple(int(v) for v in cell) for cell in r.cells),
            int(r.target),
            tuple(int(v) for v in r.clue_cell),
        )
        for r in p.runs
    )
    return {
        "rows": int(p.rows),
        "cols": int(p.cols),
        "white": p.white,
        "runs": [
            {
                "direction": direction,
                "cells": [list(cell) for cell in cells],
                "target": target,
                "clue_cell": list(clue_cell),
            }
            for direction, cells, target, clue_cell in canonical_runs
        ],
    }


def puzzle_hash(p: KakuroPuzzle) -> str:
    payload = json.dumps(
        _canonical_puzzle_payload(p),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(puzzles)").fetchall()}
    if "puzzle_hash" not in columns:
        conn.execute("ALTER TABLE puzzles ADD COLUMN puzzle_hash TEXT")

    known = {
        row[0]
        for row in conn.execute(
            "SELECT puzzle_hash FROM puzzles WHERE puzzle_hash IS NOT NULL"
        ).fetchall()
    }
    rows = conn.execute(
        "SELECT id, puzzle_json FROM puzzles WHERE puzzle_hash IS NULL ORDER BY id"
    ).fetchall()

    for pid, puzzle_json in rows:
        try:
            h = puzzle_hash(puzzle_from_dict(json.loads(puzzle_json)))
        except Exception:
            continue
        if h in known:
            continue
        conn.execute("UPDATE puzzles SET puzzle_hash=? WHERE id=?", (h, int(pid)))
        known.add(h)

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS puzzles_unique_hash "
        "ON puzzles(puzzle_hash) WHERE puzzle_hash IS NOT NULL"
    )
    conn.commit()


def puzzle_to_dict(p: KakuroPuzzle) -> Dict[str, Any]:
    return {
        "rows": p.rows,
        "cols": p.cols,
        "white": p.white,
        "runs": [
            {
                "id": r.id,
                "direction": r.direction,
                "cells": [list(x) for x in r.cells],
                "target": r.target,
                "clue_cell": list(r.clue_cell),
            }
            for r in p.runs
        ],
        "solution": p.solution,
        "difficulty": p.difficulty,
        "score": p.score,
    }


def puzzle_from_dict(d: Dict[str, Any]) -> KakuroPuzzle:
    runs = [
        Run(
            id=int(r["id"]),
            direction=r["direction"],
            cells=tuple(tuple(x) for x in r["cells"]),
            target=int(r["target"]),
            clue_cell=tuple(r["clue_cell"]),
        )
        for r in d["runs"]
    ]
    return KakuroPuzzle(
        rows=int(d["rows"]),
        cols=int(d["cols"]),
        white=d["white"],
        runs=runs,
        solution=d["solution"],
        difficulty=d.get("difficulty", "medium"),
        score=float(d.get("score", 0.0)),
    )


def insert_run(conn: sqlite3.Connection, *, args: Dict[str, Any], schedule: List[str]) -> int:
    row = {
        "created_utc": int(time.time()),
        "seed": args.get("seed"),
        "pages": len(schedule),
        "pagesize": args.get("pagesize"),
        "workers": args.get("workers"),
        "fixed_size": args.get("size"),
        "mix_easy": schedule.count("easy"),
        "mix_medium": schedule.count("medium"),
        "mix_hard": schedule.count("hard"),
        "mix_evil": schedule.count("evil"),
        "args_json": json.dumps(args, ensure_ascii=False),
    }
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO runs (
            created_utc, seed, pages, pagesize, workers, fixed_size,
            mix_easy, mix_medium, mix_hard, mix_evil, args_json
        )
        VALUES (
            :created_utc, :seed, :pages, :pagesize, :workers, :fixed_size,
            :mix_easy, :mix_medium, :mix_hard, :mix_evil, :args_json
        )
        """,
        row,
    )
    conn.commit()
    return int(cur.lastrowid)



class DuplicatePuzzleError(Exception):
    def __init__(self, existing_id: int, puzzle_hash_value: str):
        self.existing_id = int(existing_id)
        self.puzzle_hash = puzzle_hash_value
        super().__init__(
            f"Duplicate Kakuro already exists as DB={self.existing_id} "
            f"(sha256={self.puzzle_hash[:12]}...)."
        )

def insert_puzzle(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    idx_in_run: int,
    difficulty: str,
    seed: int,
    seconds: float,
    puzzle: KakuroPuzzle,
) -> int:
    cur = conn.cursor()
    h = puzzle_hash(puzzle)
    try:
        cur.execute(
            """
            INSERT INTO puzzles (
                run_id, idx_in_run, difficulty, seed, rows, cols,
                white_cells, score, seconds, puzzle_json, puzzle_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                idx_in_run,
                difficulty,
                int(seed),
                puzzle.rows,
                puzzle.cols,
                puzzle.white_count,
                float(puzzle.score),
                float(seconds),
                json.dumps(puzzle_to_dict(puzzle), ensure_ascii=False, separators=(",", ":")),
                h,
            ),
        )
    except sqlite3.IntegrityError as exc:
        existing = conn.execute(
            "SELECT id FROM puzzles WHERE puzzle_hash=?",
            (h,),
        ).fetchone()
        if existing:
            raise DuplicatePuzzleError(int(existing[0]), h) from exc
        raise
    conn.commit()
    return int(cur.lastrowid)


def fetch_puzzles(conn: sqlite3.Connection, ids: List[int]):
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT id, difficulty, seed, score, seconds, puzzle_json
        FROM puzzles
        WHERE id IN ({marks})
        """,
        ids,
    ).fetchall()
    by_id = {int(r[0]): r for r in rows}
    out = []
    for pid in ids:
        row = by_id.get(pid)
        if row:
            out.append(
                (
                    int(row[0]),
                    row[1],
                    int(row[2]),
                    float(row[3]),
                    float(row[4]),
                    puzzle_from_dict(json.loads(row[5])),
                )
            )
    return out
