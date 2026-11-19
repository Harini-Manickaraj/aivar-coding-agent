import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from tools import (
    WORKSPACE_DIR,
    edit_file,
    grep_search,
    list_files,
    read_file,
    run_command,
    todo_write,
    undo_last_checkpoint,
    write_file,
)

load_dotenv()
console = Console()

BASE_DIR = Path(__file__).resolve().parent
TRACES_DIR = BASE_DIR / "traces"
TRACES_DIR.mkdir(parents=True, exist_ok=True)

MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
]

MAX_HISTORY_TURNS = 40


def _build_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. Check your .env file.")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
    )


ALL_TOOLS = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "grep_search": grep_search,
    "run_command": run_command,
    "todo_write": todo_write,
}

# Runtime context flags injected uniformly per tool name
TOOL_CONTEXT_KWARGS = {
    "run_command": lambda auto_approve, quiet: {"auto_approve": auto_approve},
    "todo_write": lambda auto_approve, quiet: {"quiet": quiet},
}


def _load_project_memory() -> str:
    """Loads optional workspace/AGENT.md instructions if present."""
    agent_md = WORKSPACE_DIR / "AGENT.md"
    if agent_md.exists() and agent_md.is_file():
        return f"\n\nProject-Specific Instructions (from AGENT.md):\n{agent_md.read_text(encoding='utf-8')[:3000]}"
    return ""


def _system_instruction(active_tool_names: set[str]) -> str:
    rules = [
        "You are an autonomous AI software engineering agent operating inside a sandboxed `workspace/` directory.",
        "Workflow rules:",
    ]
    if "todo_write" in active_tool_names:
        rules.append("- For multi-step tasks, call `todo_write` first to outline a brief plan.")
    rules.append("- Inspect existing files with `list_files`, `read_file`, or `grep_search` before modifying code.")
    if "edit_file" in active_tool_names:
        rules.append("- Prefer `edit_file` for targeted fixes on existing files and `write_file` for new files.")
    else:
        rules.append("- Use `write_file` to create or update files.")
    rules.append("- Always verify your work by running tests or executing the script via `run_command` before finishing.")
    rules.append("- If a command fails (non-zero exit code or STDERR), diagnose the error, fix the code, and re-run until it passes.")
    return "\n".join(rules) + _load_project_memory()


def _trim_history(messages: list[types.Content]) -> list[types.Content]:
    """Keeps initial user prompt plus recent turns, slicing only on safe user text boundaries."""
    if len(messages) <= MAX_HISTORY_TURNS:
        return messages
    tail = messages[-(MAX_HISTORY_TURNS - 1) :]
    while tail:
        first = tail[0]
        has_fn_resp = any(getattr(p, "function_response", None) is not None for p in (first.parts or []))
        if first.role == "user" and not has_fn_resp:
            break
        tail.pop(0)
    return [messages[0]] + tail


def call_gemini(client: genai.Client, messages: list[types.Content], active_tools: list):
    """Calls Gemini with explicit model failover, distinguishing transient vs fatal API errors."""
    last_error = None
    active_names = {fn.__name__ for fn in active_tools}
    config = types.GenerateContentConfig(
        system_instruction=_system_instruction(active_names),
        tools=active_tools,
        temperature=0.2,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    for model_name in MODELS:
        try:
            return client.models.generate_content(
                model=model_name,
                contents=_trim_history(messages),
                config=config,
            )
        except errors.APIError as e:
            last_error = e
            if e.code in (429, 500, 502, 503, 504):
                console.print(f"[yellow]Model {model_name} unavailable ({e.code}), switching to backup...[/yellow]")
                continue
            raise RuntimeError(f"Fatal Gemini API Error ({e.code}): {e.message}") from e
        except Exception as e:
            last_error = e
            console.print(f"[yellow]Transient network issue on {model_name}: {e}, switching...[/yellow]")
            continue

    raise RuntimeError(f"All fallback Gemini models failed. Last error: {last_error}")


def run_agent(
    user_prompt: str,
    messages: list[types.Content] | None = None,
    max_steps: int = 15,
    auto_approve: bool = False,
    quiet: bool = False,
    enabled_tools: list[str] | None = None,
) -> dict:
    """Runs the ReAct agent loop and returns structured metrics for CLI & evaluation."""
    if messages is None:
        messages = []

    active_map = (
        {name: ALL_TOOLS[name] for name in enabled_tools if name in ALL_TOOLS}
        if enabled_tools is not None
        else dict(ALL_TOOLS)
    )
    active_tool_list = list(active_map.values())

    client = _build_client()
    messages.append(types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)]))

    start_time = time.perf_counter()
    total_tokens = 0
    recent_calls: list[str] = []
    trace_events: list[dict] = []

    for step in range(1, max_steps + 1):
        if not quiet:
            console.print(f"\n[bold cyan]--- Step {step}/{max_steps}: Agent Thinking ---[/bold cyan]")

        response = call_gemini(client, messages, active_tool_list)

        if response.usage_metadata and response.usage_metadata.total_token_count:
            total_tokens += response.usage_metadata.total_token_count

        if not response.candidates or not response.candidates[0].content or not response.candidates[0].content.parts:
            msg = "Agent stopped: Received an empty or safety-blocked model response."
            messages.append(types.Content(role="model", parts=[types.Part.from_text(text=msg)]))
            return {"success": False, "steps": step, "tokens": total_tokens, "elapsed": time.perf_counter() - start_time, "output": msg}

        assistant_content = response.candidates[0].content
        messages.append(assistant_content)

        function_calls = [p.function_call for p in assistant_content.parts if p.function_call]

        if not function_calls:
            final_text = response.text or "(Task completed with no text summary.)"
            elapsed = time.perf_counter() - start_time
            if not quiet:
                console.print(Panel(Markdown(final_text), title="✅ Agent Finished", border_style="green"))
                console.print(f"[dim]Stats: {step} steps | {total_tokens} tokens | {elapsed:.2f}s[/dim]")
            _save_trace(user_prompt, trace_events, success=True, steps=step, tokens=total_tokens, elapsed=elapsed)
            return {"success": True, "steps": step, "tokens": total_tokens, "elapsed": elapsed, "output": final_text}

        response_parts = []
        for call in function_calls:
            fn_name = call.name
            fn_args = dict(call.args) if call.args else {}
            call_sig = f"{fn_name}:{json.dumps(fn_args, sort_keys=True)}"
            recent_calls.append(call_sig)

            if not quiet:
                console.print(f"[bold yellow]🔧 Tool Call:[/bold yellow] {fn_name}({fn_args})")

            if fn_name not in active_map:
                result = f"Error: Tool '{fn_name}' is not available."
            else:
                try:
                    extra_kwargs = TOOL_CONTEXT_KWARGS.get(fn_name, lambda *_: {})(auto_approve, quiet)
                    result = active_map[fn_name](**fn_args, **extra_kwargs)
                except TypeError as e:
                    result = f"Error: Invalid arguments for '{fn_name}': {e}"
                except Exception as e:
                    result = f"Error executing '{fn_name}': {e}"

            if len(recent_calls) >= 3 and len(set(recent_calls[-3:])) == 1:
                result += "\n\n[SYSTEM WARNING]: You have repeated this exact tool call 3 times. Stop repeating it and try a different approach."

            if not quiet:
                preview = result if len(result) <= 600 else result[:600] + "\n... [truncated in CLI]"
                console.print(Panel(preview, title=f"Result from {fn_name}", border_style="blue"))

            trace_events.append({"step": step, "tool": fn_name, "args": fn_args, "result_preview": result[:300]})
            response_parts.append(
                types.Part.from_function_response(name=fn_name, response={"result": result})
            )

        messages.append(types.Content(role="user", parts=response_parts))

    elapsed = time.perf_counter() - start_time
    timeout_msg = f"Stopped after reaching max_steps={max_steps}."
    messages.append(types.Content(role="model", parts=[types.Part.from_text(text=timeout_msg)]))
    if not quiet:
        console.print(f"[bold red]{timeout_msg}[/bold red]")
    _save_trace(user_prompt, trace_events, success=False, steps=max_steps, tokens=total_tokens, elapsed=elapsed)
    return {"success": False, "steps": max_steps, "tokens": total_tokens, "elapsed": elapsed, "output": timeout_msg}


def _save_trace(prompt: str, events: list[dict], success: bool, steps: int, tokens: int, elapsed: float) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt,
        "success": success,
        "steps": steps,
        "tokens": tokens,
        "elapsed_sec": round(elapsed, 2),
        "events": events,
    }
    trace_file = TRACES_DIR / "runs.jsonl"
    with trace_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    console.print(
        Panel(
            "[bold green]AIVAR Personal AI Coding Agent (v2 Hardened)[/bold green]\n"
            "Commands: [bold]/undo[/bold] to roll back last file edit | [bold]exit[/bold] to quit.",
            expand=False,
        )
    )
    conversation_history: list[types.Content] = []
    while True:
        try:
            task = input("\nYou > ").strip()
            if not task:
                continue
            if task.lower() in {"exit", "quit"}:
                break
            if task.lower() == "/undo":
                msg = undo_last_checkpoint()
                console.print(f"[bold magenta]↩ {msg}[/bold magenta]")
                continue
            run_agent(task, messages=conversation_history, auto_approve=False)
        except KeyboardInterrupt:
            console.print("\n[yellow]Session interrupted. Goodbye![/yellow]")
            break