# Public benchmark results

How the four teams do on three open de-identification benchmarks, run on 2026-11-21 with
the detector each team ships, on CPU. The harness, label maps and definitions are in
[`evaluation/public_benchmarks/`](../evaluation/public_benchmarks/README.md); this page is
the results and how to read them. The ranked, sortable version of these tables is the
[live leaderboard](https://yassienashrafwasfy.github.io/PII-Masking-System/).

<!-- benchmark-winner:start -->
## Winner: mDeBERTa

**mDeBERTa** has the best mean F1 across the five benchmarks (0.653, 95% interval 0.638-0.666) and comes first in 100% of document resamples: a **clear** result.

| Rank | Team | Mean F1 (95% interval) | Mean recall | Comes first |
| ---: | --- | --- | ---: | ---: |
| 1 | mDeBERTa | 0.653 (0.638-0.666) | 0.597 | 100% |
| 2 | Octo | 0.625 (0.612-0.638) | 0.606 | 0% |
| 3 | Synthify | 0.613 (0.600-0.623) | 0.588 | 0% |
| 4 | Berts | 0.586 (0.571-0.600) | 0.555 | 0% |

Recall alone favours **Octo** (0.606 against 0.597): it masks slightly more of the sensitive spans but also over-masks more, which F1 charges for.

**Best for each benchmark**, among the teams built for that language:

| Benchmark | Best | F1 | Result |
| --- | --- | ---: | --- |
| MEDDOCAN (Spanish) | mDeBERTa | 0.793 | clear |
| MultiGraSCCo (German) | mDeBERTa | 0.643 | clear |
| MultiGraSCCo (English) | Berts | 0.730 | narrow, Synthify close behind |
| MultiGraSCCo (French) | Berts | 0.693 | clear |
| MedDeID (Dutch) | Berts | 0.610 | clear |

How the winner is calculated: mean type-agnostic F1 over all five benchmarks (every team is scored on every language), with a paired bootstrap over documents for the interval and for how often each team comes first; a win is *clear* at 95% or more. Per benchmark, only teams built for the language compete (Berts: en, fr, nl; Octo: nl). Computed by `evaluation/public_benchmarks/report.py` from the results file, not edited by hand. Zero-shot results with this project's own scorer; they do not show how a team does on your documents.
<!-- benchmark-winner:end -->

## Setup

* **Detectors:** each team's real adapter through the shared platform pipeline on the
  CPU ONNX path, with the environment its `compose.yaml` service sets. No locale was
  passed: the document language was detected, as for a gateway request. Both mDeBERTa
  rows go through the platform; "checkpoint only" sets `DETECTION_MODE=model`, which
  removes the platform's regex and arbitration layers and leaves the model's own spans.
* **Hardware:** a Kaggle CPU session, 4 vCPU, no accelerator, one session per
  configuration. The GPUs were deliberately not used: the platform serves ONNX on CPU
  (ADR 005), and these are the numbers that path produces. `s/doc` is wall time per
  document on that session, covering the platform's text extraction, language detection and span detection (masking and rendering of the output file are not included).
* **Commits:** predictions came from `c4b890f` (`run.json` in each kernel records it);
  the scorer is `fe6c82d`, whose label maps differ from `c4b890f` in two ways that change
  no type-agnostic number (see "Scoring changes" below). The calculated numbers, with
  intervals, are in `evaluation/public_benchmarks/results/2026-11-21.json`.
* **Data (all CC BY 4.0, test splits, never committed here):**
  MEDDOCAN `meddocan.zip` sha256 `d0e4708b…`; MultiGraSCCo v2 `*_PHI.json` annotations
  `b77cff11…`; MedDeID Dutch synthetic `test.jsonl` `f1fc1a04…` (Hub revision `45c601bc`).
  `fetch_data.py` pins and verifies all three.
* **Training overlap:** none of these teams trained on these benchmarks' text. The
  mDeBERTa checkpoint was fine-tuned on 11,007 synthetic documents. Every number here is
  zero-shot and out of domain.

## Metrics

* **Recall (merged)** is the headline: did a prediction cover the sensitive span, whatever
  label it carries? Spans separated only by non-alphanumeric characters are merged on
  both sides and matched exactly (MEDDOCAN sub-track 2B). The bracket is a 95% bootstrap
  interval over documents. **Precision** and **F1** use the same matching.
* **Char recall** is the share of alphanumeric gold characters a prediction covers: the
  closest number to "how much of the identifier stays readable".
* **Typed F1** is strict: exact offsets and a compatible coarse category. It is scored
  over the gold spans some team has a label for; a profession, a title or a kinship word
  stays in the headline recall (an unmasked profession is still a leak) but is not
  charged as a typed miss.
* Clinical labels (diagnosis, medication, ...) are not scored: none of these benchmarks
  annotates clinical content as PHI.

## Results

**† marks a benchmark outside the team's declared languages.** Octo is declared
Belgian-Dutch. Berts has nl, en and fr checkpoints and falls back to the Dutch one for
any other language, so Spanish and German are out of scope for it. mDeBERTa covers nl,
fr, de, es and en. Synthify declares no language scope. Do not rank a team on a † row.

### MEDDOCAN (es, 250 docs, 5,661 spans)

| Team | Recall (merged) [95% CI] | Precision | F1 | Char recall | Typed F1 | s/doc |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Octo † | **0.713** [0.700-0.724] | 0.713 | 0.713 | 0.882 | 0.612 | 3.0 |
| Synthify | **0.568** [0.555-0.580] | 0.674 | 0.616 | 0.728 | 0.535 | 7.1 |
| Berts † | **0.359** [0.348-0.369] | 0.529 | 0.428 | 0.582 | 0.307 | 2.8 |
| mDeBERTa (platform pipeline) | **0.764** [0.749-0.778] | 0.824 | 0.793 | 0.870 | 0.693 | 5.9 |
| mDeBERTa (checkpoint only) | **0.740** [0.725-0.753] | 0.802 | 0.770 | 0.860 | 0.670 | 4.8 |

### MultiGraSCCo v2 German (63 docs, 1,437 spans)

| Team | Recall (merged) [95% CI] | Precision | F1 | Char recall | Typed F1 | s/doc |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Octo † | **0.569** [0.529-0.605] | 0.613 | 0.590 | 0.860 | 0.622 | 5.1 |
| Synthify | **0.568** [0.529-0.610] | 0.555 | 0.561 | 0.760 | 0.582 | 12.9 |
| Berts † | **0.488** [0.441-0.531] | 0.453 | 0.470 | 0.667 | 0.460 | 3.9 |
| mDeBERTa (platform pipeline) | **0.576** [0.534-0.622] | 0.727 | 0.643 | 0.761 | 0.662 | 8.9 |
| mDeBERTa (checkpoint only) | **0.475** [0.426-0.529] | 0.670 | 0.556 | 0.697 | 0.595 | 7.2 |

### MultiGraSCCo v2 English (63 docs, 1,438 spans)

| Team | Recall (merged) [95% CI] | Precision | F1 | Char recall | Typed F1 | s/doc |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Octo † | **0.622** [0.585-0.661] | 0.650 | 0.636 | 0.810 | 0.702 | 4.9 |
| Synthify | **0.698** [0.665-0.728] | 0.743 | 0.720 | 0.773 | 0.729 | 8.4 |
| Berts | **0.716** [0.673-0.755] | 0.744 | 0.730 | 0.802 | 0.737 | 2.3 |
| mDeBERTa (platform pipeline) | **0.616** [0.575-0.656] | 0.729 | 0.668 | 0.747 | 0.675 | 8.8 |
| mDeBERTa (checkpoint only) | **0.486** [0.439-0.534] | 0.673 | 0.565 | 0.666 | 0.570 | 7.4 |

### MultiGraSCCo v2 French (63 docs, 1,437 spans)

| Team | Recall (merged) [95% CI] | Precision | F1 | Char recall | Typed F1 | s/doc |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Octo † | **0.603** [0.566-0.643] | 0.636 | 0.619 | 0.757 | 0.681 | 5.9 |
| Synthify | **0.627** [0.588-0.661] | 0.628 | 0.627 | 0.673 | 0.659 | 15.0 |
| Berts | **0.656** [0.609-0.699] | 0.735 | 0.693 | 0.722 | 0.753 | 3.2 |
| mDeBERTa (platform pipeline) | **0.604** [0.558-0.651] | 0.715 | 0.655 | 0.667 | 0.682 | 11.9 |
| mDeBERTa (checkpoint only) | **0.485** [0.430-0.541] | 0.660 | 0.559 | 0.598 | 0.594 | 9.8 |

### MedDeID Dutch synthetic (nl, 300 docs, 3,598 spans)

| Team | Recall (merged) [95% CI] | Precision | F1 | Char recall | Typed F1 | s/doc |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Octo | **0.523** [0.506-0.542] | 0.622 | 0.569 | 0.795 | 0.575 | 1.0 |
| Synthify | **0.481** [0.464-0.499] | 0.611 | 0.538 | 0.711 | 0.521 | 1.9 |
| Berts | **0.556** [0.537-0.575] | 0.675 | 0.610 | 0.755 | 0.580 | 0.5 |
| mDeBERTa (platform pipeline) | **0.425** [0.405-0.445] | 0.621 | 0.505 | 0.642 | 0.494 | 1.7 |
| mDeBERTa (checkpoint only) | **0.392** [0.371-0.412] | 0.603 | 0.475 | 0.617 | 0.470 | 1.3 |

### MedDeID core-PII character recall

MedDeID annotates sub-parts of each span. Counting only the identifying parts
(`labels.MEDDEID_CORE`), the character recall is: mDeBERTa (platform pipeline) 0.683,
mDeBERTa (checkpoint only) 0.656. **This is this repository's own definition, computed
without MedDeID's official `meddeid-eval` scorer, so it must not be set beside the
baseline table in MedDeID's dataset card.**

## How to read it

* **No ranking across teams from one cell.** Intervals overlap on the 63-document
  benchmarks, and the benchmarks differ in what counts as PHI. The consistent signals are
  few: mDeBERTa has the highest recall and precision on the Spanish MEDDOCAN set (a
  language Octo and Berts are not built for); Berts has the highest recall point estimates
  on English and French MultiGraSCCo among the teams built for those languages (intervals
  overlap Synthify's on French); and the platform's regex and arbitration layer adds 2.5
  to 12.9 recall points to the mDeBERTa checkpoint (smallest on Spanish, largest on
  English).
* **Recall is far from 1.0 everywhere.** These are real-style or hard synthetic notes the
  detectors never saw; a masked output should still be reviewed. Character recall is
  higher than span recall because a partly masked identifier is still partly readable.
* **Known weak spots of mDeBERTa (MEDDOCAN).** Gender is the largest single hole: 461
  `SEXO_SUJETO_ASISTENCIA` spans, of which the single letters "H" and "M" (231) were never
  covered and only 0.26 overlapped overall. Institutions (`INSTITUCION`, 0.33 overlap) and
  professions (0.0) are also missed. Names, e-mails, dates, ages and identifiers are
  covered at 0.87 to 1.00 overlap.
* **Not comparable to published scores.** The systems reported beside these benchmarks
  trained on their train splits and were scored with the benchmarks' own scripts; these
  are zero-shot and scored with this repository's scorer.
* **PyTorch and ONNX give the same spans.** All numbers were measured on the ONNX path. To
  check that they hold for the PyTorch checkpoint the Hugging Face model cards also
  describe, both engines of the demo Space were run side by side on 78 sampled documents
  (20 MEDDOCAN, 20 MultiGraSCCo de, 10 en, 8 fr, 20 MedDeID nl): every document returned
  identical spans (label, start, end), and the largest confidence-score difference was
  2.4e-5. That is a sample, not a full-corpus run.
* **Excluded:** n2c2/i2b2 2014 and BRONCO (data-use agreements), GraSCCo_PHI (the same
  texts as MultiGraSCCo-de), SPARCLING-DE (license only from its dataset card),
  MultiClinNER (registration).

## Scoring changes after the runs

The predictions are fixed offsets and labels, so the scorer can be re-run without
re-running a model. After the kernels finished, two label-map corrections were made:
MEDDOCAN's `FAMILIARES_SUJETO_ASISTENCIA` spans are kinship words ("madre", "familia"),
not names, so they no longer count as typed name misses; and the arbitration-only label
`BMI` is classed with `HEIGHT` and `WEIGHT` as not masked. Neither changes any
type-agnostic number: the re-scored results matched each kernel's own output exactly
before the first correction, and typed F1 on MEDDOCAN rose by 0.003 to 0.005 depending on the team.
