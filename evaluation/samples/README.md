# Console sample documents

Documents the masking console offers as one-click uploads, baked into the
arena-api image so they travel with the deployment. Without them the console
falls back to a plain file dialog, and browsers do not let a page choose which
folder that dialog opens to -- listing a server-side folder is the only way to
get a ready-to-use default.

`ARENA_SAMPLE_DIRECTORY` points at `/app/samples` inside the image. Mount a
host folder over that path, or point the variable elsewhere, to offer different
documents; set it empty to disable the picker entirely.

## What may go here

Synthetic test documents only. These ship inside a published container image,
so nothing here may contain real patient data, credentials, or anything not
already safe to commit to this repository.

`test_brief_01_urologie.pdf` is the repository's own synthetic HealthOne NOVA
fixture, copied from `nova_samples_filled/raw/`. Its "patients" and clinicians
are invented. It is deliberately the *unmasked* variant -- the console's whole
purpose is to send an unmasked document through the pipeline and show the
masked result beside it.

The `<locale>_<specialty>_clinical_letter.pdf` files are synthetic
single-page clinical letters, one per locale/specialty pair across six
locales: `nl-BE` (Dutch/Belgium), `fr-FR` (French/France), `en-GB`
(English/UK), `de-DE` (German/Germany), `es-ES` (Spanish/Spain) and
`ro-RO` (Romanian/Romania). Each covers `gastro` (gastro-enterology),
ophthalmology and pulmonology in the locale's own spelling. All eighteen
are unmasked, like `test_brief_01_urologie.pdf`.

Every locale carries the same three clinical cases, so masking quality is
directly comparable across all six. Only the wrapper changes: the header
furniture, the courtesy title, the national identifier scheme, the
geography, and the decimal comma four of the six locales write where
English writes a point. National identifiers carry valid check digits --
the NHS number is mod-11, the Steuer-ID is ISO 7064 MOD 11,10, the DNI
carries its check letter and the CNP its weighted digit -- so a sample
exercises the checksum path rather than shape matching alone.

`nl-BE` and `fr-FR` predate the others and were produced by a separate
pipeline; `nl-BE` still uses its own slightly different column grid. The
remaining four share one layout. The Romanian set embeds Arial because
Helvetica is WinAnsi-encoded and has no glyph for a-breve, s-comma or
t-comma, which is why those three files are larger than their siblings.

The endpoints that list and read these files, `/api/v1/samples` and
`/api/v1/runs`, sit behind the console's basic-auth router in
`deploy/mlflow-vps/arena-compose.yml`; they are not part of the public
leaderboard.
