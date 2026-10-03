# loop_agent

An autonomous engineering loop. You write `program.md`, and the agent writes everything else.

```
python run_loop.py              # Phase 1 baseline (if none yet) + 10 Phase 2 experiments
python run_loop.py -n 50        # more experiments; re-running resumes from the log
```

Open http://127.0.0.1:8000/dashboard.html while it runs (after Phase 1 creates it).

| file | written by | purpose |
|---|---|---|
| `program.md` | you | goal, metric, rules, log format. **The thing you iterate on.** |
| `run_loop.py` | you | runs one fresh `claude -p` session per experiment and enforces the rules |
| `experiments.jsonl` | agent | append-only log, git-ignored so reverts can't touch it |
| `dashboard.html`, code, tests, `evaluate.py` | agent | everything else |
| `sessions/*.txt` | runner | full transcript of each agent session |

What the runner enforces regardless of what the agent does:
- **Append-only log.** If past entries are edited, the runner restores them. If a session adds anything other than one valid entry, the runner warns.
- **Keep or revert.** If a session leaves uncommitted changes, the runner resets to the last kept commit.
- **Stops on stalls.** After 3 sessions in a row with no log entry (`--max-failures`), the runner exits.

By default the agent can only edit files and run `python`/`git` plus a few read-only shell commands. If it needs more, use `--skip-permissions`, but only in a sandbox.

## Iterating on program.md

To change the goal, rewrite the **Goal** and **Metric** sections and delete `experiments.jsonl` and the generated code. Ideas to try next:
- The runner re-runs `evaluate.py` itself and rejects entries whose metric doesn't match (anti-gaming).
- A held-out corpus the agent never sees, to catch overfitting.
- Tell the agent to read the `notes` from recent discarded runs and avoid repeating them.
- Split each session into separate planner, coder, and reviewer agents.
