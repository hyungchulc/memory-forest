from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .errors import MemoryForestError


STATE_DIRECTORY: Final[str] = ".memory-forest"
RECEIPTS_DIRECTORY: Final[str] = "receipts"
RECEIPT_NAME_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\.json")
MAX_RECEIPTS: Final[int] = 10_000
MAX_RECEIPT_BYTES: Final[int] = 256 * 1024


@dataclass(frozen=True, slots=True)
class ForestLimits:
    max_files: int = 10_000
    max_directories: int = 20_000
    max_depth: int = 32
    max_total_bytes: int = 256 * 1024 * 1024
    max_file_bytes: int = 4 * 1024 * 1024
    max_links: int = 200_000
    max_results: int = 100

    def __post_init__(self) -> None:
        for name in (
            "max_files",
            "max_directories",
            "max_depth",
            "max_total_bytes",
            "max_file_bytes",
            "max_links",
            "max_results",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


DEFAULT_LIMITS: Final[ForestLimits] = ForestLimits()


@dataclass(frozen=True, slots=True)
class ScannedDirectory:
    path: Path
    relative: str
    mode: int


@dataclass(frozen=True, slots=True)
class ScannedFile:
    path: Path
    relative: str
    mode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class ScanResult:
    root: Path
    directories: tuple[ScannedDirectory, ...]
    files: tuple[ScannedFile, ...]
    total_bytes: int


def _absolute_lexical(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _assert_real(path: Path, *, code: str = "symlink_forbidden") -> None:
    real = Path(os.path.realpath(path))
    if real != path:
        raise MemoryForestError(
            code,
            "The selected path must be a real path with no symlink components.",
            details={"path": str(path), "real_path": str(real)},
        )


def prepare_new_root(path: str | os.PathLike[str]) -> tuple[Path, bool]:
    candidate = _absolute_lexical(path)
    if os.path.lexists(candidate):
        try:
            info = candidate.lstat()
        except FileNotFoundError as exc:
            raise MemoryForestError(
                "unsafe_root",
                "The forest root changed while it was being inspected.",
                details={"path": str(candidate)},
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise MemoryForestError(
                "symlink_forbidden",
                "The selected forest root may not be a symlink.",
                details={"path": str(candidate)},
            )
        return require_real_root(candidate), True
    parent = candidate.parent
    if not parent.exists() or not parent.is_dir():
        raise MemoryForestError(
            "missing_parent",
            "The forest root requires an existing real parent directory.",
            details={"parent": str(parent)},
        )
    _assert_real(parent)
    return candidate, False


def require_real_root(path: str | os.PathLike[str]) -> Path:
    root = _absolute_lexical(path)
    try:
        info = root.lstat()
    except FileNotFoundError as exc:
        raise MemoryForestError(
            "root_not_found",
            "The selected forest root does not exist.",
            details={"path": str(root)},
        ) from exc
    if stat.S_ISLNK(info.st_mode):
        raise MemoryForestError(
            "symlink_forbidden",
            "The selected forest root may not be a symlink.",
            details={"path": str(root)},
        )
    if not stat.S_ISDIR(info.st_mode):
        raise MemoryForestError(
            "root_not_directory",
            "The selected forest root is not a directory.",
            details={"path": str(root)},
        )
    _assert_real(root)
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise MemoryForestError(
            "root_not_owned",
            "The selected forest root must be owned by the current user.",
            details={"path": str(root)},
        )
    return root


def ensure_inside(root: Path, path: Path, *, must_exist: bool = True) -> Path:
    root = require_real_root(root)
    candidate = _absolute_lexical(path if path.is_absolute() else root / path)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MemoryForestError(
            "path_escape",
            "The requested path escapes the selected forest root.",
            details={"path": str(candidate), "root": str(root)},
        ) from exc
    if must_exist:
        try:
            info = candidate.lstat()
        except FileNotFoundError as exc:
            raise MemoryForestError(
                "path_not_found",
                "The requested path does not exist.",
                details={"path": str(candidate)},
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise MemoryForestError(
                "symlink_forbidden",
                "Symlinks are not allowed inside a Memory Forest.",
                details={"path": str(candidate)},
            )
        _assert_real(candidate)
    return candidate


def secure_mkdir(path: Path, *, mode: int = 0o700) -> bool:
    if path.exists():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise MemoryForestError(
                "unsafe_existing_path",
                "An expected directory exists as a symlink or non-directory.",
                details={"path": str(path)},
            )
        os.chmod(path, mode, follow_symlinks=False)
        return False
    path.mkdir(mode=mode)
    os.chmod(path, mode, follow_symlinks=False)
    return True


def secure_create_file(path: Path, data: bytes, *, mode: int = 0o600) -> bool:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
    try:
        descriptor = os.open(path, flags, mode)
    except FileExistsError:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise MemoryForestError(
                "unsafe_existing_path",
                "An expected file exists as a symlink or non-file.",
                details={"path": str(path)},
            )
        return False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    os.chmod(path, mode, follow_symlinks=False)
    return True


def scan_forest(
    root: str | os.PathLike[str],
    *,
    limits: ForestLimits = DEFAULT_LIMITS,
) -> ScanResult:
    safe_root = require_real_root(root)
    directories: list[ScannedDirectory] = []
    files: list[ScannedFile] = []
    total_bytes = 0

    def visit(directory: Path, *, depth: int) -> None:
        nonlocal total_bytes
        try:
            entries: list[os.DirEntry[str]] = []
            entry_limit = (
                limits.max_files
                - len(files)
                + limits.max_directories
                - len(directories)
                + (1 if directory == safe_root else 0)
            )
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    entries.append(entry)
                    if len(entries) > entry_limit:
                        raise MemoryForestError(
                            "directory_entry_count_exceeded",
                            "A forest directory exceeds the remaining traversal budget.",
                            details={"limit": entry_limit},
                        )
            entries.sort(key=lambda item: item.name)
        except OSError as exc:
            raise MemoryForestError(
                "scan_failed",
                "A forest directory could not be scanned.",
                details={"path": str(directory), "reason": exc.__class__.__name__},
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(safe_root).as_posix()
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise MemoryForestError(
                    "symlink_forbidden",
                    "Symlinks are not allowed inside a Memory Forest.",
                    details={"path": relative},
                )
            if stat.S_ISDIR(info.st_mode):
                if path.parent == safe_root and entry.name == STATE_DIRECTORY:
                    _validate_state_directory(path)
                    continue
                child_depth = depth + 1
                if child_depth > limits.max_depth:
                    raise MemoryForestError(
                        "directory_depth_exceeded",
                        "The forest exceeds the configured directory-depth limit.",
                        details={
                            "depth": child_depth,
                            "limit": limits.max_depth,
                            "path": relative,
                        },
                    )
                directories.append(
                    ScannedDirectory(
                        path=path,
                        relative=relative,
                        mode=stat.S_IMODE(info.st_mode),
                    )
                )
                if len(directories) > limits.max_directories:
                    raise MemoryForestError(
                        "directory_count_exceeded",
                        "The forest exceeds the configured directory-count limit.",
                        details={
                            "count": len(directories),
                            "limit": limits.max_directories,
                        },
                    )
                visit(path, depth=child_depth)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise MemoryForestError(
                    "non_regular_file",
                    "Only regular files and directories are allowed in a forest.",
                    details={"path": relative},
                )
            if info.st_size > limits.max_file_bytes:
                raise MemoryForestError(
                    "file_too_large",
                    "A forest file exceeds the configured per-file byte limit.",
                    details={
                        "limit": limits.max_file_bytes,
                        "path": relative,
                        "size": info.st_size,
                    },
                )
            files.append(
                ScannedFile(
                    path=path,
                    relative=relative,
                    mode=stat.S_IMODE(info.st_mode),
                    size=info.st_size,
                    mtime_ns=info.st_mtime_ns,
                )
            )
            total_bytes += info.st_size
            if len(files) > limits.max_files:
                raise MemoryForestError(
                    "file_count_exceeded",
                    "The forest exceeds the configured file-count limit.",
                    details={"count": len(files), "limit": limits.max_files},
                )
            if total_bytes > limits.max_total_bytes:
                raise MemoryForestError(
                    "byte_limit_exceeded",
                    "The forest exceeds the configured total-byte limit.",
                    details={"bytes": total_bytes, "limit": limits.max_total_bytes},
                )

    visit(safe_root, depth=0)
    return ScanResult(
        root=safe_root,
        directories=tuple(directories),
        files=tuple(files),
        total_bytes=total_bytes,
    )


def _validate_state_directory(state: Path) -> None:
    info = state.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise MemoryForestError(
            "unsafe_state_directory",
            "The derived-state path must be a real directory.",
            details={"path": str(state)},
        )
    _assert_real(state)
    allowed = {"forest.json", "index.sqlite3", RECEIPTS_DIRECTORY}
    with os.scandir(state) as iterator:
        entries = list(iterator)
    for entry in entries:
        child = Path(entry.path)
        child_info = entry.stat(follow_symlinks=False)
        if entry.name == RECEIPTS_DIRECTORY:
            _validate_receipts_directory(child)
            continue
        if entry.name not in allowed or stat.S_ISLNK(child_info.st_mode) or not stat.S_ISREG(
            child_info.st_mode
        ):
            raise MemoryForestError(
                "unsafe_state_entry",
                "Derived state may contain only known files and receipts.",
                details={"path": str(child)},
            )


def _validate_receipts_directory(receipts: Path) -> None:
    info = receipts.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise MemoryForestError(
            "unsafe_receipts_directory",
            "The receipts directory must be a real private directory.",
            details={"path": str(receipts)},
        )
    _assert_real(receipts)
    count = 0
    with os.scandir(receipts) as iterator:
        entries = list(iterator)
    for entry in entries:
        count += 1
        if count > MAX_RECEIPTS:
            raise MemoryForestError(
                "receipt_count_exceeded",
                "The receipts directory exceeds the supported entry limit.",
                details={"limit": MAX_RECEIPTS},
            )
        child = Path(entry.path)
        child_info = entry.stat(follow_symlinks=False)
        if (
            RECEIPT_NAME_RE.fullmatch(entry.name) is None
            or stat.S_ISLNK(child_info.st_mode)
            or not stat.S_ISREG(child_info.st_mode)
            or stat.S_IMODE(child_info.st_mode) != 0o600
            or child_info.st_size > MAX_RECEIPT_BYTES
        ):
            raise MemoryForestError(
                "unsafe_receipt_entry",
                "Receipt entries must be bounded private transaction JSON files.",
                details={"path": str(child)},
            )


def secure_state_directory(root: str | os.PathLike[str]) -> Path:
    safe_root = require_real_root(root)
    state = safe_root / STATE_DIRECTORY
    if state.exists():
        _validate_state_directory(state)
        os.chmod(state, 0o700, follow_symlinks=False)
    else:
        state.mkdir(mode=0o700)
        os.chmod(state, 0o700, follow_symlinks=False)
    return state
