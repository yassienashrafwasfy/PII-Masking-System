# Masking a subset of a record

A caller who wants only part of what it holds masked -- one patient's letters,
say, rather than everything in a centre -- does not need a new endpoint or a
request parameter. The platform masks whatever it finds under a routed prefix, so
a subset is a routing decision:

```text
incoming/<team>/requests/routed/<filename>
```

Route the segments you want; anything else is left on the dead-letter
relationship. Ignoring a set needs no segment of its own, so do not create one:
a prefix full of files nobody masks is just unmasked PII sitting in the bucket.

Note where the choosing happens. This platform does not select documents: it has
no notion of a patient, a centre, or a document kind, and it masks every file it
is pointed at. Deciding *which* files those are belongs to whatever writes into
the bucket -- for HealthOne NOVA, its own per-patient or per-centre extraction.
Staging the wanted files under a routed prefix is the whole mechanism.

`r2-object-key-conventions.md` defines the convention and the two rules that keep
it trustworthy; `platform/nifi/configuration/team-api-routing.properties.example`
carries the operational note. This file records what decides whether a given
subset is practical, so a caller can size a test before running it.

## Masking only some labels

Send `X-Mask-Labels` and only those labels are masked; every other detected
label is left readable. That is what an agent reading letters usually wants --
the clinical content stays usable while the people in it do not.

```text
X-Mask-Labels: NAME,ORGANIZATION_NAME
```

Omit the header and the full taxonomy is masked, which is the unchanged
behaviour and the only safe default. Comma-separated, case-insensitive, and
checked post-authentication against `selectable_labels` from `GET /version`. An
unknown label is a `400`, never a quietly narrowed mask: a typo must not release
a document with less masked than the caller believed.

Nothing is configured and nothing is deployed to use this. It is decided per
request, so one deployment serves full and partial masking at the same time.

**A response produced under this header is not a fully masked document.** Every
label outside the set is left exactly as written -- real dates, real national
identifiers, real phone numbers. It goes to the same `masked/` destination as a
fully-masked file, so a consumer cannot tell them apart from the path alone.
Whoever sends the header owns that decision, and these outputs need the same
handling and retention as unmasked ones.

The deployment canary and the per-minute watchdog send no such header, so they
keep asserting a synthetic address is masked. That is why absent has to mean
everything rather than nothing.

**Evaluation cannot score a run made with this header.** `pii_recall`
denominates on the fixture's ground truth, not on the labels the caller asked
for, and the eligibility gate gates `PHONE_NUMBER`, `INSZ_NISS` and `DATE`
recall at 0.98. A names-only run fails that gate outright, and nothing in the
evaluation stack can tell "not requested" from "missed it". Score models without
the header.

## Throughput

One document per HTTP call, and one in-flight call per team. A second concurrent
call is refused immediately with `429` and `Retry-After: 5` rather than queued
(`platform/masking-gateway/src/gateway.py:220-221`, a non-blocking semaphore
acquire). NiFi is what paces a batch: it holds to one request per team and
retries `429` up to three times.

Measured on this deployment, Octo, warm model, eight two- and three-page
synthetic letters of 3-76 KB, detection plus rendering:

| | |
|---|---|
| Per letter | 0.53-1.85 s, averaging 1.26 s |
| Per team | **~48 letters per minute**, serialized |
| Cold start | add one model load on the first call after a restart |

So a few hundred letters is minutes, and a few thousand is under an hour. Two
teams processing different subsets do not contend: the semaphore is per team.

The figure collapses for large documents, because inference is slice-by-slice and
scales with body size rather than page count. `app/core/config.py:88-94` records
**0.628 seconds per KiB** and a full 10 MiB body at **107 minutes**, 2.4 GiB peak
RSS. Clinical letters sit at the other end of that range, which is why the
per-letter number above is the one to plan against. Size a subset by total bytes,
not by file count.

`MAX_CONCURRENT_REQUESTS_PER_TEAM` defaults to 1 and raising it is a capacity
decision needing staging approval, not a config tweak: the model service holds a
process-wide lock across detection and rendering
(`platform/model-service/src/service.py:206`), so a second admitted request waits
there instead of being refused. Note also that `max_concurrent_masks` in
`app/core/config.py:86` is loaded and never read -- it is not a live throttle, and
citing it will mislead.

## What is accepted

| | |
|---|---|
| Formats | PDF with a text layer, and UTF-8 text |
| Size | 10 MiB per file, `413` above it |
| Octo also | CSV and JSON |
| Synthify, Berts | PDF and text only |

The gateway advertises all four media types for every team
(`gateway.py:15-17`), but Synthify and Berts reject CSV and JSON at the model
service. That surfaces as `415 The model could not mask the document`, one layer
later than a caller might expect.

Read timeout is 6,600 s end to end, so a slow document is slow rather than failed.

## Text the pipeline never reads: refused, or stripped

Extraction reads the page content stream. A PDF can carry text in four other
places, and none of them ever reached the detector -- so a document masked with
a patient's name in any of them left the pipeline still holding it, fully
extractable:

| Channel | Verdict | Message |
| --- | --- | --- |
| Annotation contents or author | refused | `PDF annotation content cannot be masked safely` |
| AcroForm field value | refused | `PDF form field content cannot be masked safely` |
| Embedded file attachment | refused | `PDF embedded files cannot be masked safely` |
| Title / Author / Subject / Keywords | **stripped**, not refused | -- |

The three refusals follow the image ruling above: a document this pipeline
cannot fully mask must not be released looking as though it had been. They test
emptiness rather than presence, so an unfilled form field and a highlight with
nothing written on it do not refuse anything.

Metadata is stripped instead, because it holds no layout and nothing downstream
reads it, so clearing it costs the document nothing. Refusing on it would have
cost a great deal: 23 of this repository's own PDFs carry a non-empty Title --
every file in `evaluation/samples/` and all twelve generated benchmark
documents -- and every one of those values is a placeholder (`(anonymous)`,
`Synthetic PII benchmark`) holding no PII whatsoever. The XMP stream goes with
the information dictionary, since it is a second copy of the same fields.
Producer, creator and the timestamps are left alone: they name the tool, and an
operator tracing a release needs them.

## An embedded image: refused, or read with OCR

Which of the two depends on `PDF_OCR`, and the default is still refusal.

**With `PDF_OCR=off` (the default)**, `app/services/pdf_extraction.py`:

```python
if ocr is None and any(page.get_images(full=True) for page in document):
    raise ValueError("PDF image content cannot be masked safely")
```

`any(...)` spans **every page**. One image anywhere -- a hospital letterhead
logo on page 1 -- rejects the entire file with `422`, even though the text
layer is complete and every other page is clean. Verified by adding a single
8x8 pixel image to `evaluation/samples/test_brief_01_urologie.pdf`: the
unmodified file is accepted, the same file with that one image is refused while
its 1,192 characters of page-1 text still extract perfectly.

Real specialist letters usually carry a logo, so a corpus of genuine letters can
fail almost wholesale where the synthetic fixtures in `evaluation/samples/` all
pass -- those are logo-free. Run three real documents through before assembling
a test set.

The rule is sound in isolation: text living inside an image cannot be found by a
text detector, so refusing beats returning a document that only looks masked.
What was wrong is the granularity -- and that the reason does not reach the
caller, because `pdf_extraction.py` catches broadly and rewrites both this
message and "encrypted PDFs are not supported" into `invalid or unsupported PDF
content`. Expect to reproduce a rejection locally to learn why it happened.
(`OcrError` is the one exception: it is re-raised past that handler, because a
missing model artifact is not the caller's document being wrong.)

**With `PDF_OCR=on`**, a page that yields **no characters at all** is read by
OCR instead of refused. That gate is the whole of the feature's scope: OCR is a
fallback for a page that is a picture of text, not a second reader running
alongside the text layer. A page with a text layer keeps it, and an image on
such a page keeps the refusal above -- so switching OCR on widens what the
service accepts to scans, and to nothing else.

On a page that does fall back, the image blocks are rasterised at a capped
resolution, run through PP-OCRv6_small under ONNX Runtime, and written back as
an invisible text layer. Everything downstream -- extraction, reading order,
run grouping, font resolution, chip sizing, the redaction renderer -- then
treats the scan as an ordinary PDF, and the masked output is searchable rather
than a picture of a masked document.

A masked PDF also extracts in reading order: each replacement is spliced back
into the page's content stream where it was drawn, rather than appended after
everything else. Precision follows the source — a scan gets the pseudonym in
the exact word position, a born-digital line whose text is one drawing
operation gets it beside that line, and a page drawn as a single text object
gets it beside its own row rather than at the foot of the page.

Three things worth knowing before switching it on:

* **The pixels are destroyed, not covered.** A redaction over an OCR'd region
  applies `PDF_REDACT_IMAGE_PIXELS`, so the original leaves the image stream.
  Drawing a box on top would leave it recoverable with any PDF tool. The text
  layer keeps `PDF_REDACT_IMAGE_NONE`, and the two run as separate passes
  because `apply_redactions` takes its flags per page and not per annotation.
  Redaction rectangles come from glyph boxes, so a letterhead beside the text
  of a scan comes through untouched.
* **Recognition accuracy becomes a masking guarantee.** A name the recogniser
  misreads is a name the detector never sees. Spans read this way are marked
  `source_layer="ocr"` in the audit, which is what makes a misread scan
  distinguishable from a detector miss after the fact.
* **A page OCR cannot read still refuses the document.** An image that comes
  back empty, or a page too skewed for axis-aligned boxes, raises rather than
  returning something half-masked.

## Lab results are untested

The letters half of this rests on real coverage: every fixture in
`evaluation/samples/`, both PDF verification corpora, and the bulk of each team's
sample data are letters, sharing one HealthOne NOVA template that opens with the
literal token `BRIEF`.

The lab-results half has **no fixture anywhere in this repository**. Searching
`uitslag`, `labo`, `laboratorium`, and `verslag` across `evaluation/`, `teams/`,
and `docs/` matches only tokenizer vocabulary files. Recall on a lab table is
therefore unmeasured, and nobody should quote a number for it. Two or three
synthetic lab results would close that gap.

A lab table inside a PDF also takes a different path than a caller might assume.
Markdown table extraction is deliberately disabled on the shared pipeline
(`pdf_extraction.py:640-647`): its pipes and separator rows are generated text, so
a span found in that representation cannot be rendered back into the original
cell geometry. A borderless grid *is* recognised geometrically -- `MAX_CELL_WORDS
= 3`, `MIN_TABLE_ROWS = 3`, with a comment naming analyte names, numbers, units
and reference ranges -- but that verdict only suppresses a vertical cut during
reading-order extraction and is then discarded. Nothing downstream knows a page
held a table, and the structured-field path
(`masking_engine/runtime.py:117-122`) runs for CSV and JSON only, never for PDF.

## Failure behaviour across a batch

Each file is independent. One rejection writes a `FAILED` manifest and the rest
of the subset continues.

Re-uploading identical bytes to the same key is deduplicated rather than
duplicated: NiFi builds the idempotency key from source bucket, key, and ETag,
and derives the request ID from it, so the same file resolves to the same request
and overwrites its own output. Renaming the file makes it a new request.

Pair an input to its output through the manifest, never by reconstructing the
destination path -- the request ID is a UUID5 the uploader cannot predict, which
`r2-object-key-conventions.md` explains in full.
