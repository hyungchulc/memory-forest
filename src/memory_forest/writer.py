from __future__ import annotations

import hashlib
import html
import json
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Final, NoReturn, cast

from .core import (
    _load_strict_json,
    _read_utf8_file,
    audit_forest,
    load_forest_identity,
    validate_forest,
)
from .errors import MemoryForestError
from .index import INDEX_FILENAME, _index_forest_unlocked
from .locking import maintenance_lock
from .model import SCHEMA_VERSION, parse_relative_route
from .safety import (
    DEFAULT_LIMITS,
    MAX_RECEIPT_BYTES,
    MAX_RECEIPTS,
    RECEIPTS_DIRECTORY,
    STATE_DIRECTORY,
    ForestLimits,
    require_real_root,
)


DAILY_PLAN_SCHEMA: Final[str] = "memory-forest-daily-plan-v1"
PROMOTION_PLAN_SCHEMA: Final[str] = "memory-forest-promotion-plan-v1"
WRITE_RECEIPT_SCHEMA: Final[str] = "memory-forest-write-receipt-v1"
MAX_PLAN_BYTES: Final[int] = 256 * 1024
MAX_DAILY_ENTRIES: Final[int] = 128
MAX_PROMOTIONS: Final[int] = 128
MAX_SOURCE_IDS: Final[int] = 128
MAX_DAILY_COMMITS: Final[int] = 128
MAX_SUMMARY_CHARS: Final[int] = 16_384
MAX_CONTENT_CHARS: Final[int] = 65_536
MAX_TITLE_CHARS: Final[int] = 300
WRITE_JOURNAL_SCHEMA: Final[str] = "memory-forest-write-journal-v1"
WRITE_JOURNAL_SUFFIX: Final[str] = ".write-journal"
MAX_JOURNAL_TARGETS: Final[int] = 512

_HASH_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_FOREST_ID_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{32}")
_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
)
_ROUTE_SEGMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?"
)
_DAILY_ENTRY_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"^<!-- memory-forest-daily-entry-v1:"
    r"(?P<entry_id>[A-Za-z0-9][A-Za-z0-9._:-]{0,127}):"
    r"(?P<commit>[0-9a-f]{64}) -->$",
    re.MULTILINE,
)
_DAILY_TRANSACTION_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"^<!-- memory-forest-daily-transaction-v1:"
    r"(?P<transaction>[0-9a-f]{64}) -->\n"
    r"## Admitted Daily batch `(?P=transaction)`\n\n"
    r"(?P<body>.*?)"
    r"^<!-- /memory-forest-daily-transaction-v1:"
    r"(?P=transaction) -->\n?",
    re.MULTILINE | re.DOTALL,
)
_DAILY_ENTRY_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"^### Entry `(?P<title_id>[A-Za-z0-9][A-Za-z0-9._:-]{0,127})`\n"
    r"<!-- memory-forest-daily-entry-v1:"
    r"(?P<entry_id>[A-Za-z0-9][A-Za-z0-9._:-]{0,127}):"
    r"(?P<commit>[0-9a-f]{64}) -->\n\n"
    r"```json\n(?P<payload>[^\n]+)\n```\n",
    re.MULTILINE,
)
_DAILY_PROVENANCE_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"^### Provenance\n\n```json\n(?P<payload>[^\n]+)\n```\n?$",
    re.MULTILINE,
)
_WIKILINK_RE: Final[re.Pattern[str]] = re.compile(r"\[\[([^\]\n]+)\]\]")


@dataclass(frozen=True, slots=True)
class DailyEntry:
    entry_id: str
    source_record_ids: tuple[str, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class DailyProvenance:
    packet_sha256: str
    result_sha256: str
    batch_id: str


@dataclass(frozen=True, slots=True)
class DailyPlan:
    forest_id: str
    transaction_id: str
    day: str
    entries: tuple[DailyEntry, ...]
    provenance: DailyProvenance


@dataclass(frozen=True, slots=True)
class SemanticRoute:
    domain: str
    domain_title: str
    branch: str
    branch_title: str
    leaf: str


@dataclass(frozen=True, slots=True)
class Promotion:
    source_daily_entry_ids: tuple[str, ...]
    route: SemanticRoute
    title: str
    content: str
    confidence: str


@dataclass(frozen=True, slots=True)
class PromotionProvenance:
    packet_sha256: str
    result_sha256: str
    daily_commit_sha256s: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PromotionPlan:
    forest_id: str
    transaction_id: str
    day: str
    promotions: tuple[Promotion, ...]
    provenance: PromotionProvenance


@dataclass(frozen=True, slots=True)
class _DailySource:
    path: str
    commit_sha256: str


class _MutationSession:
    def __init__(self, root: Path, *, limits: ForestLimits) -> None:
        self.root = root
        self.limits = limits
        self.originals: dict[Path, bytes | None] = {}
        self.created_directories: list[Path] = []
        self.journal = Path(str(root) + WRITE_JOURNAL_SUFFIX)
        self.prepared = False
        self.stage_counter = 0
        self.closed = False

    def capture(self, path: Path) -> None:
        if path in self.originals:
            return
        if os.path.lexists(path):
            self.originals[path] = _read_private_regular_bytes(
                path,
                maximum=self.limits.max_total_bytes,
                code="unsafe_mutation_target",
            )
        else:
            self.originals[path] = None

    def prepare(self, paths: Sequence[Path]) -> None:
        if self.prepared or self.closed:
            raise MemoryForestError(
                "invalid_transaction_state",
                "The write transaction cannot be prepared twice.",
            )
        unique_paths = sorted(
            set(paths),
            key=lambda item: item.relative_to(self.root).as_posix(),
        )
        if not unique_paths or len(unique_paths) > MAX_JOURNAL_TARGETS:
            raise MemoryForestError(
                "write_transaction_too_large",
                "The write transaction exceeds its target bound.",
            )
        if os.path.lexists(self.journal):
            raise MemoryForestError(
                "write_journal_exists",
                "An earlier write journal must be recovered before a new transaction.",
            )
        _reject_casefold_collision(self.journal.parent, self.journal.name)
        self.journal.mkdir(mode=0o700)
        os.chmod(self.journal, 0o700, follow_symlinks=False)
        _fsync_directory(self.journal.parent)
        targets: list[dict[str, object]] = []
        missing_directories: set[Path] = set()
        for index, path in enumerate(unique_paths):
            try:
                relative = path.relative_to(self.root).as_posix()
            except ValueError as exc:
                raise MemoryForestError(
                    "write_target_escape",
                    "A write transaction target escapes the selected forest.",
                ) from exc
            self.capture(path)
            missing_directories.update(_missing_private_parents(self.root, path.parent))
            original = self.originals[path]
            backup_name: str | None = None
            original_sha256: str | None = None
            if original is not None:
                backup_name = f"backup-{index:04d}.bin"
                original_sha256 = hashlib.sha256(original).hexdigest()
                _write_new_private_file(self.journal / backup_name, original)
            targets.append(
                {
                    "backup": backup_name,
                    "existed": original is not None,
                    "original_sha256": original_sha256,
                    "path": relative,
                }
            )
        self.created_directories = sorted(
            missing_directories,
            key=lambda item: (len(item.relative_to(self.root).parts), item.as_posix()),
        )
        manifest = {
            "created_directories": [
                path.relative_to(self.root).as_posix()
                for path in self.created_directories
            ],
            "root": str(self.root),
            "schema_version": WRITE_JOURNAL_SCHEMA,
            "targets": targets,
        }
        _write_new_private_file(
            self.journal / "manifest.json",
            _canonical_json_bytes(manifest),
        )
        _fsync_directory(self.journal)
        self.prepared = True

    def write(self, path: Path, data: bytes) -> None:
        if not self.prepared or path not in self.originals:
            raise MemoryForestError(
                "invalid_transaction_state",
                "A write target was not captured in the durable journal.",
            )
        self._ensure_parents(path.parent)
        staged = self._stage(data)
        if os.path.lexists(path):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise MemoryForestError(
                    "unsafe_mutation_target",
                    "A mutation target changed to an unsafe file type.",
                    details={"path": _relative_or_name(self.root, path)},
                )
        os.replace(staged, path)
        os.chmod(path, 0o600, follow_symlinks=False)
        _fsync_directory(path.parent)

    def create(self, path: Path, data: bytes) -> None:
        if (
            not self.prepared
            or path not in self.originals
            or self.originals[path] is not None
        ):
            raise MemoryForestError(
                "invalid_transaction_state",
                "A create target was not journaled as absent.",
            )
        self._ensure_parents(path.parent)
        staged = self._stage(data)
        try:
            os.link(staged, path, follow_symlinks=False)
        except OSError as exc:
            raise MemoryForestError(
                "receipt_write_failed",
                "The transaction receipt could not be created safely.",
                details={"reason": exc.__class__.__name__},
            ) from exc
        os.chmod(path, 0o600, follow_symlinks=False)
        _fsync_directory(path.parent)
        staged.unlink()
        _fsync_directory(self.journal)

    def commit(self) -> None:
        if not self.prepared or self.closed:
            raise MemoryForestError(
                "invalid_transaction_state",
                "The write transaction is not open.",
            )
        _write_new_private_file(
            self.journal / "committed.json",
            _canonical_json_bytes(
                {
                    "root": str(self.root),
                    "schema_version": WRITE_JOURNAL_SCHEMA,
                    "state": "committed",
                }
            ),
        )
        self.closed = True
        _cleanup_write_journal(self.journal)
        self.originals.clear()
        self.created_directories.clear()

    def rollback(self) -> None:
        if self.closed:
            return
        failures = _restore_write_journal(self.root, self.journal, limits=self.limits)
        if not failures:
            _write_new_private_file(
                self.journal / "rolled-back.json",
                _canonical_json_bytes(
                    {
                        "root": str(self.root),
                        "schema_version": WRITE_JOURNAL_SCHEMA,
                        "state": "rolled-back",
                    }
                ),
            )
            _cleanup_write_journal(self.journal)
        self.closed = True
        if failures:
            raise MemoryForestError(
                "rollback_failed",
                "The failed write could not be fully rolled back.",
                details={"paths": sorted(set(failures))},
            )

    def _stage(self, data: bytes) -> Path:
        self.stage_counter += 1
        staged = self.journal / f"stage-{self.stage_counter:04d}.bin"
        _write_new_private_file(staged, data)
        return staged

    def _ensure_parents(self, parent: Path) -> None:
        relative = parent.relative_to(self.root)
        cursor = self.root
        for part in relative.parts:
            _reject_casefold_collision(cursor, part)
            cursor = cursor / part
            if os.path.lexists(cursor):
                info = cursor.lstat()
                if (
                    stat.S_ISLNK(info.st_mode)
                    or not stat.S_ISDIR(info.st_mode)
                    or stat.S_IMODE(info.st_mode) != 0o700
                ):
                    raise MemoryForestError(
                        "unsafe_parent_directory",
                        "Writer parent directories must be real and private.",
                        details={"path": _relative_or_name(self.root, cursor)},
                )
                continue
            if cursor not in self.created_directories:
                raise MemoryForestError(
                    "unjournaled_parent_directory",
                    "A required parent directory was not captured in the write journal.",
                    details={"path": _relative_or_name(self.root, cursor)},
                )
            cursor.mkdir(mode=0o700)
            os.chmod(cursor, 0o700, follow_symlinks=False)
            _fsync_directory(cursor.parent)


def _missing_private_parents(root: Path, parent: Path) -> set[Path]:
    relative = parent.relative_to(root)
    cursor = root
    missing: set[Path] = set()
    for part in relative.parts:
        _reject_casefold_collision(cursor, part)
        cursor = cursor / part
        if os.path.lexists(cursor):
            info = cursor.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise MemoryForestError(
                    "unsafe_parent_directory",
                    "Writer parent directories must be real and private.",
                    details={"path": _relative_or_name(root, cursor)},
                )
        else:
            missing.add(cursor)
    return missing


def _write_new_private_file(path: Path, data: bytes) -> None:
    pending = path.parent / f".{path.name}.pending"
    if os.path.lexists(pending):
        info = pending.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise MemoryForestError(
                "unsafe_write_journal",
                "A durable write-journal staging file is unsafe.",
            )
        pending.unlink()
        _fsync_directory(pending.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(pending, flags, 0o600)
    except OSError as exc:
        raise MemoryForestError(
            "write_journal_failed",
            "A durable write-journal file could not be created.",
            details={"reason": exc.__class__.__name__},
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(pending, 0o600, follow_symlinks=False)
        try:
            os.link(pending, path, follow_symlinks=False)
        except OSError as exc:
            raise MemoryForestError(
                "write_journal_failed",
                "A durable write-journal file could not be published.",
                details={"reason": exc.__class__.__name__},
            ) from exc
        _fsync_directory(path.parent)
        pending.unlink()
        _fsync_directory(path.parent)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            pending.unlink()
        except FileNotFoundError:
            pass
        raise


def _validate_write_journal_directory(journal: Path) -> None:
    info = journal.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or Path(os.path.realpath(journal)) != journal
    ):
        raise MemoryForestError(
            "unsafe_write_journal",
            "The write journal must be a real private directory.",
        )


def _read_write_journal_json(path: Path, *, maximum: int) -> object:
    try:
        raw = _read_private_regular_bytes(
            path,
            maximum=maximum,
            code="unsafe_write_journal",
        )
        return _load_strict_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, MemoryForestError) as exc:
        raise MemoryForestError(
            "unsafe_write_journal",
            "A durable write-journal JSON file is invalid.",
        ) from exc


def _read_journal_phase(root: Path, journal: Path, name: str) -> bool:
    path = journal / name
    if not os.path.lexists(path):
        return False
    value = _read_write_journal_json(path, maximum=MAX_PLAN_BYTES)
    expected_state = "committed" if name == "committed.json" else "rolled-back"
    if (
        not isinstance(value, dict)
        or set(value) != {"root", "schema_version", "state"}
        or value.get("root") != str(root)
        or value.get("schema_version") != WRITE_JOURNAL_SCHEMA
        or value.get("state") != expected_state
    ):
        raise MemoryForestError(
            "unsafe_write_journal",
            "A write-journal phase marker is invalid.",
        )
    return True


def _load_write_journal_manifest(
    root: Path,
    journal: Path,
    *,
    limits: ForestLimits,
) -> tuple[list[tuple[Path, bytes | None]], list[Path]] | None:
    manifest_path = journal / "manifest.json"
    if not os.path.lexists(manifest_path):
        return None
    value = _read_write_journal_json(
        manifest_path,
        maximum=limits.max_file_bytes,
    )
    if (
        not isinstance(value, dict)
        or set(value)
        != {"created_directories", "root", "schema_version", "targets"}
        or value.get("schema_version") != WRITE_JOURNAL_SCHEMA
        or value.get("root") != str(root)
        or not isinstance(value.get("targets"), list)
        or not 1 <= len(value["targets"]) <= MAX_JOURNAL_TARGETS
        or not isinstance(value.get("created_directories"), list)
        or len(value["created_directories"]) > limits.max_depth * MAX_JOURNAL_TARGETS
    ):
        raise MemoryForestError(
            "unsafe_write_journal",
            "The durable write-journal manifest is invalid.",
        )
    targets: list[tuple[Path, bytes | None]] = []
    seen_paths: set[str] = set()
    for item in value["targets"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"backup", "existed", "original_sha256", "path"}
            or not isinstance(item.get("path"), str)
            or type(item.get("existed")) is not bool
        ):
            raise MemoryForestError(
                "unsafe_write_journal",
                "A durable write-journal target is invalid.",
            )
        relative = PurePosixPath(item["path"])
        if (
            relative.is_absolute()
            or relative.as_posix() != item["path"]
            or any(part in {"", ".", ".."} for part in relative.parts)
            or item["path"] in seen_paths
        ):
            raise MemoryForestError(
                "unsafe_write_journal",
                "A durable write-journal path is invalid.",
            )
        seen_paths.add(item["path"])
        path = root / relative
        existed = item["existed"]
        if existed:
            backup = item.get("backup")
            original_sha256 = item.get("original_sha256")
            if (
                not isinstance(backup, str)
                or re.fullmatch(r"backup-[0-9]{4}\.bin", backup) is None
                or not isinstance(original_sha256, str)
                or _HASH_RE.fullmatch(original_sha256) is None
            ):
                raise MemoryForestError(
                    "unsafe_write_journal",
                    "A durable write-journal backup binding is invalid.",
                )
            original = _read_private_regular_bytes(
                journal / backup,
                maximum=limits.max_total_bytes,
                code="unsafe_write_journal",
            )
            if hashlib.sha256(original).hexdigest() != original_sha256:
                raise MemoryForestError(
                    "unsafe_write_journal",
                    "A durable write-journal backup hash is invalid.",
                )
        else:
            if item.get("backup") is not None or item.get("original_sha256") is not None:
                raise MemoryForestError(
                    "unsafe_write_journal",
                    "An absent write target may not carry a backup.",
                )
            original = None
        targets.append((path, original))
    directories: list[Path] = []
    seen_directories: set[str] = set()
    for item in value["created_directories"]:
        if not isinstance(item, str):
            raise MemoryForestError(
                "unsafe_write_journal",
                "A durable write-journal directory is invalid.",
            )
        relative = PurePosixPath(item)
        if (
            relative.is_absolute()
            or relative.as_posix() != item
            or any(part in {"", ".", ".."} for part in relative.parts)
            or item in seen_directories
        ):
            raise MemoryForestError(
                "unsafe_write_journal",
                "A durable write-journal directory is invalid.",
            )
        seen_directories.add(item)
        directories.append(root / relative)
    return targets, directories


def _restore_write_journal(
    root: Path,
    journal: Path,
    *,
    limits: ForestLimits,
) -> list[str]:
    loaded = _load_write_journal_manifest(root, journal, limits=limits)
    if loaded is None:
        return []
    targets, directories = loaded
    failures: list[str] = []
    for index, (path, original) in enumerate(reversed(targets)):
        try:
            if original is None:
                if os.path.lexists(path):
                    info = path.lstat()
                    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                        raise OSError("unsafe rollback target")
                    path.unlink()
                    _fsync_directory(path.parent)
            else:
                staged = journal / f"restore-{index:04d}.bin"
                if os.path.lexists(staged):
                    info = staged.lstat()
                    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                        raise OSError("unsafe recovery stage")
                    staged.unlink()
                    _fsync_directory(journal)
                _write_new_private_file(staged, original)
                _missing_private_parents(root, path.parent)
                os.replace(staged, path)
                os.chmod(path, 0o600, follow_symlinks=False)
                _fsync_directory(path.parent)
        except (OSError, MemoryForestError):
            failures.append(_relative_or_name(root, path))
    for directory in sorted(
        directories,
        key=lambda item: len(item.relative_to(root).parts),
        reverse=True,
    ):
        try:
            if os.path.lexists(directory):
                directory.rmdir()
                _fsync_directory(directory.parent)
        except OSError:
            failures.append(_relative_or_name(root, directory))
    return failures


def _cleanup_write_journal(journal: Path) -> None:
    _validate_write_journal_directory(journal)
    allowed = re.compile(
        r"(?:manifest|committed|rolled-back)\.json|"
        r"(?:backup|stage|restore)-[0-9]{4}\.bin|"
        r"\.(?:(?:manifest|committed|rolled-back)\.json|"
        r"(?:backup|stage|restore)-[0-9]{4}\.bin)\.pending|"
        r"index-[A-Za-z0-9_.-]+\.tmp"
    )
    with os.scandir(journal) as iterator:
        entries = list(iterator)
    if len(entries) > MAX_JOURNAL_TARGETS * 4:
        raise MemoryForestError(
            "unsafe_write_journal",
            "The write journal exceeds its entry bound.",
        )
    phases: list[Path] = []
    for entry in entries:
        path = Path(entry.path)
        info = entry.stat(follow_symlinks=False)
        if (
            allowed.fullmatch(entry.name) is None
            or stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
        ):
            raise MemoryForestError(
                "unsafe_write_journal",
                "The write journal contains an unsupported entry.",
            )
        if entry.name in {"committed.json", "rolled-back.json"}:
            phases.append(path)
        else:
            path.unlink()
    _fsync_directory(journal)
    for path in phases:
        path.unlink()
    _fsync_directory(journal)
    journal.rmdir()
    _fsync_directory(journal.parent)


def _recover_write_journal(root: Path, *, limits: ForestLimits) -> None:
    journal = Path(str(root) + WRITE_JOURNAL_SUFFIX)
    if not os.path.lexists(journal):
        return
    _validate_write_journal_directory(journal)
    committed = _read_journal_phase(root, journal, "committed.json")
    rolled_back = _read_journal_phase(root, journal, "rolled-back.json")
    if committed and rolled_back:
        raise MemoryForestError(
            "unsafe_write_journal",
            "The write journal has conflicting terminal states.",
        )
    if committed or rolled_back:
        _cleanup_write_journal(journal)
        return
    failures = _restore_write_journal(root, journal, limits=limits)
    if failures:
        raise MemoryForestError(
            "write_recovery_failed",
            "The interrupted write could not be fully recovered.",
            details={"paths": sorted(set(failures))},
        )
    _write_new_private_file(
        journal / "rolled-back.json",
        _canonical_json_bytes(
            {
                "root": str(root),
                "schema_version": WRITE_JOURNAL_SCHEMA,
                "state": "rolled-back",
            }
        ),
    )
    _cleanup_write_journal(journal)


def read_plan_source(
    source: str,
    *,
    stdin: BinaryIO | None = None,
) -> object:
    if source == "-":
        if stdin is None:
            raise MemoryForestError(
                "missing_plan_input",
                "Standard input was selected but no binary input stream was provided.",
            )
        data = stdin.read(MAX_PLAN_BYTES + 1)
        if len(data) > MAX_PLAN_BYTES:
            raise MemoryForestError(
                "plan_too_large",
                "The write plan exceeds the supported byte limit.",
                details={"limit": MAX_PLAN_BYTES},
            )
        return decode_plan_json(data)

    path = Path(os.path.abspath(os.path.expanduser(source)))
    if Path(os.path.realpath(path)) != path:
        raise MemoryForestError(
            "unsafe_plan_source",
            "The plan path must have no symlink components.",
            details={"path": str(path)},
        )
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise MemoryForestError(
            "plan_not_found",
            "The selected write plan does not exist.",
            details={"path": str(path)},
        ) from exc
    _validate_plan_file_info(before, path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MemoryForestError(
            "unsafe_plan_source",
            "The write plan could not be opened safely.",
            details={"reason": exc.__class__.__name__},
        ) from exc
    try:
        opened = os.fstat(descriptor)
        _validate_plan_file_info(opened, path)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise MemoryForestError(
                "plan_changed",
                "The write plan changed while it was being opened.",
            )
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(MAX_PLAN_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > MAX_PLAN_BYTES:
        raise MemoryForestError(
            "plan_too_large",
            "The write plan exceeds the supported byte limit.",
            details={"limit": MAX_PLAN_BYTES},
        )
    try:
        after = path.lstat()
    except FileNotFoundError as exc:
        raise MemoryForestError(
            "plan_changed",
            "The write plan changed while it was being read.",
        ) from exc
    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    ):
        raise MemoryForestError(
            "plan_changed",
            "The write plan changed while it was being read.",
        )
    return decode_plan_json(data)


def decode_plan_json(data: bytes | str) -> object:
    try:
        body = data.decode("utf-8") if isinstance(data, bytes) else data
    except UnicodeDecodeError as exc:
        raise MemoryForestError(
            "invalid_write_plan",
            "The write plan must be valid UTF-8 JSON.",
        ) from exc
    try:
        return _load_strict_json(body)
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise MemoryForestError(
            "invalid_write_plan",
            "The write plan must be strict JSON with unique object keys.",
        ) from exc


def validate_daily_plan(value: object) -> DailyPlan:
    plan = _exact_object(
        value,
        {
            "schema_version",
            "forest_id",
            "transaction_id",
            "date",
            "entries",
            "provenance",
        },
        code="invalid_daily_plan",
    )
    if plan["schema_version"] != DAILY_PLAN_SCHEMA:
        _invalid("invalid_daily_plan", "Unsupported Daily plan schema.")
    transaction_id = _hash(plan["transaction_id"], field="transaction_id")
    day = _iso_date(plan["date"])
    raw_entries = _bounded_array(
        plan["entries"],
        field="entries",
        minimum=0,
        maximum=MAX_DAILY_ENTRIES,
        code="invalid_daily_plan",
    )
    entries: list[DailyEntry] = []
    seen_entries: set[str] = set()
    for raw_entry in raw_entries:
        entry = _exact_object(
            raw_entry,
            {"entry_id", "source_record_ids", "summary"},
            code="invalid_daily_plan",
        )
        entry_id = _identifier(entry["entry_id"], field="entry_id")
        folded = entry_id.casefold()
        if folded in seen_entries:
            _invalid("invalid_daily_plan", "Daily entry identifiers must be unique.")
        seen_entries.add(folded)
        source_ids = _identifier_array(
            entry["source_record_ids"],
            field="source_record_ids",
        )
        entries.append(
            DailyEntry(
                entry_id=entry_id,
                source_record_ids=source_ids,
                summary=_text(
                    entry["summary"],
                    field="summary",
                    maximum=MAX_SUMMARY_CHARS,
                    multiline=True,
                ),
            )
        )
    provenance_value = _exact_object(
        plan["provenance"],
        {"packet_sha256", "result_sha256", "batch_id"},
        code="invalid_daily_plan",
    )
    provenance = DailyProvenance(
        packet_sha256=_hash(
            provenance_value["packet_sha256"], field="provenance.packet_sha256"
        ),
        result_sha256=_hash(
            provenance_value["result_sha256"], field="provenance.result_sha256"
        ),
        batch_id=_hash(provenance_value["batch_id"], field="provenance.batch_id"),
    )
    if provenance.batch_id != transaction_id:
        _invalid(
            "invalid_daily_plan",
            "provenance.batch_id must equal transaction_id.",
        )
    return DailyPlan(
        forest_id=_forest_id(plan["forest_id"]),
        transaction_id=transaction_id,
        day=day,
        entries=tuple(entries),
        provenance=provenance,
    )


def validate_promotion_plan(value: object) -> PromotionPlan:
    plan = _exact_object(
        value,
        {
            "schema_version",
            "forest_id",
            "transaction_id",
            "date",
            "promotions",
            "provenance",
        },
        code="invalid_promotion_plan",
    )
    if plan["schema_version"] != PROMOTION_PLAN_SCHEMA:
        _invalid("invalid_promotion_plan", "Unsupported promotion plan schema.")
    transaction_id = _hash(plan["transaction_id"], field="transaction_id")
    day = _iso_date(plan["date"])
    raw_promotions = _bounded_array(
        plan["promotions"],
        field="promotions",
        minimum=0,
        maximum=MAX_PROMOTIONS,
        code="invalid_promotion_plan",
    )
    promotions: list[Promotion] = []
    seen_routes: set[tuple[str, str, str]] = set()
    domain_titles: dict[str, str] = {}
    branch_titles: dict[tuple[str, str], str] = {}
    for raw_promotion in raw_promotions:
        promotion = _exact_object(
            raw_promotion,
            {"source_daily_entry_ids", "route", "title", "content", "confidence"},
            code="invalid_promotion_plan",
        )
        route_value = _exact_object(
            promotion["route"],
            {"domain", "domain_title", "branch", "branch_title", "leaf"},
            code="invalid_promotion_plan",
        )
        route = SemanticRoute(
            domain=_route_segment(route_value["domain"], field="route.domain"),
            domain_title=_text(
                route_value["domain_title"],
                field="route.domain_title",
                maximum=MAX_TITLE_CHARS,
                multiline=False,
            ),
            branch=_route_segment(route_value["branch"], field="route.branch"),
            branch_title=_text(
                route_value["branch_title"],
                field="route.branch_title",
                maximum=MAX_TITLE_CHARS,
                multiline=False,
            ),
            leaf=_route_segment(route_value["leaf"], field="route.leaf"),
        )
        route_key = (route.domain, route.branch, route.leaf)
        if route_key in seen_routes:
            _invalid(
                "invalid_promotion_plan",
                "A promotion plan may target each semantic route only once.",
            )
        seen_routes.add(route_key)
        previous_domain_title = domain_titles.setdefault(
            route.domain,
            route.domain_title,
        )
        previous_branch_title = branch_titles.setdefault(
            (route.domain, route.branch),
            route.branch_title,
        )
        if (
            previous_domain_title != route.domain_title
            or previous_branch_title != route.branch_title
        ):
            _invalid(
                "invalid_promotion_plan",
                "Shared semantic parents must use one exact display title.",
            )
        confidence = promotion["confidence"]
        if confidence not in {"low", "medium", "high"}:
            _invalid(
                "invalid_promotion_plan",
                "confidence must be exactly low, medium, or high.",
            )
        assert isinstance(confidence, str)
        promotions.append(
            Promotion(
                source_daily_entry_ids=_identifier_array(
                    promotion["source_daily_entry_ids"],
                    field="source_daily_entry_ids",
                ),
                route=route,
                title=_text(
                    promotion["title"],
                    field="title",
                    maximum=MAX_TITLE_CHARS,
                    multiline=False,
                ),
                content=_text(
                    promotion["content"],
                    field="content",
                    maximum=MAX_CONTENT_CHARS,
                    multiline=True,
                ),
                confidence=confidence,
            )
        )
    provenance_value = _exact_object(
        plan["provenance"],
        {"packet_sha256", "result_sha256", "daily_commit_sha256s"},
        code="invalid_promotion_plan",
    )
    daily_commits = tuple(
        _hash(item, field="provenance.daily_commit_sha256s")
        for item in _bounded_array(
            provenance_value["daily_commit_sha256s"],
            field="provenance.daily_commit_sha256s",
            minimum=0,
            maximum=MAX_DAILY_COMMITS,
            code="invalid_promotion_plan",
        )
    )
    if tuple(sorted(set(daily_commits))) != daily_commits:
        _invalid(
            "invalid_promotion_plan",
            "daily_commit_sha256s must contain sorted unique hashes.",
        )
    provenance = PromotionProvenance(
        packet_sha256=_hash(
            provenance_value["packet_sha256"], field="provenance.packet_sha256"
        ),
        result_sha256=_hash(
            provenance_value["result_sha256"], field="provenance.result_sha256"
        ),
        daily_commit_sha256s=daily_commits,
    )
    if provenance.result_sha256 != transaction_id:
        _invalid(
            "invalid_promotion_plan",
            "provenance.result_sha256 must equal transaction_id.",
        )
    return PromotionPlan(
        forest_id=_forest_id(plan["forest_id"]),
        transaction_id=transaction_id,
        day=day,
        promotions=tuple(promotions),
        provenance=provenance,
    )


def apply_daily(
    root: str | os.PathLike[str],
    plan_value: object,
    *,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    plan = validate_daily_plan(plan_value)
    canonical_plan = _canonical_json_bytes(plan_value)
    plan_sha256 = hashlib.sha256(canonical_plan).hexdigest()
    block = _render_daily_block(plan)
    relative = f"05 daily/{plan.day}.md"
    with maintenance_lock(root) as root_path:
        _recover_write_journal(root_path, limits=limits)
        _preflight(root_path, limits=limits)
        forest_id = load_forest_identity(root_path, limits=limits)
        if plan.forest_id != forest_id:
            raise MemoryForestError(
                "forest_identity_mismatch",
                "The Daily plan is bound to a different Memory Forest.",
            )
        receipt = _existing_receipt(
            root_path,
            operation="apply-daily",
            transaction_id=plan.transaction_id,
            plan_sha256=plan_sha256,
            forest_id=forest_id,
        )
        current = _read_optional_document(root_path, relative, limits=limits)
        exact_applied = bool(plan.entries) and current is not None and current.count(block) == 1
        if receipt is not None:
            if plan.entries and not exact_applied:
                raise MemoryForestError(
                    "receipt_state_mismatch",
                    "The receipt exists but the canonical Daily transaction is missing.",
                    details={"transaction_id": plan.transaction_id},
                )
            return _success_report(
                operation="apply-daily",
                transaction_id=plan.transaction_id,
                receipt=receipt,
                already_applied=True,
                touched=(),
            )
        start_marker = _daily_transaction_marker(plan.transaction_id)
        if plan.entries and current is not None and start_marker in current and not exact_applied:
            raise MemoryForestError(
                "transaction_conflict",
                "The Daily transaction identifier already exists with different bytes.",
                details={"transaction_id": plan.transaction_id},
            )
        sources = _collect_daily_sources(root_path, limits=limits)
        if plan.entries and not exact_applied:
            for entry in plan.entries:
                if entry.entry_id.casefold() in sources:
                    raise MemoryForestError(
                        "duplicate_daily_entry_id",
                        "A Daily entry identifier already exists in the canonical source lane.",
                        details={"entry_id": entry.entry_id},
                    )
        changes: list[tuple[str, bytes]] = []
        if plan.entries and not exact_applied:
            if current is None:
                updated = f"# Daily source, {plan.day}\n\n{block}"
            else:
                separator = "\n" if current.endswith("\n\n") else "\n\n"
                updated = current + separator + block
            changes.append((relative, updated.encode("utf-8")))
        return _execute_write(
            root_path,
            operation="apply-daily",
            forest_id=forest_id,
            transaction_id=plan.transaction_id,
            day=plan.day,
            plan_sha256=plan_sha256,
            changes=changes,
            already_applied=exact_applied,
            limits=limits,
        )


def promote(
    root: str | os.PathLike[str],
    plan_value: object,
    *,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    plan = validate_promotion_plan(plan_value)
    canonical_plan = _canonical_json_bytes(plan_value)
    plan_sha256 = hashlib.sha256(canonical_plan).hexdigest()
    with maintenance_lock(root) as root_path:
        _recover_write_journal(root_path, limits=limits)
        _preflight(root_path, limits=limits)
        forest_id = load_forest_identity(root_path, limits=limits)
        if plan.forest_id != forest_id:
            raise MemoryForestError(
                "forest_identity_mismatch",
                "The promotion plan is bound to a different Memory Forest.",
            )
        daily_sources = _collect_daily_sources(root_path, limits=limits)
        source_paths: dict[str, str] = {}
        bound_commits: set[str] = set()
        for promotion in plan.promotions:
            for source_id in promotion.source_daily_entry_ids:
                source = daily_sources.get(source_id.casefold())
                if source is None:
                    raise MemoryForestError(
                        "missing_daily_source",
                        "A promotion source identifier is absent from canonical Daily.",
                        details={"entry_id": source_id},
                    )
                source_paths[source_id] = source.path
                bound_commits.add(source.commit_sha256)
        if bound_commits != set(plan.provenance.daily_commit_sha256s):
            raise MemoryForestError(
                "daily_provenance_mismatch",
                "Promotion provenance does not exactly bind the selected Daily commits.",
                details={
                    "expected_count": len(bound_commits),
                    "received_count": len(plan.provenance.daily_commit_sha256s),
                },
            )
        receipt = _existing_receipt(
            root_path,
            operation="promote",
            transaction_id=plan.transaction_id,
            plan_sha256=plan_sha256,
            forest_id=forest_id,
        )
        changes = _promotion_changes(
            root_path,
            plan,
            source_paths=source_paths,
            limits=limits,
        )
        if receipt is not None:
            if changes:
                raise MemoryForestError(
                    "receipt_state_mismatch",
                    "The receipt exists but the promoted canonical state has changed.",
                    details={"transaction_id": plan.transaction_id},
                )
            return _success_report(
                operation="promote",
                transaction_id=plan.transaction_id,
                receipt=receipt,
                already_applied=True,
                touched=(),
            )
        return _execute_write(
            root_path,
            operation="promote",
            forest_id=forest_id,
            transaction_id=plan.transaction_id,
            day=plan.day,
            plan_sha256=plan_sha256,
            changes=changes,
            already_applied=bool(plan.promotions) and not changes,
            limits=limits,
        )


def _reserve_receipt_capacity(receipts: Path) -> None:
    if not os.path.lexists(receipts):
        return
    _validate_private_directory(receipts, code="unsafe_receipts_directory")
    with os.scandir(receipts) as iterator:
        count = sum(1 for _ in iterator)
    if count >= MAX_RECEIPTS:
        raise MemoryForestError(
            "receipt_capacity_exceeded",
            "The private receipt store is full; archive receipts before writing.",
            details={"limit": MAX_RECEIPTS},
        )


def _execute_write(
    root: Path,
    *,
    operation: str,
    forest_id: str,
    transaction_id: str,
    day: str,
    plan_sha256: str,
    changes: Sequence[tuple[str, bytes]],
    already_applied: bool,
    limits: ForestLimits,
) -> dict[str, object]:
    touched = tuple(sorted({relative for relative, _ in changes}))
    session = _MutationSession(root, limits=limits)
    index_path = root / STATE_DIRECTORY / INDEX_FILENAME
    receipts = root / STATE_DIRECTORY / RECEIPTS_DIRECTORY
    receipt_path = receipts / f"{transaction_id}.json"
    destinations: list[tuple[Path, bytes]] = []
    _reserve_receipt_capacity(receipts)
    if os.path.lexists(receipt_path):
        raise MemoryForestError(
            "receipt_collision",
            "A receipt appeared during the transaction.",
            details={"transaction_id": transaction_id},
        )
    for relative, data in changes:
        route = parse_relative_route(relative)
        if route.path != relative:
            raise MemoryForestError(
                "noncanonical_write_path",
                "Writer targets must use exact canonical paths.",
                details={"path": relative},
            )
        destination = root / PurePosixPath(relative)
        _reject_casefold_path(root, PurePosixPath(relative))
        destinations.append((destination, data))
    try:
        session.prepare(
            [index_path, receipt_path, *(path for path, _ in destinations)]
        )
        for destination, data in destinations:
            session.write(destination, data)
        validation = validate_forest(root, limits=limits)
        if not validation["ok"]:
            raise MemoryForestError(
                "postwrite_validation_failed",
                "The canonical write failed structural validation.",
                details={"errors": _report_error_codes(validation)},
            )
        audit = audit_forest(root, limits=limits)
        if not audit["ok"]:
            raise MemoryForestError(
                "postwrite_audit_failed",
                "The canonical write failed the adjacent-link audit.",
                details={"errors": _report_error_codes(audit)},
            )
        indexed = _index_forest_unlocked(
            root,
            limits=limits,
            staging_directory=session.journal,
        )
        receipt_payload = {
            "audit": _proof_summary(audit, include_links=True),
            "date": day,
            "forest_id": forest_id,
            "index": {
                "bytes_indexed": indexed["bytes_indexed"],
                "documents": indexed["documents"],
                "index": indexed["index"],
            },
            "ok": True,
            "operation": operation,
            "plan_sha256": plan_sha256,
            "schema_version": WRITE_RECEIPT_SCHEMA,
            "touched": list(touched),
            "transaction_id": transaction_id,
            "validation": _proof_summary(validation, include_links=False),
        }
        receipt_bytes = _canonical_json_bytes(receipt_payload)
        if len(receipt_bytes) > MAX_RECEIPT_BYTES:
            raise MemoryForestError(
                "receipt_too_large",
                "The deterministic receipt exceeds its supported byte limit.",
            )
        session.create(receipt_path, receipt_bytes)
        session.commit()
    except Exception:
        if os.path.lexists(session.journal):
            session.rollback()
        raise
    receipt = _read_receipt_path(receipt_path)
    return _success_report(
        operation=operation,
        transaction_id=transaction_id,
        receipt=receipt,
        already_applied=already_applied,
        touched=() if already_applied else touched,
    )


def _promotion_changes(
    root: Path,
    plan: PromotionPlan,
    *,
    source_paths: Mapping[str, str],
    limits: ForestLimits,
) -> list[tuple[str, bytes]]:
    bodies: dict[str, str] = {}
    originals: dict[str, str | None] = {}

    def body(relative: str, default: str | None = None) -> str:
        if relative in bodies:
            return bodies[relative]
        current = _read_optional_document(root, relative, limits=limits)
        originals[relative] = current
        if current is None:
            if default is None:
                raise MemoryForestError(
                    "missing_write_parent",
                    "A required canonical writer parent is missing.",
                    details={"path": relative},
                )
            current = default
        bodies[relative] = current
        return current

    root_relative = "01 xltm/XLTM.md"
    body(root_relative)
    for promotion in sorted(
        plan.promotions,
        key=lambda item: (item.route.domain, item.route.branch, item.route.leaf),
    ):
        route = promotion.route
        ltm = f"02 ltm/{route.domain}_LTM.md"
        mtm = f"03 mtm/{route.domain}/{route.branch}.md"
        stm = f"04 stm/{route.domain}/{route.branch}/{route.leaf}.md"
        root_link = f"../{ltm}"
        ltm_parent = "../01 xltm/XLTM.md"
        ltm_child = f"../{mtm}"
        mtm_parent = f"../../{ltm}"
        mtm_child = f"../../{stm}"
        stm_parent = f"../../../{mtm}"

        bodies[root_relative] = _ensure_managed_link(
            body(root_relative),
            target=root_link,
            label="Domain",
            child_path=ltm,
        )
        ltm_default = (
            f"# {_escape_heading(route.domain_title)}\n\n"
            f"Parent: [[{ltm_parent}]]\n"
        )
        ltm_body = _ensure_link(body(ltm, ltm_default), target=ltm_parent, label="Parent")
        bodies[ltm] = _ensure_managed_link(
            ltm_body,
            target=ltm_child,
            label="Branch",
            child_path=mtm,
        )
        mtm_default = (
            f"# {_escape_heading(route.branch_title)}\n\n"
            f"Parent: [[{mtm_parent}]]\n"
        )
        mtm_body = _ensure_link(body(mtm, mtm_default), target=mtm_parent, label="Parent")
        bodies[mtm] = _ensure_managed_link(
            mtm_body,
            target=mtm_child,
            label="Detail",
            child_path=stm,
        )
        stm_default = (
            f"# {_escape_heading(promotion.title)}\n\n"
            f"Parent: [[{stm_parent}]]\n"
        )
        leaf_body = _ensure_link(
            body(stm, stm_default),
            target=stm_parent,
            label="Parent",
        )
        daily_paths = sorted(
            {source_paths[source_id] for source_id in promotion.source_daily_entry_ids}
        )
        block = _render_promotion_block(plan, promotion, daily_paths=daily_paths)
        marker = _promotion_marker(plan.transaction_id)
        if marker in leaf_body and leaf_body.count(block) != 1:
            raise MemoryForestError(
                "transaction_conflict",
                "The promotion transaction identifier already exists with different bytes.",
                details={"path": stm, "transaction_id": plan.transaction_id},
            )
        if leaf_body.count(block) == 0:
            leaf_body = _append_block(leaf_body, block)
        bodies[stm] = leaf_body

    changes: list[tuple[str, bytes]] = []
    for relative in sorted(
        bodies,
        key=lambda value: (
            int(value[:2]) if value[:2].isdigit() else 99,
            value,
        ),
    ):
        if bodies[relative] != originals.get(relative):
            changes.append((relative, bodies[relative].encode("utf-8")))
    return changes


def _render_daily_block(plan: DailyPlan) -> str:
    lines = [
        _daily_transaction_marker(plan.transaction_id),
        f"## Admitted Daily batch `{plan.transaction_id}`",
        "",
    ]
    for entry in plan.entries:
        lines.extend(
            (
                f"### Entry `{entry.entry_id}`",
                (
                    f"<!-- memory-forest-daily-entry-v1:{entry.entry_id}:"
                    f"{plan.provenance.result_sha256} -->"
                ),
                "",
                "```json",
                _canonical_json_text(
                    {
                        "entry_id": entry.entry_id,
                        "source_record_ids": list(entry.source_record_ids),
                        "summary": entry.summary,
                    }
                ),
                "```",
                "",
            )
        )
    lines.extend(
        (
            "### Provenance",
            "",
            "```json",
            _canonical_json_text(
                {
                    "batch_id": plan.provenance.batch_id,
                    "packet_sha256": plan.provenance.packet_sha256,
                    "result_sha256": plan.provenance.result_sha256,
                }
            ),
            "```",
            f"<!-- /memory-forest-daily-transaction-v1:{plan.transaction_id} -->",
            "",
        )
    )
    return "\n".join(lines)


def _render_promotion_block(
    plan: PromotionPlan,
    promotion: Promotion,
    *,
    daily_paths: Sequence[str],
) -> str:
    lines = [
        _promotion_marker(plan.transaction_id),
        f"## Promoted update, {plan.day}",
        "",
    ]
    for daily_path in daily_paths:
        lines.append(f"Daily evidence: [[../../../{daily_path}]]")
    lines.extend(
        (
            "",
            "```json",
            _canonical_json_text(
                {
                    "confidence": promotion.confidence,
                    "content": promotion.content,
                    "source_daily_entry_ids": list(
                        promotion.source_daily_entry_ids
                    ),
                    "title": promotion.title,
                }
            ),
            "```",
            f"<!-- /memory-forest-promotion-v1:{plan.transaction_id} -->",
            "",
        )
    )
    return "\n".join(lines)


def _collect_daily_sources(
    root: Path,
    *,
    limits: ForestLimits,
) -> dict[str, _DailySource]:
    daily = root / "05 daily"
    _validate_private_directory(daily, code="unsafe_daily_directory")
    sources: dict[str, _DailySource] = {}
    observed_marker_count = 0
    accepted_marker_count = 0
    with os.scandir(daily) as iterator:
        entries = sorted(iterator, key=lambda item: item.name)
    for entry in entries:
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise MemoryForestError(
                "unsafe_daily_entry",
                "Canonical Daily may contain only regular Markdown files.",
                details={"path": f"05 daily/{entry.name}"},
            )
        relative = f"05 daily/{entry.name}"
        parse_relative_route(relative)
        body = _read_utf8_file(Path(entry.path), limits=limits)
        observed_marker_count += len(tuple(_DAILY_ENTRY_MARKER_RE.finditer(body)))
        for entry_id, commit in _parse_daily_machine_blocks(body, path=relative):
            accepted_marker_count += 1
            key = entry_id.casefold()
            if key in sources:
                raise MemoryForestError(
                    "duplicate_daily_entry_id",
                    "A Daily entry identifier appears more than once.",
                    details={"entry_id": entry_id},
                )
            sources[key] = _DailySource(
                path=relative,
                commit_sha256=commit,
            )
    if observed_marker_count != accepted_marker_count:
        raise MemoryForestError(
            "invalid_daily_machine_block",
            "A Daily entry marker exists outside a valid provenance-bound block.",
        )
    return sources


def _parse_daily_machine_blocks(
    document: str,
    *,
    path: str,
) -> list[tuple[str, str]]:
    parsed_entries: list[tuple[str, str]] = []
    for transaction_match in _DAILY_TRANSACTION_BLOCK_RE.finditer(document):
        transaction = transaction_match.group("transaction")
        body = transaction_match.group("body")
        provenance_match = _DAILY_PROVENANCE_BLOCK_RE.search(body)
        if provenance_match is None:
            raise MemoryForestError(
                "invalid_daily_machine_block",
                "A Daily machine block is missing exact provenance.",
                details={"path": path, "transaction_id": transaction},
            )
        provenance = _strict_embedded_object(
            provenance_match.group("payload"),
            keys={"batch_id", "packet_sha256", "result_sha256"},
            path=path,
        )
        try:
            batch_id = _hash(provenance["batch_id"], field="batch_id")
            _hash(provenance["packet_sha256"], field="packet_sha256")
            commit = _hash(provenance["result_sha256"], field="result_sha256")
        except MemoryForestError as exc:
            raise MemoryForestError(
                "invalid_daily_machine_block",
                "A Daily machine block contains invalid provenance hashes.",
                details={"path": path, "transaction_id": transaction},
            ) from exc
        if batch_id != transaction:
            raise MemoryForestError(
                "invalid_daily_machine_block",
                "A Daily machine block batch does not match its transaction.",
                details={"path": path, "transaction_id": transaction},
            )
        entries_region = body[: provenance_match.start()]
        entry_matches = tuple(_DAILY_ENTRY_BLOCK_RE.finditer(entries_region))
        marker_count = len(tuple(_DAILY_ENTRY_MARKER_RE.finditer(entries_region)))
        if not entry_matches or marker_count != len(entry_matches):
            raise MemoryForestError(
                "invalid_daily_machine_block",
                "Daily entry markers must use the exact machine block shape.",
                details={"path": path, "transaction_id": transaction},
            )
        for entry_match in entry_matches:
            entry_id = entry_match.group("entry_id")
            if (
                entry_match.group("title_id") != entry_id
                or entry_match.group("commit") != commit
            ):
                raise MemoryForestError(
                    "invalid_daily_machine_block",
                    "A Daily entry marker is not bound to its payload and provenance.",
                    details={"path": path, "transaction_id": transaction},
                )
            payload = _strict_embedded_object(
                entry_match.group("payload"),
                keys={"entry_id", "source_record_ids", "summary"},
                path=path,
            )
            try:
                payload_id = _identifier(payload["entry_id"], field="entry_id")
                _identifier_array(
                    payload["source_record_ids"],
                    field="source_record_ids",
                )
                _text(
                    payload["summary"],
                    field="summary",
                    maximum=MAX_SUMMARY_CHARS,
                    multiline=True,
                )
            except MemoryForestError as exc:
                raise MemoryForestError(
                    "invalid_daily_machine_block",
                    "A Daily entry payload violates the v1 bounds.",
                    details={"path": path, "transaction_id": transaction},
                ) from exc
            if payload_id != entry_id:
                raise MemoryForestError(
                    "invalid_daily_machine_block",
                    "A Daily entry marker does not match its payload identifier.",
                    details={"path": path, "transaction_id": transaction},
                )
            parsed_entries.append((entry_id, commit))
    return parsed_entries


def _strict_embedded_object(
    payload: str,
    *,
    keys: set[str],
    path: str,
) -> dict[str, object]:
    try:
        parsed = _load_strict_json(payload)
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise MemoryForestError(
            "invalid_daily_machine_block",
            "A Daily machine payload is not strict JSON.",
            details={"path": path},
        ) from exc
    if not isinstance(parsed, dict) or set(parsed) != keys:
        raise MemoryForestError(
            "invalid_daily_machine_block",
            "A Daily machine payload has missing or unsupported fields.",
            details={"path": path},
        )
    return parsed


def _existing_receipt(
    root: Path,
    *,
    operation: str,
    transaction_id: str,
    plan_sha256: str,
    forest_id: str,
) -> tuple[str, bytes, dict[str, object]] | None:
    receipts = root / STATE_DIRECTORY / RECEIPTS_DIRECTORY
    if not os.path.lexists(receipts):
        return None
    _validate_private_directory(receipts, code="unsafe_receipts_directory")
    path = receipts / f"{transaction_id}.json"
    if not os.path.lexists(path):
        return None
    relative, data, parsed = _read_receipt_path(path)
    if (
        parsed.get("schema_version") != WRITE_RECEIPT_SCHEMA
        or parsed.get("ok") is not True
        or parsed.get("operation") != operation
        or parsed.get("transaction_id") != transaction_id
        or parsed.get("plan_sha256") != plan_sha256
        or parsed.get("forest_id") != forest_id
    ):
        raise MemoryForestError(
            "receipt_conflict",
            "The existing receipt does not bind this exact write plan.",
            details={"transaction_id": transaction_id},
        )
    return relative, data, parsed


def _read_receipt_path(path: Path) -> tuple[str, bytes, dict[str, object]]:
    data = _read_private_regular_bytes(
        path,
        maximum=MAX_RECEIPT_BYTES,
        code="unsafe_receipt_entry",
    )
    try:
        parsed = _load_strict_json(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise MemoryForestError(
            "invalid_receipt",
            "The transaction receipt is not strict UTF-8 JSON.",
        ) from exc
    if not isinstance(parsed, dict):
        raise MemoryForestError(
            "invalid_receipt",
            "The transaction receipt must be a JSON object.",
        )
    expected = {
        "audit",
        "date",
        "forest_id",
        "index",
        "ok",
        "operation",
        "plan_sha256",
        "schema_version",
        "touched",
        "transaction_id",
        "validation",
    }
    if set(parsed) != expected:
        raise MemoryForestError(
            "invalid_receipt",
            "The transaction receipt contains unsupported fields.",
        )
    if data != _canonical_json_bytes(parsed):
        raise MemoryForestError(
            "invalid_receipt",
            "The transaction receipt must use canonical JSON bytes.",
        )
    if parsed.get("schema_version") != WRITE_RECEIPT_SCHEMA:
        raise MemoryForestError(
            "invalid_receipt",
            "The transaction receipt schema is unsupported.",
        )
    if parsed.get("ok") is not True:
        raise MemoryForestError(
            "invalid_receipt",
            "A successful receipt must bind ok to true.",
        )
    forest_id = parsed.get("forest_id")
    if not isinstance(forest_id, str) or _FOREST_ID_RE.fullmatch(forest_id) is None:
        raise MemoryForestError(
            "invalid_receipt",
            "The receipt Memory Forest identity is invalid.",
        )
    operation = parsed.get("operation")
    if operation not in {"apply-daily", "promote"}:
        raise MemoryForestError(
            "invalid_receipt",
            "The receipt operation is unsupported.",
        )
    transaction_id = parsed.get("transaction_id")
    if not isinstance(transaction_id, str) or _HASH_RE.fullmatch(transaction_id) is None:
        raise MemoryForestError("invalid_receipt", "The receipt transaction is invalid.")
    if path.name != f"{transaction_id}.json":
        raise MemoryForestError(
            "invalid_receipt",
            "The receipt filename does not match its transaction.",
        )
    plan_sha256 = parsed.get("plan_sha256")
    if not isinstance(plan_sha256, str) or _HASH_RE.fullmatch(plan_sha256) is None:
        raise MemoryForestError(
            "invalid_receipt",
            "The receipt plan digest is invalid.",
        )
    try:
        _iso_date(parsed.get("date"))
    except MemoryForestError as exc:
        raise MemoryForestError(
            "invalid_receipt",
            "The receipt date is invalid.",
        ) from exc
    touched = parsed.get("touched")
    if not isinstance(touched, list) or any(not isinstance(item, str) for item in touched):
        raise MemoryForestError("invalid_receipt", "The receipt touched list is invalid.")
    if touched != sorted(set(touched)):
        raise MemoryForestError(
            "invalid_receipt",
            "Receipt paths must be sorted and unique.",
        )
    for item in touched:
        parse_relative_route(item)
    _validate_receipt_proof(parsed.get("validation"), audit=False)
    _validate_receipt_proof(parsed.get("audit"), audit=True)
    index = parsed.get("index")
    if not isinstance(index, dict) or set(index) != {
        "bytes_indexed",
        "documents",
        "index",
    }:
        raise MemoryForestError(
            "invalid_receipt",
            "The receipt index proof has an invalid shape.",
        )
    _nonnegative_receipt_int(index.get("bytes_indexed"), field="bytes_indexed")
    _nonnegative_receipt_int(index.get("documents"), field="documents")
    if index.get("index") != f"{STATE_DIRECTORY}/{INDEX_FILENAME}":
        raise MemoryForestError(
            "invalid_receipt",
            "The receipt index path is not canonical.",
        )
    relative = f"{STATE_DIRECTORY}/{RECEIPTS_DIRECTORY}/{path.name}"
    return relative, data, parsed


def _validate_receipt_proof(value: object, *, audit: bool) -> None:
    expected = {"documents", "errors", "ok", "warnings"}
    if audit:
        expected.add("links")
    if not isinstance(value, dict) or set(value) != expected:
        raise MemoryForestError(
            "invalid_receipt",
            "A receipt verification proof has an invalid shape.",
        )
    _nonnegative_receipt_int(value.get("documents"), field="documents")
    _nonnegative_receipt_int(value.get("warnings"), field="warnings")
    if value.get("errors") != 0 or type(value.get("errors")) is not int:
        raise MemoryForestError(
            "invalid_receipt",
            "A successful receipt proof must contain zero integer errors.",
        )
    if value.get("ok") is not True:
        raise MemoryForestError(
            "invalid_receipt",
            "A successful receipt proof must bind ok to true.",
        )
    if audit:
        _nonnegative_receipt_int(value.get("links"), field="links")


def _nonnegative_receipt_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise MemoryForestError(
            "invalid_receipt",
            "A receipt count must be a nonnegative integer.",
            details={"field": field},
        )
    return value


def _success_report(
    *,
    operation: str,
    transaction_id: str,
    receipt: tuple[str, bytes, dict[str, object]],
    already_applied: bool,
    touched: Sequence[str],
) -> dict[str, object]:
    relative, data, parsed = receipt
    return {
        "already_applied": already_applied,
        "forest_id": parsed["forest_id"],
        "ok": True,
        "operation": operation,
        "receipt": relative,
        "receipt_sha256": hashlib.sha256(data).hexdigest(),
        "schema_version": SCHEMA_VERSION,
        "touched": sorted(set(touched)),
        "transaction_id": transaction_id,
    }


def _preflight(root: Path, *, limits: ForestLimits) -> None:
    validation = validate_forest(root, limits=limits)
    if not validation["ok"]:
        raise MemoryForestError(
            "preflight_validation_failed",
            "Canonical writes require a forest that already validates.",
            details={"errors": _report_error_codes(validation)},
        )
    audit = audit_forest(root, limits=limits)
    if not audit["ok"]:
        raise MemoryForestError(
            "preflight_audit_failed",
            "Canonical writes require a forest that already passes audit.",
            details={"errors": _report_error_codes(audit)},
        )


def _report_error_codes(report: Mapping[str, object]) -> list[str]:
    issues = report.get("issues")
    if not isinstance(issues, list):
        return []
    return sorted(
        {
            str(issue.get("code"))
            for issue in issues
            if isinstance(issue, dict) and issue.get("level") == "error"
        }
    )


def _proof_summary(
    report: Mapping[str, object],
    *,
    include_links: bool,
) -> dict[str, object]:
    summary = report["summary"]
    assert isinstance(summary, dict)
    result: dict[str, object] = {
        "documents": summary["documents"],
        "errors": summary["errors"],
        "ok": report["ok"],
        "warnings": summary["warnings"],
    }
    if include_links:
        result["links"] = summary["links"]
    return result


def _read_optional_document(
    root: Path,
    relative: str,
    *,
    limits: ForestLimits,
) -> str | None:
    parse_relative_route(relative)
    path = root / PurePosixPath(relative)
    _reject_casefold_path(root, PurePosixPath(relative), missing_ok=True)
    if not os.path.lexists(path):
        return None
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise MemoryForestError(
            "unsafe_mutation_target",
            "Canonical write targets must be real private files.",
            details={"path": relative},
        )
    return _read_utf8_file(path, limits=limits)


def _ensure_managed_link(
    body: str,
    *,
    target: str,
    label: str,
    child_path: str,
) -> str:
    if _contains_link(body, target):
        return body
    block = (
        f"<!-- memory-forest-child-v1:{child_path} -->\n"
        f"{label}: [[{target}]]\n"
    )
    return _append_block(body, block)


def _ensure_link(body: str, *, target: str, label: str) -> str:
    if _contains_link(body, target):
        return body
    return _append_block(body, f"{label}: [[{target}]]\n")


def _contains_link(body: str, target: str) -> bool:
    for match in _WIKILINK_RE.finditer(body):
        raw = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        if raw == target:
            return True
    return False


def _append_block(body: str, block: str) -> str:
    if not body:
        return block if block.endswith("\n") else block + "\n"
    separator = "\n" if body.endswith("\n\n") else "\n\n"
    return body + separator + (block if block.endswith("\n") else block + "\n")


def _daily_transaction_marker(transaction_id: str) -> str:
    return f"<!-- memory-forest-daily-transaction-v1:{transaction_id} -->"


def _promotion_marker(transaction_id: str) -> str:
    return f"<!-- memory-forest-promotion-v1:{transaction_id} -->"


def _escape_heading(value: str) -> str:
    escaped = html.escape(value, quote=False)
    escaped = escaped.replace("[", "&#91;").replace("]", "&#93;")
    for character in ("\\", "`", "*", "_", "#", "(", ")", "!", "|"):
        escaped = escaped.replace(character, "\\" + character)
    return escaped


def _canonical_json_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_json_bytes(value: object) -> bytes:
    return (_canonical_json_text(value) + "\n").encode("utf-8")


def _exact_object(
    value: object,
    keys: set[str],
    *,
    code: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        _invalid(code, "A plan object has missing or unsupported fields.")
    return cast(dict[str, object], value)


def _bounded_array(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
    code: str,
) -> list[object]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _invalid(
            code,
            f"{field} must contain between {minimum} and {maximum} items.",
        )
    return cast(list[object], value)


def _identifier_array(value: object, *, field: str) -> tuple[str, ...]:
    raw = _bounded_array(
        value,
        field=field,
        minimum=1,
        maximum=MAX_SOURCE_IDS,
        code="invalid_write_plan",
    )
    result = tuple(_identifier(item, field=field) for item in raw)
    if len({item.casefold() for item in result}) != len(result):
        _invalid("invalid_write_plan", f"{field} must contain unique identifiers.")
    return result


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        _invalid(
            "invalid_write_plan",
            f"{field} must be a bounded portable identifier.",
        )
    return cast(str, value)


def _route_segment(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _ROUTE_SEGMENT_RE.fullmatch(value) is None:
        _invalid(
            "invalid_promotion_plan",
            f"{field} must be a lowercase ASCII semantic slug.",
        )
    return cast(str, value)


def _hash(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _invalid(
            "invalid_write_plan",
            f"{field} must be exactly 64 lowercase hexadecimal characters.",
        )
    return cast(str, value)


def _forest_id(value: object) -> str:
    if not isinstance(value, str) or _FOREST_ID_RE.fullmatch(value) is None:
        _invalid(
            "invalid_write_plan",
            "forest_id must be exactly 32 lowercase hexadecimal characters.",
        )
    return cast(str, value)


def _iso_date(value: object) -> str:
    if not isinstance(value, str):
        _invalid("invalid_write_plan", "date must be an ISO YYYY-MM-DD string.")
    text = cast(str, value)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        _invalid("invalid_write_plan", "date must be a valid ISO calendar date.")
    if parsed.isoformat() != text:
        _invalid("invalid_write_plan", "date must use exact YYYY-MM-DD form.")
    return text


def _text(
    value: object,
    *,
    field: str,
    maximum: int,
    multiline: bool,
) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _invalid(
            "invalid_write_plan",
            f"{field} must be non-empty and at most {maximum} characters.",
        )
    text = cast(str, value)
    if unicodedata.normalize("NFC", text) != text:
        _invalid("invalid_write_plan", f"{field} must use NFC Unicode.")
    for character in text:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            _invalid("invalid_write_plan", f"{field} contains an invalid surrogate.")
        if codepoint < 0x20 and character not in ("\n", "\t"):
            _invalid("invalid_write_plan", f"{field} contains a forbidden control.")
        if codepoint == 0x7F:
            _invalid("invalid_write_plan", f"{field} contains a forbidden control.")
    if not multiline and ("\n" in text or "\r" in text or "\t" in text):
        _invalid("invalid_write_plan", f"{field} must be a single line.")
    return text


def _validate_plan_file_info(info: os.stat_result, path: Path) -> None:
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise MemoryForestError(
            "unsafe_plan_source",
            "The write plan must be a regular non-symlink file.",
            details={"path": str(path)},
        )
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise MemoryForestError(
            "unsafe_plan_permissions",
            "The write plan must be owned by the current user.",
            details={"path": str(path)},
        )
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o177 or not mode & 0o400:
        raise MemoryForestError(
            "unsafe_plan_permissions",
            "The write plan must be private, non-executable, and owner-readable.",
            details={"mode": f"{mode:04o}", "path": str(path)},
        )


def _validate_private_directory(path: Path, *, code: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise MemoryForestError(
            code,
            "A required private directory is missing.",
            details={"path": str(path)},
        ) from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or Path(os.path.realpath(path)) != path
    ):
        raise MemoryForestError(
            code,
            "A required directory must be real and mode 0700.",
            details={"path": str(path)},
        )


def _read_private_regular_bytes(
    path: Path,
    *,
    maximum: int,
    code: str,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MemoryForestError(
            code,
            "A private writer file could not be opened safely.",
            details={"reason": exc.__class__.__name__},
        ) from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > maximum
        ):
            raise MemoryForestError(
                code,
                "A private writer file has an unsafe type, mode, or size.",
            )
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(maximum + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > maximum:
        raise MemoryForestError(code, "A private writer file exceeds its byte limit.")
    return data


def _reject_casefold_path(
    root: Path,
    relative: PurePosixPath,
    *,
    missing_ok: bool = True,
) -> None:
    cursor = root
    for part in relative.parts:
        _reject_casefold_collision(cursor, part)
        candidate = cursor / part
        if not os.path.lexists(candidate):
            if missing_ok:
                cursor = candidate
                continue
            raise MemoryForestError(
                "path_not_found",
                "A canonical writer path is missing.",
                details={"path": relative.as_posix()},
            )
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise MemoryForestError(
                "symlink_forbidden",
                "Symlinks are forbidden in canonical writer paths.",
                details={"path": relative.as_posix()},
            )
        cursor = candidate


def _reject_casefold_collision(parent: Path, name: str) -> None:
    if not parent.exists():
        return
    folded = name.casefold()
    with os.scandir(parent) as iterator:
        entries = list(iterator)
    for entry in entries:
        if entry.name.casefold() == folded and entry.name != name:
            raise MemoryForestError(
                "casefold_collision",
                "A differently cased filesystem entry collides with a canonical path.",
                details={"expected": name, "existing": entry.name},
            )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _relative_or_name(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _invalid(code: str, message: str) -> NoReturn:
    raise MemoryForestError(code, message)
