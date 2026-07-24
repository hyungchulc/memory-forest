from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory_forest.writer as writer_module
from memory_forest import (
    audit_forest,
    index_forest,
    initialize_forest,
    load_forest_identity,
    structured_context_index,
    validate_forest,
)
from memory_forest.errors import MemoryForestError
from memory_forest.core import structured_forest_snapshot_sha256
from memory_forest.writer import (
    DAILY_PLAN_SCHEMA,
    PROMOTION_PLAN_SCHEMA,
    STRUCTURED_SWEEP_PLAN_SCHEMA,
    WRITE_RECEIPT_SCHEMA,
    apply_daily,
    apply_structured_sweep,
    promote,
    read_plan_source,
    validate_promotion_plan,
    validate_structured_sweep_plan,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class WriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.parent = Path(self.temporary.name).resolve()
        self.root = self.parent / "forest"
        initialize_forest(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def daily_plan(
        self,
        *,
        transaction: str = "a" * 64,
        result: str = "c" * 64,
        entry_id: str = "entry-1",
        day: str = "2042-04-13",
        summary: str = "A wholly fictional source summary.",
        forest_id: str | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": DAILY_PLAN_SCHEMA,
            "forest_id": forest_id or load_forest_identity(self.root),
            "transaction_id": transaction,
            "date": day,
            "entries": [
                {
                    "entry_id": entry_id,
                    "source_record_ids": ["record-1"],
                    "summary": summary,
                }
            ],
            "provenance": {
                "packet_sha256": "b" * 64,
                "result_sha256": result,
                "batch_id": transaction,
            },
        }

    def promotion_plan(
        self,
        *,
        transaction: str = "d" * 64,
        daily_commit: str = "c" * 64,
        entry_id: str = "entry-1",
        domain: str = "field-ops",
        domain_title: str = "Field operations",
        branch: str = "trial",
        branch_title: str = "Fictional trial",
        leaf: str = "result",
        title: str = "Synthetic result",
        content: str = "The fictional result remained bounded.",
        forest_id: str | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": PROMOTION_PLAN_SCHEMA,
            "forest_id": forest_id or load_forest_identity(self.root),
            "transaction_id": transaction,
            "date": "2042-04-13",
            "promotions": [
                {
                    "source_daily_entry_ids": [entry_id],
                    "route": {
                        "domain": domain,
                        "domain_title": domain_title,
                        "branch": branch,
                        "branch_title": branch_title,
                        "leaf": leaf,
                    },
                    "title": title,
                    "content": content,
                    "confidence": "high",
                }
            ],
            "provenance": {
                "packet_sha256": "e" * 64,
                "result_sha256": transaction,
                "daily_commit_sha256s": [daily_commit],
            },
        }

    def admit_source(self) -> dict[str, object]:
        return apply_daily(self.root, self.daily_plan())

    def structured_plan(
        self,
        root: Path,
        changes: list[dict[str, object]],
        *,
        transaction: str = "9" * 64,
        entry_id: str = "entry-1",
    ) -> dict[str, object]:
        targets = [change["target"] for change in changes]
        return {
            "schema_version": STRUCTURED_SWEEP_PLAN_SCHEMA,
            "forest_id": load_forest_identity(root),
            "transaction_id": transaction,
            "date": "2042-04-13",
            "changes": changes,
            "dispositions": [
                {
                    "daily_entry_id": entry_id,
                    "status": "promoted" if changes else "source_only",
                    "targets": targets,
                    "reason": (
                        "The integrated sweep updates the exact structured targets."
                        if changes
                        else "The source adds no separate durable structured fact."
                    ),
                }
            ],
            "provenance": {
                "packet_sha256": "7" * 64,
                "result_sha256": transaction,
                "forest_snapshot_sha256": structured_forest_snapshot_sha256(root),
                "daily_commit_sha256s": ["c" * 64],
            },
        }

    def replace_change(
        self,
        path: Path,
        target: dict[str, object],
        body: str,
        *,
        source_ids: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "action": "replace",
            "target": target,
            "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "body": body,
            "source_daily_entry_ids": source_ids or [],
            "reason": "The current structured object needs an exact full-body update.",
            "confidence": "high",
        }

    def canonical_snapshot(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for layer in self.root.iterdir()
            if layer.is_dir() and layer.name[:2].isdigit()
            for path in layer.rglob("*")
            if path.is_file()
        }

    def assert_success_shape(self, result: dict[str, object], operation: str) -> None:
        self.assertEqual(
            set(result),
            {
                "schema_version",
                "ok",
                "operation",
                "forest_id",
                "transaction_id",
                "already_applied",
                "receipt",
                "receipt_sha256",
                "touched",
            },
        )
        self.assertEqual(result["schema_version"], 1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["operation"], operation)

    def test_parent_first_promotion_creates_canonical_chain_and_links(self) -> None:
        self.admit_source()
        result = promote(self.root, self.promotion_plan())
        self.assert_success_shape(result, "promote")
        self.assertEqual(
            result["touched"],
            [
                "01 xltm/XLTM.md",
                "02 ltm/field-ops_LTM.md",
                "03 mtm/field-ops/trial.md",
                "04 stm/field-ops/trial/result.md",
            ],
        )
        root_body = (self.root / "01 xltm/XLTM.md").read_text(encoding="utf-8")
        ltm_body = (self.root / "02 ltm/field-ops_LTM.md").read_text(
            encoding="utf-8"
        )
        mtm_body = (self.root / "03 mtm/field-ops/trial.md").read_text(
            encoding="utf-8"
        )
        leaf_body = (
            self.root / "04 stm/field-ops/trial/result.md"
        ).read_text(encoding="utf-8")
        self.assertIn("[[../02 ltm/field-ops_LTM.md]]", root_body)
        self.assertIn("[[../01 xltm/XLTM.md]]", ltm_body)
        self.assertIn("[[../03 mtm/field-ops/trial.md]]", ltm_body)
        self.assertIn("[[../../02 ltm/field-ops_LTM.md]]", mtm_body)
        self.assertIn("[[../../04 stm/field-ops/trial/result.md]]", mtm_body)
        self.assertIn("[[../../../03 mtm/field-ops/trial.md]]", leaf_body)
        self.assertIn("[[../../../05 daily/2042-04-13.md]]", leaf_body)
        self.assertTrue(validate_forest(self.root)["ok"])
        self.assertTrue(audit_forest(self.root)["ok"])
        self.assertTrue((self.root / ".memory-forest/index.sqlite3").is_file())

    def test_integrated_sweep_replaces_multiple_layers_in_one_transaction(self) -> None:
        other = self.parent / "integrated"
        initialize_forest(other, example=True)
        apply_daily(
            other,
            self.daily_plan(forest_id=load_forest_identity(other)),
        )
        xltm = other / "01 xltm/XLTM.md"
        leaf = (
            other
            / "04 stm/research-notes/observatory-trial/instrument-calibration.md"
        )
        changes = [
            self.replace_change(
                xltm,
                {
                    "layer": "xltm",
                    "tree": None,
                    "branch": None,
                    "leaf": None,
                },
                xltm.read_text(encoding="utf-8") + "\nIntegrated sweep anchor.\n",
            ),
            self.replace_change(
                leaf,
                {
                    "layer": "stm",
                    "tree": "research-notes",
                    "branch": "observatory-trial",
                    "leaf": "instrument-calibration",
                },
                leaf.read_text(encoding="utf-8") + "\nIntegrated sweep detail.\n",
                source_ids=["entry-1"],
            ),
        ]
        plan = self.structured_plan(other, changes)
        result = apply_structured_sweep(other, plan)
        self.assert_success_shape(result, "apply-structured")
        self.assertEqual(
            result["touched"],
            [
                "01 xltm/XLTM.md",
                "04 stm/research-notes/observatory-trial/instrument-calibration.md",
            ],
        )
        self.assertTrue(validate_forest(other)["ok"])
        self.assertTrue(audit_forest(other)["ok"])
        self.assertTrue(
            apply_structured_sweep(other, plan)["already_applied"]
        )

    def test_integrated_sweep_orders_forest_tree_branch_leaf_materialization(
        self,
    ) -> None:
        other = self.parent / "ordered-materialization"
        initialize_forest(other, example=True)
        xltm = other / "01 xltm/XLTM.md"
        changes = [
            {
                "action": "create",
                "target": {
                    "layer": "stm",
                    "tree": "new-tree",
                    "branch": "new-branch",
                    "leaf": "new-leaf",
                },
                "expected_sha256": None,
                "body": "# New leaf\n",
                "source_daily_entry_ids": ["entry-1"],
                "reason": "Create the exact leaf.",
                "confidence": "high",
            },
            {
                "action": "create",
                "target": {
                    "layer": "mtm",
                    "tree": "new-tree",
                    "branch": "new-branch",
                    "leaf": None,
                },
                "expected_sha256": None,
                "body": "# New branch\n",
                "source_daily_entry_ids": [],
                "reason": "Create the owning branch.",
                "confidence": "high",
            },
            {
                "action": "create",
                "target": {
                    "layer": "ltm",
                    "tree": "new-tree",
                    "branch": None,
                    "leaf": None,
                },
                "expected_sha256": None,
                "body": "# New tree\n",
                "source_daily_entry_ids": [],
                "reason": "Create the owning tree.",
                "confidence": "high",
            },
            self.replace_change(
                xltm,
                {
                    "layer": "xltm",
                    "tree": None,
                    "branch": None,
                    "leaf": None,
                },
                xltm.read_text(encoding="utf-8") + "\nNew forest authority.\n",
            ),
        ]
        parsed = validate_structured_sweep_plan(self.structured_plan(other, changes))
        ordered = writer_module._structured_sweep_changes(
            other,
            parsed,
            limits=writer_module.DEFAULT_LIMITS,
        )
        self.assertEqual(
            [path for path, _ in ordered],
            [
                "01 xltm/XLTM.md",
                "02 ltm/new-tree_LTM.md",
                "03 mtm/new-tree/new-branch.md",
                "04 stm/new-tree/new-branch/new-leaf.md",
            ],
        )

    def test_integrated_sweep_rejects_stale_preimage_without_mutation(self) -> None:
        other = self.parent / "stale"
        initialize_forest(other, example=True)
        apply_daily(
            other,
            self.daily_plan(forest_id=load_forest_identity(other)),
        )
        leaf = (
            other
            / "04 stm/research-notes/observatory-trial/instrument-calibration.md"
        )
        change = self.replace_change(
            leaf,
            {
                "layer": "stm",
                "tree": "research-notes",
                "branch": "observatory-trial",
                "leaf": "instrument-calibration",
            },
            leaf.read_text(encoding="utf-8") + "\nNew detail.\n",
            source_ids=["entry-1"],
        )
        change["expected_sha256"] = "0" * 64
        before = {
            path.relative_to(other).as_posix(): path.read_bytes()
            for path in other.rglob("*.md")
        }
        with self.assertRaises(MemoryForestError) as stale:
            apply_structured_sweep(
                other,
                self.structured_plan(other, [change]),
            )
        self.assertEqual(stale.exception.code, "structured_preimage_mismatch")
        self.assertEqual(
            {
                path.relative_to(other).as_posix(): path.read_bytes()
                for path in other.rglob("*.md")
            },
            before,
        )

    def test_integrated_sweep_rolls_back_every_layer_when_validation_fails(self) -> None:
        other = self.parent / "rollback"
        initialize_forest(other, example=True)
        apply_daily(
            other,
            self.daily_plan(forest_id=load_forest_identity(other)),
        )
        xltm = other / "01 xltm/XLTM.md"
        leaf = (
            other
            / "04 stm/research-notes/observatory-trial/instrument-calibration.md"
        )
        changes = [
            self.replace_change(
                xltm,
                {
                    "layer": "xltm",
                    "tree": None,
                    "branch": None,
                    "leaf": None,
                },
                xltm.read_text(encoding="utf-8") + "\nTentative anchor.\n",
            ),
            self.replace_change(
                leaf,
                {
                    "layer": "stm",
                    "tree": "research-notes",
                    "branch": "observatory-trial",
                    "leaf": "instrument-calibration",
                },
                "# Invalid leaf without its canonical parent link\n",
                source_ids=["entry-1"],
            ),
        ]
        before = {
            path.relative_to(other).as_posix(): path.read_bytes()
            for path in other.rglob("*.md")
        }
        with self.assertRaises(MemoryForestError):
            apply_structured_sweep(
                other,
                self.structured_plan(other, changes),
            )
        self.assertEqual(
            {
                path.relative_to(other).as_posix(): path.read_bytes()
                for path in other.rglob("*.md")
            },
            before,
        )

    def test_structured_context_returns_bounded_current_bodies(self) -> None:
        other = self.parent / "context"
        initialize_forest(other, example=True)
        index_forest(other)
        result = structured_context_index(
            other,
            "instrument calibration",
            limit=3,
        )
        self.assertEqual(result["operation"], "structured-context")
        self.assertEqual(
            result["forest_snapshot_sha256"],
            structured_forest_snapshot_sha256(other),
        )
        self.assertEqual(result["documents"][0]["route"]["layer"]["name"], "xltm")
        self.assertEqual(
            set(result["documents"][0]["route"]),
            {"branch", "layer", "leaf", "path", "route_key", "tree"},
        )
        self.assertNotIn("domain", result["documents"][0]["route"])
        self.assertLessEqual(len(result["documents"]), 10)
        for document in result["documents"]:
            self.assertEqual(
                hashlib.sha256(document["body"].encode("utf-8")).hexdigest(),
                document["sha256"],
            )

    def test_structured_context_rejects_an_unselected_stale_document(self) -> None:
        other = self.parent / "stale-context"
        initialize_forest(other, example=True)
        index_forest(other)
        unrelated = (
            other
            / "04 stm"
            / "mission-operations"
            / "recovery-drill"
            / "telemetry-replay.md"
        )
        unrelated.write_text(
            unrelated.read_text(encoding="utf-8") + "\nStale detail.\n",
            encoding="utf-8",
        )
        with self.assertRaises(MemoryForestError) as captured:
            structured_context_index(
                other,
                "instrument calibration",
                limit=3,
            )
        self.assertEqual(captured.exception.code, "index_stale")

    def test_integrated_sweep_rejects_a_changed_forest_snapshot(self) -> None:
        self.admit_source()
        plan = self.structured_plan(self.root, [])
        root_document = self.root / "01 xltm" / "XLTM.md"
        root_document.write_text(
            root_document.read_text(encoding="utf-8") + "\nCurrent change.\n",
            encoding="utf-8",
        )
        with self.assertRaises(MemoryForestError) as captured:
            apply_structured_sweep(self.root, plan)
        self.assertEqual(captured.exception.code, "structured_snapshot_mismatch")
        self.assertFalse(
            (
                self.root
                / ".memory-forest"
                / "receipts"
                / f"{plan['transaction_id']}.json"
            ).exists()
        )

    def test_integrated_sweep_rejects_domain_as_a_fifth_object_level(self) -> None:
        other = self.parent / "no-domain-object"
        initialize_forest(other, example=True)
        apply_daily(
            other,
            self.daily_plan(forest_id=load_forest_identity(other)),
        )
        leaf = (
            other
            / "04 stm/research-notes/observatory-trial/instrument-calibration.md"
        )
        change = self.replace_change(
            leaf,
            {
                "layer": "stm",
                "tree": "research-notes",
                "branch": "observatory-trial",
                "leaf": "instrument-calibration",
            },
            leaf.read_text(encoding="utf-8") + "\nNew detail.\n",
            source_ids=["entry-1"],
        )
        target = change["target"]
        assert isinstance(target, dict)
        target["domain"] = target.pop("tree")
        with self.assertRaises(MemoryForestError) as invalid:
            apply_structured_sweep(
                other,
                self.structured_plan(other, [change]),
            )
        self.assertEqual(invalid.exception.code, "invalid_structured_sweep_plan")

    def test_existing_leaf_is_appended_without_replacing_prior_body(self) -> None:
        other = self.parent / "example"
        initialize_forest(other, example=True)
        other_id = load_forest_identity(other)
        apply_daily(other, self.daily_plan(forest_id=other_id))
        leaf = (
            other
            / "04 stm/research-notes/observatory-trial/instrument-calibration.md"
        )
        before = leaf.read_text(encoding="utf-8")
        plan = self.promotion_plan(
            domain="research-notes",
            domain_title="Research notes",
            branch="observatory-trial",
            branch_title="Observatory trial",
            leaf="instrument-calibration",
            forest_id=other_id,
        )
        result = promote(other, plan)
        after = leaf.read_text(encoding="utf-8")
        self.assertIn(before, after)
        self.assertIn("memory-forest-promotion-v1", after)
        self.assertIn(leaf.relative_to(other).as_posix(), result["touched"])

    def test_daily_and_promotion_retries_are_receipt_idempotent(self) -> None:
        daily_plan = self.daily_plan()
        first_daily = apply_daily(self.root, daily_plan)
        second_daily = apply_daily(self.root, daily_plan)
        self.assertFalse(first_daily["already_applied"])
        self.assertTrue(second_daily["already_applied"])
        self.assertEqual(second_daily["touched"], [])
        self.assertEqual(first_daily["receipt_sha256"], second_daily["receipt_sha256"])
        promotion_plan = self.promotion_plan()
        first = promote(self.root, promotion_plan)
        second = promote(self.root, promotion_plan)
        self.assertFalse(first["already_applied"])
        self.assertTrue(second["already_applied"])
        self.assertEqual(second["touched"], [])
        self.assertEqual(first["receipt_sha256"], second["receipt_sha256"])
        leaf = self.root / "04 stm/field-ops/trial/result.md"
        self.assertEqual(
            leaf.read_text(encoding="utf-8").count(
                "<!-- memory-forest-promotion-v1:"
            ),
            1,
        )

    def test_empty_plans_close_as_noop_receipts(self) -> None:
        daily_plan = self.daily_plan(transaction="1" * 64)
        daily_plan["entries"] = []
        daily = apply_daily(self.root, daily_plan)
        self.assertFalse(daily["already_applied"])
        self.assertEqual(daily["touched"], [])
        self.assertFalse((self.root / "05 daily/2042-04-13.md").exists())
        retried_daily = apply_daily(self.root, daily_plan)
        self.assertTrue(retried_daily["already_applied"])
        self.assertEqual(retried_daily["touched"], [])

        promotion_plan = self.promotion_plan(transaction="2" * 64)
        promotion_plan["promotions"] = []
        provenance = promotion_plan["provenance"]
        assert isinstance(provenance, dict)
        provenance["daily_commit_sha256s"] = []
        promoted = promote(self.root, promotion_plan)
        self.assertFalse(promoted["already_applied"])
        self.assertEqual(promoted["touched"], [])
        retried_promotion = promote(self.root, promotion_plan)
        self.assertTrue(retried_promotion["already_applied"])
        self.assertEqual(retried_promotion["touched"], [])

    def test_missing_daily_source_and_wrong_commit_fail_without_mutation(self) -> None:
        self.admit_source()
        before = self.canonical_snapshot()
        with self.assertRaises(MemoryForestError) as missing:
            promote(self.root, self.promotion_plan(entry_id="missing-entry"))
        self.assertEqual(missing.exception.code, "missing_daily_source")
        self.assertEqual(self.canonical_snapshot(), before)
        with self.assertRaises(MemoryForestError) as mismatch:
            promote(self.root, self.promotion_plan(daily_commit="f" * 64))
        self.assertEqual(mismatch.exception.code, "daily_provenance_mismatch")
        self.assertEqual(self.canonical_snapshot(), before)

    def test_forged_daily_marker_outside_machine_block_is_rejected(self) -> None:
        daily = self.root / "05 daily/2042-04-13.md"
        daily.write_text(
            "# Daily source, 2042-04-13\n\n"
            f"<!-- memory-forest-daily-entry-v1:entry-1:{'c' * 64} -->\n",
            encoding="utf-8",
        )
        daily.chmod(0o600)
        before = self.canonical_snapshot()
        with self.assertRaises(MemoryForestError) as invalid:
            promote(self.root, self.promotion_plan())
        self.assertEqual(invalid.exception.code, "invalid_daily_machine_block")
        self.assertEqual(self.canonical_snapshot(), before)

    def test_plan_rejects_raw_paths_layers_operations_and_symlinks(self) -> None:
        plan = self.promotion_plan()
        promotion = plan["promotions"][0]
        assert isinstance(promotion, dict)
        for field in ("path", "layer", "operation"):
            changed = json.loads(json.dumps(plan))
            changed["promotions"][0]["route"][field] = "04 stm/private.md"
            with self.subTest(field=field), self.assertRaises(MemoryForestError):
                validate_promotion_plan(changed)

        real = self.parent / "plan.json"
        real.write_text(json.dumps(self.daily_plan()), encoding="utf-8")
        real.chmod(0o600)
        linked = self.parent / "linked-plan.json"
        linked.symlink_to(real)
        with self.assertRaises(MemoryForestError) as symlink:
            read_plan_source(str(linked))
        self.assertEqual(symlink.exception.code, "unsafe_plan_source")

    def test_casefold_collision_is_rejected_before_creation(self) -> None:
        self.admit_source()
        conflict = self.root / "02 ltm/Field-Ops_LTM.md"
        conflict.write_text(
            "# Existing\n\nParent: [[../01 xltm/XLTM.md]]\n",
            encoding="utf-8",
        )
        conflict.chmod(0o600)
        with self.assertRaises(MemoryForestError) as collision:
            promote(self.root, self.promotion_plan())
        self.assertEqual(collision.exception.code, "casefold_collision")
        with os.scandir(self.root / "02 ltm") as iterator:
            exact_names = {entry.name for entry in iterator}
        self.assertNotIn("field-ops_LTM.md", exact_names)

    def test_existing_sibling_lock_blocks_writer_and_is_preserved(self) -> None:
        lock = Path(str(self.root) + ".maintenance.lock")
        lock.mkdir(mode=0o700)
        before = self.canonical_snapshot()
        with self.assertRaises(MemoryForestError) as busy:
            apply_daily(self.root, self.daily_plan())
        self.assertEqual(busy.exception.code, "maintenance_lock_busy")
        self.assertTrue(lock.is_dir())
        self.assertEqual(self.canonical_snapshot(), before)

    def test_index_failure_rolls_back_all_canonical_files_and_prior_index(self) -> None:
        self.admit_source()
        index_forest(self.root)
        before = self.canonical_snapshot()
        index_path = self.root / ".memory-forest/index.sqlite3"
        index_before = index_path.read_bytes()
        with patch(
            "memory_forest.writer._index_forest_unlocked",
            side_effect=MemoryForestError("injected_index_failure", "injected"),
        ), self.assertRaises(MemoryForestError) as failed:
            promote(self.root, self.promotion_plan())
        self.assertEqual(failed.exception.code, "injected_index_failure")
        self.assertEqual(self.canonical_snapshot(), before)
        self.assertEqual(index_path.read_bytes(), index_before)
        self.assertFalse(
            (
                self.root
                / ".memory-forest/receipts"
                / f"{'d' * 64}.json"
            ).exists()
        )

    def test_interrupted_canonical_write_is_recovered_before_exact_retry(self) -> None:
        original_write = writer_module._MutationSession.write
        calls = 0

        def crash_after_first_write(session, path, data):
            nonlocal calls
            original_write(session, path, data)
            calls += 1
            if calls == 1:
                raise SystemExit("synthetic process stop")

        with patch.object(
            writer_module._MutationSession,
            "write",
            crash_after_first_write,
        ), self.assertRaises(SystemExit):
            apply_daily(self.root, self.daily_plan())
        self.assertTrue(Path(str(self.root) + ".write-journal").is_dir())
        result = apply_daily(self.root, self.daily_plan())
        self.assertFalse(result["already_applied"])
        self.assertTrue(validate_forest(self.root)["ok"])
        self.assertTrue(audit_forest(self.root)["ok"])
        self.assertFalse(Path(str(self.root) + ".write-journal").exists())

    def test_interrupted_receipt_create_is_recovered_before_exact_retry(self) -> None:
        with patch.object(
            writer_module._MutationSession,
            "commit",
            side_effect=SystemExit("synthetic process stop"),
        ), self.assertRaises(SystemExit):
            apply_daily(self.root, self.daily_plan())
        receipt = (
            self.root
            / ".memory-forest"
            / "receipts"
            / f"{'a' * 64}.json"
        )
        self.assertTrue(receipt.is_file())
        self.assertTrue(Path(str(self.root) + ".write-journal").is_dir())
        result = apply_daily(self.root, self.daily_plan())
        self.assertFalse(result["already_applied"])
        self.assertTrue(receipt.is_file())
        self.assertFalse(Path(str(self.root) + ".write-journal").exists())

    def test_torn_manifest_publication_is_safe_to_retry(self) -> None:
        real_link = os.link

        def crash_manifest(source, destination, *args, **kwargs):
            if Path(destination).name == "manifest.json":
                raise SystemExit("synthetic process stop")
            return real_link(source, destination, *args, **kwargs)

        with patch.object(
            writer_module.os,
            "link",
            side_effect=crash_manifest,
        ), self.assertRaises(SystemExit):
            apply_daily(self.root, self.daily_plan())
        journal = Path(str(self.root) + ".write-journal")
        self.assertTrue(journal.is_dir())
        self.assertFalse((journal / "manifest.json").exists())
        result = apply_daily(self.root, self.daily_plan())
        self.assertFalse(result["already_applied"])
        self.assertFalse(journal.exists())

    def test_torn_commit_marker_is_rolled_back_before_exact_retry(self) -> None:
        real_link = os.link

        def crash_commit(source, destination, *args, **kwargs):
            if Path(destination).name == "committed.json":
                raise SystemExit("synthetic process stop")
            return real_link(source, destination, *args, **kwargs)

        with patch.object(
            writer_module.os,
            "link",
            side_effect=crash_commit,
        ), self.assertRaises(SystemExit):
            apply_daily(self.root, self.daily_plan())
        journal = Path(str(self.root) + ".write-journal")
        self.assertTrue(journal.is_dir())
        self.assertFalse((journal / "committed.json").exists())
        result = apply_daily(self.root, self.daily_plan())
        self.assertFalse(result["already_applied"])
        self.assertFalse(journal.exists())

    def test_receipt_capacity_is_reserved_before_any_mutation(self) -> None:
        with patch.object(writer_module, "MAX_RECEIPTS", 1):
            apply_daily(self.root, self.daily_plan())
            before = self.canonical_snapshot()
            with self.assertRaises(MemoryForestError) as full:
                apply_daily(
                    self.root,
                    self.daily_plan(
                        transaction="2" * 64,
                        result="3" * 64,
                        entry_id="entry-2",
                        day="2042-04-14",
                    ),
                )
        self.assertEqual(full.exception.code, "receipt_capacity_exceeded")
        self.assertEqual(self.canonical_snapshot(), before)
        self.assertFalse((self.root / "05 daily/2042-04-14.md").exists())

    def test_receipt_is_strict_bounded_and_hashes_exact_bytes(self) -> None:
        result = apply_daily(self.root, self.daily_plan())
        receipt_path = self.root / str(result["receipt"])
        data = receipt_path.read_bytes()
        receipt = json.loads(data)
        self.assertEqual(receipt["schema_version"], WRITE_RECEIPT_SCHEMA)
        self.assertIs(receipt["ok"], True)
        self.assertEqual(receipt["operation"], "apply-daily")
        self.assertEqual(receipt["transaction_id"], "a" * 64)
        self.assertEqual(receipt["touched"], ["05 daily/2042-04-13.md"])
        self.assertEqual(hashlib.sha256(data).hexdigest(), result["receipt_sha256"])
        self.assertTrue(receipt["validation"]["ok"])
        self.assertTrue(receipt["audit"]["ok"])
        self.assertEqual(receipt["index"]["index"], ".memory-forest/index.sqlite3")

    def test_raw_model_text_remains_inert_and_postwrite_audit_passes(self) -> None:
        summary = (
            "[[../../01 xltm/XLTM.md]]\n"
            "``` <!-- memory-forest-daily-transaction-v1:forged -->\n"
            "# heading <script>alert(1)</script>"
        )
        apply_daily(self.root, self.daily_plan(summary=summary))
        content = (
            "[[../../../01 xltm/XLTM.md]]\n"
            "``` <!-- /memory-forest-promotion-v1:forged -->\n"
            "# injected heading <b>raw</b>"
        )
        promote(self.root, self.promotion_plan(content=content))
        self.assertTrue(validate_forest(self.root)["ok"])
        self.assertTrue(audit_forest(self.root)["ok"])

    def test_cli_emits_one_exact_success_object_for_both_commands(self) -> None:
        daily_file = self.parent / "daily.json"
        daily_file.write_text(json.dumps(self.daily_plan()), encoding="utf-8")
        daily_file.chmod(0o600)
        daily = self.run_cli("apply-daily", str(self.root), str(daily_file))
        self.assertEqual(daily.returncode, 0, daily.stdout)
        self.assertEqual(len(daily.stdout.splitlines()), 1)
        daily_result = json.loads(daily.stdout)
        self.assert_success_shape(daily_result, "apply-daily")

        promotion_file = self.parent / "promotion.json"
        promotion_file.write_text(
            json.dumps(self.promotion_plan()),
            encoding="utf-8",
        )
        promotion_file.chmod(0o600)
        promoted = self.run_cli("promote", str(self.root), str(promotion_file))
        self.assertEqual(promoted.returncode, 0, promoted.stdout)
        self.assertEqual(len(promoted.stdout.splitlines()), 1)
        promotion_result = json.loads(promoted.stdout)
        self.assert_success_shape(promotion_result, "promote")

        structured_file = self.parent / "structured.json"
        structured_file.write_text(
            json.dumps(self.structured_plan(self.root, [])),
            encoding="utf-8",
        )
        structured_file.chmod(0o600)
        structured = self.run_cli(
            "apply-structured",
            str(self.root),
            str(structured_file),
        )
        self.assertEqual(structured.returncode, 0, structured.stdout)
        self.assertEqual(len(structured.stdout.splitlines()), 1)
        self.assert_success_shape(
            json.loads(structured.stdout),
            "apply-structured",
        )

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        source = str(REPOSITORY_ROOT / "src")
        environment["PYTHONPATH"] = (
            source
            if not environment.get("PYTHONPATH")
            else source + os.pathsep + environment["PYTHONPATH"]
        )
        return subprocess.run(
            [sys.executable, "-m", "memory_forest", "--json", *arguments],
            check=False,
            capture_output=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
