"""Drive Claude Code through program.md: Phase 1 once, then Phase 2 repeatedly.

Each session is a fresh, headless `claude -p` run, so the agent's only memory between
iterations is the repo itself, program.md, and experiments.jsonl.

    python run_loop.py                  # baseline (if needed) + 10 experiments
    python run_loop.py -n 50 --model claude-sonnet-5-5
    python run_loop.py --dry-run        # print what would run
"""

import argparse
import functools
import http.server
import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "experiments.jsonl"
SESSIONS = ROOT / "sessions"
REQUIRED_FIELDS = {
    "id", "timestamp", "hypothesis", "files_changed", "metric", "best_so_far",
    "decision", "duration_seconds", "tests_passed", "tests_total", "commit", "notes",
}
ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep",
    "Bash(python *)", "Bash(python3 *)", "Bash(git *)",
    "Bash(ls *)", "Bash(cat *)", "Bash(head *)", "Bash(tail *)", "Bash(wc *)", "Bash(mkdir *)",
]
GITIGNORE = "experiments.jsonl\nsessions/\n__pycache__/\n"

PHASE1_PROMPT = (
    "Read program.md and carry out Phase 1 (build the baseline) exactly as it describes. "
    "When the baseline is committed and logged as entry 1 in experiments.jsonl, stop."
)
PHASE2_PROMPT = (
    "Read program.md and carry out exactly one Phase 2 experiment as it describes. "
    "This will be experiment id {next_id}. When it is logged in experiments.jsonl, stop."
)


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def ensure_repo():
    if not (ROOT / ".git").exists():
        git("init")
    gi = ROOT / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE)
    if git("rev-parse", "--verify", "-q", "HEAD").returncode != 0:
        git("add", "-A")
        git("commit", "-m", "Initial program.md and loop runner")


def read_log_lines():
    if not LOG.exists():
        return []
    return [l for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]


def enforce_log(before):
    """Keep the log append-only and require exactly one valid new entry per session."""
    after = read_log_lines()
    if after[: len(before)] != before:
        print("  ! past log entries were modified; restoring them")
        after = before + [l for l in after if l not in before]
    new = after[len(before):]
    valid = []
    for line in new:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            print(f"  ! dropping unparseable log line: {line[:80]}")
            continue
        missing = REQUIRED_FIELDS - entry.keys()
        if missing:
            print(f"  ! entry missing fields {sorted(missing)}")
        valid.append(line)
    if len(valid) != 1:
        print(f"  ! expected 1 new log entry, got {len(valid)}")
    LOG.write_text("\n".join(before + valid) + ("\n" if before or valid else ""), encoding="utf-8")
    return [json.loads(l) for l in valid]


def enforce_clean_tree():
    """A session must end with its change either committed or reverted."""
    if git("status", "--porcelain").stdout.strip():
        print("  ! working tree left dirty; reverting uncommitted changes")
        git("reset", "--hard", "HEAD")
        git("clean", "-fd")  # no -x: ignored files (the log, transcripts) survive


def serve_dashboard(port):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a, **k: None
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        print(f"dashboard server not started ({e})")
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"dashboard: http://127.0.0.1:{port}/dashboard.html")


def run_session(cmd, label, timeout):
    SESSIONS.mkdir(exist_ok=True)
    transcript = SESSIONS / f"{label}.txt"
    start = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        out, code = proc.stdout + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        out, code = f"{e.stdout or ''}\n[runner] session timed out after {timeout}s", -1
    transcript.write_text(out, encoding="utf-8")
    print(f"  session finished in {time.time() - start:.0f}s (exit {code}), transcript: {transcript.name}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-n", "--iterations", type=int, default=10, help="Phase 2 experiments to run")
    p.add_argument("--model", help="model passed to claude --model")
    p.add_argument("--session-timeout", type=int, default=1800, help="seconds per agent session")
    p.add_argument("--max-failures", type=int, default=3, help="stop after this many sessions in a row log nothing")
    p.add_argument("--port", type=int, default=8000, help="dashboard port (0 to disable)")
    p.add_argument("--skip-permissions", action="store_true",
                   help="pass --dangerously-skip-permissions instead of a tool allowlist")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    claude = shutil.which("claude")
    if not claude:
        sys.exit("claude CLI not found on PATH")

    flags = []
    if args.skip_permissions:
        flags += ["--dangerously-skip-permissions"]
    else:
        flags += ["--permission-mode", "acceptEdits", "--allowedTools", *ALLOWED_TOOLS]
    if args.model:
        flags += ["--model", args.model]

    if not args.dry_run:
        ensure_repo()
        if args.port:
            serve_dashboard(args.port)

    planned = args.iterations + (0 if read_log_lines() else 1)
    failures = 0
    for i in range(planned):
        before = read_log_lines()
        if before:
            next_id = len(before) + 1
            label, prompt = f"exp_{next_id:03d}", PHASE2_PROMPT.format(next_id=next_id)
        else:
            label, prompt = "exp_001_baseline", PHASE1_PROMPT
        print(f"[{datetime.now():%H:%M:%S}] {label} ({i + 1}/{planned})")
        cmd = [claude, "-p", prompt, *flags]  # prompt first: --allowedTools is variadic
        if args.dry_run:
            print("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
            continue

        run_session(cmd, label, args.session_timeout)
        entries = enforce_log(before)
        enforce_clean_tree()

        for e in entries:
            print(f"  {e.get('decision')}: metric={e.get('metric')} best={e.get('best_so_far')} "
                  f"tests={e.get('tests_passed')}/{e.get('tests_total')} - {e.get('hypothesis')}")
        failures = 0 if entries else failures + 1
        if failures >= args.max_failures:
            sys.exit(f"stopping: {failures} sessions in a row produced no log entry")
        if not read_log_lines():
            sys.exit("stopping: baseline was not logged; check the session transcript")

    if args.port and not args.dry_run:
        input("done. dashboard still being served; press Enter to exit.")


if __name__ == "__main__":
    main()
