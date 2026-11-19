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