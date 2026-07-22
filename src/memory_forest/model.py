from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Final

from .errors import MemoryForestError


SCHEMA_VERSION: Final[int] = 1
LAYERS: Final[tuple[tuple[int, str], ...]] = (
    (0, "life_archive"),
    (1, "xltm"),
    (2, "ltm"),
    (3, "mtm"),
    (4, "stm"),
    (5, "daily"),
    (6, "istm"),
)
LAYER_DIRECTORY_NAMES: Final[tuple[str, ...]] = tuple(
    f"{number:02d} {name}" for number, name in LAYERS
)
LAYER_BY_NUMBER: Final[dict[int, str]] = dict(LAYERS)

_LAYER_RE = re.compile(
    r"^(?P<number>0[0-6])[ _-]+"
    r"(?P<name>life_archive|xltm|ltm|mtm|stm|daily|istm)$",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class Layer:
    number: int
    name: str
    directory: str

    def as_dict(self) -> dict[str, object]:
        return {
            "directory": self.directory,
            "name": self.name,
            "number": self.number,
        }


@dataclass(frozen=True, slots=True)
class Route:
    layer: Layer
    domain: str | None
    branch: str | None
    leaf: str
    path: str

    @property
    def route_key(self) -> str:
        if self.layer.name == "xltm":
            return "xltm"
        values = [self.layer.name]
        if self.domain:
            values.append(self.domain)
        if self.branch:
            values.append(self.branch)
        if not self.branch or self.leaf != self.branch:
            values.append(self.leaf)
        return "/".join(values)

    def as_dict(self) -> dict[str, object]:
        return {
            "branch": self.branch,
            "domain": self.domain,
            "layer": self.layer.as_dict(),
            "leaf": self.leaf,
            "path": self.path,
            "route_key": self.route_key,
        }


def parse_layer(segment: str) -> Layer:
    match = _LAYER_RE.fullmatch(segment)
    if match is None:
        raise MemoryForestError(
            "unknown_layer",
            "The first path segment is not a supported Memory Forest layer.",
            details={"segment": segment},
        )
    number = int(match.group("number"))
    name = match.group("name").lower()
    expected_name = LAYER_BY_NUMBER[number]
    if name != expected_name:
        raise MemoryForestError(
            "layer_number_mismatch",
            "The layer number and name do not match.",
            details={"expected": expected_name, "number": number, "received": name},
        )
    return Layer(number=number, name=name, directory=f"{number:02d} {name}")


def validate_segment(value: str, *, field: str) -> str:
    if not value or value in {".", ".."}:
        raise MemoryForestError(
            "invalid_route_segment",
            "Route segments must be non-empty and may not be dot segments.",
            details={"field": field, "value": value},
        )
    if len(value) > 200 or _CONTROL_RE.search(value) or "/" in value or "\\" in value:
        raise MemoryForestError(
            "invalid_route_segment",
            "A route segment contains a forbidden character or is too long.",
            details={"field": field, "value": value},
        )
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value:
        raise MemoryForestError(
            "noncanonical_route_segment",
            "Route segments must use NFC Unicode normalization.",
            details={"field": field, "value": value},
        )
    return value


def parse_relative_route(relative: str | PurePosixPath | Path) -> Route:
    raw = relative.as_posix() if isinstance(relative, Path) else str(relative)
    if "\\" in raw:
        raise MemoryForestError(
            "invalid_document_path",
            "Document paths must use forward slashes.",
            details={"path": raw},
        )
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise MemoryForestError(
            "path_escape",
            "A document path must stay inside the selected forest root.",
            details={"path": raw},
        )
    parts = pure.parts
    if len(parts) < 2:
        raise MemoryForestError(
            "invalid_document_path",
            "A document path must include a layer directory and memory file.",
            details={"path": raw},
        )
    layer = parse_layer(parts[0])
    if parts[0] != layer.directory:
        raise MemoryForestError(
            "noncanonical_layer_directory",
            "Layer directories must use the canonical space-separated name.",
            details={"expected": layer.directory, "received": parts[0]},
        )
    suffix = pure.suffix
    if layer.name == "istm":
        if len(parts) != 2 or suffix != ".jsonl":
            raise MemoryForestError(
                "invalid_istm_path",
                "ISTM event streams must be root-level .jsonl files.",
                details={"path": raw},
            )
        leaf = validate_segment(pure.stem, field="leaf")
        return Route(layer, None, None, leaf, pure.as_posix())
    if suffix != ".md":
        raise MemoryForestError(
            "unsupported_file_type",
            "Canonical memory documents must use .md, except ISTM .jsonl streams.",
            details={"path": raw},
        )
    leaf = validate_segment(pure.stem, field="leaf")
    if layer.name == "xltm":
        if parts != (layer.directory, "XLTM.md"):
            raise MemoryForestError(
                "invalid_xltm_path",
                "XLTM must use the canonical root path 01 xltm/XLTM.md.",
                details={"path": raw},
            )
        return Route(layer, None, None, leaf, pure.as_posix())
    if layer.name == "ltm":
        if len(parts) != 2 or not pure.stem.endswith("_LTM"):
            raise MemoryForestError(
                "invalid_ltm_path",
                "LTM domains must use 02 ltm/<domain>_LTM.md.",
                details={"path": raw},
            )
        domain = validate_segment(pure.stem.removesuffix("_LTM"), field="domain")
        return Route(layer, domain, None, leaf, pure.as_posix())
    if layer.name == "mtm":
        if len(parts) != 3:
            raise MemoryForestError(
                "invalid_mtm_path",
                "MTM branches must use 03 mtm/<domain>/<branch>.md.",
                details={"path": raw},
            )
        domain = validate_segment(parts[1], field="domain")
        return Route(layer, domain, leaf, leaf, pure.as_posix())
    if layer.name == "stm":
        if len(parts) != 4:
            raise MemoryForestError(
                "invalid_stm_path",
                "STM leaves must use 04 stm/<domain>/<branch>/<leaf>.md.",
                details={"path": raw},
            )
        domain = validate_segment(parts[1], field="domain")
        branch = validate_segment(parts[2], field="branch")
        return Route(layer, domain, branch, leaf, pure.as_posix())
    if layer.name == "daily":
        if len(parts) != 2:
            raise MemoryForestError(
                "invalid_daily_path",
                "Daily sources must be root-level ISO-date Markdown files.",
                details={"path": raw},
            )
        try:
            parsed = date.fromisoformat(leaf)
        except ValueError as exc:
            raise MemoryForestError(
                "invalid_daily_date",
                "Daily filenames must use a valid YYYY-MM-DD date.",
                details={"path": raw},
            ) from exc
        if parsed.isoformat() != leaf:
            raise MemoryForestError(
                "invalid_daily_date",
                "Daily filenames must use a valid YYYY-MM-DD date.",
                details={"path": raw},
            )
        return Route(layer, None, None, leaf, pure.as_posix())
    if layer.name == "life_archive":
        route_parts = parts[1:-1]
        if len(route_parts) > 2:
            raise MemoryForestError(
                "invalid_life_archive_path",
                "Life Archive records may use at most domain/branch/leaf depth.",
                details={"path": raw},
            )
        domain = validate_segment(route_parts[0], field="domain") if route_parts else None
        branch = (
            validate_segment(route_parts[1], field="branch")
            if len(route_parts) == 2
            else None
        )
        return Route(layer, domain, branch, leaf, pure.as_posix())
    raise MemoryForestError(
        "unsupported_layer",
        "The memory layer is not supported by this schema.",
        details={"layer": layer.name},
    )


def layers_are_adjacent(source: Layer, target: Layer) -> bool:
    return abs(source.number - target.number) == 1
