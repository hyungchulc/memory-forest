from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import MemoryForestError
from .safety import require_real_root


@contextmanager
def maintenance_lock(
    root: str | os.PathLike[str],
) -> Iterator[Path]:
    root_path = require_real_root(root)
    lock = Path(str(root_path) + ".maintenance.lock")
    if Path(os.path.realpath(lock.parent)) != lock.parent:
        raise MemoryForestError(
            "unsafe_lock_parent",
            "The maintenance lock parent must be a real directory.",
        )
    _reject_casefold_collision(lock.parent, lock.name)
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise MemoryForestError(
            "maintenance_lock_busy",
            "The sibling maintenance lock is already held.",
            details={"path": lock.name},
        ) from exc
    os.chmod(lock, 0o700, follow_symlinks=False)
    _fsync_directory(lock.parent)
    try:
        yield root_path
    finally:
        try:
            lock.rmdir()
            _fsync_directory(lock.parent)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise MemoryForestError(
                "lock_release_failed",
                "The maintenance lock could not be released safely.",
                details={"reason": exc.__class__.__name__},
            ) from exc


def _reject_casefold_collision(parent: Path, name: str) -> None:
    folded = name.casefold()
    with os.scandir(parent) as iterator:
        entries = list(iterator)
    for entry in entries:
        if entry.name.casefold() == folded and entry.name != name:
            raise MemoryForestError(
                "casefold_collision",
                "A differently cased filesystem entry collides with a lock path.",
                details={"expected": name, "existing": entry.name},
            )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
