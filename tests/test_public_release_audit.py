from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_public_release.py"
SPEC = importlib.util.spec_from_file_location("audit_public_release", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublicReleaseAuditTests(unittest.TestCase):
    def test_clean_synthetic_tree_passes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "README.md").write_text("# Synthetic Forest\n", encoding="utf-8")
            self.assertTrue(MODULE.audit(root)["ok"])

    def test_private_home_and_secret_fail(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "bad.txt").write_text(
                "private path " + "/" + "Users" + "/person/memory\n"
                "credential " + "sk-" + "abcdefghijklmnopqrstuv\n",
                encoding="utf-8",
            )
            result = MODULE.audit(root)
            self.assertFalse(result["ok"])
            self.assertEqual(
                {item["rule"] for item in result["findings"]},
                {"absolute_macos_home", "openai_key"},
            )

    def test_symlink_fails_without_reading_target(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as outside:
            root = Path(raw)
            target = Path(outside) / "private-memory.txt"
            target.write_text("private body\n", encoding="utf-8")
            (root / "linked.txt").symlink_to(target)
            result = MODULE.audit(root)
            self.assertFalse(result["ok"])
            self.assertEqual(
                result["findings"],
                [{"rule": "symlink", "path": "linked.txt", "line": 0}],
            )

    def test_oversize_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "large.txt").write_bytes(b"x" * (MODULE.MAX_FILE_BYTES + 1))
            result = MODULE.audit(root)
            self.assertFalse(result["ok"])
            self.assertEqual(
                result["findings"],
                [{"rule": "oversize_file", "path": "large.txt", "line": 0}],
            )

    def test_private_runtime_directory_fails_without_scanning_it(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            runtime = root / ".memory-forest"
            runtime.mkdir()
            (runtime / "index.sqlite3").write_bytes(b"private derived data")
            result = MODULE.audit(root)
            self.assertFalse(result["ok"])
            self.assertEqual(
                result["findings"],
                [
                    {
                        "rule": "private_runtime_data",
                        "path": ".memory-forest",
                        "line": 0,
                    }
                ],
            )

    def test_nested_private_runtime_directory_is_not_a_skip_bypass(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            runtime = root / "docs" / ".memory-forest"
            runtime.mkdir(parents=True)
            (runtime / "index.sqlite3").write_bytes(b"private derived data")
            result = MODULE.audit(root)
            self.assertFalse(result["ok"])
            self.assertEqual(
                result["findings"],
                [
                    {
                        "rule": "private_runtime_data",
                        "path": "docs/.memory-forest",
                        "line": 0,
                    }
                ],
            )

    def test_sensitive_identifier_in_filename_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            sensitive_name = "person" + "@" + "example.com.md"
            (root / sensitive_name).write_text("# Synthetic\n", encoding="utf-8")
            result = MODULE.audit(root)
            self.assertFalse(result["ok"])
            self.assertEqual(
                result["findings"],
                [
                    {
                        "rule": "email_address",
                        "path": sensitive_name,
                        "line": 0,
                    }
                ],
            )

    def test_skip_names_in_root_ancestors_do_not_disable_the_audit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "build" / "repository"
            root.mkdir(parents=True)
            (root / "bad.txt").write_text(
                "credential " + "sk-" + "abcdefghijklmnopqrstuv\n",
                encoding="utf-8",
            )
            result = MODULE.audit(root)
            self.assertFalse(result["ok"])
            self.assertEqual(
                result["findings"],
                [{"rule": "openai_key", "path": "bad.txt", "line": 1}],
            )


if __name__ == "__main__":
    unittest.main()
