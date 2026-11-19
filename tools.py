import os
import re
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = (BASE_DIR / "workspace").resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

MAX_OUTPUT_CHARS = 8000

COMMAND_DENYLIST = [
    r"\.\.",
    r"\.env",
    r"rm\s+-rf",
    r"\bsudo\b",
    r"\bcurl\b|\bwget\b|\bInvoke-WebRequest\b",
]


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Truncates oversized outputs to protect the LLM context window."""
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - limit
    return f"{text[:half]}\n\n... [{omitted} characters truncated] ...\n\n{text[-half:]}"


def _safe_path(file_path: str) -> Path:
    """Resolves a path inside WORKSPACE_DIR and blocks traversal & symlinks."""
    if not file_path or not file_path.strip():
        raise ValueError("File path cannot be empty.")

    raw_target = WORKSPACE_DIR / file_path
    for parent in [raw_target, *raw_target.parents]:
        if parent == WORKSPACE_DIR:
            break
        if parent.is_symlink():
            raise ValueError(f"Access denied: Symlinks are not allowed ({parent.name}).")

    resolved_target = raw_target.resolve()
    if not resolved_target.is_relative_to(WORKSPACE_DIR):
        raise ValueError(f"Access denied: '{file_path}' escapes the workspace sandbox.")
    return resolved_target


def list_files(directory: str = ".") -> str:
    """Recursively lists files and folders (marked with '/') inside the workspace."""
    try:
        target = _safe_path(directory)
        if not target.exists():
            return f"Error: Directory '{directory}' does not exist."
        if not target.is_dir():
            return f"Error: '{directory}' is a file, not a directory."

        entries = []
        for root, dirs, files in os.walk(target):
            dirs[:] = sorted(d for d in dirs if d not in {"__pycache__", ".git", ".pytest_cache"})
            rel_root = Path(root).relative_to(WORKSPACE_DIR)
            for d in dirs:
                rel_d = (rel_root / d).as_posix()
                entries.append(f"{rel_d}/")
            for f in sorted(files):
                rel_f = (rel_root / f).as_posix()
                entries.append(rel_f)

        return _truncate("\n".join(entries)) if entries else "The workspace folder is currently empty."
    except Exception as e:
        return f"Error listing files: {e}"


def read_file(file_path: str) -> str:
    """Reads a UTF-8 text file inside the workspace safely, rejecting binary files."""
    try:
        target = _safe_path(file_path)
        if not target.exists() or not target.is_file():
            return f"Error: File '{file_path}' not found."

        raw_bytes = target.read_bytes()
        if b"\x00" in raw_bytes[:1024]:
            return f"Error: '{file_path}' appears to be a binary file and cannot be read as text."

        text = raw_bytes.decode("utf-8")
        return _truncate(text)
    except UnicodeDecodeError:
        return f"Error: '{file_path}' is not valid UTF-8 text."
    except Exception as e:
        return f"Error reading file '{file_path}': {e}"


def write_file(file_path: str, content: str) -> str:
    """Creates or overwrites a file inside the workspace with the given content."""
    try:
        target = _safe_path(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} chars to '{file_path}'."
    except Exception as e:
        return f"Error writing file '{file_path}': {e}"


def edit_file(file_path: str, old_str: str, new_str: str) -> str:
    """Replaces an exact unique substring in an existing workspace file."""
    try:
        target = _safe_path(file_path)
        if not target.exists() or not target.is_file():
            return f"Error: File '{file_path}' not found."

        content = target.read_text(encoding="utf-8")
        occurrences = content.count(old_str)
        if occurrences == 0:
            return f"Error: `old_str` not found in '{file_path}'. Read the file first to verify exact text."
        if occurrences > 1:
            return f"Error: `old_str` matched {occurrences} times in '{file_path}'. Provide more surrounding context so the match is unique."

        updated = content.replace(old_str, new_str, 1)
        target.write_text(updated, encoding="utf-8")
        return f"Successfully edited '{file_path}' (1 replacement made)."
    except Exception as e:
        return f"Error editing file '{file_path}': {e}"


def grep_search(pattern: str, directory: str = ".") -> str:
    """Searches for a regex or text pattern across text files in the workspace."""
    try:
        target = _safe_path(directory)
        regex = re.compile(pattern)
        matches = []

        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git", ".pytest_cache"}]
            for fname in sorted(files):
                fpath = Path(root) / fname
                try:
                    lines = fpath.read_text(encoding="utf-8").splitlines()
                except Exception:
                    continue
                rel_path = fpath.relative_to(WORKSPACE_DIR).as_posix()
                for line_no, line in enumerate(lines, start=1):
                    if regex.search(line):
                        matches.append(f"{rel_path}:{line_no}: {line.strip()}")
                        if len(matches) >= 100:
                            break
            if len(matches) >= 100:
                break

        return _truncate("\n".join(matches)) if matches else f"No matches found for '{pattern}'."
    except re.error as e:
        return f"Invalid regex pattern: {e}"
    except Exception as e:
        return f"Error searching files: {e}"


def run_command(command: str, auto_approve: bool = False) -> str:
    """Runs a shell command inside WORKSPACE_DIR with denylist filtering and HITL approval."""
    for blocked in COMMAND_DENYLIST:
        if re.search(blocked, command, flags=re.IGNORECASE):
            return f"Security Error: Command blocked by denylist policy (matched `{blocked}`)."

    if not auto_approve:
        print(f"\n⚠️  [HIGH RISK] Agent wants to run command: {command}")
        approval = input("Allow execution? [y/n]: ").strip().lower()
        if approval != "y":
            return "Command cancelled by user."

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(WORKSPACE_DIR),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = f"Exit Code: {result.returncode}\n"
        if result.stdout:
            output += f"STDOUT:\n{result.stdout}\n"
        if result.stderr:
            output += f"STDERR:\n{result.stderr}\n"
        return _truncate(output)
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 60 seconds."
    except Exception as e:
        return f"Error running command: {e}"
