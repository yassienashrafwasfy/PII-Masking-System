# PII Masking Model Arena

This directory contains the shared benchmark, the official evaluator, the arena API,
and the React frontend. The frontend has three views: the **leaderboard**
(`#/arena`), which ranks models and awards category medals; the **masking console**
(`#/console`), which pushes a document through the real production pipeline and
shows the original beside the masked result; and the **team console** (`#/teams`),
which does exactly the same thing but lets you choose which team's model masks it.
Keep it in the main monorepo while the API contract and scoring policy are still
evolving together.

## Components

- `config/`: versioned entity definitions, eligibility gates, and score weights.
- `datasets/`: synthetic benchmark generator and generated private/public splits.
- `evaluator/`: sends identical files to every nominated model and logs official runs.
- `leaderboard_api/`: the arena API.
  - `ranking.py` reads qa-automation's cross-team ranking document — the board's
    real source — and preserves the rank order QA published.
  - `results.py` reads per-run QA result documents and derives ranks,
    eligibility, and medals; it also owns the medal table both readers share.
  - `runs.py` drives interactive masking runs and discovers their output.
  - `objectstore.py` is the S3/R2 access layer; `config.py` holds the settings.
- `leaderboard/`: React frontend (`Arena.jsx`, `Console.jsx`, `DocumentPane.jsx`).

## Where the leaderboard data comes from

Two different QA documents share `ARENA_RESULTS_PREFIX` in the `data` bucket, and
they are told apart by key depth, not by content:

| Key | Written by | Read by |
|---|---|---|
| `results/leaderboard-*.json` | `scripts/rank_teams.py` | `ranking.py` — **the board** |
| `results/<team>/<document-id>/pipeline-test-pw-<team>-<mode>.json` | `pipeline.spec.ts` | `ranking.py`, for model identity |
| `results/<anything-else>.json` | not currently published | `results.py` |

### The cross-team ranking document

This is what QA actually publishes and what the leaderboard shows. One document
covers a whole document set and holds two independent rankings — `ranking_overlap`
and `ranking_exact` — of every team that could be compared. `ARENA_RANKING_BASIS`
picks which one drives the board; it defaults to `overlapping`, QA's headline
number. Overlap counts a detected span that found the entity but got its
boundaries wrong; exact does not, so the gap between the two is the
boundary-precision story, and both are shown side by side in the comparison table.

**The published rank is read, never recomputed.** `rank_teams.py` does not sort by
F1: it buckets F1 at `close_threshold` (0.03), treats teams inside one bucket as
equal on detection quality, and awards the place on lower p95 latency, then on
smaller model. Re-sorting by F1 here would silently reorder any tied pair, so each
row carries QA's own `rank` and its `resolved_by` explanation, and the table marks
a place decided by tiebreak.

A team listed in `excluded` was not beaten — `rank_teams.py` drops any team missing
a report for *any* requested document, so no team is ever compared on a partial
document set. Those teams appear unranked with QA's reason attached.

Which document is shown, when the prefix holds several: `ARENA_LEADERBOARD_KEY`
pins one exactly; otherwise `ARENA_LEADERBOARD_MODE` (default `release`) filters
out `local` documents, which come from a developer running the suite against
locally served models and must never displace a real evaluation just by being
newer. Among the survivors, more documents scored wins, then recency. Comparable
documents — same mode, source, and document set, the last compared as a set so a
reordered `--document-id` list is not mistaken for a different benchmark — supply
the trend line.

### Why run history has to be archived

`rank_teams.py` names its published document after the document count and the mode
alone, and R2 has no object versioning, so **every QA run overwrites the previous
one's scores**. Left alone the prefix holds exactly one document per document set,
every trend line is a single point, and the "Iron model" card reads "1 run tracked"
however often QA has run.

`history.py` is the arena's half of the fix: each board it builds, it copies the
selected document under a content-addressed key in `ARENA_HISTORY_PREFIX`
(`results/history` by default), so past runs survive the next upload. Enable it
with `ARENA_ARCHIVE_RANKINGS=true`; it is the only place the arena writes to the
results bucket, and a credential without `PutObject` there makes it a logged no-op
rather than an error. The complementary change in qa-automation publishes a
timestamped copy of its own. Documents are deduplicated by a fingerprint over
their scores, so one run counts once however many keys hold it.

Boards are cached in-process for 60 seconds (`ranking.load_board_cached`), because
the archive means the number of objects read per request grows with every QA run.

The ranking document carries no model name or version, only a size in MB, so one
per-team report per team is read to fill those in. That is decoration: the board
renders from the ranking document alone if those objects are absent. Per-entity
recall comes from the same place and exists only for `local` runs, because release
mode infers spans from a diff of the masked bytes and has no label to attribute
recall to.

### The per-run result document

`results.py` reads documents following qa-automation's
`schemas/qa-result.schema.json` v1.0, keeps the newest per team, applies the gates
in `config/thresholds.yaml`, and ranks by `metrics.leaderboard_score`. QA defines
this schema but does not currently publish it, so this reader runs only when the
prefix holds no ranking document.

Two schema facts shape the UI, and the ranking reader adopts both. Every metric is
an `{available, value, reason}` envelope, so "unavailable, and here is why" is a
normal display state rather than a zero — a ranking document's `null`
`p95_latency_ms` becomes an envelope carrying that document's own note about
JMeter not having run. And `entity_precision`/`entity_recall`/`entity_f1` are
permanently unavailable, because the public masking API returns masked bytes
without entity spans; the precision shown for that source is `pii_precision`,
which QA derives from the non-PII preservation rate.

### Fallbacks

`ARENA_MOCK_LEADERBOARD` short-circuits everything above and defaults to **false**,
because `ranking.py` now reads what QA really publishes. Setting it true hides a
working board behind randomized preview data. With it off and the prefix empty,
the API falls back to MLflow when
`MLFLOW_TRACKING_URI` is set, and to built-in demo data otherwise. That keeps the
interface testable without production credentials, and the frontend labels demo
data explicitly. Browser code never receives storage or MLflow credentials.

## The two consoles

Both are the same `Console.jsx`, mounted twice with different props, so the flow,
the poller, the stepper and the document viewer cannot drift apart:

| | `#/console` — masking console | `#/teams` — team console |
|---|---|---|
| Teams offered | one: QA's current rank 1 | every team in `ARENA_TEAMS` |
| Label shown | always **Cegedim Egypte Model** | each team's own display name |
| Picker | fixed chip | radiogroup |

The masking console's team is not configured. It comes from the `champion` field
of `GET /api/v1/leaderboard`, which is the first eligible row — QA's rank 1, the
same row the podium crowns — so a change of winner moves the console without an
operator editing anything. Only the label is constant: the upload still goes to
`incoming/<winning-team>/requests/…`, and the page's own path preview says so. If
the board cannot be read at all, `ARENA_CHAMPION_FALLBACK_TEAM` decides.

`ARENA_TEAMS` is not decoration. `runs.py` validates the submitted team against
it, so a team offered in the picker but missing from that list would fail only at
submit time. A team that is in the list but that NiFi does not route, or whose
model service is not running, produces a run that times out after
`ARENA_RUN_TIMEOUT_SECONDS` — keep the list matched to the teams that are actually
live in the gateway's `<TEAM>_MODEL_URL` settings.

## How the masking console works

The console does not mask anything itself and never calls the gateway. Per
`contracts/masking-api/v1.2` the masking API performs no object-storage I/O, and
NiFi owns the trusted routing decision, the request ID, and the destination key. So
the console uploads to the source prefix and then watches for what NiFi produces:

```
POST /api/v1/runs        -> PUT source/incoming/<team>/requests/<run-id>/<file>
NiFi                     -> derives team from the key, calls the gateway, then
                            writes to the destination bucket:
                              masked/team=<team>/request=<uuid5>/<file>
                              manifests/team=<team>/request=<uuid5>/status.json
GET  /api/v1/runs/{id}   -> finds the manifest whose source key is ours,
                            reads the masked key out of it
GET  .../content/{side}  -> streams the original and masked bytes
```

Three buckets are involved: uploads land in `source`, NiFi writes to
`destination`, and the leaderboard reads QA results from `data`. The upload key
layout is configurable through `ARENA_INCOMING_KEY_TEMPLATE` because NiFi's watch
configuration, not this repository, decides what is correct.

NiFi derives its request ID as a UUID5 over source bucket, key, and ETag with a
namespace this repository does not define, so the destination folder **cannot** be
predicted from the upload. The run is therefore associated by discovery, matching on
the source key recorded inside the manifest. The repository documents five mutually
inconsistent key layouts and two manifest vocabularies, so the lookup tolerates
each variant it may encounter, and falls back to matching the masked object directly
when no manifest is visible.

A run that produces nothing within `ARENA_RUN_TIMEOUT_SECONDS` is reported as
`timeout`, which in practice means NiFi is not running or is not watching the
configured source prefix.

## Local quick start

```bash
cd evaluation
cp .env.example .env          # then fill in the ARENA_S3_* values
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python datasets/generate_synthetic.py --output datasets/generated --seed 20260803
set -a && . ./.env && set +a
uvicorn leaderboard_api.main:app --reload --port 8099
```

In another terminal:

```bash
cd evaluation/leaderboard
npm ci
npm run dev            # http://127.0.0.1:5173, proxying /api to port 8099
```

The dev proxy targets port **8099**, not 8080. Port 8080 is already taken in this
project by the local masking gateway (`compose.yaml`), and it is also the default
port for Jenkins and Tomcat. Pointing the proxy at an occupied port makes the
console fail with whatever that service replies — typically a `403` or `401` that
looks like an arena bug but is not. Override the target when you need to:

```bash
ARENA_API_PORT=9123 npm run dev
ARENA_API_TARGET=http://127.0.0.1:9123 npm run dev
```

Or run both in containers, which is also how the nginx `/api/` proxy is exercised:

```bash
cd evaluation
docker compose up -d --build      # UI on http://127.0.0.1:8088
```

Leave the `ARENA_S3_*` values unset to browse the leaderboard with demo data. The
console then reports itself unavailable rather than failing at request time.

## Tests

```bash
PYTHONPATH=evaluation python -m pytest -q evaluation/tests
```

`tests/fake_storage.py` provides an in-memory stand-in for the S3 subset the API
uses, plus builders for all three QA documents — the cross-team ranking, the
per-team report, and the per-run result — so the ranking, medal, and run-lifecycle
tests need no network and no credentials. The ranking fixture reproduces a real
15-document run, including the near-tie that QA resolves on model size.

## Official evaluation

```bash
python -m evaluator.run_benchmark \
  --team-id octo \
  --model-name octo-pii-masker \
  --model-version 17 \
  --endpoint https://gateway.example.com/v1/mask \
  --dataset datasets/generated/private/manifest.jsonl
```

## Troubleshooting

**"The masking console is unavailable" with a 401, 403, or 407.** The arena API
returns only `400`, `404`, `409`, `502`, and `503`, so those statuses never come
from it. Something answered before it did. The usual cause is the dev proxy
pointing at a port another service owns — check the URL in the error message and
confirm with `curl -i` against the same URL. `curl -sI http://127.0.0.1:8080/`
will show, for instance, an `X-Jenkins` header if Jenkins holds that port.

**"Object storage is not configured".** The API is running but has no
`ARENA_S3_*` values. Export the `.env` into the shell that starts uvicorn; a
`.env` file alone is not read by the process.

**A run stays in `processing` until it times out.** The arena uploaded to the
source prefix but nothing produced a masked object or a manifest. Confirm NiFi is
running and watching `ARENA_INCOMING_PREFIX` in `ARENA_SOURCE_BUCKET`.

## Handling of unmasked content

The console uploads to the shared source bucket and renders the uploaded original in
the browser, so it displays unmasked content by design. It has no access control,
which is why the supplied Compose deployment binds the UI to `127.0.0.1`. Use only
synthetic or already-approved documents. Never commit real customer or patient data.
