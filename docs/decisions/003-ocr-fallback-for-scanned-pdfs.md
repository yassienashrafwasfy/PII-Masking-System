# ADR 003: OCR for scanned and image-bearing PDFs

**Status:** Accepted

`extract_pdf_pages` refused a document the moment any page carried any image.
The rule was correct and blunt: PII inside a raster cannot be found by a text
detector, so refusing beats returning a file that only looks masked. The cost
was that a scanned note — the ordinary output of a hospital fax or a flatbed —
could not be masked at all, and neither could a clean text document carrying a
letterhead logo. Real specialist letters usually carry one, so a corpus of
genuine letters failed almost wholesale where the synthetic fixtures all passed.

**The pixels are destroyed, not covered.** A redaction over an OCR'd region
applies `PDF_REDACT_IMAGE_PIXELS`; the text layer keeps `PDF_REDACT_IMAGE_NONE`.
Measured on a real scan: the rendered ink under a redaction band goes from 3.64%
to 0.00% and the *embedded image stream's own* ink drops with it, while the same
call under `IMAGE_NONE` changes neither number. Drawing a box over a scan makes
a page look masked while `pdfimages` still recovers the original, and no test
that reads the rendered page can tell the two apart — which is why the
regression test asserts against the image stream rather than the page.

**A fallback, decided per page, and not a second reader.** The gate is the
page's own text layer: a page that yields no characters is a picture of text
and is read by OCR; a page that yields any is read from its layer alone.

An earlier cut of this read *both* sources on every page and merged them, which
is a different feature wearing this one's name. It was wrong twice over. It
re-read as a lossy raster transcript the very words the pipeline already had
exactly — a scanner's invisible text layer under a full-page scan is the common
case, and 81.3% recognition against 100% extraction is a downgrade, not a
second opinion. And it quietly widened what the service accepts: a page with a
text layer *and* a logo stopped being refused, so `PDF_OCR=on` began admitting
ordinary letterheads on the strength of an OCR sweep that had read nothing from
them. Turning OCR on now adds scanned pages to what the service accepts and
adds nothing else; an image on a page that kept its text layer keeps the
refusal that predates this ADR entirely.

The dedup rule the merged design needed survives, with one job left rather than
none: `get_pixmap(clip=...)` renders the whole content stream clipped to a
rectangle, and a page whose text layer holds only whitespace is still an OCR
candidate, so an OCR line covered 60% or more by a text block is dropped.

**Two redaction passes, because the flags are per page.**
`apply_redactions(images=..., graphics=..., text=...)` applies to every
annotation on the page, so one call cannot both erase the pixels under an OCR
box and spare an image under a text-layer box. Under the fallback rule the two
provenances meet on one page only where a whitespace-only layer sits over a
scan, but the split is what makes "erase pixels only where OCR read" a property
of the code rather than of the input. What spares a letterhead beside the text
of a scan is separate and simpler: redaction rectangles come from glyph boxes,
so an image no span sits on is never touched.

**The recognised text is injected as an invisible text layer, not carried
alongside.** `inject_text_layer` writes it into the PDF at render mode 3 and
hands the augmented bytes on, so extraction, recursive XY-cut, row grouping,
font resolution, chip sizing and the redaction renderer all work on a scanned
page exactly as on a born-digital one, the `len(text) == len(glyphs)` invariant
holds with no new bookkeeping, and the masked output is searchable rather than
a picture of a masked document.

**No OpenCV, no pyclipper.** The reference DB post-process calls
`findContours`, `minAreaRect` and a polygon offset. Neither dependency is
needed here. DBNet is trained against *shrunk* line polygons, so a thresholded
map is a set of separated, roughly rectangular line kernels; labelling those
needs connected components, not contour tracing. And Clipper's offset of an
axis-aligned rectangle by `d` is exactly that rectangle grown by `d`, which is
one line and exact rather than approximate. The usual cost argument against
pure numpy assumes labelling pixels on the full raster; detection runs on a
resized 0.68 MP map and this labels **runs** — about 400 on a page of clinical
prose. Measured: 11 ms for the whole threshold-to-ordered-boxes path, against a
61 MB wheel that would have sat behind a blocking Trivy `--exit-code 1` gate
where one future CVE blocks every release. What this gives up is rotated
quadrilaterals: boxes are axis-aligned and degrade above roughly four degrees of
skew, which is refused via the median box-fill ratio rather than silently
mis-placed.

**The artifact's own config is asserted, not assumed.** The export script reads
the `inference.yml` shipped in the tarball and refuses to write a manifest
unless its constants match what the engine hardcodes. This earned itself
immediately: **PP-OCRv6 ships DB thresholds of 0.2 / 0.45 / 1.4 where PP-OCRv4
and v5 used 0.3 / 0.6 / 1.5**, and every reimplementation that carries the older
numbers forward detects at the wrong threshold and unclips every box slightly
small. Nothing else would have caught it. The recognition charset is derived
the same way — from the graph's own output width, not from a configured flag —
because a dictionary that disagrees by one entry shifts every decoded character
by one index and produces plausible text rather than an error.

**Determinism is a pinned property, and its limits are stated.** The claim is
bit-identical output for the same bytes on the same host CPU. Threads are
pinned to a config value rather than derived from `os.cpu_count()`, because
ONNX Runtime splits some reductions across threads and the count varies with
the host and its cgroup; recognition pads to a fixed width ladder, so batch
composition cannot change a result the way PaddleOCR's pad-to-batch-max does;
box ordering uses an injective key rather than PaddleOCR's order-dependent
bubble pass. Across CPU generations the claim does not hold, because MLAS
dispatches different kernels for AVX2 and AVX-512 — acceptable for a
single-VPS deployment with a pinned image, and stated because
"byte-reproducible so NiFi can replay an idempotency key" is otherwise read as
stronger than it is.

**Off by default.** `PDF_OCR=off` leaves behaviour bit-identical to before,
image rejection included, and `PDF_OCR=on` leaves it identical on every page
that has a text layer. Switching it on changes which documents the service
accepts, which is a deliberate act per deployment rather than something that
arrives with a release. Every failure is loud: an unreadable image, a page too
skewed, more lines than the cap, a missing artifact — none of them returns a
partially-masked document. `PDF_OCR_MIN_LINE_CONFIDENCE` defaults to `0.0` and
should stay there: a dropped line is a line whose pixels stay on the page and
whose text never reaches the detector, which is a silent leak rather than a
quality trade.

**Where a replacement is written is part of masking.** A PDF extracts in the
order its operators appear, and `apply_redactions` removes the original while
every way of drawing the replacement appends a new content stream — measured
three ways, `insert_text`, `add_redact_annot(text=…)` and `clean_contents`
after either, all identical. So a masked document read on screen was correct
and the same document copied out had a hole in every sentence and its
pseudonyms piled at the foot of the page. `pdf_content_order` splices each
replacement back into position: the base stream is scanned read-only and its
bytes copied verbatim, a box and its text move as one unit, and anything the
scanner does not recognise leaves the page untouched. Rendering is unchanged —
pixel-identical across the three styles on all three samples.

That is the measurement, and the guarantee behind it is narrower, which is
worth stating rather than leaving implied. `invisible` substitutes in place and
paints nothing over anything, so it cannot move a pixel at all. A chip is drawn
a little wider than the value it replaces, so on a line that continues
immediately after one, the chip overhangs the next character or two: appended
last it covered them, and spliced into reading order it is drawn first and they
cover it. The page changes there — only ever inside a box that was already
sitting on those words, and in the direction that makes them legible. It does
not arise on the samples, because a chip there does not reach past the
whitespace around the value it replaces; it does arise on a synthetic fixture
built to collide, and that fixture is what pins it.

This is also where the injected layer earns its keep a second time. It writes
one text object per word, so a scan gets the pseudonym exactly where the value
stood. A born-digital line is reopened instead, and redaction says where: it
leaves a **kern-only `TJ`** between the surviving halves — an array holding a
displacement and no string, written at exactly the offset the value stood at.
Anchoring on that rather than on every boundary between show operators is what
makes the count come out right; a line redacted once has one slot, not one per
word. A value at the end of a line leaves no half to kern against, so the close
of the text object is offered as one further slot — three values on the FR
sample (an age, a wrapped organisation, a clinician's name) had no seam of
their own and piled at the foot of the page until it was. The object is closed
at the chosen slot and restarted at the x the glyphs already sat at, so no font
metrics are needed and an embedded subset font is no obstacle. Only a line
redaction merged into a single `TJ` array has no slot at all, and there the
replacement lands beside the line.

The separator between injected words is written as its own text object, and a
word's box may not start inside the word before it. A space carried on the end
of `"71 "` is removed with `71`, and a box that reaches back over the space in
front of a name takes that one too — `83ans,` and `votre patientCharlotte`,
both from a masked scan.

**A box may not reach into the line below it.** A detection box is unclipped,
and heights are normalised to a cluster's largest box, both so a chip covers a
line's ink completely. Together they made consecutive lines overlap — 51pt
boxes on a 44pt pitch on the FR scan. `apply_redactions` removes every glyph
whose box *intersects* the rectangle, not only those inside it, so masking the
header's organisation deleted the specialty line under it:
"Gastro-entérologie" gone from the masked letter, named by no detector,
reported by nothing, and invisible to every check that compares reading order
rather than content. Boxes are now clamped at the midpoint between the centres
of two lines that overlap both across and down, which gives up only the
unclip's over-growth — the ink of two lines does not overlap, or they would not
have been read as two lines.

Reading order is decided with a row tolerance rather than by raw baseline, in
both the OCR box ordering and the splice. Three columns of one header row have
baselines a point or two apart, and a strict sort by `y` read them
right-to-left: a masked scan copied out as "PATIENT : DATE : RESPONSABLE :"
with a date standing where the clinician belongs.

**Recognition accuracy is now a masking guarantee.** A name the recogniser
misreads is a name the detector never sees, and 81.3% is a floor on the whole
path for scanned pages. Spans read this way are marked `source_layer="ocr"` in
the audit — `MaskSpan.source_layer` already existed, so no contract version
was cut — which is what makes a misread scan distinguishable from a detector
miss after the fact.

## Amendment, 2026-09-04: production enables it, and the artifacts are staged

`PDF_OCR` still defaults to `off` in the code, so a library caller and a bare
container are unchanged. This deployment has decided otherwise: both Compose
templates now default it **on**.

That revises the ruling above rather than merely acting on it, and the reason is
the one the ruling did not anticipate. Promotion never overwrites a live `.env`,
so a fallback of `off` meant every deployment whose `.env` predates this change
would quietly stop OCR-ing scanned pages -- the silent loss the rest of this ADR
is written to avoid. A fallback of `on` fails loudly instead: preflight refuses
to deploy until the OCR artifacts are staged, which is a message rather than a
gap. Set `PDF_OCR=off` to opt out.

Two things were true when this ADR was written and had to be fixed before the
switch could mean anything.

**The artifacts never reached a deployment.** They are Git LFS objects and
`GIT_LFS_SKIP_SMUDGE=1` is global, so the copy that rode into both images was a
130-byte pointer file rather than a model. Two changes fix that from both ends.
`fetch_ocr_artifacts` now pulls and hash-checks the weights during the build,
so the published model-service image carries real models — the QA lane needs
that, because it runs the contract gate against that image and GitLab
`services:` cannot mount volumes. The gateway image takes none of them, since
it never loads OCR. And production no longer depends on the image at all: it reads an
operator-staged host directory mounted read-only at `/models/ocr`
(`OCR_MODEL_DIR`), matching how every other model artifact is provisioned.

**"Every failure is loud … a missing artifact" was only half-true.** The
startup guard asked whether those paths were files, and a pointer is a file, so
`PDF_OCR=on` would have started clean and failed on the first scanned page —
the mid-batch 503 the guard exists to prevent. The three artifact digests
(`OCR_DET_SHA256`, `OCR_REC_SHA256`, `OCR_CHARSET_SHA256`) are now pinned and
verified twice: by `deploy-gateway.sh` before any image is pulled, and again by
`_verify` as the engine loads. The preflight check is the one that matters,
because settings are cached and first read on a request rather than at process
start, and the engine is built on the first page that needs it — so "startup"
was always later than it sounded.
