from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from .core import _extract_title, _read_utf8_file, audit_forest, validate_forest
from .errors import MemoryForestError
from .index import index_forest
from .model import SCHEMA_VERSION, Route, immediate_parent_path, parse_relative_route
from .safety import (
    DEFAULT_LIMITS,
    ForestLimits,
    ensure_inside,
    require_real_root,
    secure_create_file,
    secure_mkdir,
)


PROMOTABLE_LAYERS = {"stm": 4, "mtm": 3, "ltm": 2, "xltm": 1}


def promote_memory(
    root: str | os.PathLike[str],
    source_path: str | os.PathLike[str],
    *,
    to_layer: str,
    allow_skip: bool = False,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    root_path = require_real_root(root)
    target_layer = to_layer.casefold()
    if target_layer not in {"mtm", "ltm", "xltm"}:
        raise MemoryForestError(
            "invalid_promotion_layer",
            "Promotion targets must be mtm, ltm, or xltm.",
            details={"layer": to_layer},
        )
    relative = _source_relative(root_path, source_path)
    source_route = parse_relative_route(relative)
    if source_route.layer.name not in PROMOTABLE_LAYERS or source_route.layer.name == "xltm":
        raise MemoryForestError(
            "invalid_promotion_source",
            "Promotion sources must be STM, MTM, or LTM Markdown documents.",
            details={"path": relative},
        )
    distance = source_route.layer.number - PROMOTABLE_LAYERS[target_layer]
    if distance <= 0:
        raise MemoryForestError(
            "invalid_promotion_direction",
            "A memory may only be promoted to a higher layer.",
            details={"source_layer": source_route.layer.name, "target_layer": target_layer},
        )
    if distance > 1 and not allow_skip:
        raise MemoryForestError(
            "non_adjacent_promotion",
            "Promotion may only move to the adjacent higher layer unless --allow-skip is set.",
            details={"source_layer": source_route.layer.name, "target_layer": target_layer},
        )

    steps: list[dict[str, object]] = []
    current = source_route
    while current.layer.name != target_layer:
        target_relative = immediate_parent_path(current)
        if target_relative is None:
            raise MemoryForestError(
                "invalid_promotion_route",
                "The source route has no canonical higher-layer owner.",
                details={"path": current.path},
            )
        target = parse_relative_route(target_relative)
        steps.append(_promote_one(root_path, current, target, limits=limits))
        current = target

    validation = validate_forest(root_path, limits=limits)
    if not validation["ok"]:
        raise MemoryForestError(
            "promotion_validation_failed",
            "The promoted forest did not pass validation.",
            details={"errors": _error_codes(validation)},
        )
    audit = audit_forest(root_path, limits=limits)
    if not audit["ok"]:
        raise MemoryForestError(
            "promotion_audit_failed",
            "The promoted forest did not pass the wikilink audit.",
            details={"errors": _error_codes(audit)},
        )
    indexed = index_forest(root_path, limits=limits)
    already_promoted = all(bool(step["already_promoted"]) for step in steps)
    return {
        "already_promoted": already_promoted,
        "audit": audit,
        "index": indexed,
        "ok": True,
        "operation": "promote",
        "root": str(root_path),
        "schema_version": SCHEMA_VERSION,
        "source": source_route.as_dict(),
        "status": "already promoted" if already_promoted else "promoted",
        "steps": steps,
        "target": current.as_dict(),
        "validation": validation,
    }


def _source_relative(root: Path, source_path: str | os.PathLike[str]) -> str:
    supplied = Path(source_path)
    candidate = supplied if supplied.is_absolute() else root / supplied
    safe = ensure_inside(root, candidate, must_exist=True)
    info = safe.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise MemoryForestError(
            "non_regular_file",
            "A promotion source must be a regular file.",
            details={"path": str(source_path)},
        )
    return safe.relative_to(root).as_posix()


def _promote_one(
    root: Path,
    source: Route,
    target: Route,
    *,
    limits: ForestLimits,
) -> dict[str, object]:
    source_file = ensure_inside(root, root / source.path, must_exist=True)
    source_body = _read_utf8_file(source_file, limits=limits)
    source_hash = hashlib.sha256(source_body.encode("utf-8")).hexdigest()
    title = _wikilink_label(_extract_title(source_body, source.leaf))
    target_file = ensure_inside(root, root / target.path, must_exist=False)
    created = False
    if not target_file.exists():
        _secure_parents(root, target_file.parent)
        parent = immediate_parent_path(target)
        parent_line = f"\nParent: [[{parent}]]\n" if parent is not None else ""
        target_title = target.leaf.removesuffix("_LTM").replace("-", " ").title()
        initial = f"# {target_title}\n{parent_line}"
        secure_create_file(target_file, initial.encode("utf-8"))
        created = True
    target_body = _read_utf8_file(target_file, limits=limits)
    wikilink = f"[[{source.path}|{title}]]"
    if f"[[{source.path}" in target_body:
        return {
            "already_promoted": True,
            "created": created,
            "sha256": source_hash,
            "source": source.as_dict(),
            "target": target.as_dict(),
        }
    separator = "" if target_body.endswith("\n") else "\n"
    heading = "" if "## Promotions" in target_body else "\n## Promotions\n"
    addition = (
        f"{separator}{heading}\n- {wikilink} — {title}\n"
        f"  - Source SHA-256: `{source_hash}`\n"
    )
    _replace_regular_file(target_file, (target_body + addition).encode("utf-8"))
    return {
        "already_promoted": False,
        "created": created,
        "sha256": source_hash,
        "source": source.as_dict(),
        "target": target.as_dict(),
    }


def _secure_parents(root: Path, parent: Path) -> None:
    cursor = root
    for part in parent.relative_to(root).parts:
        cursor /= part
        secure_mkdir(cursor)


def _replace_regular_file(path: Path, data: bytes) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise MemoryForestError(
            "unsafe_existing_path",
            "A promotion target must be a regular file.",
            details={"path": str(path)},
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="promote-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600, follow_symlinks=False)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _error_codes(report: dict[str, object]) -> list[str]:
    issues = report.get("issues", [])
    return sorted(
        {
            str(issue["code"])
            for issue in issues
            if isinstance(issue, dict) and issue.get("level") == "error"
        }
    )


def _wikilink_label(value: str) -> str:
    return (
        value.replace("\n", " ")
        .replace("\r", " ")
        .replace("[", "(")
        .replace("]", ")")
        .replace("|", "-")
        .strip()
    )
