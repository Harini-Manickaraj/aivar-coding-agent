# Technical Project Report: Personal AI Coding Agent

**Submitted by:** Harini Manickaraj  
**Program:** AIVAR Innovations Capstone — Project 1  
**Date:** November 2026  

---

## 1. Objective & Problem Statement

Standard conversational LLM interfaces act as passive code generators: the user must manually copy snippets, create files, run terminal commands, and paste error tracebacks back into the chat window. The objective of Project 1 was to bridge that gap by engineering an **autonomous, tool-using AI coding agent** from the ground up—capable of planning, writing code, executing tests, and self-correcting inside a local development environment.

---

## 2. System Architecture

The agent follows a modular two-layer architecture separating **orchestration/reasoning (`agent.py`)** from **local environment execution (`tools.py`)**:

```mermaid
flowchart TD
    User([User Task Prompt]) --> Memory[(Conversation State<br/>messages list)]
    Memory --> LLM[Gemini API + Fallback Router<br/>AFC Disabled]
    LLM -->|Final Text Response| Done([Rich Terminal Output])
    LLM -->|Structured Function Call| Router{Tool Dispatcher<br/>TOOL_MAP}

    Router -->|list_files / read_file / write_file| Sandbox[Path Sandbox Check<br/>workspace/ boundary]
    Sandbox --> FS[(Local Workspace Files)]

    Router -->|run_command| HITL{Human Approval<br/>Allow execution? y/n}
    HITL -->|n| Cancelled[Return Cancelled Status]
    HITL -->|y| Subproc[Subprocess Execution<br/>Capture STDOUT & STDERR]

    FS --> Result[Function Response Part]
    Cancelled --> Result
    Subproc --> Result
    Result --> Memory