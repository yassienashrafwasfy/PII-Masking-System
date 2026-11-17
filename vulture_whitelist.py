"""Names this repository declares for a framework to read, not for Python.

vulture only sees attribute *reads*, and a pydantic field is never read here:
the response model in `evaluation/leaderboard_api/schemas.py` exists so FastAPI
can filter and document the payload, and the fields are addressed by the
framework from the class body alone. Without this file every one of them is a
60%-confidence "unused variable", which is the whole `[tool.vulture]` baseline
turning to noise the first time a schema gains a field.

`paths = ["."]` in pyproject.toml already covers this file, so `vulture .` picks
it up with no extra argument -- and that is the point: the check has to stay a
plain `vulture .` in `ci/qa.yml`.

This file is parsed, never imported. pydantic moves a field off the class body
into the model, so `schemas.LabelEntry.id` raises `AttributeError` at runtime;
vulture only walks the AST and never executes it. Nothing may import this
module -- it is a list of names for a static checker, not code.

Every field of every model is listed, not only the ones vulture reports today.
It reports a name once per whole run, so a field whose name happens to be read
somewhere else in the tree (`teams`, `basis`, `critical`) is silent until that
other reader goes away. Listing the models in full means removing an unrelated
caller can never make this check fail for a reason that has nothing to do with
the change.
"""

from evaluation.leaderboard_api import schemas

# The `{available, value, reason}` envelope. `available`, `value` and `reason`
# are all genuinely read through `Metric.number` / `Metric.parse`, so only the
# pydantic-reserved config attribute needs naming.
schemas.Metric.model_config

_label = schemas.LabelEntry
_label.id
_label.display
_label.critical
_label.canonical

_taxonomy = schemas.LabelTaxonomy
_taxonomy.schema_name
_taxonomy.schema_version
_taxonomy.labels
_taxonomy.aliases

_library = schemas.LibraryConfig
_library.enabled
_library.exposes_original
_library.max_entries

_ranking = schemas.RankingConfig
_ranking.basis
_ranking.mode
_ranking.pinned_key

_paths = schemas.PathsConfig
_paths.source_bucket
_paths.incoming_prefix
_paths.masked_bucket
_paths.masked_prefix
_paths.manifest_prefix
_paths.results_bucket
_paths.results_prefix

_config = schemas.ConfigResponse
_config.teams
_config.storage_configured
_config.max_upload_bytes
_config.allowed_content_types
_config.run_timeout_seconds
_config.mock_leaderboard
_config.samples_available
_config.label_taxonomy
_config.library
_config.champion_fallback_team
_config.paths
_config.ranking
