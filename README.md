# AIVAR Autonomous AI Coding Agent

A terminal-based autonomous software engineering agent built in Python using the Google GenAI SDK (`gemini-3.5-flash-lite` / `gemini-3.8-flash` failover), featuring sandboxed file editing, unified diffs, automatic workspace Git checkpoints with `/undo`, human-in-the-loop shell approvals, and an automated multi-trial + ablation evaluation suite.

## Key Features
* **Custom ReAct Loop (`agent.py`):** Explicit control over step budgets (`max_steps=15`), boundary-safe history trimming (`_trim_history`), 3x stuck-loop detection, 2-pass model failover with 30s HTTP timeout, and JSONL telemetry (`traces/runs.jsonl`).
* **Sandboxed Workspace & Isolated Git Checkpoints (`tools.py`):** All file operations are locked inside `workspace/` via `Path.resolve().is_relative_to()` with symlink and `.git` metadata blocking. Every `write_file` and `edit_file` saves isolated `before`/`after` Git snapshots (`GIT_DIR=workspace/.git`, `GIT_WORK_TREE=workspace`), returns a unified diff on edits, and supports instant step-by-step CLI rollback via `/undo`.
* **Human-in-the-Loop Command Execution:** Shell commands run inside `workspace/` only after passing `COMMAND_DENYLIST` and explicit `[y/n]` user confirmation.
* **Project Memory & Live Planning:** Reads optional `workspace/AGENT.md` rules and renders live task checklists via `todo_write`.

## Quick Start
```bash
pip install -r requirements.txt
cp .env.example .env   # Add your GEMINI_API_KEY
python agent.py        # Interactive CLI (supports /undo and exit)
```

## Verification & Benchmarks
```bash
python -m unittest tests/test_security.py   # 8 unit & security regression tests
python evals/run_evals.py --runs 3          # 6 tasks x 3 trials (pass@3) + ablation
```

See [`REPORT.md`](./REPORT.md) for full architecture diagrams, threat model, 18-trial `pass@3` results, and `Full Toolset` vs. `Write-Only` ablation analysis.
