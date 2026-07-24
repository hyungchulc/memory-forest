from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, NoReturn

from .core import (
    MAX_QUERY_PLAN_PROBES,
    _structured_snapshot_sha256,
    load_forest_identity,
    load_retrieval_config,
    structured_forest_snapshot_sha256,
)
from .errors import MemoryForestError
from .index import (
    INDEX_SCHEMA_VERSION,
    _literal_fts_query,
    _read_current_indexed_body,
    _secure_index_path,
    _verify_schema,
)
from .model import SCHEMA_VERSION, Route, immediate_parent_path, parse_relative_route
from .safety import DEFAULT_LIMITS, ForestLimits, require_real_root


QUERY_PLAN_SCHEMA_VERSION: Final[int] = 1
MAX_QUERY_PLAN_BYTES: Final[int] = 32 * 1024
MAX_QUERY_CHARS: Final[int] = 1_000
MAX_CONTEXT_DOCUMENTS: Final[int] = 32
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class QueryPlan:
    probes: tuple[str, ...]
    schema_version: int = QUERY_PLAN_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class _Node:
    document_id: int
    parent_path: str | None
    route: Route
    title: str
    sha256: str
    size: int
    mtime_ns: int

    def as_metadata(self) -> dict[str, object]:
        return {
            "mtime_ns": self.mtime_ns,
            "route": {
                "branch": self.route.branch,
                "domain": self.route.domain,
                "layer": {
                    "name": self.route.layer.name,
                    "number": self.route.layer.number,
                },
                "leaf": self.route.leaf,
                "path": self.route.path,
                "route_key": self.route.route_key,
            },
            "sha256": self.sha256,
            "size": self.size,
            "title": self.title,
        }


def read_query_plan_source(
    source: str,
    *,
    stdin: BinaryIO | None = None,
) -> object:
    if source == "-":
        if stdin is None:
            raise MemoryForestError(
                "query_plan_input_unavailable",
                "The QueryPlan source '-' requires a binary standard-input stream.",
            )
        payload = stdin.read(MAX_QUERY_PLAN_BYTES + 1)
    else:
        path = Path(os.path.abspath(os.path.expanduser(source)))
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise MemoryForestError(
                "query_plan_not_found",
                "The selected QueryPlan file does not exist.",
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise MemoryForestError(
                "unsafe_query_plan_source",
                "The QueryPlan source must be a regular non-symlink file.",
            )
        if info.st_size > MAX_QUERY_PLAN_BYTES:
            raise MemoryForestError(
                "query_plan_too_large",
                "The QueryPlan exceeds the 32768-byte limit.",
                details={"limit": MAX_QUERY_PLAN_BYTES, "size": info.st_size},
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise MemoryForestError(
                "query_plan_open_failed",
                "The QueryPlan file could not be opened safely.",
                details={"reason": exc.__class__.__name__},
            ) from exc
        try:
            opened_info = os.fstat(descriptor)
            if not stat.S_ISREG(opened_info.st_mode):
                raise MemoryForestError(
                    "unsafe_query_plan_source",
                    "The QueryPlan source must be a regular non-symlink file.",
                )
            if (opened_info.st_dev, opened_info.st_ino) != (info.st_dev, info.st_ino):
                raise MemoryForestError(
                    "query_plan_changed",
                    "The QueryPlan source changed while it was being opened.",
                )
            if opened_info.st_size > MAX_QUERY_PLAN_BYTES:
                raise MemoryForestError(
                    "query_plan_too_large",
                    "The QueryPlan exceeds the 32768-byte limit.",
                    details={
                        "limit": MAX_QUERY_PLAN_BYTES,
                        "size": opened_info.st_size,
                    },
                )
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                payload = handle.read(MAX_QUERY_PLAN_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    if len(payload) > MAX_QUERY_PLAN_BYTES:
        raise MemoryForestError(
            "query_plan_too_large",
            "The QueryPlan exceeds the 32768-byte limit.",
            details={"limit": MAX_QUERY_PLAN_BYTES},
        )
    return decode_query_plan_json(payload)


def decode_query_plan_json(payload: str | bytes) -> object:
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MemoryForestError(
                "invalid_query_plan",
                "The QueryPlan must be valid UTF-8 JSON.",
            ) from exc
    else:
        text = payload
    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise MemoryForestError(
            "invalid_query_plan",
            "The QueryPlan must be valid UTF-8 JSON.",
        ) from exc
    if encoded_size > MAX_QUERY_PLAN_BYTES:
        raise MemoryForestError(
            "query_plan_too_large",
            "The QueryPlan exceeds the 32768-byte limit.",
            details={"limit": MAX_QUERY_PLAN_BYTES},
        )
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise MemoryForestError(
            "invalid_query_plan",
            "The QueryPlan must be strict JSON with unique object keys.",
        ) from exc


def validate_query_plan(
    value: object,
    *,
    max_probes: int,
) -> QueryPlan:
    if max_probes < 1 or max_probes > MAX_QUERY_PLAN_PROBES:
        raise ValueError("max_probes is outside the supported range")
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "probes"}:
        raise MemoryForestError(
            "invalid_query_plan",
            "A QueryPlan must contain only schema_version and probes.",
        )
    version = value["schema_version"]
    if type(version) is not int or version != QUERY_PLAN_SCHEMA_VERSION:
        raise MemoryForestError(
            "unsupported_query_plan_schema",
            "The QueryPlan schema version is unsupported.",
            details={"expected": QUERY_PLAN_SCHEMA_VERSION},
        )
    raw_probes = value["probes"]
    if not isinstance(raw_probes, list) or not 1 <= len(raw_probes) <= max_probes:
        raise MemoryForestError(
            "invalid_query_plan",
            "QueryPlan probes must be a non-empty list within the configured limit.",
            details={"maximum": max_probes},
        )
    probes: list[str] = []
    seen: set[str] = set()
    for position, raw_probe in enumerate(raw_probes):
        if not isinstance(raw_probe, Mapping) or set(raw_probe) != {"query"}:
            raise MemoryForestError(
                "invalid_query_plan_probe",
                "Each QueryPlan probe must contain only a query string.",
                details={"position": position},
            )
        query = raw_probe["query"]
        if (
            not isinstance(query, str)
            or not query
            or query != query.strip()
            or len(query) > MAX_QUERY_CHARS
            or _contains_unsafe_unicode(query)
            or unicodedata.normalize("NFC", query) != query
        ):
            raise MemoryForestError(
                "invalid_query_plan_probe",
                "Probe queries must be trimmed NFC text without control characters.",
                details={"position": position},
            )
        _literal_fts_query(query)
        key = query.casefold()
        if key in seen:
            raise MemoryForestError(
                "duplicate_query_plan_probe",
                "QueryPlan probe queries must be unique.",
                details={"position": position},
            )
        seen.add(key)
        probes.append(query)
    return QueryPlan(probes=tuple(probes))


def retrieve_index(
    root: str | os.PathLike[str],
    query: str,
    *,
    query_plan: object | QueryPlan | None = None,
    limit: int = 10,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    if limit < 1 or limit > limits.max_results:
        raise MemoryForestError(
            "invalid_limit",
            "The result limit is outside the permitted range.",
            details={"limit": limit, "maximum": limits.max_results},
        )
    _literal_fts_query(query)
    config = load_retrieval_config(root, limits=limits)
    if query_plan is None:
        plan = None
    elif isinstance(query_plan, QueryPlan):
        plan = validate_query_plan(
            {
                "schema_version": query_plan.schema_version,
                "probes": [{"query": probe} for probe in query_plan.probes],
            },
            max_probes=config.query_plan_max_probes,
        )
    else:
        plan = validate_query_plan(
            query_plan,
            max_probes=config.query_plan_max_probes,
        )
    probes = [query]
    seen_queries = {unicodedata.normalize("NFC", query).casefold()}
    if plan is not None:
        for probe in plan.probes:
            key = probe.casefold()
            if key not in seen_queries:
                probes.append(probe)
                seen_queries.add(key)

    root_path = require_real_root(root)
    index_path = _secure_index_path(root_path)
    uri = index_path.as_uri() + "?mode=ro&immutable=1"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        _verify_schema(connection)
        nodes = _load_structured_nodes(connection)
        match_scores, matched_probes = _rank_matches(
            connection,
            probes,
            limit=limit,
            limits=limits,
        )
    except MemoryForestError:
        raise
    except sqlite3.Error as exc:
        raise MemoryForestError(
            "query_failed",
            "The local SQLite FTS5 index could not be queried.",
            details={"reason": exc.__class__.__name__},
        ) from exc
    finally:
        if connection is not None:
            connection.close()

    candidates = _candidate_trails(
        nodes,
        match_scores,
        original_match_ids={
            document_id
            for document_id, positions in matched_probes.items()
            if 0 in positions
        },
        limit=limit,
        limits=limits,
    )
    ranked: list[tuple[bool, float, tuple[_Node, ...]]] = []
    for chain in candidates:
        original_query_matched = any(
            0 in matched_probes.get(node.document_id, set()) for node in chain
        )
        score = sum(match_scores.get(node.document_id, 0.0) for node in chain)
        ranked.append((original_query_matched, round(score, 8), chain))
    ranked.sort(
        key=lambda item: (
            -int(item[0]),
            -item[1],
            -len(item[2]),
            tuple(node.route.path for node in item[2]),
        )
    )
    selected = ranked[:limit]
    validated_ids: set[int] = set()
    for _, _, chain in selected:
        for node in chain:
            if node.document_id in validated_ids:
                continue
            _read_current_indexed_body(
                root_path,
                node.route.path,
                node.sha256,
                limits=limits,
            )
            validated_ids.add(node.document_id)

    trails: list[dict[str, object]] = []
    for original_query_matched, score, chain in selected:
        matched_layers = [
            node.route.layer.name for node in chain if node.document_id in match_scores
        ]
        query_plan_probe_positions = {
            position
            for node in chain
            for position in matched_probes.get(node.document_id, set())
            if position > 0
        }
        trails.append(
            {
                "complete": chain[-1].route.layer.name == "stm",
                "depth": len(chain),
                "matched_layers": matched_layers,
                "matched_query_plan_probe_count": len(query_plan_probe_positions),
                "original_query_matched": original_query_matched,
                "relationships": [
                    {
                        "from_index": position,
                        "to_index": position + 1,
                        "type": "canonical_parent_child",
                    }
                    for position in range(len(chain) - 1)
                ],
                "score": {
                    "higher_is_better_within_tier": True,
                    "method": (
                        "original_query_tier_then_weighted_reciprocal_rank_sum"
                    ),
                    "rank_tier": (
                        "original_query_match"
                        if original_query_matched
                        else "query_plan_only"
                    ),
                    "value": score,
                },
                "trail": [node.as_metadata() for node in chain],
                "validated": True,
            }
        )
    return {
        "count": len(trails),
        "limit": limit,
        "ok": True,
        "operation": "retrieve",
        "query": query,
        "query_plan": {
            "accepted_probe_count": len(plan.probes) if plan is not None else 0,
            "effective_probe_count": len(probes) - 1,
            "provided": plan is not None,
            "schema_version": QUERY_PLAN_SCHEMA_VERSION,
        },
        "retrieval": {
            "index_schema_version": int(INDEX_SCHEMA_VERSION),
            "layers": ["xltm", "ltm", "mtm", "stm"],
            "method": "deterministic_root_first_trails_v1",
        },
        "schema_version": SCHEMA_VERSION,
        "trails": trails,
    }


def structured_context_index(
    root: str | os.PathLike[str],
    query: str,
    *,
    limit: int = 3,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    root_path = require_real_root(root)
    if limit < 1 or limit > 10:
        raise MemoryForestError(
            "invalid_limit",
            "Structured context limit must be between 1 and 10.",
            details={"limit": limit},
        )
    retrieved = retrieve_index(root_path, query, limit=limit, limits=limits)
    paths: dict[str, dict[str, object]] = {}
    raw_trails = retrieved.get("trails")
    if not isinstance(raw_trails, list):
        raise MemoryForestError(
            "context_source_invalid",
            "The Structured context source has an invalid trail collection.",
        )
    for trail in raw_trails:
        assert isinstance(trail, dict)
        nodes = trail["trail"]
        assert isinstance(nodes, list)
        for node in nodes:
            assert isinstance(node, dict)
            route = node["route"]
            assert isinstance(route, dict)
            path = route["path"]
            assert isinstance(path, str)
            paths.setdefault(path, node)

    index_path = _secure_index_path(root_path)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        _verify_schema(connection)
        nodes = _load_structured_nodes(connection)
    except sqlite3.Error as exc:
        raise MemoryForestError(
            "query_failed",
            "The local SQLite FTS5 index could not provide Structured context.",
            details={"reason": exc.__class__.__name__},
        ) from exc
    finally:
        if connection is not None:
            connection.close()
    root_node = next(
        node for node in nodes.values() if node.route.layer.name == "xltm"
    )
    forest_snapshot_sha256 = structured_forest_snapshot_sha256(
        root_path,
        limits=limits,
    )
    indexed_snapshot_sha256 = _structured_snapshot_sha256(
        (node.route.path, node.sha256) for node in nodes.values()
    )
    if indexed_snapshot_sha256 != forest_snapshot_sha256:
        raise MemoryForestError(
            "index_stale",
            "The local index no longer matches the canonical forest; rebuild the index.",
            details={"action": "memory-forest index ROOT"},
        )
    paths.setdefault(root_node.route.path, root_node.as_metadata())
    if len(paths) > MAX_CONTEXT_DOCUMENTS:
        raise MemoryForestError(
            "context_too_large",
            "The selected Structured context exceeds its document bound.",
            details={"limit": MAX_CONTEXT_DOCUMENTS},
        )

    documents: list[dict[str, object]] = []
    for path in sorted(
        paths,
        key=lambda value: (
            parse_relative_route(value).layer.number,
            value,
        ),
    ):
        metadata = paths[path]
        sha256 = metadata["sha256"]
        assert isinstance(sha256, str)
        body = _read_current_indexed_body(
            root_path,
            path,
            sha256,
            limits=limits,
        )
        route = metadata["route"]
        assert isinstance(route, dict)
        layer = route["layer"]
        assert isinstance(layer, dict)
        layer_name = layer["name"]
        assert isinstance(layer_name, str)
        semantic_route = {
            "branch": route["branch"] if layer_name in {"mtm", "stm"} else None,
            "layer": layer,
            "leaf": route["leaf"] if layer_name == "stm" else None,
            "path": route["path"],
            "route_key": route["route_key"],
            "tree": route["domain"] if layer_name != "xltm" else None,
        }
        documents.append({**metadata, "route": semantic_route, "body": body})

    canonical_documents = json.dumps(
        documents,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "documents": documents,
        "forest_id": load_forest_identity(root_path, limits=limits),
        "forest_snapshot_sha256": forest_snapshot_sha256,
        "ok": True,
        "operation": "structured-context",
        "query": query,
        "schema_version": SCHEMA_VERSION,
        "snapshot_sha256": hashlib.sha256(canonical_documents).hexdigest(),
        "trail_count": retrieved["count"],
    }


def _load_structured_nodes(connection: sqlite3.Connection) -> dict[int, _Node]:
    rows = connection.execute(
        "SELECT id, path, parent_path, route_key, layer_number, layer, domain, "
        "branch, leaf, "
        "title, sha256, size, mtime_ns FROM documents "
        "WHERE layer_number BETWEEN 1 AND 4 ORDER BY path ASC"
    ).fetchall()
    nodes: dict[int, _Node] = {}
    for row in rows:
        try:
            route = parse_relative_route(row["path"])
        except MemoryForestError as exc:
            raise MemoryForestError(
                "index_corrupt",
                "The local index contains an invalid structured route; rebuild it.",
                details={"action": "memory-forest index ROOT"},
            ) from exc
        expected = (
            immediate_parent_path(route),
            route.route_key,
            route.layer.number,
            route.layer.name,
            route.domain,
            route.branch,
            route.leaf,
        )
        received = (
            row["parent_path"],
            row["route_key"],
            row["layer_number"],
            row["layer"],
            row["domain"],
            row["branch"],
            row["leaf"],
        )
        if (
            expected != received
            or not isinstance(row["id"], int)
            or not isinstance(row["title"], str)
            or not isinstance(row["sha256"], str)
            or _SHA256_RE.fullmatch(row["sha256"]) is None
            or not isinstance(row["size"], int)
            or row["size"] < 0
            or not isinstance(row["mtime_ns"], int)
        ):
            raise MemoryForestError(
                "index_corrupt",
                "The local index contains inconsistent route metadata; rebuild it.",
                details={"action": "memory-forest index ROOT"},
            )
        nodes[row["id"]] = _Node(
            document_id=row["id"],
            parent_path=row["parent_path"],
            route=route,
            title=row["title"],
            sha256=row["sha256"],
            size=row["size"],
            mtime_ns=row["mtime_ns"],
        )
    roots = [node for node in nodes.values() if node.route.layer.name == "xltm"]
    if len(roots) != 1:
        raise MemoryForestError(
            "index_corrupt",
            "The local index must contain exactly one XLTM root; rebuild it.",
            details={"action": "memory-forest index ROOT"},
        )
    return nodes


def _rank_matches(
    connection: sqlite3.Connection,
    probes: list[str],
    *,
    limit: int,
    limits: ForestLimits,
) -> tuple[dict[int, float], dict[int, set[int]]]:
    scores: dict[int, float] = {}
    matched_probes: dict[int, set[int]] = {}
    row_limit = min(limits.max_files, max(limits.max_results * 10, limit * 10))
    sql = (
        "SELECT d.id, bm25(documents_fts) AS score FROM documents_fts "
        "JOIN documents AS d ON d.id = documents_fts.rowid "
        "WHERE documents_fts MATCH ? AND d.layer_number BETWEEN 1 AND 4 "
        "ORDER BY score ASC, d.path ASC LIMIT ?"
    )
    for probe_position, probe in enumerate(probes):
        rows = connection.execute(
            sql,
            (_literal_fts_query(probe), row_limit),
        ).fetchall()
        weight = 2.0 if probe_position == 0 else 1.0
        for rank, row in enumerate(rows, start=1):
            document_id = int(row["id"])
            scores[document_id] = scores.get(document_id, 0.0) + weight / rank
            matched_probes.setdefault(document_id, set()).add(probe_position)
    return scores, matched_probes


def _candidate_trails(
    nodes: dict[int, _Node],
    scores: dict[int, float],
    *,
    original_match_ids: set[int],
    limit: int,
    limits: ForestLimits,
) -> list[tuple[_Node, ...]]:
    root = next(node for node in nodes.values() if node.route.layer.name == "xltm")
    ltm = {
        node.route.domain: node
        for node in nodes.values()
        if node.route.layer.name == "ltm" and node.route.domain is not None
    }
    mtm = {
        (node.route.domain, node.route.branch): node
        for node in nodes.values()
        if node.route.layer.name == "mtm"
        and node.route.domain is not None
        and node.route.branch is not None
    }
    stm = {
        (node.route.domain, node.route.branch, node.route.leaf): node
        for node in nodes.values()
        if node.route.layer.name == "stm"
        and node.route.domain is not None
        and node.route.branch is not None
    }
    stm_by_branch: dict[tuple[str, str], list[_Node]] = {}
    stm_by_domain: dict[str, list[_Node]] = {}
    mtm_by_domain: dict[str, list[_Node]] = {}
    for node in stm.values():
        if node.route.domain is None or node.route.branch is None:
            _raise_invalid_chain()
        stm_by_branch.setdefault((node.route.domain, node.route.branch), []).append(
            node
        )
        stm_by_domain.setdefault(node.route.domain, []).append(node)
    for node in mtm.values():
        if node.route.domain is None:
            _raise_invalid_chain()
        mtm_by_domain.setdefault(node.route.domain, []).append(node)
    for values in (
        *stm_by_branch.values(),
        *stm_by_domain.values(),
        *mtm_by_domain.values(),
    ):
        values.sort(key=lambda node: node.route.path)

    def chain_for(node: _Node) -> tuple[_Node, ...]:
        route = node.route
        if route.layer.name == "xltm":
            return _validate_chain((root,))
        if route.domain is None or route.domain not in ltm:
            _raise_invalid_chain()
        domain = ltm[route.domain]
        if route.layer.name == "ltm":
            return _validate_chain((root, domain))
        if route.branch is None or (route.domain, route.branch) not in mtm:
            _raise_invalid_chain()
        branch = mtm[(route.domain, route.branch)]
        if route.layer.name == "mtm":
            return _validate_chain((root, domain, branch))
        return _validate_chain((root, domain, branch, node))

    budget = min(limits.max_files, max(limits.max_results * 10, limit * 10))
    candidates: dict[tuple[int, ...], tuple[_Node, ...]] = {}

    def add(chain: tuple[_Node, ...]) -> None:
        if len(candidates) >= budget:
            return
        candidates.setdefault(tuple(node.document_id for node in chain), chain)

    matched_nodes = [node for node in nodes.values() if node.document_id in scores]
    matched_nodes.sort(
        key=lambda node: (
            node.document_id not in original_match_ids,
            -scores[node.document_id],
            node.route.path,
        )
    )

    def best_representative(node: _Node) -> _Node:
        route = node.route
        choices: list[_Node]
        if route.layer.name == "mtm":
            if route.domain is None or route.branch is None:
                _raise_invalid_chain()
            choices = stm_by_branch.get((route.domain, route.branch), [])
        elif route.layer.name == "ltm":
            if route.domain is None:
                _raise_invalid_chain()
            choices = stm_by_domain.get(route.domain, [])
            if not choices:
                choices = mtm_by_domain.get(route.domain, [])
        else:
            choices = []
        if not choices:
            return node
        return min(
            choices,
            key=lambda candidate: (
                candidate.document_id not in original_match_ids,
                -scores.get(candidate.document_id, 0.0),
                candidate.route.path,
            ),
        )

    for node in matched_nodes:
        if node.route.layer.name == "xltm":
            continue
        add(chain_for(best_representative(node)))
        if len(candidates) >= budget:
            break

    for node in matched_nodes:
        route = node.route
        if route.layer.name == "stm":
            add(chain_for(node))
        elif route.layer.name == "mtm":
            if route.domain is None or route.branch is None:
                _raise_invalid_chain()
            descendants = stm_by_branch.get((route.domain, route.branch), [])
            if descendants:
                for descendant in descendants:
                    add(chain_for(descendant))
            else:
                add(chain_for(node))
        elif route.layer.name == "ltm":
            if route.domain is None:
                _raise_invalid_chain()
            leaves = stm_by_domain.get(route.domain, [])
            if leaves:
                for leaf in leaves:
                    add(chain_for(leaf))
            else:
                branches = mtm_by_domain.get(route.domain, [])
                if branches:
                    for branch in branches:
                        add(chain_for(branch))
                else:
                    add(chain_for(node))
        else:
            if not candidates:
                add(_validate_chain((root,)))
        if len(candidates) >= budget:
            break
    if any(len(chain) > 1 for chain in candidates.values()):
        candidates.pop((root.document_id,), None)
    return list(candidates.values())


def _raise_invalid_chain() -> NoReturn:
    raise MemoryForestError(
        "index_corrupt",
        "The local index contains an incomplete structured trail; rebuild it.",
        details={"action": "memory-forest index ROOT"},
    )


def _validate_chain(chain: tuple[_Node, ...]) -> tuple[_Node, ...]:
    if not chain or chain[0].parent_path is not None:
        _raise_invalid_chain()
    for parent, child in zip(chain[:-1], chain[1:], strict=True):
        if child.parent_path != parent.route.path:
            _raise_invalid_chain()
    return chain


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _contains_unsafe_unicode(value: str) -> bool:
    return any(
        unicodedata.category(character) in {"Cc", "Cs"} for character in value
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")
