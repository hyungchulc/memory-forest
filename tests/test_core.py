from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_forest.cli import main
from memory_forest.core import audit_forest, initialize_forest, validate_forest
from memory_forest.errors import MemoryForestError
from memory_forest.index import index_forest, route_index, search_index
from memory_forest.model import LAYER_DIRECTORY_NAMES, parse_layer, parse_relative_route
from memory_forest.safety import ForestLimits, scan_forest


class MemoryForestCoreTests(unittest.TestCase):
    temporary: tempfile.TemporaryDirectory[str]
    parent: Path
    root: Path

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.parent = Path(self.temporary.name).resolve()
        self.root = self.parent / "forest"
        initialize_forest(self.root, example=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_example_initializes_all_layers_with_private_modes(self):
        layer_names = {
            path.name
            for path in self.root.iterdir()
            if path.is_dir() and path.name != ".memory-forest"
        }
        self.assertEqual(layer_names, set(LAYER_DIRECTORY_NAMES))
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        for path in self.root.rglob("*"):
            if path.is_dir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            elif path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_example_validates_and_audits(self):
        validation = validate_forest(self.root)
        audit = audit_forest(self.root)
        self.assertTrue(validation["ok"], validation)
        self.assertTrue(audit["ok"], audit)
        self.assertEqual(validation["summary"]["documents"], 7)
        self.assertGreaterEqual(audit["summary"]["links"], 6)

    def test_index_route_and_explicit_body_boundary(self):
        indexed = index_forest(self.root)
        self.assertEqual(indexed["documents"], 7)
        routed = route_index(self.root, "instrument calibration")
        self.assertGreaterEqual(routed["count"], 1)
        self.assertNotIn("root", routed)
        self.assertTrue(all("body" not in item for item in routed["results"]))
        hidden = search_index(self.root, "reference lamp")
        exposed = search_index(self.root, "reference lamp", include_body=True)
        self.assertTrue(all("body" not in item for item in hidden["results"]))
        self.assertTrue(any("body" in item for item in exposed["results"]))

    def test_query_refuses_a_stale_index_until_rebuilt(self):
        index_forest(self.root)
        leaf = (
            self.root
            / "04 stm"
            / "research-notes"
            / "observatory-trial"
            / "instrument-calibration.md"
        )
        leaf.write_text(
            leaf.read_text(encoding="utf-8").replace(
                "reference lamp", "canonical prism"
            ),
            encoding="utf-8",
        )
        os.chmod(leaf, 0o600)
        with self.assertRaises(MemoryForestError) as captured:
            search_index(self.root, "reference lamp", include_body=True)
        self.assertEqual(captured.exception.code, "index_stale")
        index_forest(self.root)
        refreshed = search_index(self.root, "canonical prism", include_body=True)
        self.assertGreaterEqual(refreshed["count"], 1)
        self.assertTrue(
            any("canonical prism" in item["body"] for item in refreshed["results"])
        )

    def test_init_refuses_every_existing_target(self):
        with self.assertRaises(MemoryForestError) as captured:
            initialize_forest(self.root)
        self.assertEqual(captured.exception.code, "root_exists")
        empty = self.parent / "empty"
        empty.mkdir()
        with self.assertRaises(MemoryForestError) as second:
            initialize_forest(empty)
        self.assertEqual(second.exception.code, "root_exists")

    def test_init_requires_an_existing_direct_parent(self):
        nested = self.parent / "missing" / "forest"
        with self.assertRaises(MemoryForestError) as captured:
            initialize_forest(nested)
        self.assertEqual(captured.exception.code, "missing_parent")

    def test_cli_errors_remain_single_json_objects(self):
        nested = self.parent / "missing" / "forest"
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exit_code = main(["--json", "init", str(nested)])
        self.assertNotEqual(exit_code, 0)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["error"]["code"], "missing_parent")

    def test_cli_redacts_unexpected_filesystem_failures(self):
        stream = io.StringIO()
        with patch(
            "memory_forest.cli.initialize_forest",
            side_effect=PermissionError("private local path"),
        ), contextlib.redirect_stdout(stream):
            exit_code = main(["--json", "init", str(self.parent / "new")])
        self.assertNotEqual(exit_code, 0)
        output = stream.getvalue()
        payload = json.loads(output)
        self.assertEqual(payload["error"]["code"], "filesystem_error")
        self.assertNotIn("private local path", output)

    def test_missing_parent_is_a_validation_error(self):
        (self.root / "02 ltm" / "research-notes_LTM.md").unlink()
        result = validate_forest(self.root)
        self.assertFalse(result["ok"])
        self.assertIn(
            "missing_parent",
            {issue["code"] for issue in result["issues"]},
        )

    def test_xltm_root_map_is_required(self):
        (self.root / "01 xltm" / "XLTM.md").unlink()
        result = validate_forest(self.root)
        self.assertFalse(result["ok"])
        self.assertIn(
            "missing_root_map",
            {issue["code"] for issue in result["issues"]},
        )

    def test_canonical_paths_and_links_are_case_sensitive(self):
        ltm = self.root / "02 ltm" / "research-notes_LTM.md"
        intermediate = self.root / "02 ltm" / "rename.tmp"
        renamed = self.root / "02 ltm" / "Research-Notes_LTM.md"
        ltm.rename(intermediate)
        intermediate.rename(renamed)
        result = audit_forest(self.root)
        self.assertFalse(result["ok"])
        codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("missing_parent", codes)
        self.assertIn("missing_wikilink", codes)

    def test_canonical_extensions_require_exact_lowercase(self):
        daily = self.root / "05 daily" / "2042-04-12.md"
        intermediate = self.root / "05 daily" / "rename.tmp"
        renamed = self.root / "05 daily" / "2042-04-12.MD"
        daily.rename(intermediate)
        intermediate.rename(renamed)
        result = validate_forest(self.root)
        self.assertFalse(result["ok"])
        self.assertIn(
            "unsupported_file_type",
            {issue["code"] for issue in result["issues"]},
        )

    def test_audit_requires_structured_parent_wikilinks(self):
        for path in self.root.rglob("*.md"):
            body = "\n".join(
                line for line in path.read_text(encoding="utf-8").splitlines()
                if "[[" not in line
            )
            path.write_text(body + "\n", encoding="utf-8")
            os.chmod(path, 0o600)
        result = audit_forest(self.root)
        self.assertFalse(result["ok"])
        self.assertIn(
            "missing_parent_wikilink",
            {issue["code"] for issue in result["issues"]},
        )

    def test_bare_and_same_layer_wikilinks_are_rejected(self):
        ltm = self.root / "02 ltm" / "research-notes_LTM.md"
        ltm.write_text(ltm.read_text(encoding="utf-8") + "\n[[XLTM]]\n", encoding="utf-8")
        os.chmod(ltm, 0o600)
        daily_next = self.root / "05 daily" / "2042-04-13.md"
        daily_next.write_text("# Next day\n", encoding="utf-8")
        os.chmod(daily_next, 0o600)
        daily = self.root / "05 daily" / "2042-04-12.md"
        daily.write_text(
            daily.read_text(encoding="utf-8")
            + "\n[[../05 daily/2042-04-13.md]]\n",
            encoding="utf-8",
        )
        os.chmod(daily, 0o600)
        result = audit_forest(self.root)
        codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("bare_wikilink", codes)
        self.assertIn("same_layer_wikilink", codes)

    def test_istm_requires_json_objects(self):
        events = self.root / "06 istm" / "events.jsonl"
        events.write_text("[]\n", encoding="utf-8")
        os.chmod(events, 0o600)
        result = validate_forest(self.root)
        self.assertFalse(result["ok"])
        self.assertIn(
            "invalid_jsonl_event",
            {issue["code"] for issue in result["issues"]},
        )

    def test_istm_rejects_nonfinite_and_unbounded_numbers(self):
        events = self.root / "06 istm" / "events.jsonl"
        for payload in (
            '{"value":NaN}\n',
            '{"value":' + ("9" * 257) + "}\n",
            '{"value":1e999999}\n',
        ):
            with self.subTest(payload_length=len(payload)):
                events.write_text(payload, encoding="utf-8")
                os.chmod(events, 0o600)
                result = validate_forest(self.root)
                self.assertFalse(result["ok"])
                self.assertIn(
                    "invalid_jsonl",
                    {issue["code"] for issue in result["issues"]},
                )

    def test_symlinks_fail_closed(self):
        target = self.parent / "outside.md"
        target.write_text("# Outside\n", encoding="utf-8")
        (self.root / "04 stm" / "linked.md").symlink_to(target)
        with self.assertRaises(MemoryForestError) as captured:
            validate_forest(self.root)
        self.assertEqual(captured.exception.code, "symlink_forbidden")

    def test_scan_caps_directory_count_and_depth(self):
        with self.assertRaises(MemoryForestError) as count_error:
            scan_forest(self.root, limits=ForestLimits(max_directories=1))
        self.assertEqual(count_error.exception.code, "directory_count_exceeded")
        with self.assertRaises(MemoryForestError) as depth_error:
            scan_forest(self.root, limits=ForestLimits(max_depth=1))
        self.assertEqual(depth_error.exception.code, "directory_depth_exceeded")

    def test_layer_aliases_parse_but_routes_require_canonical_names(self):
        self.assertEqual(parse_layer("00-life_archive").directory, "00 life_archive")
        with self.assertRaises(MemoryForestError) as captured:
            parse_relative_route("01-xltm/XLTM.md")
        self.assertEqual(captured.exception.code, "noncanonical_layer_directory")

    def test_cli_compact_json_is_deterministic(self):
        outputs: list[str] = []
        for _ in range(2):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                exit_code = main(["--json", "doctor", str(self.root)])
            self.assertEqual(exit_code, 0)
            outputs.append(stream.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        parsed = json.loads(outputs[0])
        self.assertTrue(parsed["checks"]["network_required"] is False)


if __name__ == "__main__":
    unittest.main()
