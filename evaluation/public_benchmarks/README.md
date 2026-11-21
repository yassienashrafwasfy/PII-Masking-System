# Public benchmarks

Scores every team on three open de-identification benchmarks, with the detector each
team ships (its real adapter, on CPU ONNX), so the numbers are what a gateway request
would have produced. The arena in `evaluation/leaderboard_api` scores synthetic
documents; this is the outside check.

| Benchmark | Language | Documents | Text | License |
| --- | --- | ---: | --- | --- |
| [MEDDOCAN](https://zenodo.org/records/4279322) test split | es | 250 | real case reports with manually inserted, realistic PHI | CC BY 4.0 |
| [MultiGraSCCo v2](https://zenodo.org/records/19489040) PHI files | de, en, fr | 63 each | rewritten German clinical letters and their machine translations | CC BY 4.0 |
| [MedDeID Dutch synthetic benchmark](https://huggingface.co/datasets/stighellemans/meddeid-dutch-synthetic-benchmark) | nl | 300 | LLM-written synthetic notes, physician-reviewed | CC BY 4.0 |

The data is not in this repository (MEDDOCAN is built on real case reports). Cite the
datasets when you use their results: Marimon et al., *Automatic De-Identification of
Medical Texts in Spanish: the MEDDOCAN Track*, IberLEF 2019; the MultiGraSCCo paper
(arXiv 2603.08879, LREC 2026); Hellemans et al., MedDeID (arXiv 2609.10049; dataset
doi 10.5281/zenodo.22689856).

Not used: n2c2/i2b2 2014 and BRONCO (data-use agreements), GraSCCo_PHI (the same texts as
MultiGraSCCo-de), SPARCLING-DE (optional, its license comes only from its dataset card),
MultiClinNER (registration).

## Run it

```sh
uv sync --locked
uv run python -m evaluation.public_benchmarks.fetch_data --out ~/benchmark-data
# one process per configuration: the engine reads its environment once
uv run python -m evaluation.public_benchmarks.runner predict --config mdeberta \
    --data ~/benchmark-data --out out/ [--limit 5]
uv run python -m evaluation.public_benchmarks.score --data ~/benchmark-data \
    --predictions out/ --out results.json --markdown results.md
```

Configurations: `octo`, `synthify`, `berts`, `mdeberta`, and `mdeberta-model-only`
(`DETECTION_MODE=model`: the checkpoint's own spans, without the platform's regex and
arbitration layers, which is what a model card describes). The environment of each is
read from its service in `compose.yaml`. No locale is passed: the document language is
detected, as for a gateway request, and recorded in the results.

`kaggle_kernel.py` is the template of the CPU kernel the published numbers were run in
(one kernel per configuration, 4 vCPU, no accelerator). It clones this repository at a
commit, installs the locked environment, downloads the pinned datasets, and writes
aggregates and offset-only predictions to `/kaggle/working/result`. To run it:

1. Replace `__CONFIG__` and `__COMMIT__` in a copy named `run.py`.
2. Add a `kernel-metadata.json` beside it: `"kernel_type": "script"`, `"is_private": true`,
   `"enable_internet": true`, `"enable_gpu": false`, and `model_sources` listing the
   Kaggle variations the team needs. Kaggle refuses to download a model in a
   non-interactive session unless it is attached here:

   | Configuration | `model_sources` (each `.../onnx/<variation>/1`) |
   | --- | --- |
   | octo | `octo-xlmr-fp32` |
   | synthify | `synthify-gliner-fp32` |
   | berts | `berts-nl-medroberta-int8`, `berts-en-bert-int8`, `berts-fr-camembert-int8` |
   | mdeberta, mdeberta-model-only | `mdeberta-medical-onnx` |

   The handle prefix is `yassienwasfy/pii-masking-system/onnx/`.
3. `kaggle kernels push -p <folder>`, then `kaggle kernels output <id> -p <out>`.

Each result folder carries `run.json` with the commit the predictions came from. Score
them again locally with `score.py`; its results file records the scorer's own commit, so
a prediction commit and a scorer commit can differ and both are stated.

## What is scored

Predictions are the platform labels each team emits, mapped to nine coarse categories
(`labels.py`). Clinical labels (diagnosis, medication, ...) are dropped: none of these
benchmarks annotates clinical content as PHI, so keeping them would charge a team a
false positive for doing what the platform asks of it.

* **Headline, type-agnostic (what masking needs).** Did a prediction cover the sensitive
  span, whatever label it carries? Spans separated only by non-alphanumeric characters
  are merged on both sides, then matched exactly (MEDDOCAN sub-track 2B), and reported as
  precision / recall / F1 with a 95% bootstrap interval over documents. Also reported:
  exact match without merging (2A), overlap recall, and **character recall** (the share
  of alphanumeric gold characters left readable if the prediction were applied).
* **Strict typed F1.** Exact offsets and a compatible coarse category, over the gold
  spans some team has a label for. Gold spans no team can label (a profession, a title)
  stay in the headline recall, because an unmasked profession is still a leak, and are
  left out of the typed score.
* **MedDeID core-PII character recall and non-PII redaction rate** are computed from its
  subannotations with *this repository's* definition (`labels.MEDDEID_CORE`). They are
  not `meddeid-eval`'s numbers and must not be set beside the table in its dataset card.
* Per-gold-tag recall tables are in the results file.

## What the numbers do not say

* They are zero-shot and out of domain. The systems published beside these benchmarks
  trained on the benchmark's train split; these detectors did not, so a gap to a published
  score is not a ranking against that system.
* MEDDOCAN is case-report prose with injected PHI, and MultiGraSCCo is a few dozen letters,
  so small per-type counts are noisy (see the intervals).
* Every team is scored on every language so the tables are complete, but a team is only
  built for some of them: Octo is declared Belgian-Dutch, and Berts has nl/en/fr
  checkpoints and falls back to Dutch for any other language (so Spanish and German are
  out of scope for it). A score outside a team's languages is marked in the published
  tables and is not a defect.
* Scores here are aggregates; no benchmark text or per-document spans are published.
