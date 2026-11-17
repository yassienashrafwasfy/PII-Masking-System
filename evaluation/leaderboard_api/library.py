"""The durable half of the masking console: runs rebuilt from object storage.

`runs.RunRegistry` is explicit that it is demo state -- a restart loses the
list, and with it every run id, so `GET /api/v1/runs/{id}` starts returning 404
for documents that are still sitting in the bucket. The durable record was
always there; nothing read it back.

This module reads it back. A completion manifest is already a full
serialization of a `runs.Run`: it records the source key -- which embeds the
arena's own run id, because `incoming_key_template` puts it there -- plus the
destination key, the correlation id, the model version and the timestamps. The
filename is the tail of either key and the content type is its suffix. So a
manifest is enough to reconstruct the run, and every existing endpoint and
React component then works against it unchanged.

Two deliberate limits, both decided with the people who asked for this:

* Only runs this arena uploaded. A manifest whose source key does not match
  `incoming_key_template` came from some other pipeline; nobody using this UI
  chose to upload it, and its key often carries a real filename.
* Masked output only. There is no authentication here, so anything served is
  readable by anyone who can reach the site. Masked output is by definition
  de-identified; the original is not. A *live* run still shows both sides --
  only the durable path is restricted.

Cost shape, since this lists a bucket that grows forever. A listing is a
Class A operation on R2, so three things keep the bill flat: a TTL cache over
the whole index (`library_cache_seconds`), a `(key, etag)` memo that outlives
that TTL so an unchanged manifest is fetched exactly once ever, and
newest-first truncation to `library_max_entries` before any body is read. In
steady state a refresh costs one listing cycle and zero object reads.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from .config import Settings
from . import objectstore, runs

log = logging.getLogger("arena.library")

# How many reconstructed runs to remember across TTL expiries. A manifest is
# written once and never changed, so caching it by (key, etag) means a rebuild
# only pays for manifests it has not seen -- the difference between O(entries)
# object reads on every refresh and O(new entries).
DOCUMENT_CACHE_SIZE = 2000

_index_lock = threading.Lock()
_index_cache: dict[Any, tuple[float, "Index"]] = {}
_document_cache: OrderedDict[tuple[str, str, str], runs.Run | None] = OrderedDict()


class Index:
    """One rebuilt view of the library, newest first."""

    def __init__(
        self, entries: list[runs.Run], refreshed_at: float, truncated: bool
    ) -> None:
        self.entries = entries
        self.refreshed_at = refreshed_at
        self.truncated = truncated
        self.by_id = {run.run_id: run for run in entries}


def _parse_time(value: Any) -> datetime | None:
    """Read an ISO timestamp out of a manifest, tolerating a trailing Z."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _content_type_for(filename: str) -> str:
    """Reverse `runs.ALLOWED_CONTENT_TYPES`, defaulting to octet-stream."""
    suffix = PurePosixPath(filename).suffix.lower()
    for content_type, extension in runs.ALLOWED_CONTENT_TYPES.items():
        if extension == suffix:
            return content_type
    return "application/octet-stream"


def _team_from_source_key(settings: Settings, source_key: str) -> str | None:
    """The segment straight after the incoming prefix, which NiFi trusts too."""
    parts = [segment for segment in source_key.split("/") if segment]
    prefix_parts = [s for s in settings.incoming_prefix.split("/") if s]
    if len(parts) <= len(prefix_parts):
        return None
    if parts[: len(prefix_parts)] != prefix_parts:
        return None
    return parts[len(prefix_parts)]


def entry_from_manifest(
    settings: Settings, stored: objectstore.StoredObject, document: dict
) -> runs.Run | None:
    """Rebuild one `runs.Run` from a terminal manifest, or None to skip it.

    Reuses `runs`' own field-name constants rather than re-spelling them:
    NiFi's two manifest vocabularies (the snake_case JSON schemas and the
    contract's uppercase prose) are already reconciled there, and a second copy
    would drift.
    """
    status_text = str(document.get("status") or "").strip().lower()
    completed = status_text in runs.COMPLETED_STATUSES
    failed = status_text in runs.FAILED_STATUSES
    if not (completed or failed):
        # A manifest that is not terminal is a race with a run still in flight.
        # The live registry owns those; picking one up here would show a
        # half-finished run with no way to keep polling it.
        return None

    source_key = runs._first(document, runs.SOURCE_KEY_FIELDS)
    if not source_key:
        return None

    run_id = runs.run_id_from_source_key(settings, source_key)
    if run_id is None and not settings.library_include_foreign:
        return None
    if run_id is None:
        # Deterministic so a deep link survives a restart, and prefixed so it
        # can never collide with a uuid4 the arena generated.
        digest = hashlib.sha256(
            f"{settings.manifest_bucket}/{stored.key}".encode()
        ).hexdigest()
        run_id = f"lib_{digest[:32]}"

    team = _team_from_source_key(settings, source_key) or str(
        document.get("team_id") or ""
    )
    if not team:
        return None
    if settings.teams and team not in settings.teams:
        return None

    masked_key = runs._first(document, runs.MASKED_KEY_FIELDS)
    filename = PurePosixPath(masked_key or source_key).name
    # The template folds the run id into the filename, so strip it back off for
    # display -- the reader uploaded "report.pdf", not "<uuid>-report.pdf".
    if filename.startswith(f"{run_id}-"):
        filename = filename[len(run_id) + 1 :]

    submitted = _parse_time(document.get("started_at")) or _parse_time(
        stored.last_modified
    )
    finished = (
        _parse_time(document.get("completed_at"))
        or _parse_time(document.get("failed_at"))
        or _parse_time(stored.last_modified)
    )
    # Every ordering below has to tolerate a backend that reports no
    # last_modified, so fall back rather than leave submitted_at unset.
    submitted = submitted or finished or datetime.now(UTC)

    run = runs.Run(
        run_id=run_id,
        team=team,
        filename=filename,
        content_type=_content_type_for(filename),
        source_bucket=settings.source_bucket,
        source_key=source_key,
        source_etag=str(document.get("source_etag") or ""),
        # Sizes are deliberately left unset: filling them costs one HEAD per
        # row, and the list view does not show them. `resolve` fills them in
        # for the single run actually being opened.
        size=0,
        submitted_at=submitted,
        status=runs.STATUS_COMPLETED if completed else runs.STATUS_FAILED,
        masked_bucket=settings.masked_bucket if masked_key else None,
        masked_key=masked_key,
        manifest_key=stored.key,
        correlation_id=runs._first(document, runs.CORRELATION_FIELDS),
        model_version=document.get("model_version"),
        model_name=runs._first(document, runs.MODEL_NAME_FIELDS)
        or runs._model_from_key(masked_key),
        completed_at=finished,
        discovered_by="library",
    )
    if failed:
        run.error_code = (
            document.get("error_code") or document.get("failure_code") or "UNKNOWN"
        )
        run.message = document.get("message") or document.get("error_message")
    run.note(run.status, f"Rebuilt from manifest {stored.key}")
    return run


def _cached_entry(
    settings: Settings, stored: objectstore.StoredObject
) -> runs.Run | None:
    """Reconstruct one manifest, reading its body at most once ever.

    NiFi writes `status.json` once and it is terminal, so the object is
    immutable in practice and its etag is a safe cache key. If a deployment
    ever does overwrite one, the etag changes and this correctly misses.
    """
    key = (settings.manifest_bucket, stored.key, stored.etag)
    if key in _document_cache:
        _document_cache.move_to_end(key)
        return _document_cache[key]

    entry: runs.Run | None = None
    try:
        body, _ = objectstore.get_bytes(settings, settings.manifest_bucket, stored.key)
        document = json.loads(body.decode("utf-8"))
        if isinstance(document, dict):
            entry = entry_from_manifest(settings, stored, document)
    except Exception as exc:
        # One unreadable manifest must not empty the whole library.
        log.debug("library_manifest_unreadable key=%s error=%s", stored.key, exc)

    _document_cache[key] = entry
    _document_cache.move_to_end(key)
    while len(_document_cache) > DOCUMENT_CACHE_SIZE:
        _document_cache.popitem(last=False)
    return entry


def build_index(settings: Settings) -> Index:
    """List the manifest prefix once and rebuild every run it describes.

    One scan from the manifest root rather than `runs._manifest_prefixes`:
    that probes about ten candidate layouts for a *single* run, whereas every
    layout this repository documents -- `manifests/team=…`,
    `manifests/completed/team=…`, `manifests/<team>/<date>/…` -- nests under
    the same root, so one listing covers all of them.
    """
    listed = objectstore.list_objects(
        settings,
        settings.manifest_bucket,
        settings.manifest_prefix,
        limit=settings.library_scan_limit,
    )
    truncated = len(listed) >= settings.library_scan_limit

    # Sort before reading any body, so the object reads below are bounded by
    # library_max_entries rather than by how large the bucket has grown.
    #
    # This sort is also why `library_scan_limit` is not a cheap way to save
    # money: `list_objects` truncates in lexicographic key order and the keys
    # embed a random UUID, so a truncated scan is an arbitrary sample, not the
    # newest slice. Hitting the limit means "partial view", which is why it is
    # reported rather than silently accepted.
    listed.sort(
        key=lambda item: (item.last_modified is not None, item.last_modified),
        reverse=True,
    )

    entries: list[runs.Run] = []
    seen_ids: set[str] = set()
    for stored in listed:
        if len(entries) >= settings.library_max_entries:
            break
        entry = _cached_entry(settings, stored)
        if entry is None or entry.run_id in seen_ids:
            # A repeated source key means the same document was masked twice;
            # the newest manifest wins because the list is already sorted.
            continue
        seen_ids.add(entry.run_id)
        entries.append(entry)

    return Index(entries=entries, refreshed_at=time.time(), truncated=truncated)


def load_index(settings: Settings) -> Index:
    """`build_index` behind a TTL, rebuilt by one caller at a time.

    The single-flight lock matters more here than it does for the board cache:
    a rebuild lists a whole prefix, so a burst of page loads arriving the
    moment the TTL expires would otherwise each start their own scan.
    """
    now = time.time()
    cached = _index_cache.get(settings)
    if cached is not None and now - cached[0] < settings.library_cache_seconds:
        return cached[1]

    with _index_lock:
        # Re-check inside the lock: whoever held it may have just rebuilt.
        cached = _index_cache.get(settings)
        if (
            cached is not None
            and time.time() - cached[0] < settings.library_cache_seconds
        ):
            return cached[1]
        index = build_index(settings)
        _index_cache[settings] = (index.refreshed_at, index)
        return index


def page(
    settings: Settings,
    *,
    registry: runs.RunRegistry,
    limit: int = 25,
    offset: int = 0,
    team: str = "",
    status: str = "",
    query: str = "",
) -> dict:
    """One filtered page of the library, newest first.

    Filtering runs against the whole in-memory index rather than the returned
    slice, so a team or status filter searches everything the index holds and
    costs no extra storage calls.
    """
    index = load_index(settings)

    # A run submitted seconds ago lives in the registry and will not appear in
    # storage until NiFi writes its manifest, so merge the live view over the
    # rebuilt one with the registry winning. Without this the console's own
    # fresh upload is missing from the library for a whole TTL.
    merged: dict[str, runs.Run] = {run.run_id: run for run in index.entries}
    for live in registry.recent(settings.library_max_entries):
        merged[live.run_id] = live

    found = list(merged.values())
    if team:
        found = [run for run in found if run.team == team]
    if status:
        found = [run for run in found if run.status == status]
    if query:
        needle = query.strip().lower()
        found = [run for run in found if needle in run.filename.lower()]

    found.sort(key=lambda run: run.submitted_at, reverse=True)
    total = len(found)
    window = found[offset : offset + limit]
    return {
        "entries": [run.payload() for run in window],
        "total": total,
        "limit": limit,
        "offset": offset,
        "refreshed_at": datetime.fromtimestamp(index.refreshed_at, UTC).isoformat(),
        "truncated": index.truncated,
    }


def resolve(
    settings: Settings, run_id: str, *, registry: runs.RunRegistry
) -> runs.Run | None:
    """Find a run by id, falling back to storage when the process forgot it.

    This is what keeps a bookmarked run URL working across a restart. On a hit
    the run is put back into the registry so later polls and content fetches
    take the hot path -- it is already terminal, so `runs.refresh` returns
    immediately and never re-scans.

    A miss deliberately does *not* trigger a fresh scan. An unknown id is the
    one input an anonymous caller fully controls, and rebuilding the index per
    request would turn a 404 into a bucket listing.
    """
    live = registry.get(run_id)
    if live is not None:
        return live
    if not settings.library_enabled:
        return None

    entry = load_index(settings).by_id.get(run_id)
    if entry is None:
        return None

    # Fill the sizes the list view deliberately skipped, now that this is the
    # one run actually being opened.
    if entry.masked_key and entry.masked_size is None:
        masked = objectstore.head(
            settings, entry.masked_bucket or settings.masked_bucket, entry.masked_key
        )
        if masked is not None:
            entry.masked_size = masked.size
    if not entry.size:
        source = objectstore.head(settings, entry.source_bucket, entry.source_key)
        if source is not None:
            entry.size = source.size
            entry.source_etag = entry.source_etag or source.etag

    registry.add(entry)
    return entry


def reset_cache() -> None:
    """Drop every cached index and manifest. Used by tests."""
    _index_cache.clear()
    _document_cache.clear()
