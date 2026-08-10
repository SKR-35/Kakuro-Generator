# Kakuro Generator

Generate **unique-solution Kakuro puzzles**, store them in SQLite and export print-ready PDF booklets with compact solution pages.

The generator supports independent puzzle counts and board sizes for **Easy, Medium, Hard and Evil** difficulties, deterministic generation from a master seed, multiprocessing, incremental SQLite persistence and duplicate protection.

## Features

- Four difficulty levels: Easy ★, Medium ★★, Hard ★★★, Evil ★★★★
- Independent board size for each difficulty
- Mixed-difficulty booklet generation in a single command
- Reproducible generation from a master seed
- Unique-solution verification
- Multiprocessing with configurable worker count
- Deterministic retries for failed generation slots
- Incremental SQLite persistence
- Duplicate-puzzle protection in SQLite
- Export selected database puzzles by ID
- PDF puzzle booklet with compact solution pages
- Gray clue/block cells by default, with optional black style
- A4, Letter, Legal, 6×9 and 8×10 page formats
- Graceful Ctrl+C handling that preserves already committed puzzles

## Requirements

- Python 3.9+
- ReportLab 4.2.2+
- pytest 8.0+ for testing

## Installation

Using Conda:

```bash
conda create -n kakuro-generator python=3.11 -y
conda activate kakuro-generator
pip install -r requirements.txt
pip install -e .
```

The package installs the `kakuro-generator` command-line entry point.

## Quick Start

Generate a single medium puzzle:

```bash
kakuro-generator --pages 1 --difficulty medium --seed 19930316
```

Generate puzzles into SQLite without creating a PDF:

```bash
kakuro-generator --easy 8 --size 8 --workers 2 --seed 19930316 --db puzzles.db --no-pdf
```

## Mixed-Difficulty Booklet

Puzzle counts and sizes can be configured independently for each difficulty.

### Windows / Anaconda Prompt

```bash
kakuro-generator --easy 8 --easy-size 8 --medium 8 --medium-size 9 --hard 8 --hard-size 10 --evil 8 --evil-size 10 --workers 2 --seed 19930316 --db puzzles.db --outfile kakuro_booklet.pdf
```

This generates one 32-puzzle booklet containing:

| Difficulty | Count | Board size |
|---|---:|---:|
| Easy ★ | 8 | 8×8 |
| Medium ★★ | 8 | 9×9 |
| Hard ★★★ | 8 | 10×10 |
| Evil ★★★★ | 8 | 10×10 |

### Multiline shell form

```bash
kakuro-generator \
  --easy 8 --easy-size 8 \
  --medium 8 --medium-size 9 \
  --hard 8 --hard-size 10 \
  --evil 8 --evil-size 10 \
  --workers 2 \
  --seed 19930316 \
  --db puzzles.db \
  --outfile kakuro_booklet.pdf
```

## Generation Modes

### Single difficulty

Use `--pages` together with `--difficulty`:

```bash
kakuro-generator --pages 5 --difficulty medium --size 9 --db puzzles.db --outfile medium_booklet.pdf
```

`--difficulty` accepts:

- `easy`
- `medium`
- `hard`
- `evil`

### Mixed difficulty

Use any combination of:

```text
--easy N
--medium N
--hard N
--evil N
```

When mixed generation is used, the corresponding count options define the generation schedule.

## Board Sizes

`--size` sets a global/fallback square board size:

```bash
kakuro-generator --hard 4 --size 10
```

Difficulty-specific overrides are also available:

```text
--easy-size N
--medium-size N
--hard-size N
--evil-size N
```

A difficulty-specific size overrides `--size` for that difficulty.

Supported sizes are **7 through 15**.

For example:

```bash
kakuro-generator --easy 4 --easy-size 8 --medium 4 --medium-size 9 --size 10
```

Here Easy uses 8×8, Medium uses 9×9 and any other requested difficulty falls back to 10×10.

## Reproducibility and Retries

Use `--seed` to initialize the master random generator:

```bash
kakuro-generator --easy 8 --size 8 --seed 19930316
```

Each requested puzzle receives a derived child seed.

If a generation slot exhausts its normal generation attempts, `--task-retries` retries that slot with deterministically derived seeds:

```bash
kakuro-generator --evil 4 --size 10 --task-retries 8
```

The default is:

```text
--max-attempts 24
--task-retries 3
```

Because retry seeds are derived deterministically, a fixed configuration and master seed remain reproducible.

## SQLite Storage

Provide `--db` to persist generated puzzles:

```bash
kakuro-generator --medium 8 --size 9 --db puzzles.db
```

The database stores generation runs and generated puzzles. Completed puzzles are inserted and committed **incrementally**, rather than waiting for the entire batch to finish.

This means that successfully completed work is preserved if a later slot fails or generation is interrupted.

Duplicate puzzle insertion is rejected by the database layer, preventing an already stored Kakuro from being inserted again.

## Export Existing Puzzles

Previously generated puzzles can be selected from SQLite and exported without generating new ones:

```bash
kakuro-generator --db puzzles.db --export-ids 3,7,11,16 --outfile curated_booklet.pdf
```

Missing IDs are skipped.

`--export-ids` requires `--db` and cannot be combined with `--no-pdf`.

## PDF Output

Unless `--no-pdf` is specified, generated puzzles are written to a PDF followed by compact solution pages.

Default output:

```text
kakuro_puzzles.pdf
```

Choose another filename with:

```bash
--outfile kakuro_booklet.pdf
```

Supported page sizes:

```text
A4
LETTER
LEGAL
6X9
8X10
```

Example:

```bash
kakuro-generator --medium 8 --size 9 --pagesize LETTER --outfile kakuro_letter.pdf
```

### Block color

Gray clue/block cells are the default:

```text
--block-color gray
```

A black style is also available:

```bash
kakuro-generator --easy 8 --size 8 --block-color black
```

## Main CLI Options

| Option | Purpose |
|---|---|
| `--pages N` | Number of puzzles in single-difficulty mode |
| `--difficulty LEVEL` | `easy`, `medium`, `hard` or `evil` |
| `--easy N` | Number of Easy puzzles |
| `--medium N` | Number of Medium puzzles |
| `--hard N` | Number of Hard puzzles |
| `--evil N` | Number of Evil puzzles |
| `--size N` | Global/fallback board size, 7–15 |
| `--easy-size N` | Easy board-size override |
| `--medium-size N` | Medium board-size override |
| `--hard-size N` | Hard board-size override |
| `--evil-size N` | Evil board-size override |
| `--seed N` | Master random seed |
| `--workers N` | Number of worker processes |
| `--max-attempts N` | Generation attempts inside each puzzle-generation call |
| `--task-retries N` | Deterministic retries for a failed puzzle slot |
| `--db PATH` | SQLite database path |
| `--outfile PATH` | Output PDF path |
| `--pagesize SIZE` | PDF page size |
| `--block-color COLOR` | `gray` or `black`; default `gray` |
| `--no-pdf` | Generate/store puzzles without PDF output |
| `--export-ids IDS` | Export comma-separated SQLite puzzle IDs |
| `--quiet` | Suppress normal progress output |

For the authoritative option list:

```bash
kakuro-generator --help
```

## Failure Handling

Generation is stochastic, so an individual requested slot can occasionally fail even after retries.

A failed slot is skipped rather than terminating the whole batch. Other successful puzzles continue to be generated and, when SQLite is enabled, are committed immediately.

On Ctrl+C, queued work is cancelled and worker processes are terminated on a best-effort basis. Puzzles already committed to SQLite remain available.

## Kakuro Rules

- White cells contain digits **1–9**.
- Digits may not repeat within a horizontal or vertical run.
- The digits in each run must sum to its clue.
- Every generated puzzle is verified to have exactly one solution.

## Difficulty

Difficulty is treated separately from board size.

The generator can therefore create, for example, both Hard and Evil puzzles on the same 10×10 board size while targeting different puzzle characteristics.

Difficulty scoring is based on puzzle structure and solver behavior rather than simply assigning difficulty from dimensions alone.

## Typical Workflow

A practical workflow is:

1. Generate a reproducible batch with a master seed.
2. Persist every successful puzzle to SQLite.
3. Let duplicate protection reject previously stored puzzles.
4. Review or curate the stored puzzle collection.
5. Export selected puzzle IDs into a final booklet.

This separates **generation**, **persistence** and **publication**, while still allowing a complete booklet to be produced in one command.

## Testing

Install the development requirements and run:

```bash
pytest
```