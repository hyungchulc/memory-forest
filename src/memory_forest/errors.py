from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class MemoryForestError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "details": dict(sorted(self.details.items())),
            "message": self.message,
        }
