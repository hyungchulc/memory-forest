#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from memory_forest import (  # pyright: ignore[reportMissingImports]
    MemoryForestError,
    retrieve_index,
    route_index,
)

GATE_SCHEMA_VERSION = 1
MAX_TURN_BYTES = 16 * 1024


def consult_turn(
    root: str,
    turn_text: str,
    *,
    limit: int = 4,
    route: Callable[..., dict[str, object]] = route_index,
    retrieve: Callable[..., dict[str, object]] = retrieve_index,
) -> dict[str, object]:
    query = turn_text.strip()
    if not query:
        return {
            "body_included": False,
            "lookup_completed": False,
            "lookup_required": False,
            "ok": True,
            "operation": "memory_forest_retrieve_gate",
            "schema_version": GATE_SCHEMA_VERSION,
            "status": "not_required",
        }

    route_result = route(root, query, limit=limit)
    _require_success(route_result, operation="route")
    _require_body_free(route_result)

    retrieve_result = retrieve(root, query, limit=limit)
    _require_success(retrieve_result, operation="retrieve")
    _require_body_free(retrieve_result)

    route_receipt = _without_keys(
        route_result,
        {"ok", "operation", "query", "schema_version"},
    )
    retrieve_receipt = _without_keys(
        retrieve_result,
        {"ok", "operation", "query", "schema_version"},
    )
    evidence_found = bool(
        _result_count(route_result) or _result_count(retrieve_result)
    )
    return {
        "body_included": False,
        "lookup_completed": True,
        "lookup_required": True,
        "ok": True,
        "operation": "memory_forest_retrieve_gate",
        "retrieve": retrieve_receipt,
        "route": route_receipt,
        "schema_version": GATE_SCHEMA_VERSION,
        "status": "evidence_found" if evidence_found else "no_evidence",
    }


def read_turn(stream: Any = sys.stdin.buffer) -> str:
    payload = stream.read(MAX_TURN_BYTES + 1)
    if len(payload) > MAX_TURN_BYTES:
        raise MemoryForestError(
            "turn_too_large",
            "The current turn exceeds the gate input limit.",
            details={"limit": MAX_TURN_BYTES},
        )
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MemoryForestError(
            "invalid_turn_encoding",
            "The current turn must be valid UTF-8 text.",
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a mandatory metadata-only Memory Forest consultation for one "
            "user-authored turn read from standard input."
        )
    )
    parser.add_argument("root", help="Exact private Memory Forest root")
    parser.add_argument(
        "--limit",
        type=int,
        default=4,
        help="Maximum route candidates and retrieve trails, 1-100",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = build_parser().parse_args(argv)
        result = consult_turn(
            arguments.root,
            read_turn(),
            limit=arguments.limit,
        )
        exit_code = 0
    except MemoryForestError as exc:
        result = _failure(exc.as_dict())
        exit_code = 2
    except (TypeError, ValueError):
        result = _failure(
            {
                "code": "invalid_gate_result",
                "details": {},
                "message": "The retrieval gate returned an invalid result.",
            }
        )
        exit_code = 2
    except Exception:
        result = _failure(
            {
                "code": "gate_failed",
                "details": {},
                "message": "The retrieval gate failed without exposing internals.",
            }
        )
        exit_code = 2
    sys.stdout.write(
        json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    return exit_code


def _require_success(result: Mapping[str, object], *, operation: str) -> None:
    if result.get("ok") is True:
        return
    raise MemoryForestError(
        "gate_phase_failed",
        f"The {operation} phase did not complete successfully.",
        details={"operation": operation},
    )


def _result_count(result: Mapping[str, object]) -> int:
    value = result.get("count", 0)
    if type(value) is not int or value < 0:
        raise MemoryForestError(
            "invalid_gate_result",
            "The retrieval gate received an invalid result count.",
        )
    return value


def _require_body_free(value: object) -> None:
    if isinstance(value, Mapping):
        if any(str(key).casefold() == "body" for key in value):
            raise MemoryForestError(
                "body_boundary_violation",
                "The retrieval gate refused a result containing a memory body.",
            )
        for nested in value.values():
            _require_body_free(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            _require_body_free(nested)


def _without_keys(
    value: Mapping[str, object],
    excluded: set[str],
) -> dict[str, object]:
    return {
        str(key): nested
        for key, nested in value.items()
        if str(key) not in excluded
    }


def _failure(error: Mapping[str, object]) -> dict[str, object]:
    raw_code = str(error.get("code") or "").strip()
    safe_code = (
        raw_code
        if raw_code
        and len(raw_code) <= 64
        and all(character.isalnum() or character == "_" for character in raw_code)
        else "gate_failed"
    )
    return {
        "body_included": False,
        "error": {
            "code": safe_code,
            "message": "The Memory Forest Retrieve gate failed closed.",
        },
        "lookup_completed": False,
        "lookup_required": True,
        "ok": False,
        "operation": "memory_forest_retrieve_gate",
        "schema_version": GATE_SCHEMA_VERSION,
        "status": "failed",
    }


if __name__ == "__main__":
    raise SystemExit(main())
