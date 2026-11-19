# AIVAR Autonomous AI Coding Agent

A terminal-based autonomous software engineering agent built in Python using the Google GenAI SDK (`gemini-3.5-flash-lite` / `gemini-3.8-flash` failover), featuring sandboxed file editing, unified diffs, isolated workspace Git checkpoints with `/undo`, human-in-the-loop shell approvals, and an automated multi-trial + ablation evaluation suite.

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

* **Multi-Trial Reliability (`pass@3`):** 18/18 trials passed across 6 tasks (`T1`?`T6`), including an independent edge-case test suite for `T2`.
* **Ablation Findings:** Removing `todo_write` and `edit_file` on small 10-line files reduced median steps from `10?12` to `7?8` and cut token usage by ~54%, demonstrating that planning and unified diffs add overhead on tiny files and pay off on larger multi-hundred-line modules.

### Sample Execution Trace
```json
{
  "prompt": "Find why `test_invoice.py` fails, fix the bug in `pricing.py`, and verify tests pass.",
  "success": true,
  "steps": 8,
  "tokens": 6785,
  "first_two_events": [
    {
      "step": 1,
      "tool": "list_files",
      "args": {},
      "result_preview": "invoice.py\npricing.py\ntest_invoice.py"
    },
    {
      "step": 2,
      "tool": "read_file",
      "args": {
        "file_path": "test_invoice.py"
      },
      "result_preview": "import unittest\r\nfrom invoice import make_invoice\r\n\r\nclass TestInvoice(unittest.TestCase):\r\n    def test_invoice(self):\r\n        self.assertEqual(make_invoice('Book', 100), 'Book: 118.00')\r\n\r\nif __name__ == '__main__':\r\n    unittest.main()\r\n"
    }
  ]
}
```

See [`REPORT.md`](./REPORT.md) for full architecture diagrams, threat model, 18-trial `pass@3` results, `Full Toolset` vs. `Write-Only` ablation tables, and failure analysis.
