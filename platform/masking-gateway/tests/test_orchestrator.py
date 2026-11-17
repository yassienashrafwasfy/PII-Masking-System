#!/usr/bin/env python3
"""Regression checks for model-to-regex fallback behavior."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import MaskSpan, Orchestrator
from masking_engine.app.engines import orchestrator as orchestrator_module

# Labels the regex owns wherever the regex spoke. Each has a checksum or a
# fixed shape, so a model guess over the same characters adds nothing and
# costs precision.
EXCLUSIVE_LABELS = ("RIZIV_INAMI", "INSZ_NISS", "BTW_EENHEID", "EMAIL", "URL")


def test_an_exclusive_label_loses_to_the_regex_span_covering_it() -> None:
    # Where both sources claim the same characters, the checksum wins.
    orchestrator = Orchestrator("model_regex")
    for label in EXCLUSIVE_LABELS:
        model_span = MaskSpan(40, 52, label, "model")
        regex_span = MaskSpan(38, 54, label, "regex")
        assert orchestrator.execute([model_span], [regex_span]) == [regex_span], label


def test_an_exclusive_label_survives_where_no_regex_span_reaches_it() -> None:
    # Exclusivity is a ruling about which source to believe, so it needs both
    # sources to have spoken. Applied to text the regex never matched it
    # deleted the only detection there was: a RIZIV number packed behind a
    # "/" is found by the model at 1.00 and by no pattern at all, and
    # suppressing it released the number in the clear.
    orchestrator = Orchestrator("model_regex")
    for label in EXCLUSIVE_LABELS:
        model_span = MaskSpan(40, 52, label, "model")
        elsewhere = MaskSpan(0, 12, label, "regex")
        assert orchestrator.execute([model_span], []) == [model_span], label
        assert orchestrator.execute([model_span], [elsewhere]) == [
            elsewhere,
            model_span,
        ], label


def test_an_exclusive_label_is_suppressed_by_a_regex_span_of_any_label() -> None:
    # The question the rule asks is "did the rules already read these
    # characters", not "did they read them as the same thing".
    orchestrator = Orchestrator("model_regex")
    model_span = MaskSpan(40, 52, "RIZIV_INAMI", "model")
    regex_span = MaskSpan(45, 60, "PHONE_NUMBER", "regex")
    assert orchestrator.execute([model_span], [regex_span]) == [regex_span]


def test_the_overlap_test_reads_a_touching_span_as_no_overlap() -> None:
    # Spans are half-open, so a regex span ending exactly where the model
    # span starts covers none of its characters and suppresses nothing.
    orchestrator = Orchestrator("model_regex")
    model_span = MaskSpan(40, 52, "EMAIL", "model")
    before = MaskSpan(30, 40, "EMAIL", "regex")
    after = MaskSpan(52, 60, "EMAIL", "regex")
    assert orchestrator.execute([model_span], [before, after]) == [
        before,
        model_span,
        after,
    ]


def test_a_model_date_survives_with_no_regex_counterpart() -> None:
    # A bare year matches no date regex, so suppressing an unmatched model
    # DATE would lose it outright.
    orchestrator = Orchestrator("model_regex")
    model_date = MaskSpan(0, 18, "DATE", "model")
    assert orchestrator.execute([model_date], []) == [model_date]


def test_a_model_date_and_a_regex_date_are_both_kept() -> None:
    # DATE is an additive union rather than an arbitration.
    orchestrator = Orchestrator("model_regex")
    model_date = MaskSpan(0, 18, "DATE", "model")
    regex_date = MaskSpan(0, 18, "DATE", "regex")
    assert orchestrator.execute([model_date], [regex_date]) == [model_date, regex_date]


def test_a_model_id_span_of_an_impossible_length_is_dropped() -> None:
    # The detector has no "not an entity" prediction for a digit run and picks
    # the identifier class by length, so a nine-digit reference number reads
    # as an eleven-digit RIZIV at 0.999 regardless of the sentence around it.
    text = "Het referentienummer is 535581078 in dit dossier."
    start = text.index("535581078")
    span = MaskSpan(start, start + 9, "RIZIV_INAMI", "model", confidence=0.999)
    orchestrator = Orchestrator("model_regex")
    assert orchestrator.execute([span], [], {None: text}) == []


def test_a_model_id_span_of_a_real_length_survives_the_gate() -> None:
    # The gate judges shape, not validity. This is the case commit 0717adb
    # recovered -- a canonical RIZIV the pattern refused to match -- and the
    # gate must not take it back.
    text = "Gepakt als 78.03.12-123.45/1-23456-78-901/09 245 67 89 in het veld."
    start = text.index("1-23456-78-901")
    span = MaskSpan(start, start + 14, "RIZIV_INAMI", "model", confidence=1.0)
    orchestrator = Orchestrator("model_regex")
    assert orchestrator.execute([span], [], {None: text}) == [span]


def test_the_gate_applies_in_model_mode_too() -> None:
    # A detection that is the wrong length for its own label is wrong whether
    # or not a regex ran, so the gate sits before the mode dispatch.
    text = "Het bedrag is 377249721033083 euro."
    start = text.index("377249721033083")
    span = MaskSpan(start, start + 15, "INSZ_NISS", "model", confidence=0.999)
    assert Orchestrator("model").execute([span], [], {None: text}) == []


def test_the_gate_is_off_when_no_rule_is_injected() -> None:
    # `MODEL_ID_SHAPE_GATE=off` passes None, which must restore the ungated
    # behaviour exactly rather than fall back to some weaker rule.
    text = "Het referentienummer is 535581078 in dit dossier."
    start = text.index("535581078")
    span = MaskSpan(start, start + 9, "RIZIV_INAMI", "model", confidence=0.999)
    ungated = Orchestrator("model_regex", shape_rule=None)
    assert ungated.execute([span], [], {None: text}) == [span]


def test_the_gate_leaves_a_regex_span_alone() -> None:
    # Regex spans arrive already validated by the grammar that found them.
    # Re-judging them here would be a second opinion, not a gate.
    text = "Het nummer is 535581078 in dit dossier."
    start = text.index("535581078")
    span = MaskSpan(start, start + 9, "RIZIV_INAMI", "regex")
    orchestrator = Orchestrator("model_regex")
    assert orchestrator.execute([], [span], {None: text}) == [span]


def test_a_label_the_gate_cannot_judge_passes_through() -> None:
    # Only the fixed-length national identifiers are gated. A NAME or an
    # ADDRESS has no shape to check and must not be dropped for want of one.
    text = "Alinafi Dudoy Di Venere woont in Zevenhuizen."
    name = MaskSpan(0, 23, "NAME", "model", confidence=0.99)
    orchestrator = Orchestrator("model_regex")
    assert orchestrator.execute([name], [], {None: text}) == [name]


def main() -> None:
    """Run every test in this module, discovered rather than listed."""
    tests = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for _, test in tests:
        test()
    print(f"Validated {len(tests)} cases in {Path(__file__).name}.")


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------- #
# The overlap index
# --------------------------------------------------------------------------- #


def test_a_claim_reaches_a_span_it_touches_and_no_further() -> None:
    claims = orchestrator_module._Claims([MaskSpan(10, 20, "EMAIL", "regex")])
    inside = [(10, 20), (12, 15), (5, 12), (19, 30), (5, 40)]
    outside = [(0, 10), (20, 30), (0, 5), (30, 40)]
    for start, end in inside:
        assert claims.overlaps(MaskSpan(start, end, "EMAIL", "model")), (start, end)
    for start, end in outside:
        assert not claims.overlaps(MaskSpan(start, end, "EMAIL", "model")), (start, end)


def test_a_claim_index_with_nothing_in_it_claims_nothing() -> None:
    claims = orchestrator_module._Claims([])
    assert not claims.overlaps(MaskSpan(0, 10, "EMAIL", "model"))


def test_an_earlier_long_claim_is_still_found_behind_later_short_ones() -> None:
    # The index answers with one bisect, so a long span early on has to keep
    # reaching past the shorter spans that start after it.
    claims = orchestrator_module._Claims(
        [
            MaskSpan(0, 100, "EMAIL", "regex"),
            MaskSpan(10, 12, "EMAIL", "regex"),
            MaskSpan(20, 22, "EMAIL", "regex"),
        ]
    )
    assert claims.overlaps(MaskSpan(50, 60, "EMAIL", "model"))
    assert not claims.overlaps(MaskSpan(100, 110, "EMAIL", "model"))


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #


def test_a_mode_the_contract_does_not_name_is_refused() -> None:
    for mode in ("hybrid", "", "MODEL", "model_only"):
        try:
            Orchestrator(mode)  # type: ignore[arg-type]  # invalid on purpose
        except ValueError as error:
            assert f"Unknown detection mode: {mode}" == str(error)
        else:
            raise AssertionError(f"accepted {mode!r}")


def test_each_mode_returns_the_sources_its_name_promises() -> None:
    model = MaskSpan(0, 5, "NAME", "model")
    regex = MaskSpan(10, 15, "EMAIL", "regex")
    assert Orchestrator("model").execute([model], [regex]) == [model]
    assert Orchestrator("regex").execute([model], [regex]) == [regex]
    assert Orchestrator("model_regex").execute([model], [regex]) == [model, regex]


def test_spans_come_back_ordered_by_page_then_position() -> None:
    spans = [
        MaskSpan(5, 6, "NAME", "model", page=1),
        MaskSpan(0, 1, "NAME", "model", page=1),
        MaskSpan(9, 10, "NAME", "model", page=0),
        MaskSpan(0, 2, "NAME", "model"),
    ]
    ordered = Orchestrator("model").execute(spans, [])
    assert [(s.page, s.start) for s in ordered] == [(None, 0), (0, 9), (1, 0), (1, 5)]


# --------------------------------------------------------------------------- #
# The identifier shape gate
# --------------------------------------------------------------------------- #


def test_the_shape_gate_drops_a_detection_of_the_wrong_length() -> None:
    text: dict[int | None, str] = {None: "RIZIV 535581078 vermeld"}
    impossible = MaskSpan(6, 15, "RIZIV_INAMI", "model")
    assert Orchestrator("model").execute([impossible], [], text) == []
    # ...and keeps one the grammar admits.
    admitted: dict[int | None, str] = {None: "1-23456-78-901"}
    real = MaskSpan(0, 14, "RIZIV_INAMI", "model")
    assert Orchestrator("model").execute([real], [], admitted) == [real]


def test_the_shape_gate_is_skipped_without_a_rule_or_without_text() -> None:
    text: dict[int | None, str] = {None: "RIZIV 535581078 vermeld"}
    impossible = MaskSpan(6, 15, "RIZIV_INAMI", "model")
    # `MODEL_ID_SHAPE_GATE=off` injects None here.
    assert Orchestrator("model", shape_rule=None).execute([impossible], [], text) == [
        impossible
    ]
    # And with no text there is nothing for the rule to read.
    assert Orchestrator("model").execute([impossible], []) == [impossible]


def test_a_span_pointing_outside_its_text_is_left_to_the_next_stage() -> None:
    # An empty slice is not evidence the label is wrong, so the gate passes.
    span = MaskSpan(500, 510, "RIZIV_INAMI", "model")
    assert Orchestrator("model").execute([span], [], {None: "short"}) == [span]


def test_an_injected_rule_is_the_one_that_decides() -> None:
    asked: list[tuple[str, str]] = []

    def refuse_everything(label: str, text: str) -> bool:
        asked.append((label, text))
        return False

    span = MaskSpan(0, 5, "NAME", "model")
    orchestrator = Orchestrator("model", shape_rule=refuse_everything)
    assert orchestrator.execute([span], [], {None: "Peter"}) == []
    assert asked == [("NAME", "Peter")]


# --------------------------------------------------------------------------- #
# What an operator reads afterwards
# --------------------------------------------------------------------------- #


def test_a_suppressed_model_span_is_logged_with_why(caplog) -> None:
    caplog.set_level(logging.INFO, logger="masking_engine.app.engines.orchestrator")
    model = MaskSpan(38, 54, "EMAIL", "model", page=2)
    regex = MaskSpan(38, 54, "EMAIL", "regex", page=2)
    Orchestrator("model_regex").execute([model], [regex])
    assert (
        "orchestration_suppression accepted_source=none accepted_label=none "
        "suppressed_source=model suppressed_label=EMAIL page=2 start=38 end=54 "
        "reason=exclusive_regex_label"
    ) in [record.getMessage() for record in caplog.records]


def test_a_span_the_shape_gate_drops_is_logged_with_why(caplog) -> None:
    # Logged by `span_shape`, not by this module: the gate is shared with the
    # teams that merge their own spans, and one of them has no orchestrator to
    # log through. The message is what an operator greps and is unchanged.
    caplog.set_level(logging.INFO, logger="masking_engine.app.engines.span_shape")
    span = MaskSpan(6, 15, "RIZIV_INAMI", "model")
    Orchestrator("model").execute([span], [], {None: "RIZIV 535581078 vermeld"})
    assert (
        "orchestration_suppression accepted_source=none accepted_label=none "
        "suppressed_source=model suppressed_label=RIZIV_INAMI page=None "
        "start=6 end=15 reason=impossible_identifier_shape"
    ) in [record.getMessage() for record in caplog.records]


def test_the_merge_counts_every_span_it_saw(caplog) -> None:
    caplog.set_level(logging.INFO, logger="masking_engine.app.engines.orchestrator")
    model = [MaskSpan(38, 54, "EMAIL", "model"), MaskSpan(0, 4, "NAME", "model")]
    regex = [MaskSpan(38, 54, "EMAIL", "regex")]
    Orchestrator("model_regex").merge(model, regex)
    assert (
        "orchestrate mode=model_regex model_in=2 regex_in=1 suppressed=1 "
        "kept_model=1 regex_suppressed=0 kept_regex=1 final=2"
    ) in [record.getMessage() for record in caplog.records]
