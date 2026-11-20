from dotenv import load_dotenv

load_dotenv()

import asyncio
import difflib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from google import genai
from google.genai import types

import tools
from agent import (
    ALL_TOOLS,
    MAX_HISTORY_TURNS,
    MODELS,
    TOOL_CONTEXT_KWARGS,
    _build_client,
    _load_project_memory,
    _save_trace,
    _system_instruction,
    _trim_history,
    call_gemini,
)
from tools import (
    COMMAND_DENYLIST,
    WORKSPACE_DIR,
    _safe_path,
    edit_file,
    grep_search,
    list_files,
    read_file,
    run_command,
    todo_write,
    undo_last_checkpoint,
    write_file,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AIVAR Autonomous Agent Workbench", version="2.5")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class WebSessionState:
    def __init__(self):
        self.history: list[types.Content] = []
        self.plan: list[str] = []
        self.plan_completed: list[str] = []
        self.total_tokens: int = 0
        self.steps_used: int = 0
        self.active_model: str = "gemini-2.5-flash"
        self.pending_approval: Optional[dict] = None
        self.is_running: bool = False
        self.recent_calls: list[str] = []

    def reset(self):
        self.history.clear()
        self.plan.clear()
        self.plan_completed.clear()
        self.total_tokens = 0
        self.steps_used = 0
        self.active_model = "gemini-2.5-flash"
        self.is_running = False
        self.recent_calls.clear()
        if self.pending_approval:
            event = self.pending_approval.get("event")
            if event:
                event.set()
            future = self.pending_approval.get("future")
            if future and not future.done():
                future.set_result(False)
        self.pending_approval = None
        tools._CURRENT_PLAN = []


session = WebSessionState()


def get_workspace_tree() -> list[dict]:
    """Scans WORKSPACE_DIR and returns structured file and directory metadata."""
    entries = []
    for root, dirs, files in os.walk(WORKSPACE_DIR):
        dirs[:] = sorted(d for d in dirs if d not in {"__pycache__", ".git", ".pytest_cache"})
        rel_root = Path(root).relative_to(WORKSPACE_DIR)
        for d in sorted(dirs):
            rel_d = (rel_root / d).as_posix()
            entries.append({"path": rel_d, "type": "directory", "name": d})
        for f in sorted(files):
            rel_f = (rel_root / f).as_posix()
            full_path = Path(root) / f
            size = full_path.stat().st_size if full_path.exists() else 0
            ext = Path(f).suffix.lower()
            entries.append({"path": rel_f, "type": "file", "name": f, "size": size, "ext": ext})
    return entries


def get_checkpoints_list() -> list[dict]:
    """Inspects workspace/.git commits to list checkpoints."""
    git_dir = WORKSPACE_DIR / ".git"
    if not git_dir.exists():
        return []

    proc = tools._git("log", "--pretty=format:%h|%s|%cr", "-n", "30")
    if proc.returncode != 0 or not proc.stdout.strip():
        return []

    checkpoints = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            h, msg, rel_time = parts
            checkpoints.append({
                "hash": h,
                "message": msg,
                "time": rel_time,
            })
        elif len(parts) == 2:
            h, msg = parts
            checkpoints.append({
                "hash": h,
                "message": msg,
                "time": "recently",
            })
    return checkpoints


class ChatRequest(BaseModel):
    prompt: str
    auto_approve: bool = False
    max_steps: int = 15


class ApproveRequest(BaseModel):
    approved: bool
    approval_id: Optional[str] = None


@app.get("/")
def serve_index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="static/index.html not found.")
    return FileResponse(str(index_path))


@app.get("/api/workspace")
def get_workspace():
    """Lists current files inside workspace/ via list_files and structured tree."""
    raw = list_files(".")
    tree = get_workspace_tree()
    return {"files": tree, "raw": raw}


@app.get("/api/checkpoints")
def get_checkpoints():
    """Returns list of recent Git checkpoints in workspace/.git."""
    return {"checkpoints": get_checkpoints_list()}


@app.get("/api/file")
def get_file_content(path: str = Query(..., description="Workspace relative file path")):
    """Returns file content safely using read_file and _safe_path."""
    try:
        target = _safe_path(path)
        if not target.exists() or not target.is_file():
            return JSONResponse({"error": f"File '{path}' not found.", "path": path}, status_code=404)
        content = read_file(path)
        if content.startswith("Error"):
            return JSONResponse({"error": content, "path": path}, status_code=400)
        return {"path": path, "content": content}
    except ValueError as e:
        return JSONResponse({"error": str(e), "path": path}, status_code=403)
    except Exception as e:
        return JSONResponse({"error": f"Error reading file '{path}': {e}", "path": path}, status_code=500)


@app.post("/api/approve")
def handle_approval(req: ApproveRequest):
    """Handles [y/n] human approval before executing shell commands."""
    if not session.pending_approval:
        return JSONResponse({"status": "error", "message": "No pending command awaiting approval."}, status_code=400)

    if req.approval_id and session.pending_approval.get("id") != req.approval_id:
        return JSONResponse({"status": "error", "message": "Approval ID mismatch."}, status_code=400)

    # Resolve threading event for background thread
    data = session.pending_approval.get("data")
    if data is not None:
        data["approved"] = req.approved
    event = session.pending_approval.get("event")
    if event:
        event.set()

    # Resolve asyncio future if present (e.g. in test suites)
    future = session.pending_approval.get("future")
    if future and not future.done():
        future.set_result(req.approved)

    session.pending_approval = None
    return {"status": "ok", "approved": req.approved}


@app.post("/api/undo")
def handle_undo():
    """Calls undo_last_checkpoint() and returns rollback message + refreshed workspace tree and checkpoints."""
    msg = undo_last_checkpoint()
    tree = get_workspace_tree()
    checkpoints = get_checkpoints_list()
    return {"message": msg, "files": tree, "checkpoints": checkpoints}


@app.post("/api/reset")
def handle_reset():
    """Clears active web session history, telemetry, and pending approvals."""
    session.reset()
    tree = get_workspace_tree()
    checkpoints = get_checkpoints_list()
    return {"status": "ok", "message": "Session and execution state cleanly reset.", "files": tree, "checkpoints": checkpoints}


def parse_diff(raw_diff: str) -> list[dict]:
    """Parses unified diff into structured lines with type annotations."""
    lines = []
    for line in raw_diff.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            lines.append({"type": "header", "text": line})
        elif line.startswith("@@"):
            lines.append({"type": "range", "text": line})
        elif line.startswith("+"):
            lines.append({"type": "add", "text": line})
        elif line.startswith("-"):
            lines.append({"type": "del", "text": line})
        else:
            lines.append({"type": "context", "text": line})
    return lines


def run_agent(
    user_prompt: str,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    auto_approve: bool = False,
    max_steps: int = 15,
) -> dict:
    """Executes the autonomous agent loop in a background thread and pushes SSE events via asyncio.Queue."""
    def emit(event: dict):
        line = f"data: {json.dumps(event)}\n\n"
        loop.call_soon_threadsafe(queue.put_nowait, line)

    session.is_running = True
    active_tools_map = dict(ALL_TOOLS)
    active_tools_list = list(active_tools_map.values())

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        session.is_running = False
        error_msg = (
            "GEMINI_API_KEY is missing from environment. Please create a .env file "
            "in the project root with GEMINI_API_KEY=your_gemini_api_key and restart."
        )
        emit({"type": "error", "message": error_msg, "title": "GEMINI_API_KEY Missing", "code": "MISSING_KEY"})
        loop.call_soon_threadsafe(queue.put_nowait, None)
        return {"success": False, "error": error_msg}

    try:
        client = _build_client()
    except Exception as e:
        session.is_running = False
        error_msg = f"Failed to initialize Gemini client: {e}. Please check your GEMINI_API_KEY in .env."
        emit({"type": "error", "message": error_msg, "title": "Client Initialization Error", "code": "INIT_ERROR"})
        loop.call_soon_threadsafe(queue.put_nowait, None)
        return {"success": False, "error": error_msg}

    start_time = time.perf_counter()
    session.history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)]))
    trace_events: list[dict] = []

    emit({"type": "run_started", "prompt": user_prompt, "max_steps": max_steps})

    try:
        for step in range(1, max_steps + 1):
            session.steps_used = step
            # Yield step_start immediately as step begins
            emit({"type": "step_start", "step": step, "max_steps": max_steps})

            try:
                response = call_gemini(client, session.history, active_tools_list)
            except Exception as e:
                err_msg = f"Gemini API failure at step {step}: {e}"
                emit({"type": "error", "step": step, "message": err_msg, "title": "Gemini API Error", "code": "API_ERROR"})
                break

            if response.usage_metadata and response.usage_metadata.total_token_count:
                session.total_tokens += response.usage_metadata.total_token_count

            emit({"type": "telemetry", "steps": step, "max_steps": max_steps, "tokens": session.total_tokens, "model": session.active_model})

            if not response.candidates or not response.candidates[0].content or not response.candidates[0].content.parts:
                blocked_msg = "Agent stopped: Received an empty or safety-blocked model response."
                session.history.append(types.Content(role="model", parts=[types.Part.from_text(text=blocked_msg)]))
                emit({"type": "assistant_done", "step": step, "text": blocked_msg, "success": False})
                emit({"type": "assistant_message", "step": step, "text": blocked_msg, "success": False})
                break

            assistant_content = response.candidates[0].content
            session.history.append(assistant_content)

            function_calls = [p.function_call for p in assistant_content.parts if p.function_call]

            # If no function calls: final assistant text ready!
            if not function_calls:
                final_text = response.text or "(Task completed with no text summary.)"
                elapsed = time.perf_counter() - start_time
                payload = {
                    "type": "assistant_done",
                    "step": step,
                    "text": final_text,
                    "steps": step,
                    "tokens": session.total_tokens,
                    "elapsed": round(elapsed, 2),
                    "success": True,
                }
                # Yield assistant_done immediately
                emit(payload)
                emit({**payload, "type": "assistant_message"})
                _save_trace(user_prompt, trace_events, success=True, steps=step, tokens=session.total_tokens, elapsed=elapsed)
                return {"success": True, "steps": step, "tokens": session.total_tokens, "output": final_text}

            response_parts = []
            for call in function_calls:
                fn_name = call.name
                fn_args = dict(call.args) if call.args else {}
                call_sig = f"{fn_name}:{json.dumps(fn_args, sort_keys=True)}"
                session.recent_calls.append(call_sig)

                # Yield tool_call immediately
                emit({"type": "tool_call", "step": step, "tool": fn_name, "args": fn_args})

                result = ""
                raw_diff = ""
                diff_lines = []
                is_security_block = False
                security_reason = ""

                # Special handling for run_command: denylist check and HITL approval
                if fn_name == "run_command":
                    cmd = fn_args.get("command", "")
                    # Denylist evaluation
                    for blocked in COMMAND_DENYLIST:
                        if re.search(blocked, cmd, flags=re.IGNORECASE):
                            is_security_block = True
                            security_reason = f"Security Error: Command blocked by denylist policy (matched `{blocked}`)."
                            result = security_reason
                            break

                    if not is_security_block:
                        if not auto_approve:
                            approval_id = str(uuid.uuid4())
                            approval_event = threading.Event()
                            approval_data = {"approved": False}
                            session.pending_approval = {
                                "id": approval_id,
                                "command": cmd,
                                "step": step,
                                "event": approval_event,
                                "data": approval_data,
                            }
                            # Yield approval_required immediately
                            emit({"type": "approval_required", "step": step, "tool": fn_name, "command": cmd, "approval_id": approval_id})
                            
                            # Block thread safely until approval event is set
                            approval_event.wait(timeout=300)
                            approved = approval_data["approved"]
                            if approved:
                                result = run_command(cmd, auto_approve=True)
                            else:
                                result = "Command cancelled by user."
                        else:
                            result = run_command(cmd, auto_approve=True)

                elif fn_name == "todo_write":
                    tasks = fn_args.get("tasks", [])
                    completed = fn_args.get("completed", [])
                    result = todo_write(tasks=tasks, completed=completed, quiet=True)
                    session.plan = list(tools._CURRENT_PLAN)
                    session.plan_completed = list(completed or [])
                    emit({"type": "plan_update", "tasks": session.plan, "completed": session.plan_completed})
                    emit({"type": "plan", "tasks": session.plan, "completed": session.plan_completed})

                elif fn_name == "edit_file":
                    result = edit_file(**fn_args)
                    if "Diff:\n" in result:
                        raw_diff = result.split("Diff:\n", 1)[1]
                        diff_lines = parse_diff(raw_diff)
                        # Yield diff_ready immediately
                        emit({"type": "diff_ready", "step": step, "file_path": fn_args.get("file_path", ""), "diff": raw_diff, "diff_lines": diff_lines})
                    if "Access denied" in result:
                        is_security_block = True
                        security_reason = result

                elif fn_name in active_tools_map:
                    try:
                        extra_kwargs = TOOL_CONTEXT_KWARGS.get(fn_name, lambda *_: {})(auto_approve, True)
                        func = active_tools_map[fn_name]
                        result = func(**fn_args, **extra_kwargs)
                    except TypeError as e:
                        result = f"Error: Invalid arguments for '{fn_name}': {e}"
                    except Exception as e:
                        result = f"Error executing '{fn_name}': {e}"

                    if isinstance(result, str) and ("Access denied" in result or "Security Error" in result):
                        is_security_block = True
                        security_reason = result
                else:
                    result = f"Error: Tool '{fn_name}' is not available."

                # Loop prevention reminder
                if len(session.recent_calls) >= 3 and len(set(session.recent_calls[-3:])) == 1:
                    result += "\n\n[SYSTEM WARNING]: You have repeated this exact tool call 3 times. Stop repeating it and try a different approach."

                if is_security_block:
                    emit({"type": "security_block", "step": step, "tool": fn_name, "reason": security_reason, "args": fn_args})

                trace_events.append({"step": step, "tool": fn_name, "args": fn_args, "result_preview": result[:300]})

                # Emit tool result event
                emit({"type": "tool_result", "step": step, "tool": fn_name, "args": fn_args, "result": result, "diff_lines": diff_lines, "is_security_block": is_security_block})

                # Notify frontend of file and workspace mutation
                if fn_name in {"write_file", "edit_file", "run_command"}:
                    tree = get_workspace_tree()
                    checkpoints = get_checkpoints_list()
                    fpath = fn_args.get("file_path", "")
                    emit({"type": "file_updated", "file_path": fpath, "tool": fn_name, "files": tree, "checkpoints": checkpoints})
                    emit({"type": "workspace_updated", "files": tree, "checkpoints": checkpoints})

                response_parts.append(
                    types.Part.from_function_response(name=fn_name, response={"result": result})
                )

            session.history.append(types.Content(role="user", parts=response_parts))

        else:
            # Reached max_steps loop exhaustion
            elapsed = time.perf_counter() - start_time
            timeout_msg = f"Stopped after reaching max_steps={max_steps}."
            session.history.append(types.Content(role="model", parts=[types.Part.from_text(text=timeout_msg)]))
            payload = {
                "type": "assistant_done",
                "step": max_steps,
                "text": timeout_msg,
                "steps": max_steps,
                "tokens": session.total_tokens,
                "elapsed": round(elapsed, 2),
                "success": False,
            }
            emit(payload)
            emit({**payload, "type": "assistant_message"})
            _save_trace(user_prompt, trace_events, success=False, steps=max_steps, tokens=session.total_tokens, elapsed=elapsed)
            return {"success": False, "steps": max_steps, "output": timeout_msg}

    finally:
        session.is_running = False
        emit({"type": "done"})
        loop.call_soon_threadsafe(queue.put_nowait, None)


async def agent_event_generator(
    user_prompt: str,
    auto_approve: bool = False,
    max_steps: int = 15,
) -> AsyncGenerator[str, None]:
    """Helper async generator that runs agent in background thread and yields SSE lines + heartbeats."""
    if session.is_running:
        yield f"data: {json.dumps({'type': 'error', 'message': 'An agent run is currently in progress.'})}\n\n"
        return

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    thread = threading.Thread(
        target=run_agent,
        args=(user_prompt, queue, loop),
        kwargs={"auto_approve": auto_approve, "max_steps": max_steps},
        daemon=True,
    )
    thread.start()

    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=1.0)
            if item is None:
                break
            yield item
        except asyncio.TimeoutError:
            # 1s heartbeat comment so the browser never buffers SSE
            yield ": ping\n\n"


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    """Runs agent in a background thread and streams SSE events via asyncio.Queue with 1s heartbeats."""
    if session.is_running:
        return JSONResponse({"error": "An agent run is currently in progress."}, status_code=409)

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    thread = threading.Thread(
        target=run_agent,
        args=(req.prompt, queue, loop),
        kwargs={"auto_approve": req.auto_approve, "max_steps": req.max_steps},
        daemon=True,
    )
    thread.start()

    async def sse_stream():
        try:
            while True:
                try:
                    # Wait up to 1.0 second for the next event from the background thread
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                    if item is None:
                        break
                    yield item
                except asyncio.TimeoutError:
                    # 1s heartbeat comment so the browser never buffers SSE
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            if session.pending_approval:
                event = session.pending_approval.get("event")
                if event:
                    event.set()
                session.pending_approval = None
            raise

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream; charset=utf-8",
            "X-Accel-Buffering": "no",
        },
    )


# Mount static assets directory
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web_server:app", host="127.0.0.1", port=8000, reload=False)
