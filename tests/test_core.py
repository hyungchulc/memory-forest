from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_forest.cli import main
from memory_forest.core import (
    audit_forest,
    initialize_forest,
    load_forest_identity,
    validate_forest,
)
from memory_forest.errors import MemoryForestError
from memory_forest.index import index_forest, route_index, search_index
from memory_forest.model import (
    LAYER_DIRECTORY_NAMES,
    immediate_parent_path,
    parse_layer,
    parse_relative_route,
)
from memory_forest.retrieval import (
    _Node,
    _candidate_trails,
    decode_query_plan_json,
    read_query_plan_source,
    retrieve_index,
    validate_query_plan,
)
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
        self.assertEqual(validation["summary"]["documents"], 10)
        self.assertGreaterEqual(audit["summary"]["links"], 10)

    def test_index_route_and_explicit_body_boundary(self):
        indexed = index_forest(self.root)
        self.assertEqual(indexed["documents"], 10)
        routed = route_index(self.root, "instrument calibration")
        self.assertGreaterEqual(routed["count"], 1)
        self.assertEqual(
            set(routed),
            {
                "count",
                "include_body",
                "limit",
                "ok",
                "operation",
                "query",
                "results",
                "schema_version",
            },
        )
        self.assertEqual(
            set(routed["results"][0]),
            {"mtime_ns", "route", "score", "sha256", "size", "title"},
        )
        self.assertNotIn("root", routed)
        self.assertTrue(all("body" not in item for item in routed["results"]))
        hidden = search_index(self.root, "reference lamp")
        exposed = search_index(self.root, "reference lamp", include_body=True)
        self.assertTrue(all("body" not in item for item in hidden["results"]))
        self.assertTrue(any("body" in item for item in exposed["results"]))

    def test_retrieve_returns_a_fresh_root_first_trail_without_bodies(self):
        index_forest(self.root)
        result = retrieve_index(self.root, "telemetry replay")
        self.assertEqual(result["operation"], "retrieve")
        self.assertGreaterEqual(result["count"], 1)
        first = result["trails"][0]
        self.assertTrue(first["validated"])
        self.assertTrue(first["complete"])
        self.assertEqual(
            [item["route"]["layer"]["name"] for item in first["trail"]],
            ["xltm", "ltm", "mtm", "stm"],
        )
        self.assertEqual(
            {
                item["route"]["domain"]
                for item in first["trail"]
                if item["route"]["domain"] is not None
            },
            {"mission-operations"},
        )
        self.assertEqual(len(first["relationships"]), 3)
        self.assertTrue(
            all(
                relationship["type"] == "canonical_parent_child"
                for relationship in first["relationships"]
            )
        )
        self.assertEqual(
            first["score"]["method"],
            "original_query_tier_then_weighted_reciprocal_rank_sum",
        )
        self.assertTrue(first["score"]["higher_is_better_within_tier"])
        self.assertEqual(first["score"]["rank_tier"], "original_query_match")
        self.assertTrue(first["original_query_matched"])
        self.assertEqual(first["matched_query_plan_probe_count"], 0)
        self.assertEqual(
            result["query_plan"],
            {
                "accepted_probe_count": 0,
                "effective_probe_count": 0,
                "provided": False,
                "schema_version": 1,
            },
        )
        self.assertEqual(
            result["retrieval"]["method"],
            "deterministic_root_first_trails_v1",
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('"body"', serialized)
        self.assertNotIn(str(self.root), serialized)

    def test_root_first_ranking_is_deterministic_across_domains(self):
        index_forest(self.root)
        first = retrieve_index(self.root, "Parent", limit=10)
        second = retrieve_index(self.root, "Parent", limit=10)
        self.assertEqual(first, second)
        terminal_paths = [
            trail["trail"][-1]["route"]["path"] for trail in first["trails"]
        ]
        self.assertEqual(len(terminal_paths), len(set(terminal_paths)))
        self.assertTrue(
            any("mission-operations" in path for path in terminal_paths)
        )
        self.assertTrue(any("research-notes" in path for path in terminal_paths))

    def test_direct_match_is_seeded_before_broad_ancestor_expansion(self):
        def build_node(document_id: int, path: str) -> _Node:
            route = parse_relative_route(path)
            return _Node(
                document_id=document_id,
                parent_path=immediate_parent_path(route),
                route=route,
                title=route.leaf,
                sha256="0" * 64,
                size=0,
                mtime_ns=0,
            )

        paths = [
            "01 xltm/XLTM.md",
            "02 ltm/alpha_LTM.md",
            "03 mtm/alpha/branch-a.md",
            *(f"04 stm/alpha/branch-a/leaf-{index:02d}.md" for index in range(11)),
            "02 ltm/beta_LTM.md",
            "03 mtm/beta/branch-b.md",
            "04 stm/beta/branch-b/direct-match.md",
        ]
        nodes = {
            document_id: build_node(document_id, path)
            for document_id, path in enumerate(paths, start=1)
        }
        candidates = _candidate_trails(
            nodes,
            {2: 2.0, len(paths): 1.0},
            original_match_ids={2, len(paths)},
            limit=1,
            limits=ForestLimits(max_results=1),
        )
        terminal_paths = {chain[-1].route.path for chain in candidates}
        self.assertIn("04 stm/beta/branch-b/direct-match.md", terminal_paths)

    def test_xltm_only_match_returns_a_root_only_partial_trail(self):
        index_forest(self.root)
        result = retrieve_index(self.root, "Memory Forest")
        self.assertEqual(result["count"], 1)
        trail = result["trails"][0]
        self.assertFalse(trail["complete"])
        self.assertEqual(trail["depth"], 1)
        self.assertEqual(trail["trail"][0]["route"]["path"], "01 xltm/XLTM.md")

    def test_query_plan_bridges_languages_with_query_only_probes(self):
        index_forest(self.root)
        plan = {
            "schema_version": 1,
            "probes": [{"query": "mission recovery"}],
        }
        result = retrieve_index(self.root, "비상 복원", query_plan=plan)
        self.assertGreaterEqual(result["count"], 1)
        self.assertTrue(result["query_plan"]["provided"])
        self.assertEqual(result["query_plan"]["accepted_probe_count"], 1)
        self.assertEqual(result["query_plan"]["effective_probe_count"], 1)
        self.assertEqual(
            result["trails"][0]["trail"][-1]["route"]["path"],
            "04 stm/mission-operations/recovery-drill/telemetry-replay.md",
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("mission recovery", serialized)

    def test_query_plan_cannot_outrank_a_direct_original_query_match(self):
        index_forest(self.root)
        plan = {
            "schema_version": 1,
            "probes": [
                {"query": query}
                for query in (
                    "mission",
                    "recovery",
                    "telemetry",
                    "crew",
                    "buffered",
                    "fictional",
                    "replays",
                    "decision",
                )
            ],
        }
        result = retrieve_index(
            self.root,
            "instrument calibration",
            query_plan=plan,
        )
        first = result["trails"][0]
        self.assertEqual(
            first["trail"][-1]["route"]["path"],
            "04 stm/research-notes/observatory-trial/instrument-calibration.md",
        )
        self.assertTrue(first["original_query_matched"])
        self.assertEqual(first["score"]["rank_tier"], "original_query_match")
        self.assertTrue(
            any(
                trail["score"]["rank_tier"] == "query_plan_only"
                for trail in result["trails"][1:]
            )
        )

    def test_duplicate_original_probe_is_accepted_but_not_effective(self):
        index_forest(self.root)
        result = retrieve_index(
            self.root,
            "telemetry replay",
            query_plan={
                "schema_version": 1,
                "probes": [{"query": "TELEMETRY REPLAY"}],
            },
        )
        self.assertEqual(
            result["query_plan"],
            {
                "accepted_probe_count": 1,
                "effective_probe_count": 0,
                "provided": True,
                "schema_version": 1,
            },
        )
        self.assertEqual(
            result["trails"][0]["matched_query_plan_probe_count"],
            0,
        )

    def test_unicode_retrieval_covers_synthetic_multiscript_cues(self):
        index_forest(self.root)
        for query in (
            "mission recovery",
            "임무 복구",
            "mission 복구",
            "ミッション復旧",
            "استعادة المهمة",
            "résumé opérationnel",
        ):
            with self.subTest(query=query):
                result = retrieve_index(self.root, query)
                paths = {
                    trail["trail"][-1]["route"]["path"]
                    for trail in result["trails"]
                }
                self.assertIn(
                    "04 stm/mission-operations/recovery-drill/telemetry-replay.md",
                    paths,
                )

    def test_query_plan_rejects_non_query_fields_and_malformed_values(self):
        invalid_plans = (
            {"schema_version": 1.0, "probes": [{"query": "mission"}]},
            {
                "schema_version": 1,
                "probes": [{"query": "mission", "path": "/private"}],
            },
            {
                "schema_version": 1,
                "probes": [{"query": "mission", "body": "memory text"}],
            },
            {
                "schema_version": 1,
                "probes": [{"query": "mission", "credentials": "secret"}],
            },
            {"schema_version": 1, "probes": [{"query": " padded "}]},
            {"schema_version": 1, "probes": [{"query": "line\nbreak"}]},
            {"schema_version": 1, "probes": [{"query": "next\u0085line"}]},
            {"schema_version": 1, "probes": [{"query": "bad\ud800text"}]},
            {
                "schema_version": 1,
                "probes": [{"query": "cafe\u0301"}],
            },
            {
                "schema_version": 1,
                "probes": [{"query": "same"}, {"query": "SAME"}],
            },
        )
        for plan in invalid_plans:
            with self.subTest(plan=plan), self.assertRaises(MemoryForestError):
                validate_query_plan(plan, max_probes=8)
        with self.assertRaises(MemoryForestError) as duplicate:
            decode_query_plan_json(
                '{"schema_version":1,"schema_version":1,"probes":[]}'
            )
        self.assertEqual(duplicate.exception.code, "invalid_query_plan")
        escaped_surrogate = decode_query_plan_json(
            b'{"schema_version":1,"probes":[{"query":"bad\\ud800text"}]}'
        )
        with self.assertRaises(MemoryForestError):
            validate_query_plan(escaped_surrogate, max_probes=8)
        accepted_format_character = validate_query_plan(
            {
                "schema_version": 1,
                "probes": [{"query": "alpha\u200dbeta"}],
            },
            max_probes=8,
        )
        self.assertEqual(accepted_format_character.probes, ("alpha\u200dbeta",))

    def test_query_plan_source_rejects_symlinks_and_oversize_files(self):
        real_plan = self.parent / "real-plan.json"
        real_plan.write_text(
            '{"schema_version":1,"probes":[{"query":"mission"}]}',
            encoding="utf-8",
        )
        linked_plan = self.parent / "linked-plan.json"
        linked_plan.symlink_to(real_plan)
        with self.assertRaises(MemoryForestError) as linked:
            read_query_plan_source(str(linked_plan))
        self.assertEqual(linked.exception.code, "unsafe_query_plan_source")
        large_plan = self.parent / "large-plan.json"
        large_plan.write_bytes(b" " * (32 * 1024 + 1))
        with self.assertRaises(MemoryForestError) as large:
            read_query_plan_source(str(large_plan))
        self.assertEqual(large.exception.code, "query_plan_too_large")

    def test_query_plan_source_revalidates_the_open_descriptor(self):
        real_plan = self.parent / "real-plan.json"
        real_plan.write_text(
            '{"schema_version":1,"probes":[{"query":"mission"}]}',
            encoding="utf-8",
        )
        replacement = self.parent / "replacement-plan.json"
        replacement.write_text(
            '{"schema_version":1,"probes":[{"query":"recovery"}]}',
            encoding="utf-8",
        )
        real_open = os.open

        def open_replacement(_path, flags):
            return real_open(replacement, flags)

        with patch(
            "memory_forest.retrieval.os.open",
            side_effect=open_replacement,
        ), self.assertRaises(MemoryForestError) as changed:
            read_query_plan_source(str(real_plan))
        self.assertEqual(changed.exception.code, "query_plan_changed")

        fifo = self.parent / "query-plan.fifo"
        os.mkfifo(fifo)

        def open_fifo(_path, flags):
            return real_open(fifo, flags)

        with patch(
            "memory_forest.retrieval.os.open",
            side_effect=open_fifo,
        ), self.assertRaises(MemoryForestError) as non_regular:
            read_query_plan_source(str(real_plan))
        self.assertEqual(non_regular.exception.code, "unsafe_query_plan_source")

    def test_old_and_optional_retrieval_configs_are_both_valid(self):
        config_path = self.root / ".memory-forest" / "forest.json"
        legacy = json.loads(config_path.read_text(encoding="utf-8"))
        del legacy["forest_id"]
        config_path.write_text(
            json.dumps(legacy, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(config_path, 0o600)
        self.assertTrue(validate_forest(self.root)["ok"])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["retrieval"] = {"query_plan": {"max_probes": 1}}
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(config_path, 0o600)
        self.assertTrue(validate_forest(self.root)["ok"])
        index_forest(self.root)
        with self.assertRaises(MemoryForestError) as captured:
            retrieve_index(
                self.root,
                "mission",
                query_plan={
                    "schema_version": 1,
                    "probes": [{"query": "recovery"}, {"query": "telemetry"}],
                },
            )
        self.assertEqual(captured.exception.code, "invalid_query_plan")

        with self.assertRaises(MemoryForestError) as identity_missing:
            load_forest_identity(self.root)
        self.assertEqual(identity_missing.exception.code, "forest_identity_missing")

    def test_retrieval_config_rejects_network_and_identity_fields(self):
        config_path = self.root / ".memory-forest" / "forest.json"
        base = json.loads(config_path.read_text(encoding="utf-8"))
        for retrieval in (
            None,
            {"oauth": {"provider": "example"}},
            {"query_plan": {"max_probes": 8, "endpoint": "example"}},
        ):
            with self.subTest(retrieval=retrieval):
                config = dict(base)
                config["retrieval"] = retrieval
                config_path.write_text(
                    json.dumps(config, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.chmod(config_path, 0o600)
                result = validate_forest(self.root)
                self.assertFalse(result["ok"])
                self.assertIn(
                    "invalid_retrieval_config",
                    {issue["code"] for issue in result["issues"]},
                )

    def test_forest_config_rejects_duplicate_keys_and_noninteger_version(self):
        config_path = self.root / ".memory-forest" / "forest.json"
        base = json.loads(config_path.read_text(encoding="utf-8"))
        duplicate_retrieval = (
            "{"
            f'"forest_id":{json.dumps(base["forest_id"])},'
            f'"layout":{json.dumps(base["layout"])},'
            f'"layers":{json.dumps(base["layers"])},'
            '"schema_version":1,'
            '"retrieval":{"oauth":{"provider":"example"}},'
            '"retrieval":{"query_plan":{"max_probes":8}}'
            "}\n"
        )
        config_path.write_text(duplicate_retrieval, encoding="utf-8")
        os.chmod(config_path, 0o600)
        duplicate_result = validate_forest(self.root)
        self.assertFalse(duplicate_result["ok"])
        self.assertIn(
            "invalid_config",
            {issue["code"] for issue in duplicate_result["issues"]},
        )

        for version in (True, 1.0):
            with self.subTest(version=version):
                config = dict(base)
                config["schema_version"] = version
                config_path.write_text(
                    json.dumps(config, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.chmod(config_path, 0o600)
                result = validate_forest(self.root)
                self.assertFalse(result["ok"])
                self.assertIn(
                    "config_mismatch",
                    {issue["code"] for issue in result["issues"]},
                )

    def test_index_schema_mismatch_requires_rebuild(self):
        index_forest(self.root)
        index_path = self.root / ".memory-forest" / "index.sqlite3"
        connection = sqlite3.connect(index_path)
        try:
            connection.execute(
                "UPDATE metadata SET value = '1' WHERE key = 'index_schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(MemoryForestError) as captured:
            route_index(self.root, "instrument")
        self.assertEqual(captured.exception.code, "index_schema_mismatch")
        self.assertEqual(
            captured.exception.details["action"],
            "memory-forest index ROOT",
        )

    def test_retrieve_rejects_a_corrupt_parent_edge(self):
        index_forest(self.root)
        index_path = self.root / ".memory-forest" / "index.sqlite3"
        connection = sqlite3.connect(index_path)
        try:
            connection.execute(
                "UPDATE documents SET parent_path = ? WHERE path = ?",
                (
                    "03 mtm/research-notes/observatory-trial.md",
                    "04 stm/mission-operations/recovery-drill/telemetry-replay.md",
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(MemoryForestError) as captured:
            retrieve_index(self.root, "telemetry replay")
        self.assertEqual(captured.exception.code, "index_corrupt")

    def test_cli_retrieve_accepts_a_strict_query_plan_file(self):
        index_forest(self.root)
        plan_path = self.parent / "query-plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "probes": [{"query": "mission recovery"}],
                }
            ),
            encoding="utf-8",
        )
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exit_code = main(
                [
                    "--json",
                    "retrieve",
                    str(self.root),
                    "비상 복원",
                    "--query-plan",
                    str(plan_path),
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["operation"], "retrieve")
        self.assertGreaterEqual(payload["count"], 1)

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

    def test_retrieve_refuses_a_stale_trail_until_rebuilt(self):
        index_forest(self.root)
        leaf = (
            self.root
            / "04 stm"
            / "mission-operations"
            / "recovery-drill"
            / "telemetry-replay.md"
        )
        leaf.write_text(
            leaf.read_text(encoding="utf-8").replace("buffered", "archived"),
            encoding="utf-8",
        )
        os.chmod(leaf, 0o600)
        with self.assertRaises(MemoryForestError) as captured:
            retrieve_index(self.root, "telemetry replay")
        self.assertEqual(captured.exception.code, "index_stale")

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
