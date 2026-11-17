"""Environment-driven settings for the arena API.

The arena reads QA result documents and drives interactive masking runs against
the same object storage NiFi uses. Every bucket, prefix, and credential is
supplied by the environment so the same image runs locally and on the VPS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_TEAMS = ("octo", "synthify", "berts")


def _clean(value: str) -> str:
    return value.strip().strip("/")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    endpoint_url: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    region: str = "auto"

    results_bucket: str = "data"
    results_prefix: str = "results"

    # Which of qa-automation's two independent rankings the board is built on.
    # "overlapping" counts a span that found the entity but got its boundaries
    # wrong; "exact" requires the boundaries too. QA's own headline number, and
    # the one asked for here, is the overlapping ranking.
    ranking_basis: str = "overlapping"
    # Empty considers every ranking document in the prefix. "release" -- the
    # default -- ignores `local` documents, which come from a developer running
    # the suite against locally served models and must never displace a real
    # release evaluation just by being newer.
    leaderboard_mode: str = "release"
    # Full object key pinning one exact ranking document, e.g.
    # `results/leaderboard-aggregate-15docs-release.json`. Overrides the
    # automatic choice; use it to freeze the board on a known document.
    leaderboard_key: str = ""

    # Where the arena keeps its own copies of the ranking documents it has seen.
    # qa-automation names its published document after the document count and
    # mode alone, so every run overwrites the last one and R2 -- which has no
    # object versioning -- loses the previous scores. Without an archive the
    # board can only ever see one run, which is why every trend line is a single
    # point and "Iron model" always reads "1 run tracked". Nested one level under
    # the results prefix so `results.is_ranking_key` still excludes it from the
    # flat-key scan; `discover` lists it explicitly instead.
    history_prefix: str = "results/history"
    # Opt-in, because it is the one place the arena writes to the results
    # bucket. A credential with read-only access to `data` leaves this a no-op
    # rather than an error -- see history.archive.
    archive_rankings: bool = False

    # Which team the masking console falls back to when the board cannot be
    # read at all. The console normally follows QA's rank 1, resolved from the
    # leaderboard payload, so this only applies when storage is unreachable.
    champion_fallback_team: str = "berts"

    # Three distinct buckets in production: QA results live in `data`, uploads in
    # `source`, and masked output plus manifests in `destination`.
    source_bucket: str = "source"
    incoming_prefix: str = "incoming"
    # NiFi's ListS3 watches `incoming/<team>/requests/`. Every source key it has
    # actually processed is flat at that depth, so the run id is folded into the
    # filename rather than added as a sub-folder: a nested key risks never being
    # listed. Keeping the run id also makes each upload unique, which matters
    # because a repeated flat key would otherwise match an earlier run's
    # manifest. Set ARENA_INCOMING_KEY_TEMPLATE to
    # `{prefix}/{team}/requests/{filename}` for pristine names, accepting that
    # re-uploading a filename becomes ambiguous.
    incoming_key_template: str = "{prefix}/{team}/requests/{run_id}-{filename}"
    masked_bucket: str = "destination"
    masked_prefix: str = "masked"
    manifest_bucket: str = "destination"
    manifest_prefix: str = "manifests"

    teams: tuple[str, ...] = field(default_factory=lambda: DEFAULT_TEAMS)
    run_timeout_seconds: int = 900
    max_upload_bytes: int = 10 * 1024 * 1024
    poll_page_size: int = 400

    # The masked-file library: runs rebuilt from the manifests NiFi already
    # writes, so a masked document stays reachable after the API restarts. The
    # in-process RunRegistry is demo state and forgets everything on restart;
    # object storage is the system of record and this reads it back. See
    # library.py.
    #
    # An operator kill-switch rather than an opt-in: the feature is the durable
    # half of the console and is meant to be on. Turning it off restores the
    # previous behaviour exactly (runs live only as long as the process does).
    library_enabled: bool = True
    # The library serves masked output only. The original side is the
    # *unmasked* document, and the arena has no authentication -- anything it
    # serves is readable by anyone who can reach the site. A live run still
    # shows both sides for its before/after view; only the durable path is
    # restricted. Turning this on publishes raw PII by URL, permanently.
    library_expose_original: bool = False
    # Manifests for documents NiFi processed that never came through the arena.
    # Off because their source keys are not the arena's own and often carry
    # real filenames, and because nobody using this UI chose to upload them.
    library_include_foreign: bool = False
    # How many entries the index keeps, newest first. Bounds both the response
    # size and how much is exposed at once.
    library_max_entries: int = 25
    # Ceiling on how many manifest keys one rebuild will list. Note the listing
    # is lexicographic by key and the keys embed a random UUID, so truncating
    # here yields an arbitrary sample rather than the newest -- keep it well
    # above the real object count and treat hitting it as "partial view".
    library_scan_limit: int = 5000
    # A bucket listing is a Class A operation on R2, so this is the knob that
    # decides the bill: cost is inversely proportional to it. At 300s a bucket
    # holding ~100k manifests still sits inside the free tier. A new masked
    # file appearing within five minutes is fine for a library; the console's
    # own polling, not this, is what makes a *live* run feel immediate.
    library_cache_seconds: int = 300
    # Read cap for the content endpoint. `objectstore.get_bytes` reads a whole
    # body into memory and `max_upload_bytes` only ever guarded the upload
    # path, so without this a large object reachable by URL is an unbounded
    # allocation.
    library_max_download_bytes: int = 0  # 0 -> fall back to max_upload_bytes

    # Opt-in, not opt-out. This defaulted on while the arena had no reader for
    # anything QA actually publishes; now that `ranking.py` reads the real
    # cross-team ranking document, defaulting on would hide a working board
    # behind randomized preview data. See mock_data.py.
    mock_leaderboard: bool = False

    # A local folder of real sample documents the console can upload without a
    # native file dialog. Read-only; empty disables the feature entirely.
    sample_directory: str = ""

    @property
    def storage_configured(self) -> bool:
        """True when object storage can be reached at all."""
        return bool(self.endpoint_url and self.access_key_id and self.secret_access_key)

    @property
    def download_limit_bytes(self) -> int:
        """Largest object the content endpoint will read into memory.

        Defaults to the upload cap rather than repeating its value, so raising
        one raises the other and a document the arena accepted can always be
        read back.
        """
        return self.library_max_download_bytes or self.max_upload_bytes


def load_settings() -> Settings:
    teams = tuple(
        team.strip()
        for team in os.getenv("ARENA_TEAMS", ",".join(DEFAULT_TEAMS)).split(",")
        if team.strip()
    )
    source_bucket = os.getenv("ARENA_SOURCE_BUCKET", "source").strip() or "source"
    masked_bucket = os.getenv("ARENA_MASKED_BUCKET", "").strip() or "destination"
    return Settings(
        endpoint_url=os.getenv("ARENA_S3_ENDPOINT_URL", "").strip().rstrip("/"),
        access_key_id=os.getenv("ARENA_S3_ACCESS_KEY_ID", "").strip(),
        secret_access_key=os.getenv("ARENA_S3_SECRET_ACCESS_KEY", "").strip(),
        region=os.getenv("ARENA_S3_REGION", "auto").strip() or "auto",
        results_bucket=os.getenv("ARENA_RESULTS_BUCKET", "data").strip() or "data",
        results_prefix=_clean(os.getenv("ARENA_RESULTS_PREFIX", "results")),
        ranking_basis=os.getenv("ARENA_RANKING_BASIS", "").strip() or "overlapping",
        # Deliberately not `_clean`ed: an empty value is meaningful here (it
        # means "consider every mode"), so it must survive as an empty string.
        leaderboard_mode=os.getenv("ARENA_LEADERBOARD_MODE", "release").strip(),
        leaderboard_key=os.getenv("ARENA_LEADERBOARD_KEY", "").strip().lstrip("/"),
        history_prefix=_clean(os.getenv("ARENA_HISTORY_PREFIX", "results/history")),
        archive_rankings=_bool("ARENA_ARCHIVE_RANKINGS", False),
        champion_fallback_team=os.getenv("ARENA_CHAMPION_FALLBACK_TEAM", "")
        .strip()
        .lower()
        or "berts",
        source_bucket=source_bucket,
        incoming_prefix=_clean(os.getenv("ARENA_INCOMING_PREFIX", "incoming")),
        # Compose passes this through as an empty string when unset, so an
        # empty value must fall back to the default rather than blank the key.
        incoming_key_template=os.getenv("ARENA_INCOMING_KEY_TEMPLATE", "").strip("/ ")
        or "{prefix}/{team}/requests/{run_id}-{filename}",
        masked_bucket=masked_bucket,
        masked_prefix=_clean(os.getenv("ARENA_MASKED_PREFIX", "masked")),
        # Manifests sit beside the masked output unless told otherwise.
        manifest_bucket=os.getenv("ARENA_MANIFEST_BUCKET", "").strip() or masked_bucket,
        manifest_prefix=_clean(os.getenv("ARENA_MANIFEST_PREFIX", "manifests")),
        teams=teams or DEFAULT_TEAMS,
        run_timeout_seconds=_int("ARENA_RUN_TIMEOUT_SECONDS", 900),
        max_upload_bytes=_int("ARENA_MAX_UPLOAD_BYTES", 10 * 1024 * 1024),
        poll_page_size=_int("ARENA_POLL_PAGE_SIZE", 400),
        library_enabled=_bool("ARENA_LIBRARY_ENABLED", True),
        library_expose_original=_bool("ARENA_LIBRARY_EXPOSE_ORIGINAL", False),
        library_include_foreign=_bool("ARENA_LIBRARY_INCLUDE_FOREIGN", False),
        library_max_entries=_int("ARENA_LIBRARY_MAX_ENTRIES", 25),
        library_scan_limit=_int("ARENA_LIBRARY_SCAN_LIMIT", 5000),
        library_cache_seconds=_int("ARENA_LIBRARY_CACHE_SECONDS", 300),
        library_max_download_bytes=_int("ARENA_LIBRARY_MAX_DOWNLOAD_BYTES", 0),
        mock_leaderboard=_bool("ARENA_MOCK_LEADERBOARD", False),
        sample_directory=os.getenv("ARENA_SAMPLE_DIRECTORY", "").strip(),
    )
