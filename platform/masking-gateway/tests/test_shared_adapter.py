#!/usr/bin/env python3
"""Check the private model-service adapter interface."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# What `service.py` calls on whichever team adapter `TEAM_ID` selects. A team
# missing one of these does not fail at import: it fails on the first request
# that reaches for it, in that team's own deployment.
REQUIRED_EXPORTS = (
    "detect",
    "ready",
    "release",
    "supported_media_types",
    "supported_model_formats",
)


def _assert_exports_the_interface(team: str) -> None:
    adapter = (ROOT / "teams" / team / "adapter.py").read_text(encoding="utf-8")
    for name in REQUIRED_EXPORTS:
        assert name in adapter, f"{team}'s adapter is missing {name}"


def test_the_octo_adapter_exports_the_interface() -> None:
    _assert_exports_the_interface("octo")


def test_the_synthify_adapter_exports_the_interface() -> None:
    _assert_exports_the_interface("synthify")


def test_the_berts_adapter_exports_the_interface() -> None:
    _assert_exports_the_interface("berts")


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
