#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


MAX_FILE_BYTES = 1_000_000
MAX_FILES = 10_000
MAX_DIRECTORIES = 10_000
MAX_DEPTH = 64
MAX_TOTAL_BYTES = 50_000_000
PRIVATE_RUNTIME_NAMES = {".memory-forest", "memory-forest-data"}
SKIP_DIRECTORY_NAMES = {".git", ".venv", "__pycache__"}
SKIP_ROOT_DIRECTORY_NAMES = {"build", "dist"}


def patterns() -> list[tuple[str, re.Pattern[str]]]:
    user_root = "/" + "Users" + "/"
    home_root = "/" + "home" + "/"
    return [
        ("absolute_macos_home", re.compile(re.escape(user_root) + r"[^/\s]+/")),
        ("absolute_linux_home", re.compile(re.escape(home_root) + r"[^/\s]+/")),
        ("email_address", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
        ("e164_phone", re.compile(r"(?<!\w)\+[1-9]\d{9,14}(?!\w)")),
        ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)),
        ("private_key", re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY")),
        ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
        ("github_token", re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b")),
        ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
        ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ]


def path_findings(
    relative: str,
    rules: list[tuple[str, re.Pattern[str]]],
) -> list[dict[str, object]]:
    return [
        {"rule": name, "path": relative, "line": 0}
        for name, pattern in rules
        if pattern.search(relative)
    ]


def audit(root: Path) -> dict[str, object]:
    findings: list[dict[str, object]] = []
    files_scanned = 0
    directories_scanned = 0
    total_bytes = 0
    rules = patterns()
    if root.is_symlink():
        return {
            "ok": False,
            "files_scanned": 0,
            "directories_scanned": 0,
            "total_bytes": 0,
            "findings": [{"rule": "root_symlink", "path": ".", "line": 0}],
        }
    if not root.is_dir():
        return {
            "ok": False,
            "files_scanned": 0,
            "directories_scanned": 0,
            "total_bytes": 0,
            "findings": [{"rule": "root_not_directory", "path": ".", "line": 0}],
        }
    stack: list[tuple[Path, int]] = [(root, 0)]
    stop = False
    while stack and not stop:
        directory, depth = stack.pop()
        try:
            entries: list[os.DirEntry[str]] = []
            entry_limit = (
                MAX_FILES - files_scanned + MAX_DIRECTORIES - directories_scanned
            )
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    entries.append(entry)
                    if len(entries) > entry_limit:
                        relative_directory = (
                            "."
                            if directory == root
                            else directory.relative_to(root).as_posix()
                        )
                        findings.append(
                            {
                                "rule": "directory_entry_count_limit",
                                "path": relative_directory,
                                "line": 0,
                            }
                        )
                        stop = True
                        break
            entries.sort(key=lambda entry: entry.name)
        except OSError as exc:
            relative_directory = (
                "." if directory == root else directory.relative_to(root).as_posix()
            )
            findings.append(
                {
                    "rule": "scan_error",
                    "path": relative_directory,
                    "line": 0,
                    "reason": exc.__class__.__name__,
                }
            )
            continue
        if stop:
            break
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            if entry.is_dir(follow_symlinks=False) and (
                entry.name in SKIP_DIRECTORY_NAMES
                or (depth == 0 and entry.name in SKIP_ROOT_DIRECTORY_NAMES)
            ):
                continue
            findings.extend(path_findings(relative, rules))
            if entry.is_symlink():
                findings.append({"rule": "symlink", "path": relative, "line": 0})
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name in PRIVATE_RUNTIME_NAMES:
                    findings.append(
                        {
                            "rule": "private_runtime_data",
                            "path": relative,
                            "line": 0,
                        }
                    )
                    continue
                directories_scanned += 1
                child_depth = depth + 1
                if directories_scanned > MAX_DIRECTORIES:
                    findings.append(
                        {"rule": "directory_count_limit", "path": relative, "line": 0}
                    )
                    stop = True
                    break
                if child_depth > MAX_DEPTH:
                    findings.append(
                        {"rule": "directory_depth_limit", "path": relative, "line": 0}
                    )
                    continue
                stack.append((path, child_depth))
                continue
            if not entry.is_file(follow_symlinks=False):
                findings.append(
                    {"rule": "non_regular_file", "path": relative, "line": 0}
                )
                continue
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError as exc:
                findings.append(
                    {
                        "rule": "stat_error",
                        "path": relative,
                        "line": 0,
                        "reason": exc.__class__.__name__,
                    }
                )
                continue
            if size > MAX_FILE_BYTES:
                findings.append(
                    {"rule": "oversize_file", "path": relative, "line": 0}
                )
                continue
            files_scanned += 1
            total_bytes += size
            if files_scanned > MAX_FILES:
                findings.append(
                    {"rule": "file_count_limit", "path": relative, "line": 0}
                )
                stop = True
                break
            if total_bytes > MAX_TOTAL_BYTES:
                findings.append(
                    {"rule": "total_bytes_limit", "path": relative, "line": 0}
                )
                stop = True
                break
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            except OSError as exc:
                findings.append(
                    {
                        "rule": "read_error",
                        "path": relative,
                        "line": 0,
                        "reason": exc.__class__.__name__,
                    }
                )
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                for name, pattern in rules:
                    if pattern.search(line):
                        findings.append(
                            {"rule": name, "path": relative, "line": line_number}
                        )
    findings.sort(
        key=lambda item: (
            str(item.get("path", "")),
            str(item.get("line", 0)),
            str(item.get("rule", "")),
        )
    )
    return {
        "ok": not findings,
        "files_scanned": files_scanned,
        "directories_scanned": directories_scanned,
        "total_bytes": total_bytes,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail closed when a public tree contains likely private data or secrets."
    )
    parser.add_argument("--root", default=".", help="Public repository root")
    args = parser.parse_args()
    root = Path(args.root).expanduser()
    result = audit(root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
