"""Interactive masking runs driven through the real NiFi pipeline.

The arena uploads one object under the source prefix and then watches object
storage for the result NiFi writes. It never calls the masking gateway itself:
per `contracts/masking-api/v1.2`, the masking API neither reads nor writes
object storage, and NiFi owns the trusted routing decision, the request ID, and
the destination key.

Because NiFi derives its request ID as a UUID5 over
`source bucket + source key + ETag` with a namespace this repository does not
define, the destination key cannot be predicted here. The run is therefore
resolved by *discovery*: find the terminal manifest whose recorded source key is
the key we uploaded, and read the masked key out of it. The repository documents
five mutually inconsistent key layouts and two manifest vocabularies, so every
lookup below tolerates each variant it may encounter.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import PurePosixPath

from .config import Settings
from . import objectstore

log = logging.getLogger("arena.runs")

MAX_TRACKED_RUNS = 200
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

# The gateway accepts only these media types (contracts/masking-api/v1.2).
ALLOWED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/json": ".json",
}

STATUS_UPLOADED = "uploaded"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"

# Manifests are written by NiFi. The JSON schemas in contracts/manifests/v1.0
# use snake_case object keys and lowercase statuses; the contract prose in
# contracts/masking-api/v1.2 uses different names and uppercase statuses.
SOURCE_KEY_FIELDS = ("source_object_key", "source_key")
MASKED_KEY_FIELDS = ("masked_object_key", "destination_key")
CORRELATION_FIELDS = ("correlation_id", "request_id")
# Which checkpoint answered. `model_version` alone cannot say: berts holds one
# per document language and reports the one that actually read the bytes, so a
# French document and a Dutch one from one team carry the same kind of version
# and a different name.
MODEL_NAME_FIELDS = ("model_name", "model")
COMPLETED_STATUSES = {"completed", "succeeded"}
FAILED_STATUSES = {"failed", "failure"}


class RunError(ValueError):
    """A submission the arena refuses before anything is uploaded."""


@dataclass
class Run:
    run_id: str
    team: str
    filename: str
    content_type: str
    source_bucket: str
    source_key: str
    source_etag: str
    size: int
    submitted_at: datetime
    status: str = STATUS_UPLOADED
    masked_bucket: str | None = None
    masked_key: str | None = None
    masked_size: int | None = None
    manifest_key: str | None = None
    correlation_id: str | None = None
    model_version: str | None = None
    model_name: str | None = None
    error_code: str | None = None
    message: str | None = None
    completed_at: datetime | None = None
    discovered_by: str | None = None
    history: list[dict] = field(default_factory=list)

    def note(self, status: str, detail: str | None = None) -> None:
        if self.history and self.history[-1]["status"] == status:
            return
        self.history.append(
            {
                "status": status,
                "detail": detail,
                "at": datetime.now(UTC).isoformat(),
            }
        )

    @property
    def terminal(self) -> bool:
        return self.status in {STATUS_COMPLETED, STATUS_FAILED, STATUS_TIMEOUT}

    def elapsed_seconds(self) -> float:
        end = self.completed_at or datetime.now(UTC)
        return max(0.0, (end - self.submitted_at).total_seconds())

    def payload(self) -> dict:
        return {
            "run_id": self.run_id,
            "team": self.team,
            "filename": self.filename,
            "content_type": self.content_type,
            "status": self.status,
            "source": {
                "bucket": self.source_bucket,
                "key": self.source_key,
                "etag": self.source_etag,
                "size": self.size,
            },
            "masked": (
                {
                    "bucket": self.masked_bucket,
                    "key": self.masked_key,
                    "size": self.masked_size,
                }
                if self.masked_key
                else None
            ),
            "manifest_key": self.manifest_key,
            "correlation_id": self.correlation_id,
            "model_version": self.model_version,
            "model_name": self.model_name,
            "error_code": self.error_code,
            "message": self.message,
            "submitted_at": self.submitted_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "elapsed_seconds": round(self.elapsed_seconds(), 1),
            "discovered_by": self.discovered_by,
            "history": self.history,
        }


class RunRegistry:
    """In-process run store.

    Runs are demo state, not a system of record: the durable record of every
    submission is the manifest NiFi writes to object storage. A restart loses
    the list but not the results, which remain discoverable in the bucket.
    """

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def add(self, run: Run) -> None:
        with self._lock:
            self._runs[run.run_id] = run
            if len(self._runs) > MAX_TRACKED_RUNS:
                oldest = sorted(self._runs.values(), key=lambda item: item.submitted_at)
                for stale in oldest[: len(self._runs) - MAX_TRACKED_RUNS]:
                    self._runs.pop(stale.run_id, None)

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            return self._runs.get(run_id)

    def recent(self, limit: int = 25) -> list[Run]:
        with self._lock:
            return sorted(
                self._runs.values(), key=lambda item: item.submitted_at, reverse=True
            )[:limit]


def _safe_filename(name: str, content_type: str) -> str:
    base = PurePosixPath(name or "").name
    cleaned = SAFE_NAME.sub("_", base).strip("._") or "document"
    if not PurePosixPath(cleaned).suffix:
        cleaned += ALLOWED_CONTENT_TYPES.get(content_type, "")
    return cleaned[:120]


def build_source_key(
    settings: Settings,
    team: str,
    run_id: str,
    filename: str,
    submitted_at: datetime,
) -> str:
    """Render the upload key from the configured template.

    The layout is configurable because this repository documents several
    mutually inconsistent ones and only NiFi's actual ListS3 configuration
    decides which is real. The default follows the v1.2 contract,
    `incoming/<team>/requests/...`, which is the layout in use.

    The gateway independently rejects a key that does not start with
    `incoming/<team-id>/`, and NiFi derives the trusted team from that same
    path, so the prefix and team segments are enforced here rather than trusted
    to the template.
    """
    rendered = settings.incoming_key_template.format(
        prefix=settings.incoming_prefix,
        team=team,
        date=submitted_at.strftime("%Y-%m-%d"),
        run_id=run_id,
        filename=filename,
    )
    # Collapse any empty segments a template may leave behind.
    key = "/".join(segment for segment in rendered.split("/") if segment)
    required = f"{settings.incoming_prefix}/{team}/"
    if not key.startswith(required):
        raise RunError(
            f"ARENA_INCOMING_KEY_TEMPLATE must produce a key under '{required}'; "
            f"it produced '{key}'."
        )
    if not key.endswith(filename):
        raise RunError(
            "ARENA_INCOMING_KEY_TEMPLATE must end with {filename} so NiFi and "
            "the model service see the original file name."
        )
    return key


# A run id as `submit` generates it: uuid4, lowercase hex with dashes.
_RUN_ID_PATTERN = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# What each template placeholder may match when reading a key back. `filename`
# is last and greedy because it is the only one allowed to contain a dot, and
# `date` is fixed-width so it cannot swallow a neighbouring segment.
_PLACEHOLDER_PATTERNS = {
    "prefix": r"[^/]+",
    "team": r"[^/]+",
    "date": r"\d{4}-\d{2}-\d{2}",
    "run_id": f"(?P<run_id>{_RUN_ID_PATTERN})",
    "filename": r".+",
}


@lru_cache(maxsize=8)
def _source_key_pattern(template: str) -> re.Pattern[str] | None:
    """Compile the inverse of `build_source_key` for one template.

    Built from the template rather than hard-coded, because the layout is
    configurable and a deployment that sets ARENA_INCOMING_KEY_TEMPLATE would
    otherwise have every key silently fail to parse. Returns None when the
    template carries no `{run_id}` -- the documented "pristine names" option --
    since then there is genuinely no id in the key to recover.

    Cached on the template string: this is called once per manifest during a
    library rebuild, and recompiling a regex thousands of times would dominate
    the work.
    """
    if "{run_id}" not in template:
        return None
    # Escape first so a literal dot or dash in the template cannot act as a
    # metacharacter, then swap the (now escaped) placeholders for their groups.
    pattern = re.escape(template)
    for name, fragment in _PLACEHOLDER_PATTERNS.items():
        pattern = pattern.replace(re.escape("{" + name + "}"), fragment)
    return re.compile(f"^{pattern}$")


def run_id_from_source_key(settings: Settings, source_key: str) -> str | None:
    """Recover the arena's own run id from a key it uploaded, or None.

    This is what makes a masked document reachable again after a restart: the
    manifest records the source key, the source key embeds the run id, and the
    run id is what every existing URL and endpoint is written against. A key
    that does not match the template was not uploaded by this arena.
    """
    if not source_key:
        return None
    pattern = _source_key_pattern(settings.incoming_key_template)
    if pattern is None:
        return None
    match = pattern.match(source_key.strip().lstrip("/"))
    return match.group("run_id") if match else None


def submit(
    settings: Settings,
    team: str,
    filename: str,
    content_type: str,
    body: bytes,
    *,
    registry: RunRegistry,
) -> Run:
    """Upload one object under the source prefix so NiFi can pick it up.

    The registry is handed in rather than reached for. Its lifetime is the
    caller's -- one per API process, emptied by a restart -- so the caller is
    also what should decide which one a submission lands in.
    """
    team = (team or "").strip().lower()
    if team not in settings.teams:
        raise RunError(
            f"Unsupported team '{team}'. Configured teams: {', '.join(settings.teams)}."
        )
    media_type = (content_type or "").split(";")[0].strip().lower()
    if media_type not in ALLOWED_CONTENT_TYPES:
        raise RunError(
            f"Unsupported content type '{media_type}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}."
        )
    if not body:
        raise RunError("The uploaded file is empty.")
    if len(body) > settings.max_upload_bytes:
        raise RunError(
            f"File is {len(body)} bytes, above the {settings.max_upload_bytes} byte limit."
        )

    submitted_at = datetime.now(UTC)
    run_id = str(uuid.uuid4())
    safe_name = _safe_filename(filename, media_type)
    source_key = build_source_key(settings, team, run_id, safe_name, submitted_at)

    etag = objectstore.put_bytes(
        settings, settings.source_bucket, source_key, body, media_type
    )

    run = Run(
        run_id=run_id,
        team=team,
        filename=safe_name,
        content_type=media_type,
        source_bucket=settings.source_bucket,
        source_key=source_key,
        source_etag=etag,
        size=len(body),
        submitted_at=submitted_at,
    )
    run.note(STATUS_UPLOADED, f"Uploaded to {settings.source_bucket}/{source_key}")
    registry.add(run)
    log.info("run_submitted run_id=%s team=%s key=%s", run_id, team, source_key)
    return run


# The live layout is the v1.2 contract's `team=<id>/request=<id>` form, so it is
# tried first. The remaining entries are the other layouts this repository
# documents; they cost one empty listing each and mean a NiFi reconfiguration
# does not silently strand every run.
def _manifest_prefixes(settings: Settings, run: Run) -> list[str]:
    date = run.submitted_at.strftime("%Y-%m-%d")
    root = settings.manifest_prefix
    return [
        # Observed in production: manifests/completed/team=<id>/request=<id>/status.json
        f"{root}/completed/team={run.team}",
        f"{root}/failed/team={run.team}",
        f"{root}/team={run.team}",
        f"{root}/completed/{run.team}/{date}",
        f"{root}/failed/{run.team}/{date}",
        f"{root}/completed/{run.team}",
        f"{root}/failed/{run.team}",
        f"{root}/{run.team}/{date}",
        f"{root}/{run.team}",
        root,
    ]


def _masked_prefixes(settings: Settings, run: Run) -> list[str]:
    date = run.submitted_at.strftime("%Y-%m-%d")
    root = settings.masked_prefix
    return [
        f"{root}/team={run.team}",
        f"{root}/{run.team}/{date}/{run.run_id}",
        f"{root}/{run.team}/{date}",
        f"{root}/{run.team}",
        root,
    ]


def _first(document: dict, fields: tuple[str, ...]) -> str | None:
    for name in fields:
        value = document.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _model_from_key(key: str | None) -> str | None:
    """Return the `model=` segment of a masked object key, or None.

    A manifest is not always readable and does not always carry the name, while
    the destination key NiFi writes is `.../model=<name>/version=<version>/...`
    in the layouts this repository documents. Reading the name from a key we
    already have is what lets the console say which checkpoint answered
    without a second transport.
    """
    if not key:
        return None
    for segment in PurePosixPath(key).parts:
        if segment.startswith("model="):
            name = segment[len("model=") :].strip()
            if name:
                return name
    return None


def _match_manifest(settings: Settings, run: Run) -> bool:
    """Look for a terminal manifest recording our source key."""
    seen: set[str] = set()
    for prefix in _manifest_prefixes(settings, run):
        try:
            objects = objectstore.list_objects(
                settings,
                settings.manifest_bucket,
                prefix,
                limit=settings.poll_page_size,
            )
        except Exception as exc:
            log.debug("manifest_list_failed prefix=%s error=%s", prefix, exc)
            continue
        # Newest first: the manifest we want was written after our upload.
        candidates = [
            stored
            for stored in objects
            if stored.key.lower().endswith(".json") and stored.key not in seen
        ]
        candidates.sort(
            key=lambda item: item.last_modified or run.submitted_at, reverse=True
        )
        for stored in candidates:
            seen.add(stored.key)
            # The manifest is always written after the upload it describes, and
            # `candidates` is sorted newest first, so the first object older
            # than our own upload marks the point past which nothing in this
            # prefix can possibly be ours -- every remaining candidate is
            # older still. Stopping here, before downloading anything, is what
            # keeps a poll cheap: without it, every 2-second tick re-fetched
            # and parsed the full manifest history for the team looking for a
            # source-key match that could only ever be in the newest handful.
            if (
                stored.last_modified is not None
                and stored.last_modified < run.submitted_at
            ):
                log.debug(
                    "manifest_scan_stopped run_id=%s key=%s", run.run_id, stored.key
                )
                break
            try:
                body, _ = objectstore.get_bytes(
                    settings, settings.manifest_bucket, stored.key
                )
                document = json.loads(body.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(document, dict):
                continue
            source_key = _first(document, SOURCE_KEY_FIELDS)
            if source_key != run.source_key:
                continue
            _apply_manifest(run, document, stored.key)
            return True
        if len(seen) >= settings.poll_page_size * 2:
            break
    return False


def _apply_manifest(run: Run, document: dict, manifest_key: str) -> None:
    run.manifest_key = manifest_key
    run.correlation_id = _first(document, CORRELATION_FIELDS)
    status = str(document.get("status") or "").strip().lower()
    if status in FAILED_STATUSES:
        run.status = STATUS_FAILED
        run.error_code = (
            document.get("error_code") or document.get("failure_code") or "UNKNOWN"
        )
        run.message = document.get("message") or document.get("error_message")
        run.completed_at = datetime.now(UTC)
        run.note(STATUS_FAILED, f"{run.error_code}: {run.message or 'no detail'}")
        return
    if status in COMPLETED_STATUSES:
        run.masked_key = _first(document, MASKED_KEY_FIELDS)
        run.model_version = document.get("model_version")
        run.model_name = _first(document, MODEL_NAME_FIELDS) or _model_from_key(
            run.masked_key
        )
        run.status = STATUS_COMPLETED
        run.completed_at = datetime.now(UTC)
        run.discovered_by = "manifest"
        run.note(STATUS_COMPLETED, f"Manifest {manifest_key}")
        return
    run.note(STATUS_PROCESSING, f"Manifest status '{status}' is not terminal")


def _match_masked_object(settings: Settings, run: Run) -> bool:
    """Fallback: find the masked object directly.

    Used when NiFi has written the output but the manifest is not visible yet, or
    when the deployment writes no manifest at all. A candidate must carry our
    filename and either our run id or a newer timestamp than the upload.
    """
    for prefix in _masked_prefixes(settings, run):
        try:
            objects = objectstore.list_objects(
                settings, settings.masked_bucket, prefix, limit=settings.poll_page_size
            )
        except Exception as exc:
            log.debug("masked_list_failed prefix=%s error=%s", prefix, exc)
            continue
        for stored in objects:
            if PurePosixPath(stored.key).name != run.filename:
                continue
            keyed = run.run_id in stored.key
            # Without our run id in the key, require both the team and a
            # timestamp newer than the upload, so a same-named object from
            # another team or an earlier run is never claimed.
            fresh = (
                stored.last_modified is not None
                and stored.last_modified >= run.submitted_at
                and run.team in stored.key
            )
            if not (keyed or fresh):
                continue
            run.masked_bucket = settings.masked_bucket
            run.masked_key = stored.key
            run.masked_size = stored.size
            # No manifest was read on this path, so the key is the only place
            # the name of the checkpoint can come from.
            run.model_name = run.model_name or _model_from_key(stored.key)
            run.status = STATUS_COMPLETED
            run.completed_at = datetime.now(UTC)
            run.discovered_by = "masked-object" if not keyed else "masked-object-run-id"
            run.note(STATUS_COMPLETED, f"Masked object {stored.key}")
            return True
    return False


def refresh(settings: Settings, run: Run) -> Run:
    """Advance a run by inspecting object storage. Safe to call repeatedly."""
    if run.terminal:
        return run

    try:
        if _match_manifest(settings, run):
            if run.status == STATUS_COMPLETED and run.masked_key:
                run.masked_bucket = run.masked_bucket or settings.masked_bucket
                stored = objectstore.head(settings, run.masked_bucket, run.masked_key)
                if stored:
                    run.masked_size = stored.size
            return run
        if _match_masked_object(settings, run):
            return run
    except objectstore.StorageUnavailable:
        raise
    except Exception as exc:
        log.warning("run_refresh_failed run_id=%s error=%s", run.run_id, exc)
        run.note(STATUS_PROCESSING, "Storage lookup failed; will retry")
        return run

    if run.elapsed_seconds() > settings.run_timeout_seconds:
        run.status = STATUS_TIMEOUT
        run.completed_at = datetime.now(UTC)
        run.message = (
            "No masked object or manifest appeared within "
            f"{settings.run_timeout_seconds}s. Confirm NiFi is running and "
            "watching this source prefix."
        )
        run.note(STATUS_TIMEOUT, run.message)
        return run

    run.status = STATUS_PROCESSING
    run.note(STATUS_PROCESSING, "Waiting for NiFi to publish the masked object")
    return run
