from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from memory_forest.cli import main
from memory_forest.core import initialize_forest
from memory_forest.errors import MemoryForestError
from memory_forest.promotion import promote_memory


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve() / "forest"
        initialize_forest(self.root, example=True)
        self.source = "04 stm/research-notes/observatory-trial/new-finding.md"
        path = self.root / self.source
        path.write_text(
            "# New fictional finding\n\n"
            "Parent: [[../../../03 mtm/research-notes/observatory-trial.md]]\n\n"
            "A synthetic observation for promotion tests.\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o600)

    def tearDown(self):
        self.temporary.cleanup()

    def test_adjacent_promotion_appends_summary_and_refreshes_index(self):
        result = promote_memory(self.root, self.source, to_layer="mtm")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "promoted")
        self.assertFalse(result["already_promoted"])
        target = self.root / "03 mtm/research-notes/observatory-trial.md"
        body = target.read_text(encoding="utf-8")
        self.assertIn(f"[[{self.source}|New fictional finding]]", body)
        self.assertIn("Source SHA-256:", body)
        self.assertEqual((self.root / self.source).read_text(encoding="utf-8").count("synthetic"), 1)
        with sqlite3.connect(self.root / ".memory-forest/index.sqlite3") as connection:
            indexed = connection.execute(
                "SELECT COUNT(*) FROM documents WHERE path = ?", (self.source,)
            ).fetchone()[0]
        self.assertEqual(indexed, 1)

    def test_non_adjacent_promotion_requires_allow_skip(self):
        with self.assertRaises(MemoryForestError) as captured:
            promote_memory(self.root, self.source, to_layer="ltm")
        self.assertEqual(captured.exception.code, "non_adjacent_promotion")
        result = promote_memory(self.root, self.source, to_layer="ltm", allow_skip=True)
        self.assertEqual([step["target"]["layer"]["name"] for step in result["steps"]], ["mtm", "ltm"])

    def test_duplicate_is_reported_without_appending(self):
        promote_memory(self.root, self.source, to_layer="mtm")
        target = self.root / "03 mtm/research-notes/observatory-trial.md"
        before = target.read_bytes()
        result = promote_memory(self.root, self.source, to_layer="mtm")
        self.assertTrue(result["already_promoted"])
        self.assertEqual(result["status"], "already promoted")
        self.assertEqual(target.read_bytes(), before)

    def test_missing_canonical_target_is_created_from_layout(self):
        target = self.root / "03 mtm/research-notes/observatory-trial.md"
        target.unlink()
        result = promote_memory(self.root, self.source, to_layer="mtm")
        self.assertTrue(result["steps"][0]["created"])
        self.assertIn(
            "[[02 ltm/research-notes_LTM.md]]",
            target.read_text(encoding="utf-8"),
        )
        self.assertTrue(result["audit"]["ok"])

    def test_source_outside_root_is_rejected(self):
        outside = self.root.parent / "outside.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        with self.assertRaises(MemoryForestError) as captured:
            promote_memory(self.root, outside, to_layer="mtm")
        self.assertEqual(captured.exception.code, "path_escape")

    def test_cli_promote_returns_json(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["promote", str(self.root), self.source, "--to", "mtm"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["operation"], "promote")


if __name__ == "__main__":
    unittest.main()
