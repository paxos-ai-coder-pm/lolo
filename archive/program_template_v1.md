# program.md

You are an autonomous engineer working alone in this repository. You write all of the
code. A human will only read the results later.

## Goal

{{GOAL}}

## Metric

If the goal names a metric, use it. Otherwise, in Phase 1, choose ONE objective number that
a script can measure automatically and that best captures how well the goal is met.

- Record it in `metric.json`:
  `{"name": "...", "direction": "lower" | "higher", "unit": "...", "description": "..."}`
- Benchmark inputs must be fixed and deterministic so every run is comparable.
- A run is only valid if all tests pass. An invalid run counts as a failure, whatever its metric.
- The full evaluation must finish within **{{BUDGET}} seconds**. Slower counts as a failure.

## Files

- `evaluate.py` runs the tests and the benchmark, then prints one JSON object to stdout:
  `{"metric": float, "tests_passed": int, "tests_total": int, "duration_seconds": float}`
- `metric.json` describes the metric (see above).
- `experiments.jsonl` is the experiment log (see below).
- `dashboard.html` is the progress dashboard (see Phase 1).
- Everything else is up to you. Prefer Python and its standard library. You cannot rely on
  installing anything.

## Rules

- Never edit `program.md`.
- Never edit or delete existing lines of `experiments.jsonl`. Only append.
- After the baseline, never change `metric.json`, the benchmark inputs, or how the metric is computed.
- Never special-case the benchmark inputs to game the metric.
- Do not run `git clean -x` or anything else that deletes ignored files.
- Do exactly one unit of work per session (the baseline, or one experiment), then stop.

## Experiment log

Append one JSON object per line to `experiments.jsonl` for every evaluation, including the baseline:

| field | meaning |
|---|---|
| `id` | sequential experiment number, starting at 1 |
| `timestamp` | ISO 8601 time the run finished |
| `hypothesis` | one sentence: what was tried and why |
| `files_changed` | list of files modified |
| `metric` | metric value for this run (`null` if the run failed) |
| `best_so_far` | best metric after this run |
| `decision` | `"kept"` or `"discarded"` |
| `duration_seconds` | how long the evaluation took |
| `tests_passed` / `tests_total` | test results for this run |
| `commit` | commit hash if kept, otherwise `null` |
| `notes` | anything learned, including why a change failed |

## Phase 1: Build the baseline

(Use this phase when `experiments.jsonl` is missing or empty.)

1. Decide the metric and write `metric.json`.
2. Design the simplest architecture that works. Minimal and correct beats ambitious.
3. Write the code, the tests, and `evaluate.py`.
4. Build `dashboard.html`: one self-contained page (no build step, no external downloads
   required to show data) that fetches `experiments.jsonl` and shows:
   - Summary cards: current best, starting baseline, total improvement (absolute and %),
     total experiments, kept vs discarded, keep rate
   - A chart of the metric for every experiment, with a line tracing best-so-far
   - A chart of test pass rate over time
   - A sortable, filterable table: id, hypothesis, metric, decision, duration, notes
   - Auto-refresh every few seconds
   It is served over HTTP from the repo root.
5. Run `evaluate.py`, confirm it works end to end, commit everything, then append entry
   `id: 1` with `decision: "kept"` and the commit hash.

## Phase 2: Improve

1. Read this file, the code, and `experiments.jsonl`.
2. Pick one hypothesis likely to improve the metric.
3. Implement it.
4. Run `evaluate.py`.
5. If the run is valid and the metric is better than `best_so_far`: commit, and log `kept`
   with the hash. Otherwise: `git reset --hard HEAD`, and log `discarded` with notes on why.
6. Append the entry to `experiments.jsonl`. Then stop.

Do not modify `dashboard.html` in Phase 2.
