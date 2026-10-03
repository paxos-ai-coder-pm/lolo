"""Web UI for the autonomous engineering loop.

    python app.py            # then open http://127.0.0.1:8000

Type a goal, and a project is created under projects/ and run_loop.py starts on it.
Every project's progress, KPIs, and live agent activity are shown in the browser.
Standard library only.
"""

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from run_loop import create_project, kill_tree

HERE = Path(__file__).resolve().parent
PROJECTS = HERE / "projects"
UI = HERE / "ui.html"

procs = {}  # project id -> Popen of its run_loop.py
procs_lock = threading.Lock()
_alive_cache = {}  # pid -> (checked_at, alive) for runners started outside this server


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_jsonl(path, tail=None):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    if tail:
        lines = lines[-tail:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def pid_alive(pid):
    checked, alive = _alive_cache.get(pid, (0, False))
    if time.time() - checked < 10:
        return alive
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        alive = str(pid) in out
    else:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    _alive_cache[pid] = (time.time(), alive)
    return alive


def project_dir(pid):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", pid or ""):
        return None
    d = PROJECTS / pid
    return d if (d / "program.md").exists() else None


def goal_of(d):
    text = (d / "program.md").read_text(encoding="utf-8")
    m = re.search(r"^## Goal\s*\n(.*?)(?=^## )", text, re.S | re.M)
    return (m.group(1) if m else text).strip()


def status_of(pid, d):
    state = read_json(d / "runner_state.json", {}) or {}
    with procs_lock:
        p = procs.get(pid)
    if p is not None and p.poll() is None:
        running = True
    elif state.get("status") in ("running", "waiting") and state.get("pid"):
        running = pid_alive(state["pid"])  # started from the CLI or before a server restart
    else:
        running = False
    status = state.get("status") or ("idle" if (d / "experiments.jsonl").exists() else "new")
    if status in ("running", "waiting") and not running:
        status = "stopped"
    return status, state


def summary(pid, d):
    status, state = status_of(pid, d)
    exps = read_jsonl(d / "experiments.jsonl")
    valid = [e for e in exps if isinstance(e.get("metric"), (int, float))]
    return {
        "id": pid,
        "goal": goal_of(d),
        "status": status,
        "experiments": len(exps),
        "baseline": valid[0]["metric"] if valid else None,
        "best": exps[-1].get("best_so_far") if exps else None,
        "metric": read_json(d / "metric.json", {}) or {},
        "cost_usd": state.get("total_cost_usd"),
    }


def detail(pid, d):
    s = summary(pid, d)
    _, state = status_of(pid, d)
    try:
        log_tail = (d / "runner.log").read_text(encoding="utf-8").splitlines()[-50:]
    except OSError:
        log_tail = []
    s.update(
        state=state,
        experiments_log=read_jsonl(d / "experiments.jsonl"),
        activity=read_jsonl(d / "activity.jsonl", tail=300),
        program_md=(d / "program.md").read_text(encoding="utf-8"),
        runner_log=log_tail,
        has_dashboard=(d / "dashboard.html").exists(),
    )
    return s


def start_runner(pid, d, iterations, model=None, skip_permissions=False):
    with procs_lock:
        p = procs.get(pid)
        if p is not None and p.poll() is None:
            return False
        cmd = [sys.executable, "-u", str(HERE / "run_loop.py"), "--project", str(d), "-n", str(iterations)]
        if model:
            cmd += ["--model", model]
        if skip_permissions:
            cmd += ["--skip-permissions"]
        out = open(d / "runner.out", "a", encoding="utf-8")  # crashes/tracebacks land here
        # own process group/session so Ctrl+C on the server doesn't kill running loops
        kw = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
              else {"start_new_session": True})
        procs[pid] = subprocess.Popen(cmd, cwd=HERE, stdout=out, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, **kw)
        return True


def stop_runner(pid, d):
    with procs_lock:
        p = procs.pop(pid, None)
    state = read_json(d / "runner_state.json", {}) or {}
    target = p.pid if p is not None else state.get("pid")
    if target:
        kill_tree(target)  # also kills the agent session it spawned
    msg = f"stopped from the UI (killed runner pid {target})"
    with open(d / "runner.log", "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    with open(d / "activity.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "session": state.get("current"),
                            "kind": "runner", "text": msg}) + "\n")
    if state:
        state["status"] = "stopped"
        state["current"] = None
        (d / "runner_state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")


def new_project_id(goal, name=None):
    base = re.sub(r"[^a-z0-9]+", "-", (name or " ".join(goal.split()[:5])).lower()).strip("-")[:40] or "project"
    pid, n = base, 2
    while (PROJECTS / pid).exists():
        pid, n = f"{base}-{n}", n + 1
    return pid


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self.send(200, UI.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/projects":
            PROJECTS.mkdir(exist_ok=True)
            items = [summary(d.name, d) for d in sorted(PROJECTS.iterdir()) if project_dir(d.name)]
            items.sort(key=lambda s: (s["status"] != "running", s["id"]))
            return self.send(200, items)
        m = re.fullmatch(r"/api/projects/([^/]+)", path)
        if m:
            d = project_dir(m.group(1))
            return self.send(200, detail(m.group(1), d)) if d else self.send(404, {"error": "not found"})
        m = re.fullmatch(r"/p/([^/]+)/(.+)", path)  # files in a project, e.g. the agent's dashboard
        if m:
            d = project_dir(m.group(1))
            f = (d / m.group(2)).resolve() if d else None
            if f and f.is_file() and f.is_relative_to(d.resolve()) and ".git" not in f.parts:
                ctype = mimetypes.guess_type(f.name)[0] or "text/plain"
                return self.send(200, f.read_bytes(), ctype)
        self.send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        b = self.body()
        if path == "/api/projects":
            goal = (b.get("goal") or "").strip()
            if len(goal) < 10:
                return self.send(400, {"error": "Describe the goal in at least a sentence."})
            pid = new_project_id(goal, b.get("name"))
            d = PROJECTS / pid
            create_project(d, goal, int(b.get("budget") or 120))
            start_runner(pid, d, int(b.get("iterations") or 10), b.get("model") or None,
                         bool(b.get("skip_permissions")))
            return self.send(201, {"id": pid})
        m = re.fullmatch(r"/api/projects/([^/]+)/(start|stop|program)", path)
        d = project_dir(m.group(1)) if m else None
        if not d:
            return self.send(404, {"error": "not found"})
        pid, action = m.groups()
        if action == "start":
            ok = start_runner(pid, d, int(b.get("iterations") or 10), b.get("model") or None,
                              bool(b.get("skip_permissions")))
            return self.send(200 if ok else 409, {"ok": ok})
        if action == "stop":
            stop_runner(pid, d)
            return self.send(200, {"ok": True})
        if action == "program":  # human edits to program.md, committed so reverts keep them
            if status_of(pid, d)[0] == "running":
                return self.send(409, {"error": "Stop the run before editing program.md."})
            (d / "program.md").write_text(b.get("text", ""), encoding="utf-8")
            if (d / ".git").exists():
                subprocess.run(["git", "commit", "-q", "-m", "Edit program.md (human)", "program.md"],
                               cwd=d, capture_output=True)
            return self.send(200, {"ok": True})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    PROJECTS.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"loop agent UI: {url}  (Ctrl+C to quit; running loops keep going)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
