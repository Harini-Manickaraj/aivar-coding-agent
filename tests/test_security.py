import unittest
from unittest.mock import MagicMock, patch
from google.genai import types

from agent import _trim_history, run_agent
from tools import edit_file, read_file, run_command, write_file


class TestAgentSecurityAndLoop(unittest.TestCase):
    def test_sibling_prefix_traversal_blocked(self):
        # Regression test for the old `startswith` bypass vulnerability
        res = write_file("../workspace_evil/exploit.txt", "malicious")
        self.assertIn("Access denied", res)

    def test_parent_env_read_blocked(self):
        res = read_file("../.env")
        self.assertIn("Access denied", res)

    def test_command_denylist_blocks_env_and_traversal(self):
        res = run_command("cat ../.env", auto_approve=True)
        self.assertIn("Security Error", res)

    def test_edit_file_requires_unique_match(self):
        write_file("sample.txt", "foo\nfoo\n")
        res = edit_file("sample.txt", "foo", "bar")
        self.assertIn("matched 2 times", res)

    def test_trim_history_never_orphans_function_response(self):
        msgs = [types.Content(role="user", parts=[types.Part.from_text(text="Start")])]
        for i in range(25):
            msgs.append(types.Content(role="model", parts=[types.Part.from_function_call(name="list_files", args={})]))
            msgs.append(types.Content(role="user", parts=[types.Part.from_function_response(name="list_files", response={"result": "ok"})]))
        trimmed = _trim_history(msgs)
        self.assertEqual(trimmed[0].role, "user")
        if len(trimmed) > 1:
            first_tail_parts = trimmed[1].parts or []
            self.assertFalse(any(getattr(p, "function_response", None) for p in first_tail_parts))

    @patch("agent._build_client")
    def test_run_agent_mocked_react_cycle(self, mock_build_client):
        # Simulates 1 tool call step followed by final text completion
        fn_part = types.Part.from_function_call(name="list_files", args={})
        resp1 = MagicMock()
        resp1.usage_metadata.total_token_count = 50
        resp1.candidates = [MagicMock(content=types.Content(role="model", parts=[fn_part]))]

        text_part = types.Part.from_text(text="Workspace inspected successfully.")
        resp2 = MagicMock()
        resp2.usage_metadata.total_token_count = 40
        resp2.text = "Workspace inspected successfully."
        resp2.candidates = [MagicMock(content=types.Content(role="model", parts=[text_part]))]

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [resp1, resp2]
        mock_build_client.return_value = mock_client

        result = run_agent("List files", max_steps=5, auto_approve=True, quiet=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["steps"], 2)
        self.assertEqual(result["tokens"], 90)


if __name__ == "__main__":
    unittest.main()
