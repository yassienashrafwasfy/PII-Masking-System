# Contributing

Create changes on a branch and open a merge request. Contract changes require a versioned directory and review from the relevant owners. Do not change an existing version incompatibly.

Never commit credentials, endpoints, NiFi state, generated outputs, or non-synthetic data. Model weights are not committed: they live on Kaggle and are fetched with `scripts/fetch_models.py`. Approved synthetic samples are allowed. Use `*.example` files with placeholders only. Before requesting review, run `pre-commit run --all-files --show-diff-on-failure` and every command in the README's `fast_checks` list. Do not split a fast check into a new CI job without reviewing the pipeline-minute impact.
