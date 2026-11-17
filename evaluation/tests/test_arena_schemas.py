"""The declared shapes: the metric envelope, and the config response model.

`Metric` replaced three hand-rolled readers that had drifted apart -- a frozen
dataclass in `results`, a dict builder in `ranking`, and a `.get()` chain in
`leaderboard`. The cases below are the ones those readers disagreed on, pinned
so the model cannot quietly acquire pydantic's default coercions: lax mode
turns `"12.5"` into a metric and rejects an `available` flag spelled `"maybe"`,
and both would change what a published QA document means.

`ConfigResponse` is pinned for the opposite reason. A response model filters,
so a field missing from the model is a field missing from the response, with
nothing failing to say so.
"""

from __future__ import annotations

import pytest

from leaderboard_api import main
from leaderboard_api.schemas import ConfigResponse, Metric

# --------------------------------------------------------------------- #
# The metric envelope
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, available, value",
    [
        ({"available": True, "value": 1}, True, 1.0),
        ({"available": True, "value": 0}, True, 0.0),
        ({"available": False, "value": 9}, False, 9.0),
        # Truthiness, not pydantic's bool parsing: a document that spells the
        # flag `1`, `"true"` or `"maybe"` is read, not rejected.
        ({"available": 1, "value": 3}, True, 3.0),
        ({"available": "true", "value": 3}, True, 3.0),
        ({"available": "maybe", "value": 3}, True, 3.0),
        ({"available": "", "value": 3}, False, 3.0),
        # An available flag with no value is not an available metric.
        ({"available": True, "value": None}, False, None),
        ({"available": True}, False, None),
        # A bare number is a metric; anything else is a missing one.
        (5, True, 5.0),
        (0, True, 0.0),
        (None, False, None),
        ({}, False, None),
        ("12.5", False, None),
    ],
)
def test_metric_reads_the_envelopes_documents_actually_carry(raw, available, value):
    metric = Metric.parse(raw)
    assert metric.available is available
    assert metric.value == value


def test_a_numeric_string_is_not_a_number():
    """Pydantic's lax mode would coerce this; the readers it replaced did not."""
    assert Metric.parse({"available": True, "value": "12.5"}).value is None
    assert Metric.parse({"available": True, "value": [1]}).value is None


def test_number_is_the_value_only_when_the_metric_is_available():
    """`number` is what a reader renders, so an unavailable metric renders none."""
    assert Metric.parse({"available": True, "value": 0.5}).number == 0.5
    assert Metric.parse({"available": False, "value": 0.5}).number is None
    assert Metric.parse({"available": "", "value": 0.5}).number is None


def test_an_unreadable_value_is_unavailable_to_a_reader():
    assert Metric.parse({"available": True, "value": "12.5"}).number is None


def test_unknown_keys_are_ignored_rather_than_rejected():
    """QA may publish a richer envelope than this reader knows about."""
    metric = Metric.parse({"available": True, "value": 2, "units": "ms"})
    assert metric.number == 2.0


def test_payload_is_the_wire_shape_in_the_wire_order():
    assert list(Metric(available=True, value=1.0).payload()) == [
        "available",
        "value",
        "reason",
    ]
    assert Metric.parse(None).payload() == {
        "available": False,
        "value": None,
        "reason": "metric missing from document",
    }


# --------------------------------------------------------------------- #
# The config response model
# --------------------------------------------------------------------- #


def test_config_response_declares_every_key_the_route_returns():
    """A response model filters: an undeclared key is dropped, silently.

    Compares the model against the dict the route builds rather than against a
    frozen list, so adding a key to `config()` and forgetting the model fails
    here instead of in the browser.
    """
    built = main.config()
    assert set(ConfigResponse.model_fields) == set(built)


def test_config_response_survives_the_route_unchanged(client):
    served = client.get("/api/v1/config").json()
    built = main.config()
    assert served == built
    assert list(served) == list(built)
