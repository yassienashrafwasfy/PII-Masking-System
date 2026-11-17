"""Read qa-automation's cross-team ranking document and build the board from it.

This is the leaderboard's real source. `results.py` reads the *other* kind of
document -- one `qa-result.schema.json` v1.0 file per run, with
`{available, value, reason}` metric envelopes -- which qa-automation defines but
does not publish to the results prefix. What `scripts/rank_teams.py` actually
uploads is a single cross-team ranking document holding both rankings for a
whole document set:

    {"document_ids": [...], "mode": "release", "source": "playwright",
     "close_threshold": 0.03, "notes": [...],
     "ranking_overlap": [{"rank": 1, "team": "berts", "documents_scored": 15,
                          "overlapping_f1": ..., "overlapping_recall": ...,
                          "overlapping_precision": ..., "p95_latency_ms": null,
                          "model_size_mb": 120.3, "resolved_by": "..."}, ...],
     "ranking_exact": [...same, with exact_* keys...],
     "excluded": {"team": "why it was left out"}}

Two facts about that document drive most of the code here.

**QA's own rank order is authoritative and cannot be recomputed.**
`rank_teams.py` does not sort by F1. It buckets F1 at `close_threshold` (0.03),
so two teams within 0.03 of each other are deemed equal on detection quality,
and then breaks that tie on lower p95 latency, then on smaller model. Sorting
these rows by F1 here would silently reorder any tied pair -- so `rank` is read,
never derived, and each row carries QA's `resolved_by` explaining which case
applied.

**The two document kinds share one prefix and are told apart by key shape.**
The ranking document is flat -- `results/<leaderboard filename>.json` -- while
per-team reports are nested at
`results/<team>/<document_id>/pipeline-test-pw-<team>-<mode>.json`. Only the
flat `leaderboard-*.json` keys are ranking documents; the predicate that decides
that is `results.is_ranking_key`, which both readers share.

A team in `excluded` is not a team that scored badly: `rank_teams.py` drops any
team missing a report for *any* requested document, so it is never compared on a
partial document set. Those teams surface here as unranked rows carrying QA's
reason, the same way an unavailable metric envelope carries its own.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import history, labels, objectstore
from .config import Settings
from .results import ACCENTS, compute_awards, is_ranking_key
from .schemas import Metric

log = logging.getLogger("arena.ranking")

# The two independent rankings in every document, keyed by the metric prefix
# their entries use. Not a blended score -- QA deliberately keeps them separate.
BASES = {
    "overlapping": "ranking_overlap",
    "exact": "ranking_exact",
}

# Newest-first cap on how many ranking documents are read per request. Each is a
# few KB, but the history prefix now genuinely accumulates one per QA run, and
# only the trend lines need the older ones -- so this bounds both the number of
# GETs a page load costs and how far back a sparkline reaches.
MAX_DOCUMENTS = 40


def _envelope(value: Any, reason: str | None = None) -> dict:
    """Wrap a bare number in the `{available, value, reason}` shape the UI reads.

    The ranking document uses plain JSON `null` where the QA result schema uses
    an envelope with an explanation, so the reason has to be supplied by the
    caller from the document's own `notes`.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return Metric(
            available=False, reason=reason or "not reported in this ranking"
        ).payload()
    return Metric(available=True, value=value).payload()


@dataclass(frozen=True)
class RankingDocument:
    """One parsed ranking document plus where and when it came from."""

    key: str
    observed_at: Any
    payload: dict

    @property
    def mode(self) -> str:
        return str(self.payload.get("mode") or "")

    @property
    def source(self) -> str:
        return str(self.payload.get("source") or "")

    @property
    def document_ids(self) -> list[str]:
        ids = self.payload.get("document_ids")
        return [str(item) for item in ids] if isinstance(ids, list) else []

    @property
    def notes(self) -> list[str]:
        notes = self.payload.get("notes")
        return [str(note) for note in notes] if isinstance(notes, list) else []

    def entries(self, basis: str) -> list[dict]:
        rows = self.payload.get(BASES.get(basis, ""), [])
        return [row for row in rows] if isinstance(rows, list) else []


def _latency_reason(notes: list[str]) -> str:
    """Explain a null p95 using the document's own note, when it has one."""
    for note in notes:
        if "latency" in note.lower():
            return note
    return "no load-test latency recorded for this run"


def _size_reason(notes: list[str]) -> str:
    for note in notes:
        if "size" in note.lower():
            return note
    return "model size not recorded for this run"


def _other_basis(basis: str) -> str:
    return "exact" if basis == "overlapping" else "overlapping"


def _entry_metrics(
    entry: dict, basis: str, notes: list[str], other: dict | None
) -> dict:
    """Map one ranking entry (plus its twin in the other ranking) onto metrics.

    Both rankings are exposed, because a team can lead on overlap while trailing
    on exact boundaries and that gap is the interesting part: overlap counts a
    span that found the entity but got its edges wrong, exact does not.
    """
    metrics: dict[str, dict] = {}
    # Prefixes follow the chosen basis: `entry` came from the basis ranking and
    # `other` from its twin, so naming them in a fixed order would file every
    # metric under the wrong key whenever the basis is `exact`.
    for prefix, row in ((basis, entry), (_other_basis(basis), other)):
        if not isinstance(row, dict):
            continue
        for suffix in ("recall", "precision", "f1"):
            name = f"{prefix}_{suffix}"
            if name in row:
                metrics[name] = _envelope(row.get(name))

    # `latency_p95_ms` rather than the document's `p95_latency_ms`: this is the
    # name the medal table and the frontend formatters already key on.
    metrics["latency_p95_ms"] = _envelope(
        entry.get("p95_latency_ms"), _latency_reason(notes)
    )
    metrics["model_size_mb"] = _envelope(
        entry.get("model_size_mb"), _size_reason(notes)
    )
    metrics["documents_scored"] = _envelope(entry.get("documents_scored"))

    # QA's own recall on a 0-100 scale -- not F1. F1 folds in precision, and the
    # release-mode precision proxy (diffing masked bytes with no true spans to
    # compare against) runs low enough to bury a genuinely strong recall number,
    # which is what QA asked the headline score to reflect. This can disagree
    # with `rank` for a tiebroken pair, because QA's own tiebreak (close_threshold,
    # then latency, then model size) is a rule over F1, not over this number.
    headline_recall = metrics.get(f"{basis}_recall", {})
    if headline_recall.get("available"):
        metrics["leaderboard_score"] = _envelope(headline_recall["value"] * 100)
    else:
        metrics["leaderboard_score"] = _envelope(
            None, f"{basis} recall missing from the ranking entry"
        )
    return metrics


def _twin(document: RankingDocument, basis: str) -> dict[str, dict]:
    """Index the *other* ranking's entries by team, to attach alongside."""
    return {
        str(entry.get("team")): entry
        for entry in document.entries(_other_basis(basis))
        if isinstance(entry, dict) and entry.get("team")
    }


def rows_from_document(document: RankingDocument, basis: str) -> list[dict]:
    """Build one row per ranked team, then one per excluded team."""
    notes = document.notes
    twin = _twin(document, basis)
    rows: list[dict] = []

    for entry in document.entries(basis):
        if not isinstance(entry, dict):
            continue
        team = str(entry.get("team") or "").strip()
        if not team:
            log.warning("ranking_entry_without_team key=%s", document.key)
            continue
        rank = entry.get("rank")
        rows.append(
            {
                "team": team,
                "model_name": team,
                "model_version": None,
                # Filled in by `enrich_from_reports` when the per-team report is
                # readable; the ranking document itself carries no model identity.
                "model": "",
                "model_digest": None,
                "eligible": True,
                "ineligible_reasons": [],
                "metrics": _entry_metrics(entry, basis, notes, twin.get(team)),
                "per_entity_recall": {},
                "gate_decisions": [],
                "dataset": {
                    "version": None,
                    "case_count": len(document.document_ids) or None,
                    "manifest_sha256": None,
                },
                "evaluation": {
                    "mode": document.mode,
                    "api_contract_version": None,
                    "elapsed_seconds": None,
                },
                "provenance": {},
                "document_count": int(entry.get("documents_scored") or 0),
                "result_key": document.key,
                "observed_at": (
                    document.observed_at.isoformat() if document.observed_at else None
                ),
                # QA's rank, not a derived one -- see the module docstring.
                "qa_rank": int(rank) if isinstance(rank, int) else None,
                "resolved_by": entry.get("resolved_by"),
                "_observed_sort": document.observed_at,
            }
        )

    ranked_teams = {row["team"] for row in rows}
    excluded = document.payload.get("excluded")
    if isinstance(excluded, dict):
        for team, reason in excluded.items():
            team = str(team).strip()
            if not team or team in ranked_teams:
                continue
            rows.append(
                {
                    "team": team,
                    "model_name": team,
                    "model_version": None,
                    "model": "",
                    "model_digest": None,
                    # Excluded is "not comparable on this document set", not
                    # "scored badly" -- it must never read as a low rank.
                    "eligible": False,
                    "ineligible_reasons": [str(reason)],
                    "metrics": {},
                    "per_entity_recall": {},
                    "gate_decisions": [],
                    "dataset": {
                        "version": None,
                        "case_count": len(document.document_ids) or None,
                        "manifest_sha256": None,
                    },
                    "evaluation": {
                        "mode": document.mode,
                        "api_contract_version": None,
                        "elapsed_seconds": None,
                    },
                    "provenance": {},
                    "document_count": 0,
                    "result_key": document.key,
                    "observed_at": (
                        document.observed_at.isoformat()
                        if document.observed_at
                        else None
                    ),
                    "qa_rank": None,
                    "resolved_by": None,
                    "_observed_sort": document.observed_at,
                }
            )
    return rows


def is_history_key(key: str, prefix: str) -> bool:
    """True for one of the arena's own archived copies.

    Deliberately a separate predicate from `is_ranking_key` rather than a
    loosening of it: that one's flat-key rule is what tells a cross-team ranking
    apart from a per-team report sharing the same prefix, and relaxing it would
    make every `results/<team>/<doc>/report.json` look like a board.
    """
    if not prefix:
        return False
    return key.startswith(f"{prefix}/") and key.endswith(".json")


def _parse_observed(value: Any) -> Any:
    """Turn a recorded ISO timestamp back into a datetime, or give up quietly."""
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _sort_key(item: Any) -> tuple:
    """Oldest first, with unknown timestamps sorted ahead of every known one."""
    observed = getattr(item, "observed_at", None) or getattr(
        item, "last_modified", None
    )
    return (observed is not None, observed)


def _stored_candidates(settings: Settings) -> list:
    """Every object that could be a ranking document, from both prefixes.

    A LIST is recursive, so when the history prefix sits under the results
    prefix -- which it does by default -- one call already returns both kinds
    and a second would only duplicate them.
    """
    stored = list(
        objectstore.list_objects(
            settings, settings.results_bucket, settings.results_prefix
        )
    )
    # Not named `history`: that is the module this one imports, and shadowing it
    # here would be a trap for the next edit rather than a bug today.
    history_prefix = settings.history_prefix
    if history_prefix and not history_prefix.startswith(f"{settings.results_prefix}/"):
        stored.extend(
            objectstore.list_objects(settings, settings.results_bucket, history_prefix)
        )
    return [
        item
        for item in stored
        if is_ranking_key(item.key, settings.results_prefix)
        or is_history_key(item.key, history_prefix)
    ]


def discover(settings: Settings) -> list[RankingDocument]:
    """Read every ranking document the arena can see, oldest first.

    Two sources: the flat `leaderboard-*.json` keys qa-automation publishes, and
    the arena's own archive of the documents it has already seen. The archive is
    what gives the board more than one point to draw a trend from -- see
    `history` for why QA's own keys cannot supply one.

    Documents are deduplicated by content, keeping the earliest sighting of each.
    That matters in both directions: an archived copy is byte-identical to the
    original it was taken from, and qa-automation publishes a stable "latest" key
    alongside its timestamped one, so without this a single QA run would land on
    the trend line two or three times over.
    """
    stored = _stored_candidates(settings)
    # Newest wins if the prefixes have grown beyond the cap, but the survivors
    # are handed back oldest-first so trend lines read left to right.
    stored.sort(key=_sort_key)
    documents: list[RankingDocument] = []
    seen: set[str] = set()
    for item in stored[-MAX_DOCUMENTS:]:
        try:
            body, _ = objectstore.get_bytes(settings, settings.results_bucket, item.key)
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            log.warning("ranking_unreadable key=%s error=%s", item.key, exc)
            continue
        except Exception as exc:
            log.warning("ranking_fetch_failed key=%s error=%s", item.key, exc)
            continue
        if not isinstance(payload, dict) or "ranking_overlap" not in payload:
            continue
        # An archived copy carries the time QA published the original, which is
        # the honest x-axis position; the object's own mtime is when this
        # service happened to copy it.
        payload, recorded = history.unwrap(payload)
        fingerprint = history.content_fingerprint(payload)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        documents.append(
            RankingDocument(
                key=item.key,
                observed_at=_parse_observed(recorded) or item.last_modified,
                payload=payload,
            )
        )
    # Re-sorted because an archived copy's recorded time can predate the mtime
    # the pre-read ordering used.
    documents.sort(key=_sort_key)
    return documents


def select(
    documents: list[RankingDocument], settings: Settings
) -> RankingDocument | None:
    """Choose which ranking document the board shows.

    An exact key pin wins outright. Otherwise the mode filter comes first, and
    it defaults to `release` on purpose: `local` documents come from a developer
    running the suite against locally served models, and one of those uploaded
    by accident must never displace a real release evaluation. Among the
    survivors, more documents scored beats fewer -- a 15-document aggregate is
    a stronger statement than a single-document run -- and only then does
    recency decide.
    """
    if not documents:
        return None
    if settings.leaderboard_key:
        pinned = [item for item in documents if item.key == settings.leaderboard_key]
        if pinned:
            return pinned[-1]
        log.warning(
            "ranking_pin_not_found key=%s falling back to automatic selection",
            settings.leaderboard_key,
        )

    candidates = documents
    if settings.leaderboard_mode:
        matching = [
            item for item in documents if item.mode == settings.leaderboard_mode
        ]
        if matching:
            candidates = matching
        else:
            log.warning(
                "no_ranking_document_in_mode mode=%s falling back to all modes",
                settings.leaderboard_mode,
            )

    def key(item: RankingDocument) -> tuple:
        return (
            len(item.document_ids),
            item.observed_at is not None,
            item.observed_at,
        )

    return max(candidates, key=key)


# The metrics a per-label entry may carry. Recall is the one QA can always
# measure -- it owns the ground truth, so it knows whether a known entity
# survived masking. Precision and F1 need the *detected* spans, which the v1.2
# masking API does not return, so they are usually unavailable-with-a-reason
# rather than absent.
PER_LABEL_METRICS = ("recall", "precision", "f1", "support")

# QA reports counts under prf2's names. Read both rather than asking QA to
# rename a field every one of its own tests already asserts on.
_COUNT_ALIASES = {"support": "actual_count"}

# Why precision and F1 are missing when they are missing. Stated once here so
# every row carries the same explanation instead of the UI inventing one.
_NO_SPANS_REASON = (
    "the masking API returns masked bytes without entity spans, so a detected "
    "span cannot be attributed to a label"
)


def _per_label_block(per_label: dict, *, basis: str) -> dict:
    """Normalise one report's `per_label` into the board's own shape.

    Keeps every metric the report carries, not just recall: an earlier version
    read `.get("recall")` and dropped precision, F1 and support on the floor,
    which meant the arena could never show them however good the upstream data
    got.

    A metric the report does not carry becomes an unavailable envelope with a
    reason rather than a missing key, because "not reported, and here is why"
    is a state the per-label view has to render -- a blank cell and a zero look
    identical and only one of them is true.
    """
    block: dict[str, dict] = {}
    for raw_label, scores in per_label.items():
        if not isinstance(scores, dict):
            continue
        canonical, aliased_from, is_canonical = labels.normalize(str(raw_label))
        if not canonical:
            continue
        entry: dict[str, Any] = {
            "basis": basis,
            "aliased_from": aliased_from,
            "canonical": is_canonical,
        }
        for metric in PER_LABEL_METRICS:
            value = scores.get(metric)
            if value is None and metric in _COUNT_ALIASES:
                # QA emits prf2's own key names; the board speaks of support.
                value = scores.get(_COUNT_ALIASES[metric])
            if isinstance(value, (int, float)):
                entry[metric] = _envelope(value)
            elif metric in {"precision", "f1"}:
                # QA states its own reason when it has one -- release mode
                # explains that a diff-inferred span carries no label. Prefer
                # it over this module's generic wording.
                entry[metric] = _envelope(
                    None, scores.get("precision_reason") or _NO_SPANS_REASON
                )
            else:
                entry[metric] = _envelope(
                    None, f"not reported in this team's {basis} metrics"
                )
        # How many documents this label was actually seen in. Without it a
        # label present in 2 of 15 documents reads exactly like one present in
        # all 15.
        contributing = scores.get("documents_contributing")
        entry["documents_contributing"] = (
            contributing if isinstance(contributing, int) else None
        )
        # A label reported twice under two spellings (PHONE and PHONE_NUMBER)
        # collapses to one row; the first one wins rather than being silently
        # averaged with the second, which would be a different measurement.
        block.setdefault(canonical, entry)
    return block


def _recall_only(block: dict) -> dict:
    """The legacy `per_entity_recall` shape, derived from `per_label`.

    Kept populated so `EntityRecallRows` and the masking preview keep working
    while both shapes are in flight. Derived rather than built separately, so
    the two can never disagree about the same number.
    """
    return {label: entry["recall"] for label, entry in block.items()}


def enrich_from_reports(
    settings: Settings, rows: list[dict], document: RankingDocument
) -> None:
    """Attach model identity and per-label recall from each team's own report.

    The ranking document carries no model name, version, or digest -- only a
    size in MB -- so without this every row would render as a bare team name.
    One report per team is enough: `candidate_version` and `model_info` describe
    the model, not the document. Bounded to one fetch per team and best-effort
    throughout, because the board must still render from the ranking document
    alone if these objects are absent.

    `per_label` is populated only in `local` mode -- release mode infers spans
    from a diff of the masked bytes and so has no label to attribute recall to
    (see pipeline.spec.ts) -- which is why per-entity recall stays empty for a
    release board rather than being reported as zero.
    """
    _merge_evaluation_metrics(settings, rows)

    stem = "pipeline-test-pw" if document.source == "playwright" else "pipeline-test"
    for row in rows:
        team = row["team"]
        report = None
        for document_id in document.document_ids:
            key = (
                f"{settings.results_prefix}/{team}/{document_id}/"
                f"{stem}-{team}-{document.mode}.json"
            )
            try:
                body, _ = objectstore.get_bytes(settings, settings.results_bucket, key)
                report = json.loads(body.decode("utf-8"))
                break
            except Exception:
                continue
        if not isinstance(report, dict):
            continue

        version = report.get("candidate_version")
        if isinstance(version, dict):
            name = str(version.get("model_name") or team)
            revision = str(version.get("model_version") or "").strip()
            digest = version.get("model_digest")
            row["model_name"] = name
            row["model_version"] = revision or None
            row["model_digest"] = digest

            # "unknown" is a literal value the model service reports when
            # MODEL_VERSION isn't set at deploy time, not an absence -- left
            # unhandled it rendered as "name vunknown" with no way to tell
            # which build actually ran. Fall back to a shortened digest.
            display_revision = revision
            if revision in ("", "unknown") and digest:
                display_revision = f"sha256:{str(digest).split(':', 1)[-1][:12]}"
            row["model"] = f"{name} v{display_revision}" if display_revision else name
            row["provenance"] = {
                "service_release": version.get("service_release"),
                "source_sha": version.get("git_sha"),
                "image_digest": version.get("image_digest"),
            }
            row["evaluation"]["api_contract_version"] = version.get(
                "api_contract_version"
            )
            # The ranking document's own model_size_mb comes from
            # model_info.onnx_file_size_mb, which rank_teams.py can only fill
            # in from a masking-repo working copy -- absent in CI, so it is
            # null on every published ranking. The model service now reports
            # its own weights' size on /version instead, which is the one
            # place that can actually see them, and pipeline_test.py stores
            # that response verbatim. Prefer whatever the ranking already had;
            # only fill the gap.
            size = version.get("model_file_size_mb")
            if isinstance(size, (int, float)) and not row["metrics"].get(
                "model_size_mb", {}
            ).get("available"):
                row["metrics"]["model_size_mb"] = _envelope(size)

        info = report.get("model_info")
        if isinstance(info, dict):
            row["model_info"] = info
            parameters = info.get("parameter_count_millions")
            if isinstance(parameters, (int, float)):
                row["metrics"]["parameter_count_millions"] = _envelope(parameters)

        detection = report.get("detection_metrics")
        if isinstance(detection, dict):
            confidence = detection.get("mean_confidence")
            if isinstance(confidence, (int, float)):
                row["metrics"]["mean_confidence"] = _envelope(confidence)
            per_label = detection.get("per_label")
            if isinstance(per_label, dict):
                row["per_label"] = _per_label_block(per_label, basis="detection")
                row["per_entity_recall"] = _recall_only(row["per_label"])


# Metrics that exist only in run_evaluation.py's per-run document, never in the
# cross-team ranking. Three of them back medals the board could not previously
# award; the rest let the comparison table show label-aware numbers beside the
# ranking's own.
EVALUATION_METRICS = (
    "critical_entity_recall",
    "latency_p95_ms",
    "latency_p50_ms",
    "throughput_docs_min",
    "pii_recall",
    "pii_precision",
    "pii_f1",
    "leakage_rate",
    "document_success_rate",
    "contract_pass_rate",
    "api_error_rate",
)


def _merge_evaluation_metrics(settings: Settings, rows: list[dict]) -> None:
    """Fold each team's qa-result document into its ranking row.

    The two documents measure different things and both are worth showing. The
    ranking's overlap metrics come from spans inferred by diffing masked bytes
    against the original, because the public API returns no spans -- a
    deliberately approximate proxy. run_evaluation.py instead measures whether
    each known PII literal survived masking, which needs no span inference at
    all, and is what produces critical_entity_recall, latency and throughput.

    Ranking metrics are never overwritten: the published rank must stay
    explainable by the document that declared it. These are added alongside.

    Best effort per team -- a missing document leaves that row exactly as the
    ranking defined it, rather than failing the board.
    """
    for row in rows:
        key = f"{settings.results_prefix}/{row['team']}/qa-result.json"
        try:
            body, _ = objectstore.get_bytes(settings, settings.results_bucket, key)
            document = json.loads(body.decode("utf-8"))
        except Exception:
            continue
        if not isinstance(document, dict):
            continue
        metrics = document.get("metrics")
        if not isinstance(metrics, dict):
            continue
        for name in EVALUATION_METRICS:
            raw = metrics.get(name)
            if not isinstance(raw, dict):
                continue
            # Fill gaps, never overwrite a real value. The ranking already
            # carries latency_p95_ms as an unavailable placeholder whenever
            # JMeter has not run, so testing mere presence would skip exactly
            # the metric this is here to supply.
            if row["metrics"].get(name, {}).get("available"):
                continue
            value = raw.get("value")
            if raw.get("available") and isinstance(value, (int, float)):
                row["metrics"][name] = _envelope(value)
            else:
                row["metrics"][name] = _envelope(None, raw.get("reason"))
        source = document.get("source")
        if isinstance(source, dict) and not row.get("model_version"):
            name = str(source.get("model_name") or row["team"])
            revision = str(source.get("model_version") or "").strip()
            row["model_name"] = name
            row["model_version"] = revision or None
            row["model"] = f"{name} v{revision}" if revision else name


def _trends(
    documents: list[RankingDocument], selected: RankingDocument, basis: str
) -> dict[str, list[float]]:
    """Score history per team across comparable ranking documents.

    Only documents sharing the selected one's mode, source, and document set are
    included: a team's recall over 15 documents is not a later point on the same
    line as its recall over one, so mixing them would draw a trend that moved for
    no reason but a change of dataset.

    The document set is compared as a *set*, not as the list QA happened to
    serialise. `rank_teams.py` takes `--document-id` as ordered arguments and
    writes them back verbatim, so an operator who listed the same 15 documents
    in a different order would otherwise break every trend line in two.
    """
    wanted = sorted(selected.document_ids)
    series: dict[str, list[float]] = {}
    for document in documents:
        if (
            document.mode != selected.mode
            or document.source != selected.source
            or sorted(document.document_ids) != wanted
        ):
            continue
        for entry in document.entries(basis):
            if not isinstance(entry, dict):
                continue
            team = str(entry.get("team") or "")
            recall = entry.get(f"{basis}_recall")
            if team and isinstance(recall, (int, float)):
                series.setdefault(team, []).append(round(recall * 100, 2))
    return series


def load_board(settings: Settings) -> dict | None:
    """Build the board from the newest applicable ranking document, or None.

    Returning None -- rather than an empty board -- is what lets the caller fall
    back to the `qa-result.schema.json` reader in `results.py`, so publishing
    either document kind works.
    """
    documents = discover(settings)
    selected = select(documents, settings)
    if selected is None:
        return None

    # Before anything else can fail: this is the only chance to preserve these
    # scores, because the next QA run will overwrite the key they came from.
    history.archive(settings, selected.key, selected.observed_at, selected.payload)

    basis = settings.ranking_basis if settings.ranking_basis in BASES else "overlapping"
    rows = rows_from_document(selected, basis)
    if not rows:
        return None

    try:
        enrich_from_reports(settings, rows, selected)
    except objectstore.StorageUnavailable:
        pass
    except Exception:
        # Enrichment is decoration; a failure here must not blank the board.
        log.exception("ranking_enrichment_failed key=%s", selected.key)

    trends = _trends(documents, selected, basis)

    # Ranked rows keep QA's order; excluded teams follow, alphabetically, since
    # QA assigned them no rank at all.
    ranked = sorted(
        (row for row in rows if row["qa_rank"] is not None),
        key=lambda row: row["qa_rank"],
    )
    unranked = sorted(
        (row for row in rows if row["qa_rank"] is None), key=lambda row: row["team"]
    )
    ordered = ranked + unranked

    for index, row in enumerate(ordered):
        score = row["metrics"].get("leaderboard_score", {})
        row["rank"] = row["qa_rank"] if row["qa_rank"] is not None else index + 1
        row["accent"] = ACCENTS[index % len(ACCENTS)]
        row["score"] = round(score["value"], 2) if score.get("available") else None
        # `run_count` counts the same points the sparkline draws. Deriving it
        # from the pre-fallback series instead would report 0 runs for a board
        # whose chart shows one, and the "Iron model" card reads this number.
        scored = trends.get(row["team"]) or (
            [row["score"]] if row.get("score") is not None else []
        )
        row["trend"] = scored
        row["run_count"] = len(scored)
        row.pop("_observed_sort", None)

    return {
        "models": ordered,
        "awards": compute_awards(ordered),
        "skipped": [],
        "source": {
            "bucket": settings.results_bucket,
            "prefix": settings.results_prefix,
            "kind": "cross-team-ranking",
            "key": selected.key,
            "basis": basis,
            "mode": selected.mode,
            "evaluation_source": selected.source,
            "document_ids": selected.document_ids,
            "documents_found": len(ordered),
            "close_threshold": selected.payload.get("close_threshold"),
            "notes": selected.notes,
            "rankings_available": sorted(
                name for name in BASES if selected.entries(name)
            ),
            "candidates_considered": len(documents),
        },
    }


# How long a built board is reused before storage is read again. The board only
# changes when QA publishes, which is minutes to days apart, so a short window
# costs nothing in freshness. It buys a great deal in load: every visitor used to
# trigger one GET per ranking document plus one per team for enrichment, and the
# history archive means the first of those numbers now grows with every QA run
# rather than staying at one.
BOARD_CACHE_SECONDS = 60

_board_cache: dict[Any, tuple[float, dict | None]] = {}


def load_board_cached(settings: Settings) -> dict | None:
    """`load_board` behind a short per-process TTL.

    Keyed on the settings themselves, so a deployment that changes a prefix or
    pins a different document is not served the previous one. `None` is cached
    too -- it is the answer that sends the caller to the `results.py` reader, and
    recomputing it every request would mean paying for a full listing to learn
    nothing.

    Per process, not shared: with more than one worker each keeps its own copy,
    which is fine for a value this cheap to rebuild and this tolerant of being a
    minute stale.
    """
    now = time.monotonic()
    cached = _board_cache.get(settings)
    if cached is not None and now - cached[0] < BOARD_CACHE_SECONDS:
        return cached[1]
    board = load_board(settings)
    _board_cache[settings] = (now, board)
    return board


def reset_board_cache() -> None:
    """Drop every cached board. Used by tests, which must not see each other's."""
    _board_cache.clear()
