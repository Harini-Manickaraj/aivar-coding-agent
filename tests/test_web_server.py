import os
import shutil
import stat
import unittest

from tools import WORKSPACE_DIR, edit_file, write_file
from web_server import (
    ApproveRequest,
    get_file_content,
    get_workspace,
    get_workspace_tree,
    get_checkpoints,
    handle_approval,
    handle_reset,
    handle_undo,
    parse_diff,
    run_agent,
    serve_index,
    session,
)


class MockFuture:
    def __init__(self):
        self._done = False
        self._result = None

    def done(self):
        return self._done

    def set_result(self, val):
        self._done = True
        self._result = val

    def result(self):
        return self._result


class TestWebServer(unittest.TestCase):
    def setUp(self):
        def _rm_readonly(func, path, _):
            os.chmod(path, stat.S_IWRITE)
            func(path)

        for item in WORKSPACE_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item, onerror=_rm_readonly)
            else:
                item.unlink()
        session.reset()

    def test_serve_index(self):
        res = serve_index()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(os.path.exists(res.path))
        with open(res.path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("AIVAR WORKSTATION", content)
        self.assertIn("01 // WORKSPACE", content)
        self.assertIn("02 // AGENT PLAN", content)
        self.assertIn("03 // TELEMETRY", content)

    def test_get_workspace_empty_and_populated(self):
        data = get_workspace()
        self.assertIn("files", data)
        self.assertEqual(len(data["files"]), 0)

        write_file("module.py", "x = 42\n")
        data2 = get_workspace()
        paths = [f["path"] for f in data2["files"]]
        self.assertIn("module.py", paths)

    def test_get_file_valid_and_traversal(self):
        write_file("note.txt", "hello world")
        res = get_file_content("note.txt")
        self.assertEqual(res["content"], "hello world")

        # Path traversal refusal check
        res_traversal = get_file_content("../.env")
        self.assertEqual(res_traversal.status_code, 403)
        body = res_traversal.body.decode("utf-8")
        self.assertIn("Access denied", body)

    def test_undo_endpoint(self):
        write_file("undo_test.txt", "v1\n")
        edit_file("undo_test.txt", "v1", "v2")

        res = handle_undo()
        self.assertIn("Rolled back", res["message"])

    def test_reset_endpoint(self):
        session.steps_used = 5
        session.total_tokens = 1000
        res = handle_reset()
        self.assertEqual(res["status"], "ok")
        self.assertEqual(session.steps_used, 0)
        self.assertEqual(session.total_tokens, 0)

    def test_approve_endpoint_no_pending(self):
        res = handle_approval(ApproveRequest(approved=True))
        self.assertEqual(res.status_code, 400)

    def test_approve_endpoint_with_pending(self):
        future = MockFuture()
        session.pending_approval = {
            "id": "test-id",
            "command": "pytest",
            "future": future,
            "step": 1,
        }
        res = handle_approval(ApproveRequest(approved=True, approval_id="test-id"))
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["approved"])
        self.assertTrue(future.done())
        self.assertTrue(future.result())

    def test_get_checkpoints(self):
        data = get_checkpoints()
        self.assertIn("checkpoints", data)
        self.assertIsInstance(data["checkpoints"], list)

    def test_run_agent_queue_and_heartbeat(self):
        import asyncio
        import json
        import threading
        from unittest.mock import MagicMock, patch
        from google.genai import types

        write_file("discount.py", "def calc(price):\n    return price\n")

        # Mock responses:
        # Step 1: edit_file (triggers diff_ready)
        # Step 2: run_command (triggers approval_required)
        # Step 3: final text (triggers assistant_done)
        call_edit = types.FunctionCall(name="edit_file", args={"file_path": "discount.py", "old_str": "return price", "new_str": "return price * 0.9"})
        resp1 = MagicMock()
        resp1.usage_metadata.total_token_count = 50
        resp1.candidates = [MagicMock(content=types.Content(role="model", parts=[types.Part.from_function_call(name=call_edit.name, args=dict(call_edit.args))]))]
        resp1.text = None

        call_cmd = types.FunctionCall(name="run_command", args={"command": "python -c 1"})
        resp2 = MagicMock()
        resp2.usage_metadata.total_token_count = 50
        resp2.candidates = [MagicMock(content=types.Content(role="model", parts=[types.Part.from_function_call(name=call_cmd.name, args=dict(call_cmd.args))]))]
        resp2.text = None

        resp3 = MagicMock()
        resp3.usage_metadata.total_token_count = 50
        resp3.candidates = [MagicMock(content=types.Content(role="model", parts=[types.Part.from_text(text="Done discount logic.")]))]
        resp3.text = "Done discount logic."

        events_received = []
        import queue
        thread_queue = queue.Queue()

        class MockLoop:
            def call_soon_threadsafe(self, fn, arg):
                fn(arg)

        mock_loop = MockLoop()
        mock_queue = MagicMock()
        mock_queue.put_nowait.side_effect = lambda item: thread_queue.put(item)

        with (
            patch("web_server._build_client"),
            patch("web_server.call_gemini", side_effect=[resp1, resp2, resp3]),
            patch("web_server.run_command", return_value="Exit Code: 0\n"),
            patch("tools.checkpoint_workspace"),
            patch("web_server.get_checkpoints_list", return_value=[]),
        ):
            t = threading.Thread(
                target=run_agent,
                args=("Add discount", mock_queue, mock_loop),
                kwargs={"auto_approve": False, "max_steps": 5},
                daemon=True,
            )
            t.start()

            while True:
                item = thread_queue.get(timeout=3.0)
                if item is None:
                    break
                if item.startswith("data: "):
                    data = json.loads(item[6:].strip())
                    events_received.append(data)
                    if data.get("type") == "approval_required":
                        session.pending_approval["data"]["approved"] = True
                        session.pending_approval["event"].set()
            t.join(timeout=3.0)

        types_list = [e.get("type") for e in events_received]
        self.assertIn("step_start", types_list)
        self.assertIn("tool_call", types_list)
        self.assertIn("diff_ready", types_list)
        self.assertIn("approval_required", types_list)
        self.assertIn("assistant_done", types_list)

    def test_parse_diff(self):
        diff_text = "--- a/test.py\n+++ b/test.py\n@@ -1,1 +1,1 @@\n-old\n+new\n context"
        lines = parse_diff(diff_text)
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[0]["type"], "header")
        self.assertEqual(lines[1]["type"], "header")
        self.assertEqual(lines[2]["type"], "range")
        self.assertEqual(lines[3]["type"], "del")
        self.assertEqual(lines[4]["type"], "add")
        self.assertEqual(lines[5]["type"], "context")


if __name__ == "__main__":
    unittest.main()
