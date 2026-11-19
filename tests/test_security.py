import unittest
from tools import edit_file, grep_search, read_file, run_command, write_file


class TestAgentSecurityAndTools(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
