from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from memory_forest import (  # pyright: ignore[reportMissingImports]
    index_forest,
    initialize_forest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TURN_GATE_PATH = (
    REPOSITORY_ROOT
    / "examples"
    / "memory-forest-retrieve"
    / "turn-gate.py"
)
REAL_TEMPORARY_ROOT = Path(tempfile.gettempdir()).resolve()


def load_turn_gate():
    spec = importlib.util.spec_from_file_location(
        "memory_forest_retrieve_turn_gate",
        TURN_GATE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the turn gate")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TURN_GATE = load_turn_gate()


class MemoryForestRetrieveGateTests(unittest.TestCase):
    def test_nonempty_turn_runs_route_then_retrieve_even_when_route_is_empty(self):
        calls: list[str] = []

        def route(_root: str, _query: str, *, limit: int):
            calls.append(f"route:{limit}")
            return {
                "count": 0,
                "limit": limit,
                "ok": True,
                "operation": "route",
                "query": "private input",
                "results": [],
                "schema_version": 1,
            }

        def retrieve(_root: str, _query: str, *, limit: int):
            calls.append(f"retrieve:{limit}")
            return {
                "count": 1,
                "limit": limit,
                "ok": True,
                "operation": "retrieve",
                "query": "private input",
                "query_plan": {"provided": False},
                "retrieval": {"layers": ["xltm", "ltm", "mtm", "stm"]},
                "schema_version": 1,
                "trails": [{"validated": True, "trail": []}],
            }

        result = TURN_GATE.consult_turn(
            "/private/forest",
            "reference lamp",
            route=route,
            retrieve=retrieve,
        )

        self.assertEqual(calls, ["route:4", "retrieve:4"])
        self.assertTrue(result["lookup_completed"])
        self.assertEqual(result["status"], "evidence_found")
        self.assertFalse(result["body_included"])
        self.assertNotIn("query", result["route"])
        self.assertNotIn("query", result["retrieve"])

    def test_whitespace_only_turn_is_a_noop(self):
        calls: list[str] = []

        def unexpected(*_args, **_kwargs):
            calls.append("called")
            raise AssertionError("lookup must not run")

        result = TURN_GATE.consult_turn(
            "/private/forest",
            " \n\t ",
            route=unexpected,
            retrieve=unexpected,
        )

        self.assertEqual(calls, [])
        self.assertFalse(result["lookup_required"])
        self.assertFalse(result["lookup_completed"])
        self.assertEqual(result["status"], "not_required")

    def test_body_field_is_rejected(self):
        def route(_root: str, _query: str, *, limit: int):
            return {
                "count": 1,
                "ok": True,
                "operation": "route",
                "query": "q",
                "results": [{"body": "must not cross"}],
                "schema_version": 1,
            }

        def retrieve(_root: str, _query: str, *, limit: int):
            return {
                "count": 0,
                "ok": True,
                "operation": "retrieve",
                "query": "q",
                "schema_version": 1,
                "trails": [],
            }

        with self.assertRaisesRegex(
            Exception,
            "refused a result containing a memory body",
        ):
            TURN_GATE.consult_turn(
                "/private/forest",
                "query",
                route=route,
                retrieve=retrieve,
            )

    def test_route_failure_stops_before_retrieve(self):
        calls: list[str] = []

        def route(_root: str, _query: str, *, limit: int):
            calls.append(f"route:{limit}")
            return {
                "count": 0,
                "ok": False,
                "operation": "route",
                "query": "private input",
                "schema_version": 1,
            }

        def retrieve(*_args, **_kwargs):
            calls.append("retrieve")
            raise AssertionError("retrieve must not run after a failed route phase")

        with self.assertRaisesRegex(Exception, "route phase"):
            TURN_GATE.consult_turn(
                "/private/forest",
                "query",
                route=route,
                retrieve=retrieve,
            )

        self.assertEqual(calls, ["route:4"])

    def test_failure_receipt_omits_private_error_details(self):
        result = TURN_GATE._failure(
            {
                "code": "unsafe_root",
                "details": {
                    "path": "/private/forest",
                    "query": "private user turn",
                },
                "message": "Failure at /private/forest for private user turn",
            }
        )

        encoded = json.dumps(result, sort_keys=True)
        self.assertIn('"code": "unsafe_root"', encoded)
        self.assertNotIn("/private/forest", encoded)
        self.assertNotIn("private user turn", encoded)
        self.assertNotIn('"details"', encoded)

    def test_turn_reader_rejects_oversized_input(self):
        with self.assertRaisesRegex(Exception, "exceeds the gate input limit"):
            TURN_GATE.read_turn(
                io.BytesIO(b"x" * (TURN_GATE.MAX_TURN_BYTES + 1))
            )

    def test_real_gate_does_not_short_circuit_after_zero_route_results(self):
        with tempfile.TemporaryDirectory(dir=REAL_TEMPORARY_ROOT) as raw:
            forest = Path(raw) / "forest"
            initialize_forest(forest, example=True)
            index_forest(forest)
            environment = os.environ.copy()
            source_path = str(REPOSITORY_ROOT / "src")
            environment["PYTHONPATH"] = source_path
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TURN_GATE_PATH),
                    str(forest),
                ],
                input="reference lamp",
                text=True,
                check=False,
                capture_output=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["route"]["count"], 0)
        self.assertGreaterEqual(result["retrieve"]["count"], 1)
        self.assertTrue(result["lookup_completed"])
        self.assertFalse(result["body_included"])
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn(str(forest), encoded)
        self.assertNotIn("reference lamp", encoded)
        self.assertNotIn('"body"', encoded)

    def test_punctuation_only_turn_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=REAL_TEMPORARY_ROOT) as raw:
            forest = Path(raw) / "forest"
            initialize_forest(forest, example=True)
            index_forest(forest)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TURN_GATE_PATH),
                    str(forest),
                ],
                input="!!!",
                text=True,
                check=False,
                capture_output=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
            )

        self.assertNotEqual(completed.returncode, 0)
        result = json.loads(completed.stdout)
        self.assertFalse(result["ok"])
        self.assertTrue(result["lookup_required"])
        self.assertFalse(result["lookup_completed"])
        self.assertEqual(result["status"], "failed")

    def test_prompt_states_host_enforcement_and_current_turn_binding(self):
        prompt = (
            REPOSITORY_ROOT
            / "examples"
            / "memory-forest-retrieve"
            / "system-prompt.md"
        ).read_text(encoding="utf-8")
        normalized = " ".join(prompt.split())
        self.assertIn("current user-authored turn", normalized)
        self.assertIn(
            "successful receipt belongs to the current turn",
            normalized,
        )
        self.assertIn("prompt is advisory", normalized.lower())


if __name__ == "__main__":
    unittest.main()
