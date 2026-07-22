from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Final, NoReturn

from .core import _read_utf8_file, inspect_forest
from .errors import MemoryForestError
from .model import SCHEMA_VERSION, immediate_parent_path
from .safety import (
    DEFAULT_LIMITS,
    ForestLimits,
    ensure_inside,
    require_real_root,
    secure_state_directory,
)


INDEX_FILENAME: Final[str] = "index.sqlite3"
INDEX_SCHEMA_VERSION: Final[str] = "2"
_QUERY_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_INSERT_DOCUMENT_SQL = (
    "INSERT INTO documents ("
    "id, path, parent_path, route_key, layer_number, layer, domain, branch, "
    "leaf, title, body, sha256, size, mtime_ns"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_SCHEMA_SQL = (
    "PRAGMA journal_mode = DELETE;"
    "PRAGMA synchronous = FULL;"
    "PRAGMA temp_store = MEMORY;"
    "CREATE TABLE metadata ("
    "key TEXT PRIMARY KEY, value TEXT NOT NULL"
    ") WITHOUT ROWID;"
    "CREATE TABLE documents ("
    "id INTEGER PRIMARY KEY,"
    "path TEXT NOT NULL UNIQUE,"
    "parent_path TEXT,"
    "route_key TEXT NOT NULL UNIQUE,"
    "layer_number INTEGER NOT NULL,"
    "layer TEXT NOT NULL,"
    "domain TEXT,"
    "branch TEXT,"
    "leaf TEXT NOT NULL,"
    "title TEXT NOT NULL,"
    "body TEXT NOT NULL,"
    "sha256 TEXT NOT NULL,"
    "size INTEGER NOT NULL,"
    "mtime_ns INTEGER NOT NULL"
    ");"
    "CREATE INDEX documents_parent_path ON documents("
    "parent_path, layer_number, path"
    ");"
    "CREATE VIRTUAL TABLE routes_fts USING fts5("
    "route, title, tokenize = 'unicode61 remove_diacritics 2'"
    ");"
    "CREATE VIRTUAL TABLE documents_fts USING fts5("
    "route, title, body, tokenize = 'unicode61 remove_diacritics 2'"
    ");"
)


def index_forest(
    root: str | os.PathLike[str],
    *,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    inspection = inspect_forest(root, audit_links=True, limits=limits)
    if not inspection.ok:
        error_codes = sorted(
            {issue.code for issue in inspection.issues if issue.level == "error"}
        )
        raise MemoryForestError(
            "forest_invalid",
            "Indexing requires a forest that passes validation and audit.",
            details={"errors": error_codes},
        )
    state = secure_state_directory(inspection.root)
    index_path = state / INDEX_FILENAME
    if os.path.lexists(index_path):
        info = index_path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise MemoryForestError(
                "unsafe_index_path",
                "The index path must be a regular file or not exist.",
                details={"path": str(index_path)},
            )
    descriptor, raw_temp_path = tempfile.mkstemp(prefix="index-", suffix=".tmp", dir=state)
    os.close(descriptor)
    temp_path = Path(raw_temp_path)
    temp_path.unlink()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temp_path)
        _create_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        for document_id, document in enumerate(inspection.documents, start=1):
            route = document.route
            connection.execute(
                _INSERT_DOCUMENT_SQL,
                (
                    document_id,
                    route.path,
                    immediate_parent_path(route),
                    route.route_key,
                    route.layer.number,
                    route.layer.name,
                    route.domain,
                    route.branch,
                    route.leaf,
                    document.title,
                    document.body,
                    document.sha256,
                    document.size,
                    document.mtime_ns,
                ),
            )
            route_text = " ".join(
                value
                for value in (
                    route.path,
                    route.route_key,
                    route.layer.name,
                    route.domain,
                    route.branch,
                    route.leaf,
                )
                if value
            )
            connection.execute(
                "INSERT INTO routes_fts(rowid, route, title) VALUES (?, ?, ?)",
                (document_id, route_text, document.title),
            )
            connection.execute(
                "INSERT INTO documents_fts(rowid, route, title, body) VALUES (?, ?, ?, ?)",
                (document_id, route_text, document.title, document.body),
            )
        metadata = {
            "document_count": str(len(inspection.documents)),
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "memory_schema_version": str(SCHEMA_VERSION),
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        connection.commit()
        connection.execute("PRAGMA optimize")
        connection.close()
        connection = None
        os.chmod(temp_path, 0o600, follow_symlinks=False)
        os.replace(temp_path, index_path)
        os.chmod(index_path, 0o600, follow_symlinks=False)
        _fsync_directory(state)
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        code = "fts5_unavailable" if "fts5" in str(exc).lower() else "index_failed"
        raise MemoryForestError(
            code,
            "The local SQLite FTS5 index could not be built.",
            details={"reason": exc.__class__.__name__},
        ) from exc
    except Exception:
        if connection is not None:
            connection.close()
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return {
        "bytes_indexed": sum(document.size for document in inspection.documents),
        "documents": len(inspection.documents),
        "index": f".memory-forest/{INDEX_FILENAME}",
        "ok": True,
        "operation": "index",
        "root": str(inspection.root),
        "schema_version": SCHEMA_VERSION,
    }


def route_index(
    root: str | os.PathLike[str],
    query: str,
    *,
    limit: int = 10,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    return _query_index(
        root,
        query,
        table="routes_fts",
        operation="route",
        include_body=False,
        limit=limit,
        limits=limits,
    )


def search_index(
    root: str | os.PathLike[str],
    query: str,
    *,
    include_body: bool = False,
    limit: int = 10,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    return _query_index(
        root,
        query,
        table="documents_fts",
        operation="search",
        include_body=include_body,
        limit=limit,
        limits=limits,
    )


def _query_index(
    root: str | os.PathLike[str],
    query: str,
    *,
    table: str,
    operation: str,
    include_body: bool,
    limit: int,
    limits: ForestLimits,
) -> dict[str, object]:
    if limit < 1 or limit > limits.max_results:
        raise MemoryForestError(
            "invalid_limit",
            "The result limit is outside the permitted range.",
            details={"limit": limit, "maximum": limits.max_results},
        )
    match_query = _literal_fts_query(query)
    root_path = require_real_root(root)
    index_path = _secure_index_path(root_path)
    uri = index_path.as_uri() + "?mode=ro&immutable=1"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        _verify_schema(connection)
        select_sql = (
            "SELECT d.path, d.route_key, d.layer_number, d.layer, d.domain, "
            "d.branch, d.leaf, d.title, d.sha256, d.size, d.mtime_ns, "
            f"bm25({table}) AS score FROM {table} "
            f"JOIN documents AS d ON d.id = {table}.rowid "
            f"WHERE {table} MATCH ? ORDER BY score ASC, d.path ASC LIMIT ?"
        )
        rows = connection.execute(
            select_sql,
            (match_query, limit),
        ).fetchall()
    except sqlite3.Error as exc:
        raise MemoryForestError(
            "query_failed",
            "The local SQLite FTS5 index could not be queried.",
            details={"reason": exc.__class__.__name__},
        ) from exc
    finally:
        if connection is not None:
            connection.close()
    results: list[dict[str, object]] = []
    for row in rows:
        result: dict[str, object] = {
            "mtime_ns": row["mtime_ns"],
            "route": {
                "branch": row["branch"],
                "domain": row["domain"],
                "layer": {
                    "name": row["layer"],
                    "number": row["layer_number"],
                },
                "leaf": row["leaf"],
                "path": row["path"],
                "route_key": row["route_key"],
            },
            "score": round(float(row["score"]), 8),
            "sha256": row["sha256"],
            "size": row["size"],
            "title": row["title"],
        }
        if include_body:
            result["body"] = _read_current_indexed_body(
                root_path,
                row["path"],
                row["sha256"],
                limits=limits,
            )
        results.append(result)
    return {
        "count": len(results),
        "include_body": include_body,
        "limit": limit,
        "ok": True,
        "operation": operation,
        "query": query,
        "results": results,
        "schema_version": SCHEMA_VERSION,
    }


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA_SQL)


def _literal_fts_query(query: str) -> str:
    if len(query) > 1_000:
        raise MemoryForestError(
            "query_too_long",
            "The query exceeds the 1000-character limit.",
            details={"length": len(query)},
        )
    tokens = _QUERY_TOKEN_RE.findall(query)
    if not tokens:
        raise MemoryForestError(
            "empty_query",
            "The query must contain at least one searchable word.",
        )
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _secure_index_path(root: Path) -> Path:
    state = root / ".memory-forest"
    index_path = state / INDEX_FILENAME
    for path, expected_type in ((state, stat.S_ISDIR), (index_path, stat.S_ISREG)):
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise MemoryForestError(
                "index_not_found",
                "Build the local index before querying it.",
                details={"path": f".memory-forest/{INDEX_FILENAME}"},
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not expected_type(info.st_mode):
            raise MemoryForestError(
                "unsafe_index_path",
                "The index path contains a symlink or unexpected file type.",
                details={"path": path.relative_to(root).as_posix()},
            )
    if stat.S_IMODE(state.stat().st_mode) != 0o700:
        raise MemoryForestError(
            "state_permissions",
            "The derived-state directory must have mode 0700.",
            details={"path": ".memory-forest"},
        )
    if stat.S_IMODE(index_path.stat().st_mode) != 0o600:
        raise MemoryForestError(
            "index_permissions",
            "The local index must have mode 0600.",
            details={"path": f".memory-forest/{INDEX_FILENAME}"},
        )
    return index_path


def _verify_schema(connection: sqlite3.Connection) -> None:
    values = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
    if (
        values.get("index_schema_version") != INDEX_SCHEMA_VERSION
        or values.get("memory_schema_version") != str(SCHEMA_VERSION)
    ):
        raise MemoryForestError(
            "index_schema_mismatch",
            "The local index schema is unsupported; rebuild the index.",
            details={"action": "memory-forest index ROOT"},
        )


def _raise_stale_index() -> NoReturn:
    raise MemoryForestError(
        "index_stale",
        "The local index no longer matches the canonical forest; rebuild the index.",
        details={"action": "memory-forest index ROOT"},
    )


def _read_current_indexed_body(
    root: Path,
    relative: str,
    indexed_sha256: str,
    *,
    limits: ForestLimits,
) -> str:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not pure.parts
        or "." in pure.parts
        or ".." in pure.parts
        or "\\" in relative
    ):
        _raise_stale_index()
    cursor = root
    entry_limit = limits.max_files + limits.max_directories + 1
    for position, part in enumerate(pure.parts):
        match: Path | None = None
        try:
            with os.scandir(cursor) as iterator:
                for count, entry in enumerate(iterator, start=1):
                    if count > entry_limit:
                        raise MemoryForestError(
                            "directory_entry_count_exceeded",
                            "A forest directory exceeds the traversal budget.",
                            details={"limit": entry_limit},
                        )
                    if entry.name != part:
                        continue
                    if entry.is_symlink():
                        _raise_stale_index()
                    final = position == len(pure.parts) - 1
                    if final and not entry.is_file(follow_symlinks=False):
                        _raise_stale_index()
                    if not final and not entry.is_dir(follow_symlinks=False):
                        _raise_stale_index()
                    match = Path(entry.path)
                    break
        except OSError as exc:
            raise MemoryForestError(
                "index_stale",
                "The indexed canonical file could not be reopened safely; rebuild the index.",
                details={"action": "memory-forest index ROOT"},
            ) from exc
        if match is None:
            _raise_stale_index()
        cursor = match
    try:
        current_path = ensure_inside(root, cursor)
        current_body = _read_utf8_file(current_path, limits=limits)
    except MemoryForestError as exc:
        raise MemoryForestError(
            "index_stale",
            "The indexed canonical file could not be reopened safely; rebuild the index.",
            details={"action": "memory-forest index ROOT"},
        ) from exc
    current_sha256 = hashlib.sha256(current_body.encode("utf-8")).hexdigest()
    if current_sha256 != indexed_sha256:
        _raise_stale_index()
    return current_body


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
