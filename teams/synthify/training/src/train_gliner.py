"""Fine-tune GLiNER from prepared Synthify JSONL datasets.

Reproduces the training configuration of the source notebook
(Milestone_1/train_colab.ipynb, cell 9), which produced the committed
checkpoint: focal loss and a weighted sampler as a dual class-imbalance
strategy, plus early stopping on `eval_loss`.

Omitting any of those does not merely slow training down -- the notebook's run
stopped at epoch 6.0 of 10, and the class weights span 5.8x (INSZ 2.37 down to
URL 0.41), so a plain run is a materially different model.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

# Per-class sampling weights from the source notebook. A sample's weight is the
# maximum weight among its entities, so records carrying rare types (INSZ,
# RVIZ, CREDITCARDNUMBER) are drawn more often -- oversampling the *sampler*
# rather than duplicating rows, which would overfit on repeated copies.
PER_CLASS_WEIGHTS = {
    "INSZ": 2.37,
    "RVIZ": 1.94,
    "CREDITCARDNUMBER": 1.74,
    "Hospital_Name": 1.35,
    "Age": 1.15,
    "Gender": 1.15,
    "TELEFOON": 1.00,
    "Title": 0.87,
    "Email": 0.74,
    "Address": 0.62,
    "Date": 0.49,
    "Name": 0.43,
    "URL": 0.41,
}


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def sample_weights(dataset: list[dict]) -> list[float]:
    """Weight each sample by the rarest (highest-weighted) entity it contains."""
    return [
        max(
            [1.0] + [PER_CLASS_WEIGHTS.get(label, 1.0) for _, _, label in sample["ner"]]
        )
        for sample in dataset
    ]


def _install_trainer_patches(weights: list[float], patience: int) -> None:
    """Give GLiNER's internal Trainer a weighted sampler and early stopping.

    `train_model()` constructs its HuggingFace Trainer itself and exposes no
    hook for either, so the class is swapped out before that call -- the same
    approach the notebook takes.
    """
    import sys

    import gliner.training.trainer as trainer_module
    from torch.utils.data import WeightedRandomSampler
    from transformers import EarlyStoppingCallback

    original = trainer_module.Trainer

    # Subclass the attribute, not the local: a variable is not a valid base
    # class to mypy, and the two are the same object here.
    class WeightedTrainer(trainer_module.Trainer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.add_callback(EarlyStoppingCallback(early_stopping_patience=patience))

        def _get_train_sampler(self, *args, **kwargs):
            # Signature varies across transformers versions; the sampler does
            # not depend on the arguments, so they are accepted and ignored.
            return WeightedRandomSampler(
                weights=weights,
                num_samples=len(self.train_dataset),
                replacement=True,
            )

    trainer_module.Trainer = WeightedTrainer
    for name in list(sys.modules):
        if name.startswith("gliner"):
            module = sys.modules[name]
            if getattr(module, "Trainer", None) is original:
                setattr(module, "Trainer", WeightedTrainer)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-model", default="urchade/gliner_medium-v2.1")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=5e-6)
    parser.add_argument("--others-lr", type=float, default=1e-5)
    parser.add_argument("--focal-loss-alpha", type=float, default=0.75)
    parser.add_argument("--focal-loss-gamma", type=float, default=2.0)
    parser.add_argument("--early-stopping-patience", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--no-weighted-sampler",
        action="store_true",
        help="Disable class-balanced sampling (the notebook keeps it on)",
    )
    args = parser.parse_args()

    import torch
    from gliner import GLiNER
    from gliner.training import TrainingArguments

    train, validation = load_jsonl(args.train), load_jsonl(args.validation)
    if not train or not validation:
        raise ValueError("Both training and validation data must contain records")
    args.output.mkdir(parents=True, exist_ok=True)

    weights = sample_weights(train)
    if not args.no_weighted_sampler:
        _install_trainer_patches(weights, args.early_stopping_patience)

    # The notebook hardcodes bf16 and tf32, which its A100 supports. Both need
    # Ampere or newer, and TrainingArguments raises on older cards, so they are
    # detected here instead of assumed -- same settings on the same hardware.
    on_cuda = torch.cuda.is_available()
    use_bf16 = on_cuda and torch.cuda.is_bf16_supported()
    use_fp16 = on_cuda and not use_bf16
    use_tf32 = on_cuda and torch.cuda.get_device_capability(0)[0] >= 8

    steps_per_epoch = max(len(train) // args.batch_size, 1)
    total_steps = steps_per_epoch * args.epochs

    counts = Counter(label for sample in train for _, _, label in sample["ner"])
    print(f"Train samples: {len(train):,}   Validation samples: {len(validation):,}")
    print(f"Entity counts: {dict(counts.most_common())}")
    print(f"Steps/epoch:   {steps_per_epoch:,}   Total: {total_steps:,} (max)")
    print(f"Sampler:       {'uniform' if args.no_weighted_sampler else 'weighted'}")
    print(f"Early stop:    patience={args.early_stopping_patience} on eval_loss")
    print(f"Focal loss:    alpha={args.focal_loss_alpha} gamma={args.focal_loss_gamma}")
    print(f"Precision:     bf16={use_bf16} fp16={use_fp16} tf32={use_tf32}")

    model = GLiNER.from_pretrained(args.base_model)
    training_args = TrainingArguments(
        output_dir=str(args.output),
        bf16=use_bf16,
        fp16=use_fp16,
        tf32=use_tf32,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        max_steps=total_steps,
        learning_rate=args.learning_rate,
        others_lr=args.others_lr,
        warmup_ratio=0.1,
        weight_decay=0.01,
        eval_strategy="steps",
        eval_steps=steps_per_epoch,
        save_strategy="steps",
        save_steps=steps_per_epoch,
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        focal_loss_alpha=args.focal_loss_alpha,
        focal_loss_gamma=args.focal_loss_gamma,
        logging_strategy="steps",
        logging_steps=100,
        logging_dir=str(args.output / "logs"),
        report_to="none",
        seed=args.seed,
    )

    started = time.time()
    trainer = model.train_model(
        train_dataset=train, eval_dataset=validation, training_args=training_args
    )
    elapsed = time.time() - started

    steps = trainer.state.global_step
    print(f"Training complete in {elapsed / 60:.1f} min")
    print(f"  Ran {steps / steps_per_epoch:.1f}/{args.epochs} epochs ({steps:,} steps)")
    if steps < total_steps:
        print("  Early stopping triggered -- best checkpoint loaded")

    model.save_pretrained(str(args.output))
    print(f"Saved model to {args.output}")


if __name__ == "__main__":
    main()
