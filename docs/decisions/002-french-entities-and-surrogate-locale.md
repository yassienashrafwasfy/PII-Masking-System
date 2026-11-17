# ADR 002: French entities (NIR, RPPS) and the surrogate locale

**Status:** Accepted

NIR and RPPS were detected long before they were reportable. The regex engine
had a mod-97-validated NIR rule and a keyword-gated RPPS/ADELI rule, and
Octo's checkpoint carried `B-NIR/I-NIR/B-RPPS/I-RPPS` (ids 20/26/52/58), but
both paths discarded the spans: the engine listed them in
`_UNMAPPED_OUTPUT_LABELS` and `LabelConverter.convert` dropped any label
outside `masked_labels()`. Masking a French document therefore released the
patient's national number and the prescriber's registration number intact.

**They become canonical labels rather than routing-only or aliases.**
`routing_only_labels` would have masked them without QA scoring or an arena
column, and aliasing NIR onto `INSZ_NISS` and RPPS onto `RIZIV_INAMI` would
have masked correctly while labelling a French number as a Belgian one in the
audit record and the PDF legend. Both were rejected in favour of adding them
to `qa-pii-v1` entity_labels, which bumps the schema to version 3 and moves
the six vendored copies of the label list together.

**Regex owns them; the model's own classes stay dormant.** Both join
`Orchestrator._REGEX_EXCLUSIVE` alongside `RIZIV_INAMI`, `INSZ_NISS` and
`BTW_EENHEID`, following the 2026-08-25 clinical-board ruling on model false
positives for identifiers a rule validates outright. NIR carries a checksum
and RPPS only ever appears next to its own keyword, so neither needs the
model's guess. Octo does not have to be retrained for this; its predictions
are dropped in the orchestrator, not the converter.

**One locale per document, not per label.** Everything that varies by
language — Faker locale, month names, gender words and letters, the masked
URL's TLD — moves into a `LocaleProfile` in `app/core/locales.py`, and the
router holds one profile for the document it is masking. It lives in `core`
because both `app/engines/masking_router.py` and `app/core/config.py` read
it, the way both already read the taxonomy from `label_registry`.

The consequence is accepted deliberately: a country-bound identifier follows
the document rather than its own country, so a NIR in an `nl_BE` document is
replaced with an 11-digit Belgian national number and an INSZ in an `fr_FR`
document with a 15-digit French one. Per-label locale overrides would keep
each identifier's shape, at the cost of a router that holds several locales
at once. Revisit this if cross-border documents become common.

**Surrogate values.** `fr_FR.ssn()` returns a real 15-digit NIR whose last
two digits are the mod-97 key of the first thirteen — exactly what the regex
engine's `_nir_ok` validates — so a masked NIR survives a downstream check
the way a masked INSZ already does. RPPS has no Faker provider and no
checksum in the detector, so its digits are redrawn with separators kept,
making the surrogate exactly as valid as the values the engine recognises.

**The locale is detected per document, not configured.** A pseudonym has to
read like the document it replaced a value in, so the locale is a property of
the document. `app/core/locale_detection.py` resolves it from the extracted
text with `lingua-language-detector`, and `redact_document` calls it when no
locale is passed. A deployment-scoped `MASKING_LOCALE` setting was built
first and then removed: it made an instance serving both countries mask both
with one locale, which is the thing this change exists to avoid.

Detection loads three languages: Dutch and French map to a locale, and
English is loaded only so an English note is recognised and rejected rather
than forced into one of them. Without that decoy a detector holding two
languages scored an English report 0.97 as French, past any threshold.
(Superseded in part by [ADR 004](004-english-locale.md): English now maps to
a locale of its own, so it is no longer rejected. The 0.97 measurement is why
it was loaded in the first place and still stands.) Only
the languages this corpus actually contains are loaded, because every loaded
language also becomes a row on the `PDF_LOCALE_BADGE` overlay. Resident cost
is ~35 MB; the 170 MB wheel is a package-size cost, not a runtime one.

A wrong verdict changes every surrogate in the document while the fallback
only keeps the previous behaviour, so two blunt guards send a document to
`nl_BE` instead of guessing: under 40 letters of signal, or top confidence
under 0.70. Real notes score above 0.99; the values worth rejecting are
short identifier stubs scoring 0.85-0.91.

Masking must stay byte-reproducible for NiFi to replay an idempotency key.
`lingua` is deterministic for a fixed version, the version is pinned exactly,
the detector is built once per process, and the sample is a fixed
2000-character prefix. A `lingua` upgrade is therefore a change to masking
output and not a routine bump.

**Organisation surrogates come from the French registers.** Faker has no
generator that produces a plausible national trade name, so each locale
carries curated pools scraped from that country's own open registers. French
mirrors the Dutch design exactly: `scripts/build_french_organization_pool.py`
takes 50 000 names from SIRENE, INSEE's business register, and
`scripts/build_french_medical_organization_pool.py` takes 50 000 from FINESS,
the national register of health and social-care establishments. Both are
published under the Licence Ouverte v2.0, both source archives stay outside
the repository, and each generated pool ships a provenance record naming the
source URL, snapshot date and SHA-256.

Two SIRENE exclusions are load-bearing. Only `denominationUniteLegale` is
read, because a sole trader is registered under a real person's name. Only
rows whose `statutDiffusionUniteLegale` is `O` are read, because INSEE marks
a record `P` when the registrant restricted its publication and a restricted
record must not be shipped in this repository.

The general builder's `_PERSON_LIKE` filter turned out to be a no-op on this
data: it requires Title Case and SIRENE writes every denomination in
capitals, so "JULES LARDIERE" passed a filter written to stop exactly that.
`_is_person_name` redoes the check on the shape SIRENE publishes, matching a
two-or-three-token all-alphabetic name whose first token is a known French
given name. It rejects only a whole name, so "SARL AMBULANCE PHILIPPE
ARDOUIN" stays -- a business that contains a person's name, not a person.
