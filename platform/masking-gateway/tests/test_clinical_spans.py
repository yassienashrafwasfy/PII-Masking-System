#!/usr/bin/env python3
"""The clinical detector table, pinned on the dose/concentration boundary.

A dose and a concentration are written from the same parts -- a number and a
unit -- and differ only in what follows: a dose ends at the unit, a
concentration carries a denominator after a "/". Reading the numerator of a
concentration as a dose masks a laboratory value, which is both a false
positive and a loss of clinical meaning in the released document.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.engines.regex_clinical import _clinical_spans


def found(text: str) -> list[tuple[str, str]]:
    return [(text[s.start : s.end], s.label) for s in _clinical_spans(text)]


# A dose stands alone, so every one of these is claimed whole.
DOSES = (
    ("metformine 500 mg tweemaal daags", "500 mg"),
    ("paracetamol 1000 mg", "1000 mg"),
    ("insuline 12 IU", "12 IU"),
    ("amoxicilline 1 g IV", "1 g"),
    ("salbutamol 2,5 ml", "2,5 ml"),
)

# Each of these is a measured concentration. The engine must claim no part of
# it -- emitting the numerator alone is the defect this file exists for.
CONCENTRATIONS = (
    "Hb 14,2 g/dL",
    "CRP 42 mg/L",
    "Ferritin 180 ug/L",
    "glucose 5,4 mmol/L",
    "creatinine 88 umol/L",
    "PaCO2 5,3 mmHg",
)


def test_a_dose_is_detected_whole() -> None:
    for text, value in DOSES:
        assert found(text) == [(value, "DOSAGE")], text


def test_a_concentration_yields_no_dose() -> None:
    for text in CONCENTRATIONS:
        assert [span for span in found(text) if span[1] == "DOSAGE"] == [], text


def test_a_dose_beside_a_concentration_survives() -> None:
    """The guard refuses the concentration without costing the dose next to it."""
    text = "Hb 14,2 g/dL na start van metformine 500 mg per dag"
    assert found(text) == [("500 mg", "DOSAGE")]
