# AIVAR Personal AI Coding Agent

A terminal-based autonomous AI coding agent built from scratch in Python using the Google Gen AI SDK (`google-genai`). Designed around an explicit **ReAct (Observe → Reason → Act)** execution loop without heavy third-party agent frameworks, this agent can inspect project directories, read and write source code, execute shell commands, observe runtime tracebacks, and iteratively debug its own output.

## Key Features

- **Custom ReAct Execution Loop:** Explicit multi-step reasoning loop with persistent conversation state and manual tool dispatch.
- **Sandboxed File System Tools:** Path-validated `list_files`, `read_file`, and `write_file` operations restricted strictly to a `workspace/` directory.
- **Human-in-the-Loop (HITL) Execution:** Interactive `[y/n]` terminal confirmation before running any shell command via `run_command`.
- **Self-Healing Debugging:** Captures both `STDOUT` and `STDERR` from executed commands so the agent can detect test failures or missing tools and fix them automatically.
- **Resilient Model Failover:** Fast failover across multiple Gemini endpoints (`gemini-3.5-flash-lite`, `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.6-flash`) to handle transient `503` traffic spikes gracefully.
- **Bounded Autonomy:** Enforces a configurable `max_steps=15` circuit breaker to prevent runaway loops.

## Project Structure

```text
ai_agent/
├── agent.py            # Core ReAct loop, model failover, and Rich CLI interface
├── tools.py            # Sandboxed file operations and HITL shell execution tools
├── test_brain.py       # Standalone API connectivity and model availability check
├── requirements.txt    # Python dependencies
├── README.md           # Setup and usage instructions
└── REPORT.md           # Technical architecture report and engineering learnings