"""Drive Claude Code through a project's program.md: Phase 1 once, then Phase 2 repeatedly.

Each session is a fresh, headless `claude -p` run, so the agent's only memory between
iterations is the project repo, program.md, and experiments.jsonl.

    python run_loop.py --project projects/compressor -n 10
    python run_loop.py --project projects/new --goal "Build a fast sudoku solver"
    python run_loop.py --project projects/compressor --dry-run

Usually started from the web UI (app.py) rather than by hand.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "program_template.md"
REQUIRED_FIELDS = {
    "id", "timestamp", "hypothesis", "files_changed", "metric", "best_so_far",
    "decision", "duration_seconds", "tests_passed", "tests_total", "commit", "notes",
}
ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep",
    "Bash(python *)", "Bash(python3 *)", "Bash(git *)",
    "Bash(ls *)", "Bash(cat *)", "Bash(head *)", "Bash(tail *)", "Bash(wc *)", "Bash(mkdir *)",
]
# Runner-owned files: ignored by git so keep/revert never touches them.
IGNORED = ["experiments.jsonl", "sessions/", "activity.jsonl", "runner_state.json", "runner.log", "runner.out", "__pycache__/"]

PHASE1_PROMPT = (
    "Read program.md and carry out Phase 1 (build the baseline) exactly as it describes. "
    "When the baseline is committed and logged as entry 1 in experiments.jsonl, stop."
)
PHASE2_PROMPT = (
    "Read program.md and carry out exactly one Phase 2 experiment as it describes. "
    "This will be experiment id {next_id}. When it is logged in experiments.jsonl, stop."
)


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def create_project(root, goal, budget=120):
    """Write program.md for a new project from the template."""
    root.mkdir(parents=True, exist_ok=True)
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{GOAL}}", goal.strip()).replace("{{BUDGET}}", str(int(budget)))
    (root / "program.md").write_text(text, encoding="utf-8")


def kill_tree(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.killpg(pid, 9)
        except OSError:
            pass


class Runner:
    def __init__(self, root):
        self.root = root
        self.log_path = root / "experiments.jsonl"
        self.sessions = root / "sessions"
        self.state_path = root / "runner_state.json"
        self.state = {}

    # ---- bookkeeping -------------------------------------------------------

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)

    def save_state(self, **changes):
        self.state.update(changes, updated_at=now())
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        for _ in range(20):  # Windows: the UI may be reading the file at this instant
            try:
                os.replace(tmp, self.state_path)
                return
            except PermissionError:
                time.sleep(0.05)

    def activity(self, kind, text):
        rec = {"t": now(), "session": self.state.get("current"), "kind": kind, "text": text}
        with open(self.root / "activity.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def say(self, msg):
        print(msg, flush=True)
        with open(self.root / "runner.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
        self.activity("runner", msg)

    def read_log_lines(self):
        if not self.log_path.exists():
            return []
        return [l for l in self.log_path.read_text(encoding="utf-8").splitlines() if l.strip()]

    # ---- guardrails --------------------------------------------------------

    def ensure_repo(self):
        if not (self.root / ".git").exists():
            self.git("init")
        gi = self.root / ".gitignore"
        lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
        missing = [p for p in IGNORED if p not in lines]
        if missing:
            gi.write_text("\n".join(lines + missing) + "\n", encoding="utf-8")
            self.git("add", ".gitignore")
            if self.git("rev-parse", "--verify", "-q", "HEAD").returncode == 0:
                self.git("commit", "-q", "-m", "Ignore runner-owned files")
        if self.git("rev-parse", "--verify", "-q", "HEAD").returncode != 0:
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "Initial program.md")

    def enforce_log(self, before):
        """Keep the log append-only and require exactly one valid new entry per session."""
        after = self.read_log_lines()
        if after[: len(before)] != before:
            self.say("! past log entries were modified; restoring them")
            after = before + [l for l in after if l not in before]
        valid = []
        for line in after[len(before):]:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                self.say(f"! dropping unparseable log line: {line[:80]}")
                continue
            missing = REQUIRED_FIELDS - entry.keys()
            if missing:
                self.say(f"! entry missing fields {sorted(missing)}")
            valid.append(line)
        if len(valid) != 1:
            self.say(f"! expected 1 new log entry, got {len(valid)}")
        lines = before + valid
        self.log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return [json.loads(l) for l in valid]

    def enforce_clean_tree(self):
        """A session must end with its change either committed or reverted."""
        if self.git("status", "--porcelain").stdout.strip():
            self.say("! working tree left dirty; reverting uncommitted changes")
            self.git("reset", "--hard", "HEAD")
            self.git("clean", "-fd")  # no -x: ignored files (log, transcripts) survive

    # ---- sessions ----------------------------------------------------------

    def handle_event(self, line):
        """Turn one stream-json line into a readable activity record; return the final result event."""
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            if line.strip():
                self.activity("raw", line.strip()[:300])
            return None
        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    self.activity("text", block["text"].strip()[:600])
                elif block.get("type") == "tool_use":
                    inp = block.get("input", {})
                    detail = (inp.get("command") or inp.get("file_path") or inp.get("pattern")
                              or inp.get("description") or "")
                    detail = re.sub(r'^cd\s+("[^"]*"|\S+)\s*&&\s*', "", str(detail))  # drop cd-into-repo noise
                    self.activity("tool", f"{block.get('name')}: {str(detail)[:200]}")
        elif ev.get("type") == "result":
            return ev
        return None

    def run_session(self, cmd, label, timeout):
        self.sessions.mkdir(exist_ok=True)
        start = time.time()
        result = None
        popen_kw = {"start_new_session": True} if os.name != "nt" else {}
        proc = subprocess.Popen(cmd, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", **popen_kw)
        timed_out = threading.Event()

        def on_timeout():
            timed_out.set()
            kill_tree(proc.pid)

        timer = threading.Timer(timeout, on_timeout)
        timer.start()
        with open(self.sessions / f"{label}.jsonl", "w", encoding="utf-8") as raw:
            for line in proc.stdout:
                raw.write(line)
                raw.flush()
                result = self.handle_event(line) or result
        proc.wait()
        timer.cancel()
        if timed_out.is_set():
            self.say(f"! session timed out after {timeout}s")
        info = {
            "label": label,
            "seconds": round(time.time() - start, 1),
            "exit": proc.returncode,
            "cost_usd": (result or {}).get("total_cost_usd"),
            "turns": (result or {}).get("num_turns"),
        }
        self.say(f"session {label} finished in {info['seconds']:.0f}s (exit {proc.returncode})")
        return info

    # ---- main loop ---------------------------------------------------------

    def run(self, args, claude):
        flags = ["--output-format", "stream-json", "--verbose"]
        if args.skip_permissions:
            flags += ["--dangerously-skip-permissions"]
        else:
            flags += ["--permission-mode", "acceptEdits", "--allowedTools", *ALLOWED_TOOLS]
        if args.model:
            flags += ["--model", args.model]

        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.state.setdefault("sessions", [])

        if not args.dry_run:
            self.ensure_repo()
            self.enforce_clean_tree()  # recover from a run that was stopped mid-experiment

        planned = args.iterations + (0 if self.read_log_lines() else 1)
        if not args.dry_run:
            self.save_state(status="running", pid=os.getpid(), planned=planned, done=0,
                            current=None, started_at=now(), model=args.model)
        failures = 0
        for i in range(planned):
            before = self.read_log_lines()
            if before:
                next_id = len(before) + 1
                label, prompt, phase = f"exp_{next_id:03d}", PHASE2_PROMPT.format(next_id=next_id), 2
            else:
                label, prompt, phase = "exp_001_baseline", PHASE1_PROMPT, 1
            cmd = [claude, "-p", prompt, *flags]  # prompt first: --allowedTools is variadic
            if args.dry_run:
                print(f"{label}: " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
                continue

            self.save_state(current=label, phase=phase, done=i, session_started_at=now())
            self.say(f"starting {label} ({i + 1}/{planned}, phase {phase})")
            info = self.run_session(cmd, label, args.session_timeout)
            entries = self.enforce_log(before)
            self.enforce_clean_tree()

            self.state["sessions"].append(info)
            costs = [s["cost_usd"] for s in self.state["sessions"] if s.get("cost_usd")]
            self.save_state(total_cost_usd=round(sum(costs), 4), done=i + 1, current=None)
            for e in entries:
                self.say(f"{e.get('decision')}: metric={e.get('metric')} best={e.get('best_so_far')} "
                         f"tests={e.get('tests_passed')}/{e.get('tests_total')} - {e.get('hypothesis')}")
            failures = 0 if entries else failures + 1
            if failures >= args.max_failures:
                self.say(f"stopping: {failures} sessions in a row produced no log entry")
                self.save_state(status="error")
                return 1
            if not self.read_log_lines():
                self.say("stopping: baseline was not logged; check the session transcript")
                self.save_state(status="error")
                return 1
        if not args.dry_run:
            self.save_state(status="finished", current=None)
            self.say("run finished")
        return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True, help="project directory")
    p.add_argument("--goal", help="create the project with this goal if it has no program.md")
    p.add_argument("--budget", type=int, default=120, help="evaluation time budget for new projects (s)")
    p.add_argument("-n", "--iterations", type=int, default=10, help="Phase 2 experiments to run")
    p.add_argument("--model", help="model passed to claude --model")
    p.add_argument("--session-timeout", type=int, default=1800, help="seconds per agent session")
    p.add_argument("--max-failures", type=int, default=3, help="stop after this many sessions in a row log nothing")
    p.add_argument("--skip-permissions", action="store_true",
                   help="pass --dangerously-skip-permissions instead of a tool allowlist")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    root = Path(args.project).resolve()
    if not (root / "program.md").exists():
        if not args.goal:
            sys.exit(f"{root} has no program.md; pass --goal to create it")
        create_project(root, args.goal, args.budget)

    claude = shutil.which("claude")
    if not claude:
        sys.exit("claude CLI not found on PATH")
    sys.exit(Runner(root).run(args, claude))


if __name__ == "__main__":
    main()
