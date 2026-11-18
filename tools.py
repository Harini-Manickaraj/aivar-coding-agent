import os
import subprocess

# Create a safe sandbox folder so the agent never touches your main system files
WORKSPACE_DIR = os.path.abspath("workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)


def _safe_path(file_path: str) -> str:
    """Ensures the agent stays strictly inside the workspace folder."""
    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, file_path))
    if not full_path.startswith(WORKSPACE_DIR):
        raise ValueError("Access denied: Agent can only work inside the workspace folder.")
    return full_path


def list_files(directory: str = ".") -> str:
    """Lists all files and folders inside the workspace directory."""
    try:
        target = _safe_path(directory)
        items = os.listdir(target)
        return "\n".join(items) if items else "The workspace folder is currently empty."
    except Exception as e:
        return f"Error listing files: {e}"


def read_file(file_path: str) -> str:
    """Reads and returns the text content of a file inside the workspace."""
    try:
        target = _safe_path(file_path)
        with open(target, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file '{file_path}': {e}"


def write_file(file_path: str, content: str) -> str:
    """Creates or overwrites a file inside the workspace with the given content."""
    try:
        target = _safe_path(file_path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to '{file_path}'."
    except Exception as e:
        return f"Error writing file '{file_path}': {e}"


def run_command(command: str) -> str:
    """Runs a terminal command inside the workspace after asking the user for permission."""
    print(f"\n⚠️  Agent wants to run command: {command}")
    approval = input("Allow execution? [y/n]: ").strip().lower()
    if approval != "y":
        return "Command cancelled by user."

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKSPACE_DIR,
            capture_output=True,
            text=True,
            timeout=30
        )
        output = f"Exit Code: {result.returncode}\n"
        if result.stdout:
            output += f"STDOUT:\n{result.stdout}\n"
        if result.stderr:
            output += f"STDERR:\n{result.stderr}\n"
        return output
    except Exception as e:
        return f"Error running command: {e}"