# Synthetic benchmark dataset

Run `python generate_synthetic.py --output generated` to create public and private
splits. Every value is synthetic. The private split should be uploaded to the
restricted `data` R2 bucket and excluded from team access.

Each JSONL record stores the document path, SHA-256 digest, synthetic entities, and
expected mask token. Official evaluation compares extracted output text against
these labels. The starter generator is deliberately small; QA should review entity
coverage and add layout variants before treating the scores as final.
