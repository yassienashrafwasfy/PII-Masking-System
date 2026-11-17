# ADR 004: One English locale, and the identifiers that come with it

**Status:** Accepted

Supersedes in part [ADR 002](002-french-entities-and-surrogate-locale.md),
whose "recognised and rejected" ruling on English no longer holds.

English was detected and then thrown away. `app/core/locale_detection.py`
loaded three languages into `lingua`, but only Dutch and French mapped to a
`LocaleProfile`; English sat in `_DECOY_LANGUAGES`, loaded so an English note
would be *recognised and rejected* rather than forced into French, which a
two-language detector did at 0.97 confidence. The rejection worked, and its
consequence was that an English document was masked as Belgian Dutch.

Measured on an English letter that `lingua` scored `ENGLISH 1.00`:
`482 Lakeshore Drive, Chicago, IL 60601` came back
`Kattyring 98 3778 La Gleize`; `Riverside Regional Medical Center` came back
`Zorgteam Martin en Simone`; `male` came back `non-binair`; and the masked URL
ended `.be`. Two identifiers in that letter — `SSN 123-45-6789` and
`NPI 1023456789` — were claimed by no rule and by no model, and left the
document intact.

## One English, not a British and an American one

An earlier draft of this work carried `en_GB` and `en_US` as separate profiles
with a request header to choose between them. That is rejected.

`lingua` exposes a single `ENGLISH`, so no detector can tell the two dialects
apart, and a dialect would therefore have had to be chosen by a fixed default
plus an explicit override. That default would be wrong for half the documents
and the override would rarely be sent. More to the point, the distinction does
not hold in the population this engine sees: US citizens are treated in
Britain and British citizens in the States, and a document's language says
nothing about which. Two profiles would have doubled the surface — two pools,
two month tables, two date orders, two sets of tests — to encode a fact the
input does not carry.

So there is one locale, `en`. What matters for privacy is not whose spelling
the pseudonyms use but whether both countries' identifiers are found, and that
is what the taxonomy bump below delivers.

**The house style is British.** `faker_locale="en_GB"`, so names, streets and
postcodes read British, and a masked URL ends `.uk`. The cost is stated: a
Chicago address is replaced by a British-shaped one. That is the same trade
ADR 002 already made when it ruled one locale per document — a country-bound
value follows the document, not its own country — applied one level down.

`DEFAULT_LOCALE` stays `nl_BE`, so a deployment that never sees an English
document is unaffected.

## Four labels move in one schema bump

`SSN`, `NPI`, `NHS_NUMBER` and `GMC` become canonical labels in `qa-pii-v1`,
which bumps the schema to version 4 and moves the vendored copies of the label
list with it.

Two of the four were already fully built. `_NHS_CONTEXT_RE`, `_NHS_RE`,
`_nhs_ok` with its real mod-11 check and `_GMC_CONTEXT_RE` all existed, were
tested, and emitted spans that `_UNMAPPED_OUTPUT_LABELS` then dropped before
output — the same state NIR and RPPS were in before ADR 002. Removing two
entries from that set is the whole of their detection work. `SSN` and `NPI`
are new: `_SSN_RE` insists on the 3-2-4 grouping and `_ssn_ok` applies the
allocation rules (area 000, area 666, the 900-999 block, group 00, serial
0000 are never issued), and `_NPI_CONTEXT_RE` is keyword-only because a bare
ten-digit run is already the NHS rule's territory.

All four join `Orchestrator._REGEX_EXCLUSIVE`, following ADR 002's ruling for
NIR and RPPS: each carries a checksum or a keyword gate, so the model's guess
adds only guesswork. No retraining. The models read `id2label` from their own
`config.json` and `label_registry.masked_labels()` is a frozenset, so
appending labels re-indexes nothing.

**Three surrogates are generated rather than drawn from Faker, and are
locale-independent.** `en_GB.ssn()` returns a National Insurance number
(`ZZ 099603 T`), which would replace nine digits with letters and destroy the
field. So `SSN`, `NPI` and `NHS_NUMBER` carry generators in the router: an SSN
inside the allocatable blocks, an NPI whose Luhn check over the `80840`
issuer prefix passes, and an NHS number that satisfies the engine's own
`_nhs_ok`. A masked NHS number that failed the check the detector validated it
with would be a downgrade a reader could spot, which is the same reasoning
that made `fr_FR.ssn()` the right NIR surrogate in ADR 002.

Unlike `INSZ_NISS` and `NIR`, these three do not follow the document's locale.
An NHS number is a mod-11 ten-digit value in a Dutch note as much as in an
English one, so the surrogate follows the identifier. `GMC` redraws its digits
and keeps an associate's `A` prefix, exactly as `RPPS` does.

**The reverse case degrades, narrowly.** An `INSZ_NISS` or `NIR` inside an
English document can no longer come from Faker, for the NINO reason above, so
it falls back to the existing digit redraw: the value keeps its width and
loses its checksum. This affects only a document that crossed a border, and
`LocaleProfile.faker_national_number` records per locale whether Faker can
generate that locale's national number rather than leaving it implicit.

## Dates are read in both orders

English is the first locale where the parser failed outright rather than
merely reading foreign. `_NUMERIC_DATE_FORMATS` was day-first only, so
`02/17/2026` matched nothing, and `_WRITTEN_DATE_RE` was day-month-year only,
so `February 17, 2026` matched nothing either. Both fell through to the
unparsed fallback, which renders an ISO date — a shape the source never wrote,
often longer than the slot it has to fit.

Parsing therefore gains a month-first numeric pass and a `Month D, YYYY`
written pattern, both **locale-independent**, which is the principle
`locales.month_number` already states: parsing accepts every locale's spelling
while rendering follows the document. A Dutch note can quote an American date
as easily as an English one, and which order parses is decided by the value.
The month-first patterns are tried only after every day-first pattern has
failed, so nothing that parses today changes.

Where both orders make a real date — `03/04/2026` — day-first wins, which is
British usage as well as Belgian. Nothing leaks either way and the rendered
shape is the pattern that matched, so the choice moves which surrogate date
lands in the field and nothing a reader could see.

`LocaleProfile.day_ordinal_suffix` becomes a rule rather than a string.
English needs a different suffix for nearly every day (1st, 2nd, 3rd, 11th),
which one string cannot express, so the profile carries a function plus
`ordinal_is_mandatory` — true for French, where `1 janvier` is a misspelling
and the surrogate writes `1er` whatever the source did, and false for English,
where a surrogate carries `3rd` only where the source carried an ordinal.
Dutch and French output is byte-identical under the new fields.

**This changes masked bytes** for any existing document containing a date that
is unparseable today. It is strictly an improvement — those fields currently
come back as ISO dates — but masking output is what NiFi replays an
idempotency key against, so it is a deliberate change and not a routine fix.

## The organisation pool merges two registers

`scripts/build_english_medical_organization_pool.py` builds one
`surrogate_medical_organizations_en.txt` from both countries' open registers:
NHS ODS (the Organisation Data Service directory, ~217,000 active
organisations, no key, Open Government Licence v3.0) and the CMS
provider-data facility listings (hospitals, hospice, dialysis, ambulatory
surgical centres; a US Government work in the public domain). Merged for the
same reason there is one locale: either country's document should be able to
be masked with an organisation that could plausibly exist.

NHS ODS outnumbers the CMS listings roughly nine to one, so each register
contributes at most half the pool and a shortfall on one side is filled from
the other. Without the cap a straight sample of the union would have been
essentially British.

The person-name filter is load-bearing here as it was for SIRENE, and needed
work the Dutch and French filters do not do: ODS writes every name in capitals
and registers a single-practitioner practice under its practitioner
(`DR J SMITH`, `A B SMITH`, `DR A B SMITH & PARTNERS`), so the English filter
strips a leading title and a partnership tail before testing what remains. A
pool whose whole purpose is that it names nobody must not carry somebody.

**Only the medical pool is built.** English has no `general` entry, and a
general request against it falls back to a seeded Faker `.company()` rather
than failing the document — deterministic, and an organisation, which is what
the field has to carry. `organization_pool_for` returns an empty pool for a
kind a locale has no register for, and still raises for a kind no locale
defines at all, because that one is a caller error.

## An English address puts its number first

Detection had a second English-shaped gap, found while masking the sample
letters rather than by reading the rules. `_building_number_spans` anchors on
a postcode and only then looks for the house number, and `carries_postcode`
knew a UK postcode and a Belgian or French one. A US ZIP is five bare digits
after a two-letter state, which matched neither, so a US address line was
never recognised as an address line at all -- and because
`_ADDRESS_LEADING_NUMBER_RE` never ran on one, "482" in
`482 Lakeshore Drive, Chicago, IL 60601` stayed in the released document while
the street, the city and the ZIP around it were masked. Dutch and French write
the number last, which is why the trailing-number branch had always been the
one doing the work.

`_US_ZIP_RE` closes it, and matches the state against the real list of
fifty-plus abbreviations rather than any two capitals: five digits are a year,
a quantity or a reference far more often than a postcode, and a bare
two-letter prefix would have claimed "Ward ER 60601" out of ordinary prose.
The ZIP is now emitted as an address span in its own right too, so a
regex-only deployment -- which is the default `DETECTION_MODE` -- covers a US
address rather than depending on the model to find one.

## Two consequences worth knowing about

**A US telephone number can be masked as an NHS number.** `_NHS_RE` claims any
bare ten-digit run that satisfies mod-11 and does not open with a zero, and a
US number written `598.309.9256` is exactly that. The phone rules cover the
Belgian, Dutch, French and UK shapes, not the American one, so nothing
outbids it. Before this change the span was detected and then dropped, which
means the number left the document unmasked; now it is masked, keeps its
shape, and is recorded under the wrong label. That is the better of the two
failures, and the real fix belongs to the phone rules rather than here.

**A house number next to a phone number merged into one address span — now
fixed.** The runtime already documented this hazard: a model phone span that
swallows the first token of the line below overlaps the address span there,
and `coalesce_spans` merges the two. `_trim_line_overrun` existed for it but
trimmed only a one- or two-character tail, and English puts the house number
first, so a three-digit one defeated the length test. The highlight overlay
showed the result plainly: `ADDR` drawn on a `Tel.` line.

`_trim_line_overrun` now also trims a tail of **any** length when another
span begins exactly where the tail begins and reaches at least as far. That
test is safer than the length one rather than looser: the span that covers
the tail still masks it, so trimming moves which label the value is masked
under and never whether it is masked. The covering span must start on the
next line and carry no break of its own, so it cannot itself be a span the
same pass would trim — without that, two overrunning spans would cover each
other into a trim that left the tail masked by neither.

## Small things that follow from existing rules

**A month-first date loses its comma.** "February 17, 2026" is parsed and
rendered month-first, but `_sanitize` replaces a comma with a space so no
replacement can end a CSV field or a JSON string. The surrogate is therefore
"November 3 2019". The order and the written month -- what make the field read
as English -- both survive, and weakening a rule that keeps every replacement
safe in every output format would be the wrong trade for a comma.

**A gender surrogate may equal the original.** The field holds one of three
values, so a draw returns its own value about a third of the time. That is
left alone deliberately: forcing a different value would tell a reader the
output can never equal the input, which narrows the original from three
possibilities to two.

## `X-Document-Locale` and contract v1.3

One optional request header overrides detection. It exists because detection
cannot help a document with too little signal — under 40 letters, or top
confidence under 0.70, both guards unchanged — and because a caller
integrating this engine sometimes knows the language from its own metadata.

It is published as `contracts/masking-api/v1.3`, copied from v1.2 rather than
edited into it. A v1.2 caller is a valid v1.3 caller: the header is absent,
the language is detected from the document as before, and every response
header keeps its meaning. It is validated post-authentication in the gateway
and re-validated in the model service, the way `X-Model-Format` and
`X-Mask-Labels` already are, and echoed on a 200 only when the caller sent it
— the gateway cannot report a *detected* locale, because detection happens
behind it.

**Called out for review:** this is the second request header that influences
masking output, after `X-Mask-Labels`. It selects no team, service or model,
so it does not cross the trust boundary, and unlike `X-Mask-Labels` it cannot
reduce how much of a document is masked — only which language the pseudonyms
read as. It should still be named explicitly when the contract goes to
MLOps/QA.
