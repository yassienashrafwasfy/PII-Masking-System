"""Keep the arena's own copies of the ranking documents QA publishes.

qa-automation's `rank_teams.py` names its cross-team output after nothing but
the document count and the run mode -- `leaderboard-aggregate-15docs-release.json`
for a multi-document run, `leaderboard-<document-id>-<mode>.json` for a single
one. There is no timestamp, run id, or commit sha in that name, so every run
over the same document set writes the *same* object key, and R2 offers no object
versioning (see objectstore's module docstring). The previous run's scores are
gone the moment the next run uploads.

That is why `ranking.discover` finds exactly one document, every trend line is a
single point, and the board's "Iron model" card has always read "1 run tracked"
no matter how often QA runs. The board is not losing the history; the history is
destroyed before the board ever sees it.

This module is the arena's half of the fix: each time the board is built, the
document it selected is copied under a content-addressed key that cannot
collide with a later run's. The other half is a change in qa-automation to
publish a timestamped copy alongside its stable key; the two are complementary,
and `content_fingerprint` is what stops them counting the same run twice.

Writing here is the only place the arena writes to the results bucket, so the
whole module is best effort by contract: a read-only credential, a missing
bucket, or a transient failure must leave the board exactly as it would have
been, never raise. `archive_rankings` is opt-in for the same reason.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from . import objectstore
from .config import Settings

log = logging.getLogger("arena.history")

# The fields that make one ranking document a distinct observation. Everything
# else -- the key it was read from, when it was observed, the archive wrapper --
# is provenance, and folding provenance into the fingerprint would defeat the
# deduplication this exists for.
FINGERPRINT_FIELDS = (
    "document_ids",
    "mode",
    "source",
    "ranking_overlap",
    "ranking_exact",
    "excluded",
)

# Key under which the original document's own identity is recorded. Read back by
# `ranking.RankingDocument`, so an archived copy reports the time QA published
# it rather than the time this module happened to copy it.
PROVENANCE_FIELD = "archived_from"


def content_fingerprint(payload: dict) -> str:
    """A stable digest of one ranking document's scores.

    Canonical JSON -- sorted keys, no incidental whitespace -- so two documents
    that say the same thing hash the same even if their serialisation differs.
    """
    subset = {name: payload.get(name) for name in FINGERPRINT_FIELDS}
    canonical = json.dumps(subset, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def unwrap(payload: dict) -> tuple[dict, Any]:
    """Split an archived document into the ranking itself and when it was seen.

    A document that was never archived is returned unchanged with no recorded
    time, so callers can treat both kinds identically.
    """
    provenance = payload.get(PROVENANCE_FIELD)
    if not isinstance(provenance, dict):
        return payload, None
    inner = {name: value for name, value in payload.items() if name != PROVENANCE_FIELD}
    return inner, provenance.get("observed_at")


def archive_key(settings: Settings, payload: dict, fingerprint: str) -> str:
    """Content-addressed, so re-archiving an unchanged document is a no-op.

    The mode and document count lead the name purely so the prefix stays
    legible to a human listing it; the digest is what makes it unique.
    """
    mode = str(payload.get("mode") or "unknown").replace("/", "-")
    ids = payload.get("document_ids")
    count = len(ids) if isinstance(ids, list) else 0
    return f"{settings.history_prefix}/{mode}-{count}docs-{fingerprint[:12]}.json"


def archive(
    settings: Settings, key: str, observed_at: Any, payload: dict
) -> str | None:
    """Copy one observed ranking document into the history prefix.

    Returns the key written, or None when nothing was written -- which covers
    both "already archived" and every failure mode. Never raises: this runs on
    the read path for the public leaderboard, and a storage permission the
    deployment happens not to grant must not turn a working board into a 500.
    """
    if not settings.archive_rankings or not settings.history_prefix:
        return None
    if key.startswith(f"{settings.history_prefix}/"):
        # Already one of our own copies. Worth checking by prefix and not just
        # by key equality: an archive written under an older naming scheme would
        # otherwise be re-copied under the current one, leaving two objects
        # holding one run.
        return None

    fingerprint = content_fingerprint(payload)
    destination = archive_key(settings, payload, fingerprint)
    if destination == key:
        return None

    try:
        if objectstore.head(settings, settings.results_bucket, destination) is not None:
            return None
        body = dict(payload)
        body[PROVENANCE_FIELD] = {
            "key": key,
            "observed_at": observed_at.isoformat() if observed_at else None,
        }
        objectstore.put_bytes(
            settings,
            settings.results_bucket,
            destination,
            json.dumps(body, sort_keys=True, default=str).encode("utf-8"),
            "application/json",
        )
    except Exception as exc:
        # Logged once per failed attempt rather than raised. The most likely
        # cause by far is a results-bucket credential with read but not write,
        # in which case the arena simply keeps behaving as it did before.
        log.warning("ranking_archive_failed key=%s error=%s", destination, exc)
        return None

    log.info("ranking_archived from=%s to=%s", key, destination)
    return destination
