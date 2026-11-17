# R2 object key conventions

Three buckets are in use. `source` holds uploads, `destination` holds masked
output and terminal manifests, and `data` holds QA datasets and published
results. The audit `logs` bucket is separate again and is described in
[security and secrets](security-and-secrets.md).

| Bucket | Key | Written by |
|---|---|---|
| `source` | `incoming/<team>/requests/…/<filename>` | the uploader |
| `destination` | `masked/team=<team-id>/request=<request-id>/<filename>` | NiFi |
| `destination` | `manifests/team=<team-id>/request=<request-id>/status.json` | NiFi |
| `data` | `Validation_Datasets/…` | out of band; QA reads it |
| `data` | `results/leaderboard-*.json` | QA's `rank_teams.py`, one cross-team ranking per document set |
| `data` | `results/<team>/<document-id>/<report>.json` | QA's pipeline test, one report per team per document |

This matches section 8 of
[the v1.2 masking API contract](../contracts/masking-api/v1.2/PII_Masking_API_Contract_v1.2.md),
which is the authoritative description of the flow.

NiFi's `ListS3` watches `incoming/<team>/requests/` recursively and derives the
trusted `team_id` from that path; it never accepts a team from file metadata.
It then builds an idempotency key from source bucket, key, and ETag, derives a
deterministic UUID5 request ID from it, and uses that ID in both the masked key
and the manifest key. **The request ID is therefore not chosen by the uploader
and cannot be predicted from the upload alone.** To pair an input with its
output, read the manifest and match its recorded source key; do not attempt to
reconstruct the destination path.

The two `results/` layouts are distinguished by depth: a cross-team ranking sits
directly under the prefix, a per-team report two levels below it. The leaderboard
relies on exactly that to tell them apart, so a ranking document must never be
filed inside a team folder.

Keys must not include patient names, identifiers, credentials, or raw PII.
Preserve the same request ID across the masked and manifest paths.

## Masking a subset of what a caller holds

The `…` in the incoming key may carry an optional segment naming what the
platform does with the files below it:

```text
incoming/<team>/requests/routed/<filename>
```

Files under a routed segment are masked. Everything else is listed and left
alone, so ignoring a set takes no segment of its own -- and a prefix holding
files nobody masks is a place for unmasked PII to accumulate, which is a reason
not to create one.

This is how a caller masks part of what it holds rather than everything, without
a new endpoint or a request parameter. NiFi already routes on the incoming prefix
and derives the trusted team from that same path, so enabling a segment is a
routing change and nothing else: the gateway checks the key with
`source_key.startswith(f"incoming/{team_id}/")` and does not constrain what
follows, so the deeper key needs no code change.

Two rules hold the trust model in place:

- The segment is chosen by **which prefix the uploader wrote to**, never by file
  metadata, a filename, or a header. It is trusted for exactly the reason
  `team_id` is trusted, and for no other reason.
- The segment stays a **fixed vocabulary** the platform agrees in advance. The
  rule above applies to it: a segment may not name a patient, a case, an episode,
  or anything else derived from the document's content.

Name the segment for what the routing does with it, not for what the documents
are. A segment called `letters/` or `labs/` claims the platform knows a document
kind; it does not. Nothing here inspects a document, and the segment is matched
as a string. Choosing *which* documents belong in `routed/` is the caller's job,
done before upload -- for HealthOne NOVA that is its own per-patient extraction.

A segment with no matching route has its uploads listed and then never processed,
which looks like silence rather than a failure -- the same trap as a team missing
from `SUPPORTED_TEAMS`. A typo behaves identically, because `route/` and
`routed/` are two different strings and one of them matches nothing.

`docs/document-subsets.md` records the label policy, plus the throughput and
format limits that decide whether a given subset is practical.

## An earlier revision of this file was wrong

It previously described a single `data` bucket with
`incoming/{team}/{yyyy-mm-dd}/{correlation-id}/{filename}` and
`manifests/{completed|failed}/…`. No such layout is in use. Several other
documents still disagree with each other; `platform/r2/README.md`,
`platform/nifi/README.md`, and the root `README.md` each describe a `masked/`
prefix that includes model name and version. Treat the contract as authoritative
and this file as its summary.

Because the layout has been documented inconsistently, the arena API keeps it
configurable rather than hard-coded, and its run lookup tolerates each
documented variant (see `evaluation/leaderboard_api/runs.py`).
