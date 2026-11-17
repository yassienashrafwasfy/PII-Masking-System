from __future__ import annotations

import io
import logging
import os
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import labels, leaderboard, library, objectstore, runs, samples, schemas
from .config import load_settings

logging.basicConfig(level=os.getenv("ARENA_LOG_LEVEL", "INFO"))
log = logging.getLogger("arena.api")

app = FastAPI(title="PII Masking Model Arena API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "LEADERBOARD_ALLOWED_ORIGINS", "http://localhost:5173"
    ).split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# The leaderboard is a router rather than routes on `app`: every name it needs
# -- the demo rows, the badge map, the metric flattening, the MLflow fallback --
# is used by that one endpoint and by nothing else here.
app.include_router(leaderboard.router)

# The one run registry this process serves. It is constructed here, in the
# module that wires the application together, rather than inside `runs`: the
# registry is process state with a process lifetime, and that is a decision
# for whoever assembles the process, not for the module that defines the type.
#
# Every route receives it through `Depends`, so nothing on a request path
# reaches for a global, and a test substitutes its own with
# `app.dependency_overrides[get_registry]` -- scoped to the app and removed
# again -- instead of rebinding an attribute on another module.
REGISTRY = runs.RunRegistry()


def get_registry() -> runs.RunRegistry:
    """Provide the run registry this process serves."""
    return REGISTRY


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now(UTC).isoformat()}


@app.get("/api/v1/config", response_model=schemas.ConfigResponse)
def config() -> dict:
    """Tell the frontend which features are actually usable in this deployment."""
    settings = load_settings()
    return {
        "teams": list(settings.teams),
        "storage_configured": settings.storage_configured,
        "max_upload_bytes": settings.max_upload_bytes,
        "allowed_content_types": sorted(runs.ALLOWED_CONTENT_TYPES),
        "run_timeout_seconds": settings.run_timeout_seconds,
        # TEMPORARY: see mock_data.py. True until real results are published.
        "mock_leaderboard": settings.mock_leaderboard,
        "samples_available": bool(settings.sample_directory),
        # Published so the frontend takes label ordering, display names and
        # criticality from one place, instead of keeping a second copy of the
        # taxonomy in JS that could drift from the gateway's.
        "label_taxonomy": labels.taxonomy_payload(),
        # The durable file list. Surfaced so the frontend hides the nav entry
        # and the original-document pane outright, rather than discovering the
        # restriction by way of a 404 or a 403 after the reader has clicked.
        "library": {
            "enabled": settings.library_enabled,
            "exposes_original": settings.library_expose_original,
            "max_entries": settings.library_max_entries,
        },
        # Only used when the board cannot be read at all. The console normally
        # takes its team from the leaderboard payload's `champion`, so this is
        # the answer to "who masks when we cannot tell who is winning", not a
        # second opinion about who the champion is.
        "champion_fallback_team": settings.champion_fallback_team,
        "paths": {
            "source_bucket": settings.source_bucket,
            "incoming_prefix": settings.incoming_prefix,
            "masked_bucket": settings.masked_bucket,
            "masked_prefix": settings.masked_prefix,
            "manifest_prefix": settings.manifest_prefix,
            "results_bucket": settings.results_bucket,
            "results_prefix": settings.results_prefix,
        },
        # Which of qa-automation's two rankings the board is built from, and
        # which document set it is allowed to draw on. Surfaced so the UI can
        # label the recall and precision columns with the basis they came from
        # instead of guessing.
        "ranking": {
            "basis": settings.ranking_basis,
            "mode": settings.leaderboard_mode,
            "pinned_key": settings.leaderboard_key or None,
        },
    }


@app.get("/api/v1/samples")
def get_samples() -> dict:
    """List documents in the configured local sample folder, if any."""
    settings = load_settings()
    return {"samples": samples.list_samples(settings)}


@app.post("/api/v1/runs")
async def create_run(
    team: str = Form(...),
    file: UploadFile | None = File(None),
    sample: str | None = Form(None),
    registry: runs.RunRegistry = Depends(get_registry),
) -> dict:
    """Upload one document to the source prefix and start tracking it.

    Accepts either a browser-uploaded `file` or the `name` of a document in
    the configured sample folder, read server-side. Exactly one must be given.
    """
    settings = load_settings()

    if sample and file is not None:
        raise HTTPException(
            status_code=400, detail="Provide either file or sample, not both."
        )
    if sample:
        try:
            body, content_type = samples.read_sample(settings, sample)
        except samples.SampleNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        filename = sample
    elif file is not None:
        body = await file.read()
        content_type = file.content_type or ""
        if not content_type or content_type == "application/octet-stream":
            guessed = {
                ".pdf": "application/pdf",
                ".txt": "text/plain",
                ".csv": "text/csv",
                ".json": "application/json",
            }
            suffix = os.path.splitext(file.filename or "")[1].lower()
            content_type = guessed.get(suffix, content_type)
        filename = file.filename or "document"
    else:
        raise HTTPException(status_code=400, detail="Provide a file or a sample name.")

    try:
        run = runs.submit(
            settings, team, filename, content_type, body, registry=registry
        )
    except runs.RunError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except objectstore.StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("run_submit_failed")
        raise HTTPException(
            status_code=502, detail=f"Upload to object storage failed: {exc}"
        ) from exc
    return run.payload()


@app.get("/api/v1/runs")
def list_runs(
    limit: int = Query(default=25, ge=1, le=100),
    registry: runs.RunRegistry = Depends(get_registry),
) -> dict:
    return {"runs": [run.payload() for run in registry.recent(limit)]}


@app.get("/api/v1/library")
def list_library(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    team: str = Query(default=""),
    status: str = Query(default=""),
    q: str = Query(default=""),
    registry: runs.RunRegistry = Depends(get_registry),
) -> dict:
    """Masked documents rebuilt from storage, so they outlive this process.

    Distinct from `/api/v1/runs`, which means "runs this process created" and
    is left alone: the two answer different questions, and blurring them would
    make the registry's restart-scoped semantics silently untrue.
    """
    settings = load_settings()
    if not settings.library_enabled:
        raise HTTPException(status_code=404, detail="The file library is disabled.")
    try:
        return library.page(
            settings,
            registry=registry,
            limit=limit,
            offset=offset,
            team=team,
            status=status,
            query=q,
        )
    except objectstore.StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str, registry: runs.RunRegistry = Depends(get_registry)) -> dict:
    settings = load_settings()
    try:
        run = library.resolve(settings, run_id, registry=registry)
    except objectstore.StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(
            status_code=404, detail="No run with that id is in the current index."
        )
    try:
        runs.refresh(settings, run)
    except objectstore.StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return run.payload()


@app.get("/api/v1/runs/{run_id}/content/{side}")
def get_run_content(
    run_id: str, side: str, registry: runs.RunRegistry = Depends(get_registry)
):
    """Stream the original or masked document through the API.

    Bytes are proxied rather than handed out as presigned URLs so storage
    credentials stay server-side and the browser needs no bucket CORS rules.
    """
    settings = load_settings()
    try:
        run = library.resolve(settings, run_id, registry=registry)
    except objectstore.StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(
            status_code=404, detail="No run with that id is in the current index."
        )
    if side == "original":
        # The original is the *unmasked* document. A run submitted in this
        # process keeps its before/after view; one rebuilt from storage does
        # not, because there is no authentication here and a durable URL to raw
        # PII is a very different thing from a transient one.
        #
        # The test is the run's own provenance, not whether it is in the
        # registry: `library.resolve` deliberately caches what it rebuilt, so
        # after one masked fetch a rehydrated run *is* in the registry and a
        # membership check would quietly start allowing the original through.
        if run.discovered_by == "library" and not settings.library_expose_original:
            raise HTTPException(
                status_code=403,
                detail=(
                    "The original document is only available while its run is "
                    "in progress. The library serves masked output only."
                ),
            )
        bucket, key = run.source_bucket, run.source_key
    elif side == "masked":
        if not run.masked_key:
            raise HTTPException(
                status_code=409, detail="The masked document is not available yet."
            )
        bucket, key = (run.masked_bucket or settings.masked_bucket), run.masked_key
    else:
        raise HTTPException(status_code=404, detail="Unknown document side.")

    # HEAD before GET. `objectstore.get_bytes` reads a whole body into memory
    # and the upload cap never applied to this path, so without this an object
    # of any size is an unbounded allocation on an endpoint anyone can call.
    stored = objectstore.head(settings, bucket, key)
    if stored is None:
        # The manifest still references it, so this is not an unknown run --
        # the object itself is gone, most likely aged out by a lifecycle rule.
        raise HTTPException(
            status_code=410,
            detail=(
                f"The {side} document is referenced by its manifest but is no "
                "longer in the bucket."
            ),
        )
    if stored.size > settings.download_limit_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"The {side} document is {stored.size} bytes, over the "
                f"{settings.download_limit_bytes}-byte read limit."
            ),
        )

    try:
        body, content_type = objectstore.get_bytes(settings, bucket, key)
    except objectstore.StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not read {side} document: {exc}"
        ) from exc

    if content_type == "application/octet-stream" and run.content_type:
        content_type = run.content_type
    return StreamingResponse(
        io.BytesIO(body),
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{run.filename}"',
            # A terminal run's masked object never changes, and it is now
            # refetched on every navigation into the library, so it is worth
            # caching. An in-flight run must not be: its masked side appears
            # partway through and a cached 404 or stale body would stick.
            "Cache-Control": ("private, max-age=60" if run.terminal else "no-store"),
            "ETag": f'"{stored.etag}"',
            "X-Content-Type-Options": "nosniff",
            # These documents are durably addressable now. Without this a
            # crawler that reaches the site puts masked clinical documents
            # into a search index.
            "X-Robots-Tag": "noindex, nofollow",
        },
    )
