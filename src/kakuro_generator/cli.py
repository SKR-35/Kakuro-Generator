from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import random
import sys
import time
from typing import List, Optional, Tuple

from reportlab.pdfgen.canvas import Canvas

from .db import DuplicatePuzzleError, ensure_schema, fetch_puzzles, insert_puzzle, insert_run, open_db
from .generator import generate_kakuro
from .pdf import PageSizeMap, draw_puzzle_page, draw_solutions_pages


def _worker_task(args: Tuple[str, int, Optional[int], int, int]):
    """
    Generate one requested puzzle slot.

    If a stochastic generation attempt exhausts --max-attempts, retry the same
    slot with deterministic derived seeds. This keeps the whole batch alive
    while preserving reproducibility from the master seed.
    """
    difficulty, seed, size, max_attempts, task_retries = args
    t0 = time.time()

    retry_rng = random.Random(seed ^ 0x9E3779B97F4A7C15)
    seeds = [seed] + [
        retry_rng.randrange(2**63 - 1)
        for _ in range(max(0, task_retries))
    ]

    last_error = None
    for retry_no, actual_seed in enumerate(seeds):
        try:
            rng = random.Random(actual_seed)
            puzzle = generate_kakuro(
                rng,
                difficulty=difficulty,
                size=size,
                max_attempts=max_attempts,
            )
            return (
                difficulty,
                actual_seed,
                puzzle,
                time.time() - t0,
                retry_no,
            )
        except RuntimeError as exc:
            last_error = exc

    raise RuntimeError(
        f"{difficulty} slot failed after {len(seeds)} deterministic seed attempt(s). "
        f"Last error: {last_error}"
    )


def _build_schedule(args) -> List[str]:
    any_mix = any(v is not None for v in (args.easy, args.medium, args.hard, args.evil))
    if not any_mix:
        return [args.difficulty] * max(0, args.pages)

    out = []
    for name in ("easy", "medium", "hard", "evil"):
        count = max(0, getattr(args, name) or 0)
        out.extend([name] * count)
    return out



def _size_for_difficulty(args, difficulty: str) -> Optional[int]:
    override = getattr(args, f"{difficulty}_size", None)
    return override if override is not None else args.size


def _shutdown_executor_now(executor: cf.ProcessPoolExecutor) -> None:
    """
    Best-effort immediate shutdown for Ctrl+C on Windows.

    cancel_futures only cancels queued work; running worker processes otherwise
    continue until their current generation call returns.
    """
    try:
        processes = list(getattr(executor, "_processes", {}).values())
        for process in processes:
            if process.is_alive():
                process.terminate()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Generate unique Kakuro puzzles, store them in SQLite, and export PDF booklets."
    )

    p.add_argument("--pages", type=int, default=1)
    p.add_argument(
        "--difficulty",
        choices=["easy", "medium", "hard", "evil"],
        default="medium",
    )
    p.add_argument("--easy", type=int, default=None)
    p.add_argument("--medium", type=int, default=None)
    p.add_argument("--hard", type=int, default=None)
    p.add_argument("--evil", type=int, default=None)

    p.add_argument("--size", type=int, default=None, help="Global/fallback board size, 7..15.")
    p.add_argument("--easy-size", type=int, default=None)
    p.add_argument("--medium-size", type=int, default=None)
    p.add_argument("--hard-size", type=int, default=None)
    p.add_argument("--evil-size", type=int, default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--max-attempts", type=int, default=24)
    p.add_argument(
        "--task-retries",
        type=int,
        default=3,
        help="Retry a failed puzzle slot with deterministic derived seeds (default: 3).",
    )
    p.add_argument("--quiet", action="store_true")

    p.add_argument("--outfile", default="kakuro_puzzles.pdf")
    p.add_argument("--pagesize", choices=list(PageSizeMap), default="A4")
    p.add_argument(
        "--block-color",
        choices=["gray", "black"],
        default="gray",
        help="PDF block/clue-cell fill color (default: gray).",
    )
    p.add_argument("--db", default=None)
    p.add_argument("--no-pdf", action="store_true")
    p.add_argument(
        "--export-ids",
        default=None,
        help="Comma-separated SQLite puzzle IDs. Skips generation.",
    )

    args = p.parse_args()

    for option_name in ("size", "easy_size", "medium_size", "hard_size", "evil_size"):
        value = getattr(args, option_name)
        if value is not None and not 7 <= value <= 15:
            p.error(f"--{option_name.replace('_', '-')} must be between 7 and 15.")

    pagesize = PageSizeMap[args.pagesize]

    # -----------------------------
    # DB -> PDF export mode
    # -----------------------------
    if args.export_ids:
        if not args.db:
            p.error("--export-ids requires --db.")
        if args.no_pdf:
            p.error("--export-ids cannot be combined with --no-pdf.")

        ids = [int(x.strip()) for x in args.export_ids.split(",") if x.strip()]
        conn = open_db(args.db)
        ensure_schema(conn)
        rows = fetch_puzzles(conn, ids)

        found_ids = {row[0] for row in rows}
        missing = [x for x in ids if x not in found_ids]
        if missing and not args.quiet:
            print(f"⚠ Missing puzzle IDs skipped: {missing}")

        if not rows:
            if not args.quiet:
                print("No matching puzzles found. Nothing written.")
            return

        c = Canvas(args.outfile, pagesize=pagesize)
        puzzles = [row[-1] for row in rows]
        db_ids = [row[0] for row in rows]

        for i, (pid, _, _, _, _, puzzle) in enumerate(rows, start=1):
            draw_puzzle_page(c, puzzle, i, len(rows), pagesize, db_id=pid, block_color=args.block_color)
            if i < len(rows):
                c.showPage()

        draw_solutions_pages(c, puzzles, pagesize, db_ids=db_ids, block_color=args.block_color)
        c.save()

        if not args.quiet:
            print(f"✔ Exported {len(rows)} puzzle(s) from SQLite → {args.outfile}")
        return

    # -----------------------------
    # Generation mode
    # -----------------------------
    schedule = _build_schedule(args)
    if not schedule:
        if not args.quiet:
            print("No puzzles requested.")
        return

    master = random.Random(args.seed)
    child_seeds = [master.randrange(2**63 - 1) for _ in schedule]
    work = [
        (diff, seed, _size_for_difficulty(args, diff), args.max_attempts, args.task_retries)
        for diff, seed in zip(schedule, child_seeds)
    ]

    if not args.quiet:
        mix = ", ".join(
            f"{d}:{schedule.count(d)}"
            for d in ("easy", "medium", "hard", "evil")
            if d in schedule
        )
        size_mix = ", ".join(
            f"{d}={_size_for_difficulty(args, d) or 'auto'}"
            for d in ("easy", "medium", "hard", "evil")
            if d in schedule
        )
        print(
            f"▶ Generating {len(schedule)} Kakuro puzzle(s) [{mix}] "
            f"sizes=[{size_mix}] workers={args.workers or os.cpu_count()}"
        )
        sys.stdout.flush()

    t_all = time.time()

    # Open the SQLite run before generation starts. Each completed puzzle is
    # inserted and committed immediately, so Ctrl+C does not lose prior work.
    conn = None
    run_id = None
    if args.db:
        conn = open_db(args.db)
        ensure_schema(conn)
        run_id = insert_run(
            conn,
            args={
                "seed": args.seed,
                "pages": len(schedule),
                "pagesize": args.pagesize,
                "block_color": args.block_color,
                "workers": args.workers,
                "size": args.size,
                "easy_size": args.easy_size,
                "medium_size": args.medium_size,
                "hard_size": args.hard_size,
                "evil_size": args.evil_size,
                "max_attempts": args.max_attempts,
                "task_retries": args.task_retries,
                "outfile": args.outfile,
                "no_pdf": args.no_pdf,
                "easy": args.easy,
                "medium": args.medium,
                "hard": args.hard,
                "evil": args.evil,
            },
            schedule=schedule,
        )
        if not args.quiet:
            print(f"✔ Opened SQLite run_id={run_id}: {args.db}")

    results_by_idx = {}
    db_ids_by_idx = {}

    executor = cf.ProcessPoolExecutor(max_workers=args.workers)
    future_to_idx = {
        executor.submit(_worker_task, item): idx
        for idx, item in enumerate(work, start=1)
    }

    try:
        completed = 0
        failed_slots = []

        for future in cf.as_completed(future_to_idx):
            idx = future_to_idx[future]

            try:
                result = future.result()
            except Exception as exc:
                failed_slots.append(idx)
                if not args.quiet:
                    print(
                        f"⚠ slot {idx} failed and was skipped: {exc}"
                    )
                    sys.stdout.flush()
                continue

            diff, seed, puzzle, seconds, retry_no = result

            if conn is not None and run_id is not None:
                try:
                    pid = insert_puzzle(
                        conn,
                        run_id=run_id,
                        idx_in_run=idx,
                        difficulty=diff,
                        seed=seed,
                        seconds=seconds,
                        puzzle=puzzle,
                    )
                except DuplicatePuzzleError as exc:
                    failed_slots.append(idx)
                    if not args.quiet:
                        print(f"⚠ slot {idx} generated a duplicate and was skipped: {exc}")
                        sys.stdout.flush()
                    continue
                db_ids_by_idx[idx] = pid

            results_by_idx[idx] = result
            completed += 1

            if not args.quiet:
                db_msg = f" DB={db_ids_by_idx[idx]}" if idx in db_ids_by_idx else ""
                retry_msg = f" retry={retry_no}" if retry_no else ""
                print(
                    f"[{completed}/{len(schedule)} | slot {idx}] {diff:6s} "
                    f"{puzzle.rows}x{puzzle.cols} "
                    f"white={puzzle.white_count:3d} "
                    f"score={puzzle.score:5.1f} "
                    f"{seconds:6.2f}s{retry_msg}{db_msg}"
                )
                sys.stdout.flush()

    except KeyboardInterrupt:
        if not args.quiet:
            print("\n⚠ Interrupted. Completed puzzles were already committed to SQLite.")
            sys.stdout.flush()
        for future in future_to_idx:
            future.cancel()
        _shutdown_executor_now(executor)
        if conn is not None:
            conn.close()
        return
    else:
        executor.shutdown(wait=True)

    if failed_slots and not args.quiet:
        print(
            f"⚠ Batch completed with {len(failed_slots)} skipped slot(s): "
            f"{failed_slots}"
        )

    if conn is not None:
        if not args.quiet:
            ordered_saved_ids = [db_ids_by_idx[i] for i in sorted(db_ids_by_idx)]
            print(
                f"✔ Saved {len(ordered_saved_ids)} puzzle(s) incrementally to SQLite: "
                f"{args.db} (run_id={run_id}, puzzle_ids={ordered_saved_ids})"
            )
        conn.close()

    # as_completed() gives fastest persistence; sort back to requested schedule
    # order only for final PDF rendering.
    ordered_indices = sorted(results_by_idx)
    results = [results_by_idx[i] for i in ordered_indices]
    db_ids = [db_ids_by_idx.get(i) for i in ordered_indices]

    if not args.no_pdf:
        if not results:
            if not args.quiet:
                print("No puzzles were generated successfully; PDF was not written.")
            return

        c = Canvas(args.outfile, pagesize=pagesize)
        puzzles = [r[2] for r in results]

        for i, puzzle in enumerate(puzzles, start=1):
            draw_puzzle_page(
                c,
                puzzle,
                i,
                len(puzzles),
                pagesize,
                db_id=db_ids[i - 1],
                block_color=args.block_color,
            )
            if i < len(puzzles):
                c.showPage()

        draw_solutions_pages(c, puzzles, pagesize, db_ids=db_ids, block_color=args.block_color)
        c.save()

        if not args.quiet:
            print(
                f"✔ Wrote {args.outfile} with {len(puzzles)} puzzle(s) + solutions "
                f"in {(time.time() - t_all) / 60:.2f} min."
            )
    elif not args.quiet:
        print("✔ Generation complete; PDF suppressed with --no-pdf.")


if __name__ == "__main__":
    main()
