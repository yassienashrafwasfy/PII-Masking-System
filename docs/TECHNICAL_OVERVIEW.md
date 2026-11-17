# Technical overview

## 1. Pipeline

Every document follows one fully automated path from intake to delivery: a
file arrives, gets routed to a model, gets scanned for sensitive content,
gets that content replaced, and comes back out in its original format. No
manual review step sits between arrival and output, and no human touches
the document in between.

## 2. Dual-layer detection engine

Detection runs two methods in parallel, each covering what the other
can't:

- A deterministic, checksum-validated layer for anything with a verifiable
  structure — national ID numbers, VAT numbers, healthcare practitioner
  numbers, phone numbers, dates, emails, URLs. These are confirmed
  mathematically, so false positives are near zero.
- A trained model layer for anything that can't be verified that way —
  names, addresses, organizations, ages, gender. These require context,
  not pattern matching.

Where both layers detect the same content, the deterministic layer wins,
because a verified match is more trustworthy than a model's best guess.

## 3. In-house model portfolio, continuously benchmarked

Three models exist, built independently by three separate teams on three
different architectures. None of them is a wrapped third-party API — all
run in-house, so there is no external vendor dependency and no licensing
exposure tied to a single supplier's roadmap.

Quality isn't validated once at launch and left static. All three run
continuously against the same fixed synthetic benchmark and are ranked
live on an internal leaderboard (the "PII Masking Model Arena"), scored on
precision, recall, F1, and p95 latency, with eligibility gates that keep
an underperforming or non-compliant model off the board rather than
silently degrading the fleet. This turns model quality into an ongoing,
visible, competitive process instead of a one-time sign-off.

## 4. Entity coverage

Twelve canonical categories, covering both general-purpose PII and
Belgian-specific regulatory identifiers:

Name, gender, date, organization name, address, age, phone number, email,
URL, national ID number (INSZ/NISS), VAT number (BTW-eenheid), and
healthcare practitioner number (RIZIV/INAMI).

Model-specific label vocabularies (each team's own training taxonomy) are
translated into this one canonical set before anything is masked, so the
output guarantee is identical regardless of which model handled the
document.

## 5. Format fidelity

Supported input types are PDF, plain text, CSV, and JSON. PDF output
preserves the original layout: tables stay tables, multi-column pages
retain reading order, fonts and formatting are matched exactly. The
document isn't flattened to plain text and reconstructed — its structure
is read in and written back out, which removes any downstream
reformatting cost for whatever system consumes the masked output next.

## 6. Localization

General availability today covers Belgian Dutch. Multilingual expansion is
scoped on the roadmap (see section 10).

## 7. Deterministic output

Every replacement is a pure function of the document's own bytes and the
detected entity. The same document processed twice produces byte-for-byte
identical masked output, with no way to reconstruct the original value
from the replacement. This removes reprocessing drift as a source of
inconsistency and keeps every release reproducible.

## 8. Throughput and capacity

Each document is capped at 10 MB. Multiple models can run concurrently
with no shared bottleneck between them — one team's request volume does
not compete with another's for capacity. Within a single model, requests
are processed one at a time, which keeps behavior predictable at the cost
of not parallelizing within one team's queue.

## 9. Integration cost

The external surface is one interface: a document goes in, a masked
document of the same format comes back out. There is no per-team API to
integrate against and no client-supplied parameter that changes what
happens internally — routing is resolved from where the document already
sits in trusted storage, not from anything the caller sends. Onboarding a
new document source means pointing it at one endpoint, not building
per-model integrations.

## 10. Extensible architecture and roadmap

New models, new document formats, and new entity categories are additive:
each plugs into the same routing, evaluation, and output guarantees
without changing how any existing model works. Planned expansion:

- **OCR** — support for scanned/image-only documents, which today are
  rejected outright rather than risking undetected text.
- **Multilingual detection** — coverage beyond the current Belgian Dutch
  baseline.
- **Medical-domain entity support** — clinical-specific categories for
  healthcare documents beyond the general PII taxonomy above.
