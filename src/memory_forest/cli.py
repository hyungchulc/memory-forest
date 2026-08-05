from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any, NoReturn

from . import __version__
from .core import (
    audit_forest,
    doctor_forest,
    health_forest,
    initialize_forest,
    validate_forest,
)
from .errors import MemoryForestError
from .index import index_forest, route_index, search_index
from .model import SCHEMA_VERSION
from .retrieval import read_query_plan_source, retrieve_index


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise MemoryForestError(
            "invalid_arguments",
            "The command arguments are invalid.",
            details={"reason": message},
        )


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="memory-forest",
        description=(
            "Build and query a local, provenance-aware memory forest. "
            "Commands never use the network."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit compact deterministic JSON instead of indented JSON.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser(
        "init", help="Safely initialize an empty local forest."
    )
    init_parser.add_argument(
        "root",
        help="New forest path under an existing real direct parent",
    )
    init_parser.add_argument(
        "--example",
        action="store_true",
        help="Create a private synthetic 00-06 demo forest.",
    )

    doctor_parser = commands.add_parser(
        "doctor", help="Check local configuration, SQLite FTS5, and index state."
    )
    doctor_parser.add_argument("root", help="Forest root")

    validate_parser = commands.add_parser(
        "validate", help="Validate paths, routes, encoding, limits, and permissions."
    )
    validate_parser.add_argument("root", help="Forest root")

    audit_parser = commands.add_parser(
        "audit", help="Validate the forest and enforce adjacent-layer wikilinks."
    )
    audit_parser.add_argument("root", help="Forest root")

    health_parser = commands.add_parser(
        "health",
        help="Report layer balance, freshness metadata gaps, and semantic duplicates.",
    )
    health_parser.add_argument("root", help="Forest root")
    health_parser.add_argument(
        "--duplicate-threshold",
        type=float,
        default=0.72,
        help="Advisory cosine-similarity threshold, 0.5-1.0",
    )

    index_parser = commands.add_parser(
        "index", help="Atomically build the local SQLite FTS5 index."
    )
    index_parser.add_argument("root", help="Forest root")

    route_parser = commands.add_parser(
        "route", help="Search route metadata without returning memory bodies."
    )
    route_parser.add_argument("root", help="Forest root")
    route_parser.add_argument("query", help="Literal route query")
    route_parser.add_argument("--limit", type=int, default=10, help="Result limit, 1-100")

    search_parser = commands.add_parser(
        "search", help="Search the local index; bodies remain hidden by default."
    )
    search_parser.add_argument("root", help="Forest root")
    search_parser.add_argument("query", help="Literal full-text query")
    search_parser.add_argument("--limit", type=int, default=10, help="Result limit, 1-100")
    search_parser.add_argument(
        "--include-body",
        action="store_true",
        help="Explicitly include full memory bodies in results.",
    )

    retrieve_parser = commands.add_parser(
        "retrieve",
        help="Return a validated root-first structured trail without memory bodies.",
    )
    retrieve_parser.add_argument("root", help="Forest root")
    retrieve_parser.add_argument("query", help="Literal retrieval query")
    retrieve_parser.add_argument(
        "--limit", type=int, default=10, help="Trail limit, 1-100"
    )
    retrieve_parser.add_argument(
        "--query-plan",
        help=(
            "Strict query-only expansion plan as a regular JSON file, or '-' for stdin"
        ),
    )
    return parser


def run_command(arguments: argparse.Namespace) -> tuple[dict[str, object], int]:
    command = arguments.command
    if command == "init":
        result = initialize_forest(arguments.root, example=arguments.example)
    elif command == "doctor":
        result = doctor_forest(arguments.root)
    elif command == "validate":
        result = validate_forest(arguments.root)
    elif command == "audit":
        result = audit_forest(arguments.root)
    elif command == "health":
        result = health_forest(
            arguments.root,
            duplicate_threshold=arguments.duplicate_threshold,
        )
    elif command == "index":
        result = index_forest(arguments.root)
    elif command == "route":
        result = route_index(arguments.root, arguments.query, limit=arguments.limit)
    elif command == "search":
        result = search_index(
            arguments.root,
            arguments.query,
            include_body=arguments.include_body,
            limit=arguments.limit,
        )
    elif command == "retrieve":
        query_plan = (
            read_query_plan_source(arguments.query_plan, stdin=sys.stdin.buffer)
            if arguments.query_plan is not None
            else None
        )
        result = retrieve_index(
            arguments.root,
            arguments.query,
            query_plan=query_plan,
            limit=arguments.limit,
        )
    else:
        raise MemoryForestError(
            "unknown_command",
            "The requested command is not supported.",
            details={"command": command},
        )
    return result, 0 if result.get("ok") else 1


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    compact_json = "--json" in raw_arguments
    if compact_json:
        raw_arguments = [argument for argument in raw_arguments if argument != "--json"]
    parser = build_parser()
    try:
        arguments = parser.parse_args(raw_arguments)
        arguments.json = compact_json
        result, exit_code = run_command(arguments)
    except MemoryForestError as exc:
        result = {
            "error": exc.as_dict(),
            "ok": False,
            "schema_version": SCHEMA_VERSION,
        }
        exit_code = 2
    except OSError as exc:
        result = {
            "error": {
                "code": "filesystem_error",
                "details": {"reason": exc.__class__.__name__},
                "message": "A local filesystem operation failed safely.",
            },
            "ok": False,
            "schema_version": SCHEMA_VERSION,
        }
        exit_code = 2
    except Exception:
        result = {
            "error": {
                "code": "internal_error",
                "details": {},
                "message": "The command failed without exposing internal details.",
            },
            "ok": False,
            "schema_version": SCHEMA_VERSION,
        }
        exit_code = 2
    except KeyboardInterrupt:
        result = {
            "error": {
                "code": "interrupted",
                "details": {},
                "message": "The operation was interrupted.",
            },
            "ok": False,
            "schema_version": SCHEMA_VERSION,
        }
        exit_code = 130
    _emit(result, compact=compact_json)
    return exit_code


def _emit(result: dict[str, Any], *, compact: bool) -> None:
    if compact:
        text = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    else:
        text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write(text + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
