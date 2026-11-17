"""The shapes this API declares, as pydantic models.

Two kinds of shape live here. `Metric` is the `{available, value, reason}`
envelope that both result sources build and every reader consumes -- it was
a frozen dataclass in `results` and a bare dict in `ranking`, which is how the
two came to disagree about what an unavailable metric looks like. The
`Config*` models are what `GET /api/v1/config` publishes, declared so the
frontend can read the schema instead of the source.

Nothing here narrows a payload on the way out. A response model *filters*:
a key the model does not declare is dropped from the response silently. That
is safe for the config document, whose every field is written out a few lines
away in `main`, and deliberately not applied to the leaderboard, whose rows
pass through whatever the published QA document carried.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class Metric(BaseModel):
    """The `{available, value, reason}` envelope from the QA schema."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    available: bool = False
    value: float | None = None
    reason: str | None = None

    @field_validator("available", mode="before")
    @classmethod
    def _truthiness(cls, raw: Any) -> bool:
        """Read the flag the way every hand-rolled reader here read it.

        Documents carry `1`, `"true"` and `""` in this field. Plain Python
        truthiness accepts all of them; pydantic's own bool coercion raises on
        a string it cannot map, which would turn one odd document into a 500.
        """
        return bool(raw)

    @field_validator("value", mode="before")
    @classmethod
    def _numbers_only(cls, raw: Any) -> float | None:
        """A string that looks like a number is not a number.

        Every reader this model replaced tested `isinstance(raw, (int, float))`.
        Pydantic's lax mode would quietly coerce `"12.5"` into a metric, so the
        check is kept explicit rather than left to the framework.
        """
        return float(raw) if isinstance(raw, (int, float)) else None

    @field_validator("reason", mode="before")
    @classmethod
    def _text(cls, raw: Any) -> str | None:
        return raw if raw is None or isinstance(raw, str) else str(raw)

    @classmethod
    def parse(cls, raw: Any) -> Metric:
        """Read one metric from a document, never raising on a malformed one."""
        if isinstance(raw, dict):
            metric = cls.model_validate(raw)
            if raw.get("value") is None:
                # An available flag with no value is not an available metric.
                # The test is on the raw key, not the parsed number: a document
                # whose value is present but unreadable stays `available` here,
                # exactly as it did before, and is filtered by `number` instead.
                return metric.model_copy(update={"available": False})
            return metric
        if isinstance(raw, (int, float)):
            return cls(available=True, value=raw)
        return cls(reason="metric missing from document")

    @property
    def number(self) -> float | None:
        """The value a reader should render, or None when there is not one."""
        return self.value if self.available else None

    def payload(self) -> dict:
        """The envelope as it crosses the wire."""
        return self.model_dump()


class LabelEntry(BaseModel):
    """One taxonomy label as `GET /api/v1/config` publishes it."""

    id: str
    display: str
    critical: bool
    canonical: bool


class LabelTaxonomy(BaseModel):
    schema_name: str
    schema_version: int
    labels: list[LabelEntry]
    aliases: dict[str, str]


class LibraryConfig(BaseModel):
    enabled: bool
    exposes_original: bool
    max_entries: int


class RankingConfig(BaseModel):
    basis: str
    mode: str
    pinned_key: str | None


class PathsConfig(BaseModel):
    source_bucket: str
    incoming_prefix: str
    masked_bucket: str
    masked_prefix: str
    manifest_prefix: str
    results_bucket: str
    results_prefix: str


class ConfigResponse(BaseModel):
    """Which features are actually usable in this deployment.

    Field order is the response's key order, so it follows the dict `config()`
    builds. Adding a key there without adding it here drops it from the
    response -- see the module docstring.
    """

    teams: list[str]
    storage_configured: bool
    max_upload_bytes: int
    allowed_content_types: list[str]
    run_timeout_seconds: int
    mock_leaderboard: bool
    samples_available: bool
    label_taxonomy: LabelTaxonomy
    library: LibraryConfig
    champion_fallback_team: str
    paths: PathsConfig
    ranking: RankingConfig
