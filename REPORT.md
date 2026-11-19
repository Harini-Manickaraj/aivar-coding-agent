# Technical Project Report: AIVAR Autonomous AI Coding Agent

## 1. Problem Statement
Standard chat-based LLM interfaces generate static code snippets that require manual copy-pasting, file creation, execution, and error pasting by the developer. The objective of Project 1 is to build an autonomous, terminal-based coding agent capable of inspecting a project workspace, planning multi-step edits, executing verification tests under human supervision, and iteratively self-debugging from `STDERR` tracebacks.

## 2. System Architecture & Design Decisions

```mermaid
flowchart TD
    U[User Prompt] --> H[Conversation State and Sliding Window]
    H --> M[Gemini API and Model Failover]
    M --> D{Function Call?}
    D -- Yes --> R[Tool Router and Stuck-Loop Guard]
    R --> T1[Sandboxed File Tools: list, read, write, edit, grep]
    R --> T2[Planning Tool: todo_write]
    R --> T3[HITL Shell Runner: Denylist and y/n Prompt]
    T1 & T2 & T3 --> O[Truncated Observation]
    O --> H
    D -- No --> F[Final Verified Response and JSONL Trace]
```

### Why a Manual ReAct Loop Instead of Automatic Function Calling (AFC)?
While `google-genai` offers Automatic Function Calling, we explicitly disabled it (`AutomaticFunctionCallingConfig(disable=True)` in `agent.py`) and engineered our own `while` loop for four production reasons:
1. **Human-in-the-Loop (HITL) Interception:** Shell commands must pause for explicit `[y/n]` user confirmation before execution.
2. **Bounded Step Budget & Valid Role Alternation:** Enforces `max_steps=15` and appends a closing `model` message upon timeout so subsequent user turns never violate Gemini's strict `user -> model` turn structure.
3. **Stuck-Loop & Context Overflow Protection:** Tracks the last 3 tool signatures to inject a recovery warning if the model repeats an identical call, and trims history to `MAX_HISTORY_TURNS=40` on safe user text boundaries (never orphaning a `function_response` from its `function_call`) while truncating oversized outputs (`MAX_OUTPUT_CHARS=8000`).
4. **Telemetry & Model Failover:** Captures per-step token counts, latency, and JSONL traces (`traces/runs.jsonl`), while separating transient `429`/`503` overload errors (failing over across backup Flash models) from fatal `401`/`400` errors.

### Tool Design & Temperature Choice (`0.2`)
* **`edit_file` vs `write_file`:** Providing surgical substring replacement (`edit_file`) that fails on `0` or `>1` matches prevents full-file accidental overwrites on existing modules, while `grep_search` enables cross-file root-cause tracing.
* **Low Temperature (`0.2`):** Code generation, JSON tool arguments, and exact `old_str` matching require deterministic token selection rather than creative variance.

## 3. Safety Threat Model & Security Hardening
During security auditing of v1, three concrete vulnerabilities were identified, reproduced, and remediated:

| Vulnerability | Exploit Vector in v1 | Remediation in v2 | Verification |
| :--- | :--- | :--- | :--- |
| **Sibling-Prefix Path Traversal** | `_safe_path` used `str.startswith(WORKSPACE_DIR)`, allowing `../workspace_evil/x.txt` to pass prefix matching. | Replaced with `Path.resolve().is_relative_to(WORKSPACE_DIR)` plus explicit symlink blocking and `Path(__file__)` anchoring. | Verified in `tests/test_security.py` (`test_sibling_prefix_traversal_blocked`). |
| **Unrestricted Shell Command Arguments** | `subprocess.run(..., shell=True, cwd=WORKSPACE_DIR)` only sets the starting directory; commands like `cat ../.env` could read parent secrets. | Added regex `COMMAND_DENYLIST` (`..`, `.env`, `rm -rf`, `sudo`, `curl` / `wget`) as a fast pre-filter prior to the mandatory `[y/n]` HITL approval prompt. | Verified in `tests/test_security.py` and `T6_security_boundary`. |
| **Hanging Subprocesses & Binary Crashes** | Interactive commands hung indefinitely and binary files crashed UTF-8 decoding. | Added `stdin=subprocess.DEVNULL`, a 60s timeout, and null-byte binary detection in `read_file`. | Verified across automated benchmark runs. |

## 4. Automated Evaluation Results (`evals/run_evals.py`)
To evaluate reliability beyond single toy demos, an automated 6-task benchmark suite (`max_steps=15`) was built covering bug-fixing, greenfield generation, multi-file refactoring, stateful JSON persistence, Data Science CSV cleaning, and security boundary enforcement:

| Task ID | Benchmark Task | Result | Steps | Tokens | Time (s) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `T1_bugfix_math` | Fix Off-By-One & Zero Division Bug | PASS | 11 | 13,839 | 17.92 |
| `T2_from_scratch_string` | Create Slugify Utility + Tests | PASS | 11 | 20,342 | 229.08 |
| `T3_multifile_refactor` | Cross-File Function Rename & Bug Fix | PASS | 12 | 15,419 | 37.80 |
| `T4_json_persistence` | Stateful Task Store with JSON | PASS | 10 | 12,765 | 185.24 |
| `T5_data_cleaning` | CSV Data Cleaning & Summary (Data Science) | PASS | 9 | 10,958 | 49.93 |
| `T6_security_boundary` | Sandbox Traversal Refusal Check | PASS | 5 | 4,250 | 11.44 |
| **TOTAL / AVG** | **Pass Rate: 6/6 (100%)** | **6/6** | **9.7** | **12,929** | **88.57s** |

*Note: All 6 tasks finished cleanly within the 15-step budget (`5` to `12` steps). During `T2` and `T4`, primary Flash endpoints returned transient `429`/`503` responses, and the failover router automatically switched to backup Flash models and completed both tasks without manual intervention.*

## 5. Challenges & Engineering Learnings
* **History Window Boundary Integrity:** Slicing conversation history purely by list index can orphan a `function_response` from its preceding `function_call`, triggering a `400 INVALID_ARGUMENT` API error on long sessions. Advancing the slice pointer in `_trim_history` to the next user text boundary (verified via unit test) eliminates this failure mode.
* **Encoding & Platform Pitfalls:** PowerShell's `>` redirection initially wrote `requirements.txt` in UTF-16LE, breaking cross-platform `pip install -r`. Re-encoding to UTF-8 and pinning only direct dependencies (`google-genai`, `python-dotenv`, `rich`) resolved environment reproducibility.
* **Self-Debugging Feedback Loop:** Feeding full `STDOUT` and `STDERR` tracebacks back into the conversation state transforms a stateless LLM into an iterative engineer capable of catching edge cases (empty-list zero division in `T1` and negative/null rows in `T5`).

## 6. Known Limitations & Future Roadmap (Project 2)
* **OS-Level Isolation vs Regex Denylist:** `COMMAND_DENYLIST` is a lightweight speed bump against accidental or prompt-injected traversal—it can be bypassed by shell quoting or absolute paths outside `workspace/`. The mandatory human `[y/n]` approval gate is the primary control in v2, and Project 2 will execute commands inside an ephemeral Docker container for true OS-level isolation.
* **Git Checkpoints & Multi-Agent SDLC:** Future iterations will auto-commit workspace snapshots before edits (`/undo` support) and split planning, coding, and QA review into dedicated collaborating agents.
