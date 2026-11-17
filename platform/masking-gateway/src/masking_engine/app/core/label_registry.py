"""Read the label files once and answer every question about them.

Three things need the same two files. Detection converts a team's vocabulary
with `app/core/label_profiles.json`, the router decides what it masks from
`app/core/taxonomy.json`, and startup validates the configured profile against
both. Each of them asking the filesystem separately let the canonical label set
be computed in two places, which is one place too many for a set that decides
what leaves a document.

Every reader here is cached, so the files are read once per process and a
deployment that edits one has to restart to pick it up.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

_TAXONOMY_PATH = Path(__file__).resolve().parent / "taxonomy.json"
_PROFILES_PATH = Path(__file__).resolve().parent / "label_profiles.json"

_SEPARATORS = re.compile(r"[\s\-_]+")


@cache
def taxonomy() -> dict:
    """Return the canonical taxonomy."""
    return json.loads(_TAXONOMY_PATH.read_text(encoding="utf-8"))


@cache
def profiles() -> dict:
    """Return the label profile registry."""
    return json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))


@cache
def profile_names() -> tuple[str, ...]:
    """Return every label profile a deployment may select."""
    return tuple(sorted(profiles()["profiles"]))


@cache
def masked_labels() -> frozenset[str]:
    """Return every label the router masks, canonical and routing-only."""
    return frozenset(
        set(taxonomy()["entity_labels"]) | set(profiles()["routing_only_labels"])
    )


def normalize(label: str) -> str:
    """Return *label* with its case and its separators reduced to one form.

    `doctor_name`, `Doctor-Name`, and `DOCTORNAME` name one thing, so they have
    to reach one entry. Separators disappear rather than converge on one of
    them, because a team that writes `DOCTORNAME` gave us no separator to keep.
    """
    return _SEPARATORS.sub("", label.strip()).upper()


@cache
def canonical_aliases() -> dict[str, str]:
    """Return the native-to-canonical map every profile inherits."""
    return {
        normalize(native): canonical.upper()
        for native, canonical in taxonomy()["aliases"].items()
    }


@cache
def _canonical_by_key() -> dict[str, str]:
    return {normalize(label): label for label in masked_labels()}


def canonical_label(label: str) -> str | None:
    """Return the canonical label for *label*, or None when nothing matches.

    This resolves the taxonomy aliases and the spelling of a canonical label.
    A profile adds its own entries on top: see `app/engines/label_converter.py`.
    """
    key = normalize(label)
    return canonical_aliases().get(key) or _canonical_by_key().get(key)
