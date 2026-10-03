# loop_agent

An autonomous engineering loop. You describe what to build. An agent builds a working
baseline, then improves it one experiment at a time, keeping only changes that improve the
metric.

```
python app.py        # opens http://127.0.0.1:8000
```

Type a goal, then click **Start building**. Each goal becomes a project in `projects/<name>/`
with its own git repo. The UI shows live agent activity, KPIs, the metric and test-pass
charts, every experiment, and an editor for that project's `program.md`.

| file | purpose |
|---|---|
| `program_template.md` | the default `program.md` every new project starts from. **Iterate on this.** |
| `run_loop.py` | runs one fresh `claude -p` session per experiment and enforces the rules |
| `app.py` + `ui.html` | the web UI (standard library only, no build step) |
| `projects/<name>/` | everything the agent wrote, plus `experiments.jsonl` and run state |

You can run a project without the UI too: `python run_loop.py --project projects/compressor -n 10`.

What the runner enforces regardless of what the agent does:
- **Append-only log.** If past entries are edited, the runner restores them. If a session adds anything other than one valid entry, the runner warns.
- **Keep or revert.** If a session leaves uncommitted changes, the runner resets to the last kept commit. This also happens on resume after **Stop**.
- **Stops on stalls.** After 3 sessions in a row with no log entry, the runner exits.

By default the agent can only edit files and run `python`/`git` plus a few read-only shell
commands. Only use **Skip permission checks** in a sandbox.

## Ideas for program_template.md
- The runner re-runs `evaluate.py` itself and rejects entries whose metric doesn't match (anti-gaming).
- A held-out benchmark the agent never sees, to catch overfitting.
- Tell the agent to read the `notes` from recent discarded runs and avoid repeating them.
- Split each session into separate planner, coder, and reviewer agents.
