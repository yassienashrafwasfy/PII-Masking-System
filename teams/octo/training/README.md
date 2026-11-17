# Octo training

This is the migrated Unified-repo dataset-preparation and XLM-R training worker. Supply approved data paths explicitly; datasets, caches, outputs, and weights stay outside Git.

```sh
python -m pip install -r requirements.example.txt
PYTHONPATH=training python training/src/prepare_datasets.py --help
PYTHONPATH=training python training/src/train_token_classifier.py --help
```

Train against [`../../../platform/masking-gateway/src/masking_engine/app/core/taxonomy.json`](../../../platform/masking-gateway/src/masking_engine/app/core/taxonomy.json) so the new model emits the QA `qa-pii-v1` labels directly. The migrated model in `../model/` retains its legacy head only for deployment compatibility; it is not a checkpoint to resume for this new schema.
