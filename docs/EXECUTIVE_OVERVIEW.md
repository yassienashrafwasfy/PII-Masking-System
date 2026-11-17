# Executive overview

## 1. The problem

Manual redaction of sensitive data from documents is slow, inconsistent
across reviewers, and doesn't scale with document volume. Every document
that waits on a person to redact it is a bottleneck and a point of human
error.

## 2. What it delivers

Sensitive information is stripped out of documents automatically, at the
moment a document arrives, before it ever reaches a person or a downstream
system. The document comes back in its original format — PDF, text, CSV,
or JSON — with names, addresses, contact details, and Belgian regulatory
identifiers (national ID, VAT, healthcare practitioner numbers) replaced
by realistic synthetic values rather than blacked out.

## 3. Competitive quality assurance, built in

Quality isn't a one-time launch checkbox. Three independently built
models compete continuously against one fixed benchmark and are ranked
live on an internal leaderboard. No single model, and no single vendor,
is the permanent answer — the best performer earns the traffic, and a
regression shows up immediately instead of surfacing downstream.

## 4. Time-to-market for adoption

Integration is one interface: a document goes in, a masked document comes
back out, same format. Existing document intake workflows plug in without
re-architecture and without building a separate integration per model.

## 5. Growth trajectory

- **OCR** for scanned and image-based documents.
- **Multilingual detection** beyond the current Belgian Dutch baseline.
- **Medical-domain entity support** for clinical documents.
