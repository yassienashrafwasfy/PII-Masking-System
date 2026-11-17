# MLOps Handoff

## What Runs

One public gateway receives raw files on port `8080` and routes them to private
model services. Only the gateway has a published port.

```text
NiFi -> gateway:8080 -> octo-model
                      -> synthify-model
                      -> berts-model
```

The gateway selects a service from the trusted `X-Team-ID` and verifies that
`X-Source-Object-Key` starts with `incoming/<team-id>/`. Model URLs are never
provided by NiFi or exposed to callers.

| Team | Runtime | Accepted types | Status |
| --- | --- | --- | --- |
| Octo | XLM-R plus regex | PDF, text, CSV, JSON | Demo-ready |
| Synthify | GLiNER | PDF, text | Demo-only, QA pending |
| Berts | int8 ONNX, one checkpoint per document language, plus the shared platform regex | PDF, text | Production-wired; staging and capacity approval required |

Every team serves an exported ONNX graph and nothing else: Octo and Synthify
float32 on CPU ONNX Runtime, Berts int8. `X-Model-Format` accepts `onnx` alone
and defaults to it, so a caller normally omits it; the gateway validates the
header after bearer-token authentication and forwards it only to the selected
private service. `ACCELERATOR=cuda` is supported by no team's artifact --
production runs `ACCELERATOR=cpu`, and the ONNX engine refuses `cuda` outright.
Re-exporting a graph means re-obtaining the source checkpoint from outside this
repository and re-deriving `teams/<team>/onnx/manifest.json`; there is no
in-repo export script. See
[ADR 005](decisions/005-onnx-only-serving.md).

For PDF and text, both services use the same platform pipeline: recursive
XY-cut extraction, model detection, deterministic pseudonymization, and output
rendering. The original file type is returned. Image-bearing PDFs are rejected. `PDF_OCR=on` adds one fallback: a page with no extractable characters is read with the shared PP-OCRv6 engine and the pixels it redacts are erased. A page that has a text layer is never OCR'd, and an image on such a page is still rejected.

Production runs with `PDF_OCR=on`. The engine reads its artifacts from a host
directory staged by an operator and mounted read-only at `/models/ocr`, pointed
at by `OCR_MODEL_DIR` — the same way every other model artifact reaches
production, rather than the copy baked into the image. The weights are Git LFS
objects; `fetch_ocr_artifacts` pulls them during the build so the published
model-service image carries real models for QA, which runs against that image
and cannot mount volumes. The gateway image takes none of them. `deploy-gateway.sh` verifies all three artifact digests before pulling
images, because an existence check cannot tell a Git LFS pointer from a model.
`deploy/README.md` carries the staging recipe. With `PDF_OCR=off` nothing is
mounted and nothing is checked.

PDF output rendering is controlled by three model-service env vars, all
optional and defaulted for production pseudonymization:

- `PDF_REDACTION_STYLE` (default `invisible`): `invisible` matches the
  original glyph's font/size/color exactly, so a reader cannot tell a value
  was replaced. `clinical_soft` and `bold_chips` instead draw a visible,
  entity-colored box.
- `PDF_REDACTION_MODE` (default `pseudonymize`): only takes effect when
  `PDF_REDACTION_STYLE` is not `invisible`. It chooses whether that visible
  box shows the entity-type abbreviation (`detect`) or the fitted pseudonym
  (`pseudonymize`). Setting `PDF_REDACTION_MODE=detect` while
  `PDF_REDACTION_STYLE` stays `invisible` fails at startup rather than
  silently doing nothing.
- `PDF_CHIP_REFLOW` (default `on`): lets a replacement that wants more width
  than the value it covers take it from the rest of its line, moving what
  follows to the right. The gap in front of a second column, a table cell or
  a right-aligned field absorbs the shift, so those stay where they are, and a
  line with no room left renders the way it did before. Applies to every
  style, `invisible` included: a visible box capped at whatever its neighbour
  left shrank "AGE 97%" until it truncated to "A...", and an invisible
  pseudonym in the same position was condensed instead -- "De Smet" replaced
  by "Steffi Desmet Polfliet" squeezed to a third of its width. Set
  `PDF_CHIP_REFLOW=off` to keep the source layout untouched and accept both.
  The same switch covers the vertical half: a value wide enough to have been
  a whole line leaves that line with nothing on it, and the lines under it
  come up by one leading to close it. Only lines of the same paragraph move
  -- one left edge, one leading -- so a heading, a footer and the next
  paragraph stay where they are, and the room the paragraph gives up lands in
  the gap before whatever follows it.
- `MODEL_ID_SHAPE_GATE` (default `on`): refuses a model detection whose text
  is the wrong length to be the national identifier it claims. The detector
  has no "not an entity" prediction for a digit run -- the Belgian training
  corpus labels all but one of its 571 bare runs as an identifier -- so it
  picks the class by length and reads nine digits as an eleven-digit RIZIV at
  0.999 whatever the surrounding sentence says. The gate checks length and
  permitted characters only, never the checksum: a validated number is one the
  regex engines already claim, and refusing everything they would not claim
  would take the genuine detections they miss down with it. Counts come from
  this repository's own validators (RIZIV eight or eleven, INSZ eleven, NIR
  fifteen, NHS ten). Regex spans are never judged, and a label with no fixed
  length (NAME, ADDRESS, DATE, PHONE_NUMBER) is never gated. AGE is gated on a
  weaker shape -- it has to carry a digit -- because a prediction over a word
  reaches a surrogate with no number to reformat and is redrawn as letters:
  "Young woman presenting" was released as "Rrovz woman presenting". Applies to
  every team, including the ones that merge their own spans without the
  platform orchestrator. Set `MODEL_ID_SHAPE_GATE=off` to pass every model
  detection through as it stands.

Setting `PDF_REDACTION_MODE` alone, with `PDF_REDACTION_STYLE` left at its
`invisible` default, has no visible effect — the mode only changes what a
*visible* box draws. To compare `detect` vs `pseudonymize` output, set
`PDF_REDACTION_STYLE` to `clinical_soft` or `bold_chips` on both runs.

### Document language

The locale is resolved **per document**, from the document's own extracted
text. There is no setting: a pseudonym has to read like the document it
replaced a value in, and that is a property of the document rather than of the
deployment, so one instance masks a Dutch note with Flemish surrogates, a
French note with French ones and an English note with British ones. For Berts
the same verdict also decides which model reads the document -- see *Per-model
language routing* below. A caller that already knows the language -- or holds a document
too short to detect -- can override detection with the optional
`X-Document-Locale` request header (`nl_BE`, `fr_FR` or `en`); omitting it is
the normal case and detects as before.

- Detection uses `lingua-language-detector`, pinned exactly. Three languages
  are loaded and all three map to a locale: Dutch to `nl_BE`, French to
  `fr_FR` and English to `en`. Resident cost is about 35 MB and roughly
  0.02 s, both one-off per process.
- There is one English locale, not a British and an American one. `lingua`
  reports a single `ENGLISH`, and the two populations overlap far too much for
  a document's language to settle which country wrote it, so the house style
  is British while the identifiers of *both* countries are detected and
  masked: `SSN` and `NPI` alongside `NHS_NUMBER` and `GMC`.
- `PDF_LOCALE_BADGE` stamps one chip per language, with its confidence, on
  a masked PDF's first page, drawn with the same renderer as the entity boxes.
  The selected language is drawn as the selected chip, so the badge says both
  which locale was used and whether the detector chose it or the fallback did.
  **On by default**, in the code and in `compose.yaml`: a reviewer holding a
  masked document cannot otherwise tell a detected locale from a fallback, and
  that is the first question asked of a document masked in the wrong one. Set
  `PDF_LOCALE_BADGE=off` for a release where the overlay would be noise.
- Two guards send a document to the `nl_BE` fallback instead of guessing:
  fewer than 40 letters in the sample (an identifier stub carries no
  signal), or a top confidence below 0.70. A real clinical note scores above
  0.99, so neither guard fires on one.
- The locale decides the Faker locale behind names, addresses and the
  national number, plus month names, gender words and letters (Dutch `V` for
  "vrouw", French `F` for "femme", English `F` for "female"), the masked
  URL's TLD, how the day of the month is written (French `1er`, English
  `1st`), and which organisation-name pool `ORGANIZATION_SURROGATE_POOL`
  selects from. Each pool holds 50 000 names scraped from that country's own
  open registers -- KVK and CIBG for Dutch, SIRENE and FINESS for French, NHS
  ODS and CMS provider-data for English. Pools load on demand, so a
  deployment only pays for the locales it actually sees.
- Not every locale carries every kind of pool. English carries a **medical**
  pool only, and `ORGANIZATION_SURROGATE_POOL=general` against an English
  document falls back to a seeded Faker company name rather than failing the
  document.
- A numeric date is read day-first and, when that cannot make a real date,
  month-first, so `02/17/2026` keeps its shape rather than coming back as an
  ISO date. A written date is read in either order too -- `17 February 2026`
  and `February 17, 2026` both return in the shape the source wrote.
- One locale decides every label in a document, including the country-bound
  identifiers: a NIR in a Dutch document is replaced with a Belgian national
  number and an INSZ in a French one with a French number. That is a
  deliberate ruling in favour of one locale per document over per-label
  locales — it keeps the router single-locale, at the cost of changing an
  identifier's digit count on a cross-border document.
- Three identifiers are the exception, because their surrogate has to satisfy
  the check the detector validated the original with: `SSN`, `NPI` and
  `NHS_NUMBER` are generated by the router rather than by Faker, and follow
  the identifier rather than the document. A masked NHS number still passes
  mod-11 in a Dutch note. The reverse case degrades: an INSZ or NIR inside an
  English document keeps its width but loses its checksum, because
  `en_GB.ssn()` is a National Insurance number and would replace digits with
  letters.

Masking stays byte-reproducible, which NiFi needs to replay an idempotency
key: `lingua` is deterministic for a fixed version, the detector is built
once per process, and the text sample is a fixed 2000-character prefix.
**Treat a `lingua` version bump as a change to masking output, not a routine
dependency update** — it can change which locale a borderline document
resolves to. See
`docs/decisions/002-french-entities-and-surrogate-locale.md` and
`docs/decisions/004-english-locale.md`.

### Per-model language routing

Detection stays locale-blind in the platform engine: every country's rules run
on every document, so a French identifier in a Dutch letter is still found.
Berts narrows that for its *model* half only. It holds one int8 ONNX
checkpoint per language and picks the one matching the document's resolved
locale, falling back to Dutch for anything else:

| Locale | Checkpoint | Reported `X-Model-Name` |
| --- | --- | --- |
| `nl_BE` (default) | `teams/berts/onnx/` | `berts-pii-medroberta-nl-int8` |
| `en` | `teams/berts/onnx-en/` | `berts-pii-bert-en-int8` |
| `fr_FR` | `teams/berts/onnx-fr/` | `berts-pii-camembert-bio-fr-int8` |

Consequences worth knowing before enabling it:

- The model service resolves the locale **before** detection, from the same
  2000-character sample and the same `X-Document-Locale` override the
  surrogate half uses, so exactly one verdict per document drives both. A
  caller pinning the header pins the model too.
- `X-Model-Name`, `X-Model-Version` and `X-Model-Digest` name the checkpoint
  that actually answered, so they differ between a Dutch and a French document
  from one deployment. Anything comparing those headers against a single
  expected value -- a canary, a NiFi output path, a dashboard -- has to pin
  the locale or expect three. `verify-gateway.sh` pins `nl_BE` on its main
  Berts canary and adds one `en` and one `fr_FR` canary.
- Checkpoints load lazily, on the first document in their language, and idle
  unloading drops them all together. Warm RSS therefore depends on the traffic
  mix; measure `BERTS_WARM_RSS_MIB` with all three loaded.
- **`BERTS_LANGUAGE_ROUTING=off` is the rollback.** Every document then goes to
  the Dutch checkpoint, which is exactly what this service did before the
  other two existed, and the promotion gate stops requiring the English and
  French artifacts to be staged. A checkpoint that turns out to mask badly is
  backed out by an operator restarting one service, rather than by waiting for
  a release to remove it from the table. A misspelled value is refused at
  startup and in preflight rather than read as "off".
- Octo and Synthify are untouched: they hold one model for every language and
  ignore the locale their adapter is now handed.

## Local Deployment

1. Run `git lfs pull`.
2. Copy `.env.example` to `.env` and set a long random `SERVICE_TOKEN`.
3. Run `docker compose --parallel 1 build`.
4. Start the gateway and one model profile:

   ```text
   docker compose --profile octo up --detach --no-build
   ```

5. Run the matching smoke test:

   ```text
   $env:COMPOSE_TEAM_ID = "octo"
   uv run python platform/masking-gateway/tests/test_compose_smoke.py
   ```

   Use `synthify` or `berts` in both commands to test those services. Start all
   profiles only when every model must be available:

   ```text
   docker compose --profile octo --profile synthify --profile berts up --detach --no-build
   ```

Use `docker compose down` to stop the stack. The Postman collection in
[`postman/`](../postman/README.md) covers readiness, version, text, PDF, and
Octo JSON requests.

## NiFi Contract

Configure one URL:

```text
MASKING_GATEWAY_BASE_URL=http://<gateway-host>:8080
```

The private NiFi VPS deployment uses the stable bridge endpoint
`http://172.18.0.1:18080` from the NiFi container.

For `POST /v1/mask`, send raw bytes with:

```text
Authorization: Bearer <SERVICE_TOKEN>
X-Team-ID: octo
X-Source-Object-Key: incoming/octo/<request-id>/<filename>
X-Source-ETag: <source-etag>
X-Request-ID: <request-id>
Idempotency-Key: <request-id>
Content-Type: <file MIME type>
Accept: <file MIME type>
X-Model-Format: onnx # optional; Berts accepts onnx only
```

`GET /health`, `GET /ready`, and `GET /version` support probes. Restrict
`/ready` and `/version` at the ingress layer for an internet-facing deployment.

`/health` reports that the gateway process is running. `/ready` reports `503`
until the first masking request loads the selected model, then reports `200`
while its weights remain loaded. `/version` does not load model weights. Demo
services unload after two idle minutes by default. Production sets
`MODEL_IDLE_TIMEOUT_SECONDS=0`, and its deployment/watchdog canaries keep every
enabled team loaded. A restart still creates one cold load before readiness is
restored.

There is no response cache or idempotency store. The shared masking policy is
deterministic for identical document bytes and detected spans.

## Memory Verification

Idle unloading is best-effort: Python releases the model references and runs
garbage collection, but native allocators can retain some process memory. Test
the deployed runtime before QA runs, once for each profile:

```text
docker stats --no-stream octo-model
# Submit one POST /v1/mask request for Octo.
docker stats --no-stream octo-model
# Wait at least 125 seconds, then confirm GET /ready with X-Team-ID: octo returns 503.
docker stats --no-stream octo-model
```

Record the pre-load, loaded, and post-unload values. Submit another request to
confirm that the service reloads successfully. Replace `octo-model` and `octo`
with `synthify-model`/`synthify` or `berts-model`/`berts` for the other profiles.

If the post-unload memory remains too high, restart the active model container
to force the operating system to reclaim all of its process memory:

```text
docker compose restart octo-model
```

## Production Handoff

Before promotion, MLOps must provide a secret manager for `SERVICE_TOKEN`, a
private image registry, immutable model artifact delivery, TLS/ingress policy,
and logs, metrics, alerts, and retention rules that exclude document content.

The production identifier is `berts`; `onnx` is only the value of
`X-Model-Format`. The private production path is:

```text
NiFi/API client -> http://172.18.0.1:18080 -> gateway -> berts-model:8080
```

Enable Berts with `ENABLED_TEAMS=octo,synthify,berts` after staging all three
complete ONNX/tokenizer directories under
`/srv/pii-models/releases/<release-sha>/berts/`: `onnx/`, `onnx-en/` and
`onnx-fr/`. The deployment preflight requires each configured name, revision
and ONNX SHA-256 to agree with the staged files and that checkpoint's manifest;
a missing directory fails preflight rather than the first document written in
that language. The directory is mounted read-only and the service has no public
port. `deploy/README.md` holds the staging commands and the six `.env` keys.

Berts canaries explicitly send `X-Team-ID: berts` and `X-Model-Format: onnx` and
verify `X-Git-SHA`, `X-Image-Digest`, `X-Model-Name`, `X-Model-Version`,
`X-Model-Digest`, and `X-Model-Format`. Because the answering checkpoint
follows the document's language, they also pin `X-Document-Locale`, and two
extra Berts canaries exercise `en` and `fr_FR`. Removing `berts` from `ENABLED_TEAMS`
disables its service and upstream without changing Octo or Synthify.

An 11 GiB VPS without swap does not provide safe blue/green headroom for two
complete three-model slots. Validate real loaded residency and upgrade to at
least 16 GiB before manual deployment; never weaken the memory gate to force a
promotion. NiFi routing for `incoming/berts/...` remains a separate later step
and is not changed by the production API deployment.

Keep model services private. Publish only the gateway. Pin the gateway and
model image digests with the corresponding model digest. Synthify cannot move
beyond demo use until its label mapping and QA evaluation are approved.
