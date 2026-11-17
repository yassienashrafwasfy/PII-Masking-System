from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.train_token_classifier import (
    MIN_VERDICT_SUPPORT,
    TrainConfig,
    assert_best_checkpoint_complete,
    assert_checkpoint_labels_match,
    assert_checkpoint_resumable,
    checkpoint_name_from_reference,
    config_log_payload,
    dev_scores,
    effective_batch_size,
    find_resumable_checkpoint,
    label_support,
    log_alignment_check,
    select_completed_checkpoint,
    split_by_verdict_support,
    training_plan,
    training_reached_endpoint,
    validation_scores,
    verdict_block,
    write_run_artifacts,
)


def make_config(tmp_path: Path, **overrides: object) -> TrainConfig:
    settings: dict[str, object] = {
        "model_id": "xlm-roberta-base",
        "run_name": "test-run",
        "train_path": tmp_path / "train.jsonl",
        "validation_path": tmp_path / "validation.jsonl",
        "output_dir": tmp_path / "out",
        "cache_dir": tmp_path / "cache",
    }
    settings.update(overrides)
    return TrainConfig(**settings)  # type: ignore[arg-type]


def write_checkpoint(
    directory: Path, *, lora: bool = False, rng_states: int = 1, **state: object
) -> Path:
    """Create a checkpoint directory that passes the resume check."""
    directory.mkdir(parents=True, exist_ok=True)
    names = ["optimizer.pt", "scheduler.pt"]
    if lora:
        names += ["adapter_config.json", "adapter_model.safetensors"]
    else:
        names += ["config.json", "tokenizer_config.json"]
        names += ["model.safetensors", "tokenizer.json"]
    for name in names:
        (directory / name).write_text("{}", encoding="utf-8")
    for index in range(rng_states):
        (directory / f"rng_state_{index}.pth").write_text("", encoding="utf-8")
    (directory / "trainer_state.json").write_text(json.dumps(state), encoding="utf-8")
    return directory


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("/data/run/checkpoints/checkpoint-500", "checkpoint-500"),
        ("C:\\runs\\checkpoints\\checkpoint-12", "checkpoint-12"),
        ("/data/run/checkpoints/checkpoint-7/", "checkpoint-7"),
        ("checkpoint-1", "checkpoint-1"),
    ],
)
def test_checkpoint_name_from_reference(reference: str, expected: str) -> None:
    assert checkpoint_name_from_reference(reference) == expected


def test_training_reached_endpoint_uses_steps_when_max_steps_is_set(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, max_steps=100, epochs=99.0)
    assert training_reached_endpoint({"global_step": 100}, config) is True
    assert training_reached_endpoint({"global_step": 99}, config) is False


def test_training_reached_endpoint_uses_epochs_when_max_steps_is_unset(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, max_steps=-1, epochs=3.0)
    assert training_reached_endpoint({"epoch": 3.0}, config) is True
    assert training_reached_endpoint({"epoch": 2.5}, config) is False
    assert training_reached_endpoint({}, config) is False


def test_completed_full_run_selects_the_best_checkpoint(tmp_path: Path) -> None:
    config = make_config(tmp_path, run_mode="full")
    state = {"best_model_checkpoint": "/elsewhere/checkpoints/checkpoint-40"}
    selected = select_completed_checkpoint(
        str(tmp_path / "checkpoints" / "checkpoint-90"), state, config
    )
    assert selected == tmp_path / "checkpoints" / "checkpoint-40"


def test_completed_smoke_run_selects_the_last_checkpoint(tmp_path: Path) -> None:
    config = make_config(tmp_path, run_mode="smoke")
    last = str(tmp_path / "checkpoints" / "checkpoint-90")
    assert select_completed_checkpoint(last, {}, config) == Path(last)


def test_completed_full_run_without_a_best_checkpoint_fails(tmp_path: Path) -> None:
    config = make_config(tmp_path, run_mode="full")
    with pytest.raises(RuntimeError, match="best_model_checkpoint"):
        select_completed_checkpoint(str(tmp_path / "checkpoint-1"), {}, config)


def test_resumable_checkpoint_returns_its_trainer_state(tmp_path: Path) -> None:
    checkpoint = write_checkpoint(tmp_path / "checkpoint-10", epoch=2.0)
    assert assert_checkpoint_resumable(checkpoint, False, 1) == {"epoch": 2.0}


def test_checkpoint_missing_the_optimizer_cannot_resume(tmp_path: Path) -> None:
    checkpoint = write_checkpoint(tmp_path / "checkpoint-10")
    (checkpoint / "optimizer.pt").unlink()
    with pytest.raises(RuntimeError, match="optimizer.pt"):
        assert_checkpoint_resumable(checkpoint, False, 1)


def test_checkpoint_missing_weights_cannot_resume(tmp_path: Path) -> None:
    checkpoint = write_checkpoint(tmp_path / "checkpoint-10")
    (checkpoint / "model.safetensors").unlink()
    with pytest.raises(RuntimeError, match="has_model=False"):
        assert_checkpoint_resumable(checkpoint, False, 1)


def test_checkpoint_with_too_few_rng_states_cannot_resume(tmp_path: Path) -> None:
    checkpoint = write_checkpoint(tmp_path / "checkpoint-10", rng_states=1)
    with pytest.raises(RuntimeError, match="rng_states=1/4"):
        assert_checkpoint_resumable(checkpoint, False, 4)


def test_lora_checkpoint_resumes_without_a_tokenizer(tmp_path: Path) -> None:
    checkpoint = write_checkpoint(tmp_path / "checkpoint-10", lora=True, epoch=1.0)
    assert assert_checkpoint_resumable(checkpoint, True, 1) == {"epoch": 1.0}


def test_incomplete_best_checkpoint_fails(tmp_path: Path) -> None:
    best = write_checkpoint(tmp_path / "checkpoint-40")
    (best / "config.json").unlink()
    with pytest.raises(RuntimeError, match="missing or incomplete"):
        assert_best_checkpoint_complete(best, False)


def test_missing_best_checkpoint_directory_fails(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="missing or incomplete"):
        assert_best_checkpoint_complete(tmp_path / "absent", False)


def test_no_checkpoints_directory_starts_from_scratch(tmp_path: Path) -> None:
    assert find_resumable_checkpoint(tmp_path / "absent", False, 1, True) == (None, {})


def test_empty_checkpoints_directory_starts_from_scratch(tmp_path: Path) -> None:
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    assert find_resumable_checkpoint(checkpoints, False, 1, True) == (None, {})


def test_found_checkpoint_is_returned_with_its_state(tmp_path: Path) -> None:
    checkpoints = tmp_path / "checkpoints"
    write_checkpoint(checkpoints / "checkpoint-20", epoch=2.0, global_step=20)
    found, state = find_resumable_checkpoint(checkpoints, False, 1, True)

    assert found is not None
    assert Path(found).name == "checkpoint-20"
    assert state["global_step"] == 20


def test_found_checkpoint_with_a_broken_best_reference_fails(tmp_path: Path) -> None:
    checkpoints = tmp_path / "checkpoints"
    write_checkpoint(
        checkpoints / "checkpoint-20",
        best_model_checkpoint="/old/run/checkpoints/checkpoint-10",
    )
    with pytest.raises(RuntimeError, match="missing or incomplete"):
        find_resumable_checkpoint(checkpoints, False, 1, True)


def test_checkpoint_labels_must_match_the_dataset(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint-10"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text(
        json.dumps({"id2label": {"0": "O", "1": "B-EMAIL"}}), encoding="utf-8"
    )

    assert_checkpoint_labels_match(checkpoint, ["O", "B-EMAIL"])
    with pytest.raises(RuntimeError, match="label map"):
        assert_checkpoint_labels_match(checkpoint, ["O", "B-PHONE"])


def test_checkpoint_labels_are_ordered_by_id_not_by_string(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint-10"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text(
        json.dumps({"id2label": {"10": "B-URL", "2": "B-AGE", "0": "O"}}),
        encoding="utf-8",
    )

    assert_checkpoint_labels_match(checkpoint, ["O", "B-AGE", "B-URL"])


def test_effective_batch_size_counts_every_rank(tmp_path: Path) -> None:
    config = make_config(
        tmp_path, train_batch_size=8, gradient_accumulation=4, class_weighting="none"
    )
    assert effective_batch_size(config, 2) == 64


def test_training_plan_derives_steps_from_the_dataset_size(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        train_batch_size=10,
        gradient_accumulation=2,
        class_weighting="none",
        epochs=3.0,
        warmup_ratio=0.1,
        max_steps=-1,
    )
    plan = training_plan(config, train_feature_count=100, world_size=1)

    assert plan.batches_per_epoch == 10
    assert plan.optimizer_steps == 15
    assert plan.warmup_steps == 2


def test_training_plan_honours_max_steps(tmp_path: Path) -> None:
    config = make_config(
        tmp_path, train_batch_size=10, max_steps=7, warmup_ratio=0.0, epochs=99.0
    )
    plan = training_plan(config, train_feature_count=100, world_size=1)

    assert plan.optimizer_steps == 7
    assert plan.warmup_steps == 0


def test_training_plan_splits_batches_across_ranks(tmp_path: Path) -> None:
    config = make_config(tmp_path, train_batch_size=10, gradient_accumulation=1)
    assert training_plan(config, 100, world_size=4).batches_per_epoch == 3


def test_label_support_reports_configured_labels_with_no_gold_spans() -> None:
    gold = [{"label": "EMAIL"}, {"label": "EMAIL"}, {"label": "PHONE"}]
    support = label_support(gold, ["O", "B-EMAIL", "I-EMAIL", "B-PHONE", "B-AGE"])

    assert support == {"EMAIL": 2, "PHONE": 1, "AGE": 0}


def test_label_support_keeps_labels_absent_from_the_taxonomy() -> None:
    assert label_support([{"label": "IBAN"}], ["O"]) == {"IBAN": 1}


def test_rare_labels_are_excluded_from_the_verdict() -> None:
    support = {"EMAIL": MIN_VERDICT_SUPPORT, "AGE": MIN_VERDICT_SUPPORT - 1, "URL": 0}
    eligible, insufficient = split_by_verdict_support(support)

    assert eligible == {"EMAIL"}
    assert insufficient == {"AGE": MIN_VERDICT_SUPPORT - 1, "URL": 0}


def test_full_run_over_the_whole_split_gets_a_verdict(tmp_path: Path) -> None:
    config = make_config(tmp_path, run_mode="full", max_validation_rows=0)
    block = verdict_block(config, {"recall": 0.96, "precision": 0.9}, {"EMAIL"}, {})

    assert block["targets_met"] == {"recall": True, "precision": True}
    assert block["verdict"]["applicable"] is True
    assert block["verdict"]["eligible_labels"] == ["EMAIL"]


def test_missed_targets_are_reported_as_false(tmp_path: Path) -> None:
    config = make_config(tmp_path, run_mode="full", max_validation_rows=0)
    block = verdict_block(config, {"recall": 0.5, "precision": 0.5}, set(), {})

    assert block["targets_met"] == {"recall": False, "precision": False}


@pytest.mark.parametrize(
    ("run_mode", "max_validation_rows"),
    [("smoke", 0), ("probe", 0), ("full", 100)],
)
def test_partial_runs_report_no_verdict(
    tmp_path: Path, run_mode: str, max_validation_rows: int
) -> None:
    config = make_config(
        tmp_path, run_mode=run_mode, max_validation_rows=max_validation_rows
    )
    block = verdict_block(config, {"recall": 1.0, "precision": 1.0}, set(), {})

    assert block["targets_met"] == {"recall": None, "precision": None}
    assert block["verdict"]["applicable"] is False


def test_alignment_check_refuses_an_all_o_dataset() -> None:
    with pytest.raises(RuntimeError, match="zero entity tokens"):
        log_alignment_check({"O": 5000}, ["O", "B-EMAIL"])


def test_alignment_check_accepts_entity_tokens(capsys: pytest.CaptureFixture) -> None:
    log_alignment_check({"O": 5000, "B-EMAIL": 12}, ["O", "B-EMAIL"])
    printed = capsys.readouterr().out

    assert "entity_tokens=12" in printed
    assert "B-EMAIL=12" in printed


def test_config_log_payload_records_the_effective_batch_size(tmp_path: Path) -> None:
    config = make_config(
        tmp_path, train_batch_size=8, gradient_accumulation=2, class_weighting="none"
    )
    payload = config_log_payload(config, world_size=4)

    assert payload["effective_batch_size"] == 64
    assert payload["world_size"] == 4
    assert payload["adapter_learning_rate"] is None


def test_config_log_payload_is_json_serialisable(tmp_path: Path) -> None:
    payload = config_log_payload(make_config(tmp_path), world_size=1)
    assert json.loads(json.dumps(payload, sort_keys=True)) == payload


def make_spans() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Return one predicted span and the gold span it matches."""
    span = {
        "sample_index": 0,
        "label": "EMAIL",
        "start": 0,
        "end": 5,
        "confidence": 0.9,
        "text": "a@b.c",
    }
    return [dict(span)], [dict(span)]


def test_dev_scores_report_overlap_shared_pii_and_source() -> None:
    predictions, gold = make_spans()
    frame = pd.DataFrame({"source_dataset": ["openpii"]})
    scores = dev_scores(predictions, gold, frame, 0.5)

    assert set(scores) == {"dev_overlap", "dev_overlap_shared_pii", "dev_by_source"}
    assert scores["dev_overlap"]["recall"] == 1.0
    assert scores["dev_by_source"]["openpii"]["rows"] == 1


def test_validation_scores_report_every_match_mode_and_breakdown() -> None:
    predictions, gold = make_spans()
    frame = pd.DataFrame({"language": ["nl"], "source_dataset": ["openpii"]})
    scores = validation_scores(predictions, gold, frame, 0.5)

    assert set(scores) == {
        "validation_overlap",
        "validation_overlap_shared_pii",
        "validation_exact",
        "validation_iou",
        "validation_by_language",
        "validation_by_source",
    }
    assert scores["validation_exact"]["match"] == "exact"
    assert scores["validation_iou"]["match"] == "iou"
    assert scores["validation_by_language"]["nl"]["rows"] == 1


def test_a_prediction_below_the_threshold_does_not_count() -> None:
    predictions, gold = make_spans()
    frame = pd.DataFrame({"source_dataset": ["openpii"]})
    scores = dev_scores(predictions, gold, frame, 0.95)

    assert scores["dev_overlap"]["recall"] == 0.0


def test_run_artifacts_are_written_as_json(tmp_path: Path) -> None:
    write_run_artifacts(tmp_path, {"validation_overlap": {"f1": 0.9}}, ["O", "B-EMAIL"])

    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    labels = json.loads((tmp_path / "labels.json").read_text(encoding="utf-8"))

    assert metrics["validation_overlap"]["f1"] == 0.9
    assert labels == ["O", "B-EMAIL"]
