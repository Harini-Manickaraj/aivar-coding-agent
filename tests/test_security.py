import shutil
import unittest
from pathlib import Path

from agent import _trim_history
from google.genai import types
from tools import (
    WORKSPACE_DIR,
    edit_file,
    read_file,
    run_command,
    undo_last_checkpoint,
    write_file,
)


class TestSecurityAndCheckpoints(unittest.TestCase):
    def setUp(self):
        for item in WORKSPACE_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()

    def test_parent_traversal_blocked(self):
        res = read_file("../.env")
        self.assertIn("Access denied", res)

    def test_sibling_prefix_traversal_blocked(self):
        res = write_file("../workspace_evil/pwn.txt", "bad")
        self.assertIn("Access denied", res)

    def test_git_metadata_access_blocked(self):
        write_file("hello.txt", "v1")
        res = read_file(".git/config")
        self.assertIn("Access denied", res)

    def test_denylist_blocks_env_and_parent(self):
        self.assertIn("Security Error", run_command("cat ../.env", auto_approve=True))
        self.assertIn("Security Error", run_command("rm -rf /", auto_approve=True))

    def test_edit_file_returns_diff_and_undo_restores_previous_state(self):
        write_file("calc.py", "def add(a, b):\n    return a - b\n")
        edit_res = edit_file("calc.py", "return a - b", "return a + b")
        self.assertIn("Diff:", edit_res)
        self.assertIn("+    return a + b", edit_res)
        self.assertIn("return a + b", read_file("calc.py"))

        undo_res = undo_last_checkpoint()
        self.assertIn("Rolled back workspace", undo_res)
        self.assertIn("return a - b", read_file("calc.py"))

    def test_trim_history_preserves_user_boundary(self):
        msgs = [types.Content(role="user", parts=[types.Part.from_text(text="initial task")])]
        for i in range(25):
            msgs.append(
                types.Content(
                    role="model",
                    parts=[types.Part.from_function_call(name="list_files", args={})],
                )
            )
            msgs.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name="list_files", response={"result": f"ok {i}"})],
                )
            )
        msgs.append(types.Content(role="user", parts=[types.Part.from_text(text="follow up question")]))
        msgs.append(types.Content(role="model", parts=[types.Part.from_text(text="final answer")]))

        trimmed = _trim_history(msgs)
        self.assertEqual(trimmed[0].parts[0].text, "initial task")
        first_tail_has_fn_resp = any(
            getattr(p, "function_response", None) is not None for p in (trimmed[1].parts or [])
        )
        self.assertFalse(first_tail_has_fn_resp)


if __name__ == "__main__":
    unittest.main()