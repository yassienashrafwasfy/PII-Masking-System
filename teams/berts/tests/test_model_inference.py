#!/usr/bin/env python3
"""Exercise Berts' int8 ONNX runtime and canonical label conversion."""

from __future__ import annotations

import sys
from pathlib import Path


def spans_for(detect, text: str, locale: str | None = None) -> set[tuple[str, str]]:
    metadata = {"content_type": "text/plain", "model_format": "onnx"}
    if locale is not None:
        metadata["document_locale"] = locale
    return {
        (span.label, text[span.start : span.end]) for span in detect(text, metadata)
    }


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "platform"))
    sys.path.insert(0, str(root / "platform/masking-gateway/src"))
    from teams.berts.runtime import CHECKPOINTS, detect, ready, release, unload

    text = (
        "Patient Emily Carter visited St Thomas Hospital on 14 January 2026. "
        "Call +32 470 12 34 56 or email emily.carter@example.co.uk."
    )
    found = spans_for(detect, text)
    assert ("NAME", "Emily Carter") in found
    assert ("DATE", "14 January 2026") in found
    assert ("PHONE_NUMBER", "+32 470 12 34 56") in found
    assert ("EMAIL", "emily.carter@example.co.uk") in found
    assert ready("onnx") and release()[2].startswith("sha256:")

    # A referral letter writes the national number the way this one does, and
    # the number in it does not satisfy the mod-97 key. Detection used to drop
    # it and the phone pattern took `.07.03-456.12` out of the middle, so the
    # released document still carried `78` -- the patient's year of birth --
    # in a field that read as masked. Every digit of the number is covered.
    letter = (
        "Rijksregisternummer\n(INSZ/NISS): 78.07.03-456.12.\n"
        "Behandelend arts: Dr. Peter Van Damme, RIZIV-nummer 1-23456-78-901."
    )
    national = letter.index("78.07.03-456.12")
    masked = set()
    for span in detect(letter, {"content_type": "text/plain", "model_format": "onnx"}):
        masked.update(range(span.start, span.end))
    assert set(range(national, national + len("78.07.03-456.12"))) <= masked
    # `safetensors` was a valid value up to contract v1.3. Nothing serves it
    # now, and a caller that still sends it must be told so rather than served
    # by the ONNX graph under a format name that is no longer true.
    try:
        detect(text, {"content_type": "text/plain", "model_format": "safetensors"})
    except ValueError:
        pass
    else:
        raise AssertionError("Berts accepted the retired SafeTensors format")

    # Every language's checkpoint loads, reads its own language, and returns
    # the same canonical taxonomy. Names and the national number are asserted
    # rather than the full span set: what the three models agree on is the
    # contract, and their individual recall is the arena's business.
    documents = {
        "nl_BE": (
            "Patiente Emilie Halleux, 47 jaar, INSZ 80.01.01-001.07, "
            "gezien op 12/10/2025 in AZ Sint-Jan te Brugge."
        ),
        "en": (
            "Patient Emilie Halleux, 47 years old, National Registration No. "
            "80.01.01-001.07, seen on 12/10/2025 at St John's Hospital."
        ),
        "fr_FR": (
            "Patiente Emilie Halleux, âgée de 47 ans, "
            "NISS 80.01.01-001.07, vue le 12/10/2025 à la Clinique "
            "Saint-Jean à Bruges."
        ),
    }
    for locale, document in documents.items():
        found = spans_for(detect, document, locale)
        assert ("NAME", "Emilie Halleux") in found, (locale, sorted(found))
        assert ("INSZ_NISS", "80.01.01-001.07") in found, (locale, sorted(found))
        # No span opens or closes on whitespace. A SentencePiece tokenizer
        # reports the space before a word as part of its first token, which
        # would put the French model's spans one character to the left of
        # every other model's for the same value.
        assert all(value == value.strip() for _, value in found), (
            locale,
            sorted(found),
        )

    # `release` names the artifact that read the document, not a bundle: the
    # masked object is written under `model=<name>/version=<version>/`, and
    # rolling one language back means knowing which one produced a file.
    names = {locale: release("onnx", locale=locale)[0] for locale in CHECKPOINTS}
    assert len(set(names.values())) == len(CHECKPOINTS), names
    assert release("onnx") == release("onnx", locale="nl_BE")
    # An unknown locale is not an error; it reads Dutch, as the surrogates do.
    assert release("onnx", locale="de_DE") == release("onnx", locale="nl_BE")

    # The rollback: `BERTS_LANGUAGE_ROUTING=off` sends every language to the
    # default checkpoint, which is what this team shipped before the other two
    # existed. An operator uses it to back a bad checkpoint out by restarting
    # one service, so it has to hold for every locale, not just for unknown
    # ones, and `release` has to report the artifact that really answered.
    import os

    from teams.berts.runtime import checkpoint_for, language_routing

    assert language_routing() is True
    os.environ["BERTS_LANGUAGE_ROUTING"] = "off"
    try:
        assert language_routing() is False
        for locale in CHECKPOINTS:
            assert checkpoint_for(locale).locale == "nl_BE", locale
            assert release("onnx", locale=locale) == release("onnx", locale="nl_BE")
        # A typo must not read as "off" and quietly leave routing on, nor as
        # "on" and quietly leave a bad checkpoint serving traffic.
        os.environ["BERTS_LANGUAGE_ROUTING"] = "of"
        try:
            language_routing()
        except ValueError as exc:
            assert "must be one of: off, on" in str(exc), exc
        else:
            raise AssertionError("a misspelled BERTS_LANGUAGE_ROUTING was accepted")
    finally:
        del os.environ["BERTS_LANGUAGE_ROUTING"]
    assert language_routing() is True
    assert checkpoint_for("fr_FR").locale == "fr_FR"

    unload()
    assert not ready("onnx")
    print("Validated Berts int8 ONNX inference and label conversion.")


if __name__ == "__main__":
    main()
