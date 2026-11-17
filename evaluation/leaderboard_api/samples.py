"""Read-only listing of a local folder of sample documents.

Browsers do not let a page choose which folder a native file dialog opens to,
so there is no way to make `<input type="file">` "default" to a given path.
This is the actual way to get that outcome: the console lists files from a
configured folder and the user picks one, which the API then reads and
uploads server-side using the same `runs.submit()` path a browser upload uses.

Off by default. Only files directly inside `ARENA_SAMPLE_DIRECTORY` are ever
exposed -- no subfolders, no symlinks, no path traversal via a crafted name.
"""

from __future__ import annotations

from pathlib import Path

from .config import Settings
from .runs import ALLOWED_CONTENT_TYPES

_CONTENT_TYPE_BY_SUFFIX = {
    suffix: content_type for content_type, suffix in ALLOWED_CONTENT_TYPES.items()
}


class SampleNotFound(ValueError):
    """Raised for a missing, unsupported, or disallowed sample name."""


def _root(settings: Settings) -> Path | None:
    if not settings.sample_directory:
        return None
    root = Path(settings.sample_directory)
    return root if root.is_dir() else None


def list_samples(settings: Settings) -> list[dict]:
    """List the supported files directly inside the sample directory."""
    root = _root(settings)
    if root is None:
        return []
    samples = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.is_symlink():
            continue
        content_type = _CONTENT_TYPE_BY_SUFFIX.get(path.suffix.lower())
        if content_type is None:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        samples.append({"name": path.name, "size": size, "content_type": content_type})
    return samples


def read_sample(settings: Settings, name: str) -> tuple[bytes, str]:
    """Read one sample's bytes and content type by exact filename."""
    root = _root(settings)
    if root is None:
        raise SampleNotFound("No sample directory is configured.")

    # `name` must be a bare filename: no separators, no "..", no empty string.
    # Path.resolve() below is the real backstop, but rejecting these up front
    # gives a clearer error than a generic "not found".
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise SampleNotFound(f"Invalid sample name: {name!r}")

    path = root / name
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SampleNotFound(f"Sample not found: {name}") from exc
    if resolved.parent != root.resolve() or not resolved.is_file() or path.is_symlink():
        raise SampleNotFound(f"Sample not found: {name}")

    content_type = _CONTENT_TYPE_BY_SUFFIX.get(resolved.suffix.lower())
    if content_type is None:
        raise SampleNotFound(f"Unsupported sample type: {name}")

    return resolved.read_bytes(), content_type
