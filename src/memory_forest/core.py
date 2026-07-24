from __future__ import annotations

import hashlib
import json
import math
import os
import posixpath
import re
import secrets
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from collections.abc import Iterable
from typing import Any, Final

from .errors import MemoryForestError
from .model import (
    LAYER_DIRECTORY_NAMES,
    SCHEMA_VERSION,
    Route,
    immediate_parent_path,
    layers_are_adjacent,
    parse_relative_route,
)
from .safety import (
    DEFAULT_LIMITS,
    MAX_RECEIPT_BYTES,
    MAX_RECEIPTS,
    RECEIPT_NAME_RE,
    RECEIPTS_DIRECTORY,
    STATE_DIRECTORY,
    ForestLimits,
    ScanResult,
    prepare_new_root,
    require_real_root,
    scan_forest,
    secure_create_file,
    secure_mkdir,
    secure_state_directory,
)


CONFIG_FILENAME: Final[str] = "forest.json"
WIKILINK_RE: Final[re.Pattern[str]] = re.compile(r"\[\[([^\]\n]+)\]\]")
DEFAULT_QUERY_PLAN_MAX_PROBES: Final[int] = 8
MAX_QUERY_PLAN_PROBES: Final[int] = 16
FOREST_ID_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    level: str
    message: str
    path: str | None = None
    line: int | None = None
    target: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "level": self.level,
            "line": self.line,
            "message": self.message,
            "path": self.path,
            "target": self.target,
        }


@dataclass(frozen=True, slots=True)
class Document:
    route: Route
    title: str
    body: str
    sha256: str
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class Inspection:
    root: Path
    scan: ScanResult
    documents: tuple[Document, ...]
    issues: tuple[Issue, ...]
    link_count: int

    @property
    def ok(self) -> bool:
        return not any(issue.level == "error" for issue in self.issues)

    def report(self, operation: str) -> dict[str, object]:
        error_count = sum(issue.level == "error" for issue in self.issues)
        warning_count = sum(issue.level == "warning" for issue in self.issues)
        return {
            "issues": [issue.as_dict() for issue in self.issues],
            "ok": self.ok,
            "operation": operation,
            "root": str(self.root),
            "schema_version": SCHEMA_VERSION,
            "summary": {
                "bytes": self.scan.total_bytes,
                "directories": len(self.scan.directories),
                "documents": len(self.documents),
                "errors": error_count,
                "files": len(self.scan.files),
                "links": self.link_count,
                "warnings": warning_count,
            },
        }


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    query_plan_max_probes: int = DEFAULT_QUERY_PLAN_MAX_PROBES


def initialize_forest(
    root: str | os.PathLike[str],
    *,
    example: bool = False,
) -> dict[str, object]:
    candidate, existed = prepare_new_root(root)
    if existed or os.path.lexists(candidate):
        raise MemoryForestError(
            "root_exists",
            "Initialization requires a new path and never writes into an existing one.",
            details={"path": str(candidate)},
        )
    try:
        candidate.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise MemoryForestError(
            "root_exists",
            "Initialization requires a new path and never writes into an existing one.",
            details={"path": str(candidate)},
        ) from exc
    os.chmod(candidate, 0o700, follow_symlinks=False)
    root_path = require_real_root(candidate)
    created: list[str] = []
    for directory_name in LAYER_DIRECTORY_NAMES:
        directory = root_path / directory_name
        secure_mkdir(directory)
        created.append(directory_name + "/")
    state = secure_state_directory(root_path)
    created.append(STATE_DIRECTORY + "/")
    config = {
        "forest_id": secrets.token_hex(16),
        "layout": "layer/domain/branch/leaf",
        "layers": list(LAYER_DIRECTORY_NAMES),
        "schema_version": SCHEMA_VERSION,
    }
    config_bytes = (
        json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    config_path = state / CONFIG_FILENAME
    secure_create_file(config_path, config_bytes)
    created.append(f"{STATE_DIRECTORY}/{CONFIG_FILENAME}")
    documents = _example_documents() if example else _empty_documents()
    for relative, body in sorted(documents.items()):
        destination = root_path / Path(relative)
        _secure_parent_directories(root_path, destination.parent)
        secure_create_file(destination, body.encode("utf-8"))
        created.append(relative)
    return {
        "created": sorted(created),
        "example": example,
        "forest_id": config["forest_id"],
        "ok": True,
        "operation": "init",
        "permissions": {"directories": "0700", "files": "0600"},
        "root": str(root_path),
        "schema_version": SCHEMA_VERSION,
    }


def inspect_forest(
    root: str | os.PathLike[str],
    *,
    audit_links: bool,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> Inspection:
    scan = scan_forest(root, limits=limits)
    issues: list[Issue] = []
    documents: list[Document] = []
    root_mode = stat.S_IMODE(scan.root.stat().st_mode)
    if root_mode != 0o700:
        issues.append(
            Issue(
                code="directory_permissions",
                level="error",
                message="Forest directories must have mode 0700.",
                path=".",
            )
        )
    expected_layers = set(LAYER_DIRECTORY_NAMES)
    present_layers: set[str] = set()
    for directory in scan.directories:
        if directory.mode != 0o700:
            issues.append(
                Issue(
                    code="directory_permissions",
                    level="error",
                    message="Forest directories must have mode 0700.",
                    path=directory.relative,
                )
            )
        if "/" not in directory.relative:
            if directory.relative in expected_layers:
                present_layers.add(directory.relative)
            else:
                issues.append(
                    Issue(
                        code="unexpected_top_level_directory",
                        level="error",
                        message="Only canonical layer directories may exist at the forest root.",
                        path=directory.relative,
                    )
                )
    for missing in sorted(expected_layers - present_layers):
        issues.append(
            Issue(
                code="missing_layer",
                level="error",
                message="A canonical layer directory is missing.",
                path=missing,
            )
        )
    issues.extend(_state_issues(scan.root, limits=limits))
    seen_route_keys: dict[str, str] = {}
    for scanned in scan.files:
        if scanned.mode != 0o600:
            issues.append(
                Issue(
                    code="file_permissions",
                    level="error",
                    message="Forest files must have mode 0600.",
                    path=scanned.relative,
                )
            )
        try:
            route = parse_relative_route(scanned.relative)
            body = _read_utf8_file(scanned.path, limits=limits)
            if route.layer.name == "istm":
                _validate_jsonl(body, path=scanned.relative)
        except MemoryForestError as exc:
            issues.append(
                Issue(
                    code=exc.code,
                    level="error",
                    message=exc.message,
                    path=scanned.relative,
                )
            )
            continue
        route_key = route.route_key.casefold()
        if route_key in seen_route_keys:
            issues.append(
                Issue(
                    code="duplicate_route",
                    level="error",
                    message="Two documents resolve to the same canonical route.",
                    path=scanned.relative,
                    target=seen_route_keys[route_key],
                )
            )
            continue
        seen_route_keys[route_key] = scanned.relative
        documents.append(
            Document(
                route=route,
                title=_extract_title(body, route.leaf),
                body=body,
                sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                size=scanned.size,
                mtime_ns=scanned.mtime_ns,
            )
        )
    issues.extend(_parent_chain_issues(documents))
    link_count = 0
    if audit_links:
        link_issues, link_count = _audit_wikilinks(documents, limits=limits)
        issues.extend(link_issues)
    issues.sort(key=_issue_sort_key)
    documents.sort(key=lambda document: document.route.path)
    return Inspection(
        root=scan.root,
        scan=scan,
        documents=tuple(documents),
        issues=tuple(issues),
        link_count=link_count,
    )


def _structured_snapshot_sha256(
    bindings: Iterable[tuple[str, str]],
) -> str:
    payload = [
        {"path": path, "sha256": sha256}
        for path, sha256 in sorted(bindings)
    ]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def structured_forest_snapshot_sha256(
    root: str | os.PathLike[str],
    *,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> str:
    inspection = inspect_forest(root, audit_links=False, limits=limits)
    if not inspection.ok:
        raise MemoryForestError(
            "structured_snapshot_invalid",
            "The current Structured forest must validate before it can be bound.",
        )
    return _structured_snapshot_sha256(
        (document.route.path, document.sha256)
        for document in inspection.documents
        if 1 <= document.route.layer.number <= 4
    )


def validate_forest(
    root: str | os.PathLike[str],
    *,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    return inspect_forest(root, audit_links=False, limits=limits).report("validate")


def audit_forest(
    root: str | os.PathLike[str],
    *,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    return inspect_forest(root, audit_links=True, limits=limits).report("audit")


def doctor_forest(root: str | os.PathLike[str]) -> dict[str, object]:
    root_path = require_real_root(root)
    state_path = root_path / STATE_DIRECTORY
    config_path = state_path / CONFIG_FILENAME
    index_path = state_path / "index.sqlite3"
    fts5 = True
    try:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(value)")
        finally:
            connection.close()
    except sqlite3.Error:
        fts5 = False
    state_private = _mode_is(state_path, 0o700, directory=True)
    config_private = _mode_is(config_path, 0o600, directory=False)
    checks = {
        "config_present": config_path.is_file() and not config_path.is_symlink(),
        "config_private": config_private,
        "fts5_available": fts5,
        "index_present": index_path.is_file() and not index_path.is_symlink(),
        "network_required": False,
        "root_private": stat.S_IMODE(root_path.stat().st_mode) == 0o700,
        "state_private": state_private,
    }
    required = (
        "config_present",
        "config_private",
        "fts5_available",
        "root_private",
        "state_private",
    )
    return {
        "checks": checks,
        "ok": all(bool(checks[key]) for key in required),
        "operation": "doctor",
        "root": str(root_path),
        "schema_version": SCHEMA_VERSION,
    }


def load_retrieval_config(
    root: str | os.PathLike[str],
    *,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> RetrievalConfig:
    root_path = require_real_root(root)
    state_path = root_path / STATE_DIRECTORY
    config_path = state_path / CONFIG_FILENAME
    if not _mode_is(state_path, 0o700, directory=True):
        raise MemoryForestError(
            "state_permissions",
            "The derived-state directory must exist with mode 0700.",
            details={"path": STATE_DIRECTORY},
        )
    if not _mode_is(config_path, 0o600, directory=False):
        raise MemoryForestError(
            "not_initialized",
            "The forest configuration is missing or not private.",
            details={"path": f"{STATE_DIRECTORY}/{CONFIG_FILENAME}"},
        )
    try:
        parsed = _load_strict_json(_read_utf8_file(config_path, limits=limits))
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise MemoryForestError(
            "invalid_config",
            "The forest configuration is not valid JSON.",
            details={"path": f"{STATE_DIRECTORY}/{CONFIG_FILENAME}"},
        ) from exc
    return _parse_forest_config(parsed)


def load_forest_identity(
    root: str | os.PathLike[str],
    *,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> str:
    root_path = require_real_root(root)
    state_path = root_path / STATE_DIRECTORY
    config_path = state_path / CONFIG_FILENAME
    if not _mode_is(state_path, 0o700, directory=True):
        raise MemoryForestError(
            "state_permissions",
            "The derived-state directory must exist with mode 0700.",
            details={"path": STATE_DIRECTORY},
        )
    if not _mode_is(config_path, 0o600, directory=False):
        raise MemoryForestError(
            "not_initialized",
            "The forest configuration is missing or not private.",
            details={"path": f"{STATE_DIRECTORY}/{CONFIG_FILENAME}"},
        )
    try:
        parsed = _load_strict_json(_read_utf8_file(config_path, limits=limits))
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise MemoryForestError(
            "invalid_config",
            "The forest configuration is not valid JSON.",
            details={"path": f"{STATE_DIRECTORY}/{CONFIG_FILENAME}"},
        ) from exc
    _parse_forest_config(parsed)
    assert isinstance(parsed, dict)
    forest_id = parsed.get("forest_id")
    if not isinstance(forest_id, str):
        raise MemoryForestError(
            "forest_identity_missing",
            "This forest predates write identities; canonical writes require a "
            "forest_id created by a supported migration or new initialization.",
            details={"path": f"{STATE_DIRECTORY}/{CONFIG_FILENAME}"},
        )
    return forest_id


def _empty_documents() -> dict[str, str]:
    return {
        "01 xltm/XLTM.md": (
            "# Memory Forest\n\n"
            "Add a domain pointer only after creating its LTM owner.\n"
        )
    }


def _example_documents() -> dict[str, str]:
    return {
        "00 life_archive/field-season-2042.md": (
            "# Field season 2042\n\n"
            "A fictional observatory trial established a reusable calibration checklist.\n\n"
            "Root map: [[../01 xltm/XLTM.md]]\n\n"
            "Provenance source: 04 stm/research-notes/observatory-trial/"
            "instrument-calibration.md\n"
        ),
        "01 xltm/XLTM.md": (
            "# Memory Forest\n\n"
            "## Domains\n\n"
            "- [[../02 ltm/mission-operations_LTM.md]]\n"
            "- [[../02 ltm/research-notes_LTM.md]]\n"
        ),
        "02 ltm/mission-operations_LTM.md": (
            "# Mission operations\n\n"
            "Parent: [[../01 xltm/XLTM.md]]\n\n"
            "Active branch: [[../03 mtm/mission-operations/recovery-drill.md]]\n"
        ),
        "02 ltm/research-notes_LTM.md": (
            "# Research notes\n\n"
            "Parent: [[../01 xltm/XLTM.md]]\n\n"
            "Active branch: [[../03 mtm/research-notes/observatory-trial.md]]\n"
        ),
        "03 mtm/mission-operations/recovery-drill.md": (
            "# Recovery drill\n\n"
            "Parent: [[../../02 ltm/mission-operations_LTM.md]]\n\n"
            "Detail: [[../../04 stm/mission-operations/recovery-drill/"
            "telemetry-replay.md]]\n"
        ),
        "03 mtm/research-notes/observatory-trial.md": (
            "# Observatory trial\n\n"
            "Parent: [[../../02 ltm/research-notes_LTM.md]]\n\n"
            "Detail: [[../../04 stm/research-notes/observatory-trial/"
            "instrument-calibration.md]]\n"
        ),
        "04 stm/mission-operations/recovery-drill/telemetry-replay.md": (
            "# Telemetry replay\n\n"
            "Parent: [[../../../03 mtm/mission-operations/recovery-drill.md]]\n\n"
            "The fictional crew replays buffered telemetry before a recovery decision.\n\n"
            "Synthetic retrieval cues: 임무 복구, ミッション復旧, "
            "استعادة المهمة, résumé opérationnel.\n\n"
            "Daily evidence: [[../../../05 daily/2042-04-12.md]]\n"
        ),
        "04 stm/research-notes/observatory-trial/instrument-calibration.md": (
            "# Instrument calibration\n\n"
            "Parent: [[../../../03 mtm/research-notes/observatory-trial.md]]\n\n"
            "The fictional trial checks a reference lamp before each observation window.\n\n"
            "Daily evidence: [[../../../05 daily/2042-04-12.md]]\n"
        ),
        "05 daily/2042-04-12.md": (
            "# Daily source, 2042-04-12\n\n"
            "Mission detail: [[../04 stm/mission-operations/recovery-drill/"
            "telemetry-replay.md]]\n\n"
            "Research detail: [[../04 stm/research-notes/observatory-trial/"
            "instrument-calibration.md]]\n\n"
            "Raw event: [[../06 istm/events.jsonl]]\n"
        ),
        "06 istm/events.jsonl": (
            '{"captured_at":"2042-04-12T08:30:00Z",'
            '"event":"instrument calibration completed",'
            '"source":"synthetic sensor log"}\n'
        ),
    }


def _secure_parent_directories(root: Path, parent: Path) -> None:
    cursor = root
    for part in parent.relative_to(root).parts:
        cursor = cursor / part
        secure_mkdir(cursor)


def _state_issues(root: Path, *, limits: ForestLimits) -> list[Issue]:
    state = root / STATE_DIRECTORY
    config = state / CONFIG_FILENAME
    issues: list[Issue] = []
    if not _mode_is(state, 0o700, directory=True):
        issues.append(
            Issue(
                code="state_permissions",
                level="error",
                message="The derived-state directory must exist with mode 0700.",
                path=STATE_DIRECTORY,
            )
        )
        return issues
    try:
        entries = sorted(os.scandir(state), key=lambda entry: entry.name)
    except OSError:
        return [
            Issue(
                code="state_unreadable",
                level="error",
                message="The derived-state directory could not be inspected.",
                path=STATE_DIRECTORY,
            )
        ]
    for entry in entries:
        info = entry.stat(follow_symlinks=False)
        if entry.name == RECEIPTS_DIRECTORY:
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                issues.append(
                    Issue(
                        code="unsafe_receipts_directory",
                        level="error",
                        message="The receipts directory must be a real private directory.",
                        path=f"{STATE_DIRECTORY}/{RECEIPTS_DIRECTORY}",
                    )
                )
                continue
            with os.scandir(entry.path) as iterator:
                receipt_entries = sorted(iterator, key=lambda child: child.name)
            if len(receipt_entries) > MAX_RECEIPTS:
                issues.append(
                    Issue(
                        code="receipt_count_exceeded",
                        level="error",
                        message="The receipts directory exceeds the supported entry limit.",
                        path=f"{STATE_DIRECTORY}/{RECEIPTS_DIRECTORY}",
                    )
                )
            for receipt_entry in receipt_entries[: MAX_RECEIPTS + 1]:
                receipt_info = receipt_entry.stat(follow_symlinks=False)
                if (
                    RECEIPT_NAME_RE.fullmatch(receipt_entry.name) is None
                    or stat.S_ISLNK(receipt_info.st_mode)
                    or not stat.S_ISREG(receipt_info.st_mode)
                    or stat.S_IMODE(receipt_info.st_mode) != 0o600
                    or receipt_info.st_size > MAX_RECEIPT_BYTES
                ):
                    issues.append(
                        Issue(
                            code="unsafe_receipt_entry",
                            level="error",
                            message=(
                                "Receipt entries must be bounded private transaction "
                                "JSON files."
                            ),
                            path=(
                                f"{STATE_DIRECTORY}/{RECEIPTS_DIRECTORY}/"
                                f"{receipt_entry.name}"
                            ),
                        )
                    )
            continue
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            issues.append(
                Issue(
                    code="state_file_permissions",
                    level="error",
                    message="Derived-state entries must be regular files with mode 0600.",
                    path=f"{STATE_DIRECTORY}/{entry.name}",
                )
            )
    if not _mode_is(config, 0o600, directory=False):
        issues.append(
            Issue(
                code="not_initialized",
                level="error",
                message="The forest configuration is missing or not private.",
                path=f"{STATE_DIRECTORY}/{CONFIG_FILENAME}",
            )
        )
        return issues
    try:
        parsed = _load_strict_json(_read_utf8_file(config, limits=limits))
    except (MemoryForestError, json.JSONDecodeError, RecursionError, ValueError):
        issues.append(
            Issue(
                code="invalid_config",
                level="error",
                message="The forest configuration is not valid JSON.",
                path=f"{STATE_DIRECTORY}/{CONFIG_FILENAME}",
            )
        )
        return issues
    try:
        _parse_forest_config(parsed)
    except MemoryForestError as exc:
        issues.append(
            Issue(
                code=exc.code,
                level="error",
                message=exc.message,
                path=f"{STATE_DIRECTORY}/{CONFIG_FILENAME}",
            )
        )
    return issues


def _parse_forest_config(value: object) -> RetrievalConfig:
    if not isinstance(value, dict):
        raise MemoryForestError(
            "config_mismatch",
            "The forest configuration does not match this schema.",
        )
    required = {
        "layout": "layer/domain/branch/leaf",
        "layers": list(LAYER_DIRECTORY_NAMES),
        "schema_version": SCHEMA_VERSION,
    }
    forest_id = value.get("forest_id")
    invalid_forest_id = forest_id is not None and (
        not isinstance(forest_id, str) or FOREST_ID_RE.fullmatch(forest_id) is None
    )
    if (
        type(value.get("schema_version")) is not int
        or invalid_forest_id
        or any(value.get(key) != expected for key, expected in required.items())
    ):
        raise MemoryForestError(
            "config_mismatch",
            "The forest configuration does not match this schema.",
        )
    if set(value) - {*required, "forest_id", "retrieval"}:
        raise MemoryForestError(
            "config_mismatch",
            "The forest configuration contains unsupported fields.",
        )
    if "retrieval" not in value:
        return RetrievalConfig()
    retrieval = value["retrieval"]
    if not isinstance(retrieval, dict) or set(retrieval) != {"query_plan"}:
        raise MemoryForestError(
            "invalid_retrieval_config",
            "The optional retrieval configuration must contain only query_plan.",
        )
    query_plan = retrieval["query_plan"]
    if not isinstance(query_plan, dict) or set(query_plan) != {"max_probes"}:
        raise MemoryForestError(
            "invalid_retrieval_config",
            "The query_plan configuration must contain only max_probes.",
        )
    max_probes = query_plan["max_probes"]
    if (
        isinstance(max_probes, bool)
        or not isinstance(max_probes, int)
        or max_probes < 1
        or max_probes > MAX_QUERY_PLAN_PROBES
    ):
        raise MemoryForestError(
            "invalid_retrieval_config",
            "query_plan.max_probes must be an integer from 1 through 16.",
        )
    return RetrievalConfig(query_plan_max_probes=max_probes)


def _parent_chain_issues(documents: list[Document]) -> list[Issue]:
    paths = {document.route.path for document in documents}
    issues: list[Issue] = []
    root_map = "01 xltm/XLTM.md"
    if root_map not in paths:
        issues.append(
            Issue(
                code="missing_root_map",
                level="error",
                message="Every forest requires the canonical XLTM root map.",
                path=root_map,
            )
        )
    for document in documents:
        route = document.route
        required: list[str] = []
        if route.layer.name in {"ltm", "mtm", "stm"}:
            required.append(root_map)
        if route.layer.name in {"mtm", "stm"} and route.domain:
            required.append(f"02 ltm/{route.domain}_LTM.md")
        if route.layer.name == "stm" and route.domain and route.branch:
            required.append(f"03 mtm/{route.domain}/{route.branch}.md")
        for parent in required:
            if parent not in paths:
                issues.append(
                    Issue(
                        code="missing_parent",
                        level="error",
                        message="A structured document is missing a canonical parent owner.",
                        path=route.path,
                        target=parent,
                    )
                )
    return issues


def _validate_jsonl(body: str, *, path: str) -> None:
    seen = 0
    for line_number, line in enumerate(body.splitlines(), start=1):
        if not line.strip():
            continue
        seen += 1
        try:
            value = json.loads(
                line,
                parse_constant=_reject_json_constant,
                parse_float=_parse_json_float,
                parse_int=_parse_json_int,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise MemoryForestError(
                "invalid_jsonl",
                "An ISTM event line is not valid JSON.",
                details={"line": line_number, "path": path},
            ) from exc
        if not isinstance(value, dict):
            raise MemoryForestError(
                "invalid_jsonl_event",
                "Each ISTM event line must be a JSON object.",
                details={"line": line_number, "path": path},
            )
    if seen == 0:
        raise MemoryForestError(
            "empty_jsonl",
            "An ISTM event stream must contain at least one JSON object.",
            details={"path": path},
        )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_strict_json(body: str) -> object:
    return json.loads(
        body,
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_json_constant,
        parse_float=_parse_json_float,
        parse_int=_parse_json_int,
    )


def _parse_json_int(value: str) -> int:
    if len(value.removeprefix("-")) > 256:
        raise ValueError("JSON integer exceeds the 256-digit limit")
    return int(value)


def _parse_json_float(value: str) -> float:
    if len(value) > 256:
        raise ValueError("JSON float exceeds the 256-character limit")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON numbers must be finite")
    return parsed


def _read_utf8_file(path: Path, *, limits: ForestLimits) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MemoryForestError(
            "file_open_failed",
            "A forest file could not be opened safely.",
            details={"reason": exc.__class__.__name__},
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise MemoryForestError(
                "non_regular_file",
                "Memory documents must be regular files.",
            )
        if info.st_size > limits.max_file_bytes:
            raise MemoryForestError(
                "file_too_large",
                "A forest file exceeds the configured per-file byte limit.",
                details={"limit": limits.max_file_bytes, "size": info.st_size},
            )
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(limits.max_file_bytes + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > limits.max_file_bytes:
        raise MemoryForestError(
            "file_too_large",
            "A forest file grew beyond the configured byte limit while reading.",
            details={"limit": limits.max_file_bytes},
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MemoryForestError(
            "invalid_utf8",
            "Memory documents must contain valid UTF-8 text.",
        ) from exc


def _extract_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()[:300]
    return fallback


def _body_without_code_fences(body: str) -> str:
    kept: list[str] = []
    fence: str | None = None
    for line in body.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            fence = None if fence == marker else marker if fence is None else fence
            kept.append("\n")
            continue
        kept.append(line if fence is None else "\n")
    return "".join(kept)


def _wikilinks(body: str) -> list[tuple[str, int]]:
    searchable = _body_without_code_fences(body)
    return [
        (match.group(1).strip(), searchable.count("\n", 0, match.start()) + 1)
        for match in WIKILINK_RE.finditer(searchable)
    ]


def _audit_wikilinks(
    documents: list[Document],
    *,
    limits: ForestLimits,
) -> tuple[list[Issue], int]:
    by_path = {document.route.path: document for document in documents}
    issues: list[Issue] = []
    link_count = 0
    for source in documents:
        if not source.route.path.endswith(".md"):
            continue
        resolved_targets: set[str] = set()
        for raw_target, line in _wikilinks(source.body):
            link_count += 1
            if link_count > limits.max_links:
                raise MemoryForestError(
                    "link_count_exceeded",
                    "The forest exceeds the configured wikilink limit.",
                    details={"count": link_count, "limit": limits.max_links},
                )
            target_text = raw_target.split("|", 1)[0].split("#", 1)[0].strip()
            if not target_text:
                issues.append(
                    Issue(
                        code="empty_wikilink",
                        level="error",
                        line=line,
                        message="A wikilink has no target.",
                        path=source.route.path,
                        target=raw_target,
                    )
                )
                continue
            if "/" not in target_text:
                issues.append(
                    Issue(
                        code="bare_wikilink",
                        level="error",
                        line=line,
                        message="Wikilinks must use an explicit path-qualified target.",
                        path=source.route.path,
                        target=target_text,
                    )
                )
                continue
            resolved = _resolve_wikilink(source, target_text, by_path)
            if isinstance(resolved, Issue):
                issues.append(
                    Issue(
                        code=resolved.code,
                        level=resolved.level,
                        line=line,
                        message=resolved.message,
                        path=source.route.path,
                        target=target_text,
                    )
                )
                continue
            resolved_targets.add(resolved.route.path)
            if resolved.route.path == source.route.path:
                issues.append(
                    Issue(
                        code="self_wikilink",
                        level="error",
                        line=line,
                        message="A document may not link to itself.",
                        path=source.route.path,
                        target=target_text,
                    )
                )
            elif resolved.route.layer.number == source.route.layer.number:
                issues.append(
                    Issue(
                        code="same_layer_wikilink",
                        level="error",
                        line=line,
                        message="Canonical wikilinks may not connect documents in the same layer.",
                        path=source.route.path,
                        target=resolved.route.path,
                    )
                )
            elif not layers_are_adjacent(source.route.layer, resolved.route.layer):
                issues.append(
                    Issue(
                        code="non_adjacent_wikilink",
                        level="error",
                        line=line,
                        message="Canonical wikilinks may connect adjacent layers only.",
                        path=source.route.path,
                        target=resolved.route.path,
                    )
                )
        required_parent = immediate_parent_path(source.route)
        if (
            required_parent is not None
            and required_parent in by_path
            and required_parent not in resolved_targets
        ):
            issues.append(
                Issue(
                    code="missing_parent_wikilink",
                    level="error",
                    message=(
                        "A structured document must link to its canonical immediate parent."
                    ),
                    path=source.route.path,
                    target=required_parent,
                )
            )
    return issues, link_count


def _resolve_wikilink(
    source: Document,
    target: str,
    by_path: dict[str, Document],
) -> Document | Issue:
    normalized_target = target.replace("\\", "/")
    if normalized_target != target or normalized_target.startswith("/"):
        return Issue(
            code="wikilink_path_escape",
            level="error",
            message="Absolute or backslash wikilink targets are forbidden.",
        )
    target_path = PurePosixPath(normalized_target)
    root_qualified = bool(
        target_path.parts and target_path.parts[0] in LAYER_DIRECTORY_NAMES
    )
    if root_qualified:
        candidate = posixpath.normpath(normalized_target)
    else:
        source_parent = PurePosixPath(source.route.path).parent.as_posix()
        candidate = posixpath.normpath(posixpath.join(source_parent, normalized_target))
    if candidate == ".." or candidate.startswith("../") or candidate.startswith("/"):
        return Issue(
            code="wikilink_path_escape",
            level="error",
            message="A wikilink target escapes the selected forest root.",
        )
    candidates = [candidate]
    if not PurePosixPath(candidate).suffix:
        candidates.append(candidate + ".md")
    matches = {by_path[path].route.path: by_path[path] for path in candidates if path in by_path}
    if not matches:
        return Issue(
            code="missing_wikilink",
            level="error",
            message="A wikilink target could not be resolved.",
        )
    if len(matches) > 1:
        return Issue(
            code="ambiguous_wikilink",
            level="error",
            message="A wikilink target resolves to more than one document.",
        )
    return next(iter(matches.values()))


def _mode_is(path: Path, mode: int, *, directory: bool) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    return (
        not stat.S_ISLNK(info.st_mode)
        and expected_type(info.st_mode)
        and stat.S_IMODE(info.st_mode) == mode
    )


def _issue_sort_key(issue: Issue) -> tuple[Any, ...]:
    return (
        issue.path or "",
        issue.line if issue.line is not None else -1,
        issue.code,
        issue.target or "",
        issue.message,
    )
