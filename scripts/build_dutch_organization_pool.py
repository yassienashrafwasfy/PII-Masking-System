#!/usr/bin/env python3
"""Build the checked-in Dutch organisation surrogate pool from an archive.

The source is deliberately kept outside the repository.  This script retains
only a fixed-size, deduplicated set of trade names suitable for deterministic
pseudonymisation, plus a small provenance record beside the generated pool.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

POOL_SIZE = 50_000
SOURCE_URL = "https://mirror.openstreetmap.nl/openkvk/kvk-btw-handelsnamen.csv.gz"
SNAPSHOT_DATE = "2021-12-24"
_UNSAFE = re.compile(r'[,;"\\\r\n\t]')
_SPACE = re.compile(r"\s+")

_WORD = r"[A-Z][a-zA-ZÀ-ÖØ-öø-ÿ'’-]*[a-zÀ-ÖØ-öø-ÿ]"
# Dutch surnames carry a lowercase particle that is part of the name, so
# "A. de Gruijter" has to read as one person and not as initials followed by
# a trade word.
_PARTICLE = (
    r"(?:van\s+de|van\s+der|van\s+den|in\s+het|op\s+de|"
    r"van|de|den|der|het|ten|ter|te)"
)

# A leading dotted abbreviation is a legal form or a facility type far more
# often than it is somebody's initials: "B.V. Remia Handelmaatschappij" is a
# company and "M.A.S. IKIGAÏ" a French maison d'accueil spécialisée. They are
# the same shape as "A.C. Nanninga", so shape alone cannot separate them and
# the known forms are listed instead.
_LEGAL_FORMS = frozenset(
    {
        "BV",
        "NV",
        "CV",
        "CVA",
        "VOF",
        "UA",
        "EV",
        "COV",
        "BVBA",
        "CVBA",
        "VZW",
        "ASBL",
        "SA",
        "SAS",
        "SASU",
        "SARL",
        "SPRL",
        "EURL",
        "SNC",
        "SCS",
        "SCI",
        "SCM",
        "SCP",
        "SCIC",
        "SCOP",
        "SEL",
        "SELARL",
        "GIE",
        "MAS",
        "FAM",
        "ITEP",
        "SSR",
        "USLD",
    }
)

# Two shapes of personal name, kept apart because only the first may end at a
# single surname. Allowing the word-only branch to do that would reject every
# one-word organisation in the source.
_PERSON_INITIALS = re.compile(
    rf"^(?P<initials>(?:[A-Z]\.){{1,3}})\s*(?:{_PARTICLE}\s+)?"
    rf"(?:{_WORD}\s+){{0,2}}{_WORD}$"
)
_PERSON_LIKE = re.compile(
    r"^(?:[A-Z][a-zÀ-ÖØ-öø-ÿ'’-]+\s+){1,3}" r"(?:[A-Z][a-zÀ-ÖØ-öø-ÿ'’-]+)$"
)

_VOWELS = frozenset("aeiouyàáâãäåèéêëìíîïòóôõöùúûüýÿœæ")
# An establishment register keeps administrative prose in the name column:
# a record superseded by another is written up as a note where the name
# belongs ("REMPLACÉ PAR LE CADA N°030000988 - MAIL DDCS DU 12/10/2020").
_ADMIN_NOTE = re.compile(
    r"REMPLAC\w*\s+(?:PAR|LES?|LA|L')\b|N[°º]\s*\d{6,}", re.IGNORECASE
)
# A register's own test row. Both halves are required: "TEST" alone is an
# ordinary word in a trade name ("Test Your Health B.V."), and "FAUX" alone
# opens a real trade ("FAUX PLAFONDS" is false ceilings).
_TEST_RECORD = re.compile(
    r"\bTEST\b(?=.*\b(?:FAUX|FICTI\w*|DUMMY|ESSAI)\b)"
    r"|\b(?:FAUX|FICTI\w*|DUMMY|ESSAI)\b(?=.*\bTEST\b)",
    re.IGNORECASE,
)
# Three or more X's is a redaction placeholder ("CHU XXX"). Two is not: French
# writes a century that way, as in "HOTEL ET BAR BRESSAN DU XX SIECLE".
_PLACEHOLDER = re.compile(r"X{3,}", re.IGNORECASE)
_LATIN = frozenset("abcdefghijklmnopqrstuvwxyzàáâãäåçèéêëìíîïñòóôõöùúûüýÿœæ")


def _is_mojibake(value: str) -> bool:
    """Return True when *value* is UTF-8 that was decoded as Latin-1.

    "PHARMACIE LONDINIÃRES" is "PHARMACIE LONDINIÈRES" read through the wrong
    codec upstream. Re-encoding to Latin-1 and decoding as UTF-8 is the exact
    inverse of that mistake, so a round trip that succeeds *and changes the
    string* identifies one without having to enumerate the damaged sequences.
    """
    try:
        return value.encode("latin-1").decode("utf-8") != value
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


def is_person_name(value: str) -> bool:
    """Return True when *value* is a personal name rather than a trade name.

    The initials branch used to be dead: written `[A-Z]\\.` inside a raw
    string it matched a literal backslash, so "A. Boutouzine" and 1,600 other
    register entries that are nothing but somebody's initials and surname were
    copied into a pool whose entire purpose is that it names nobody.
    """
    initials = _PERSON_INITIALS.fullmatch(value)
    if initials is not None:
        return initials["initials"].replace(".", "").upper() not in _LEGAL_FORMS
    return _PERSON_LIKE.fullmatch(value) is not None


def is_junk_name(value: str) -> bool:
    """Return True for an entry no document should ever be masked with.

    These are register artefacts rather than names: a wrong-codec mangling, a
    clerk's note left in the name column, a redaction placeholder, and the
    bare acronyms ("ICU", "RTZ", "APSLLB") that read as a stray code rather
    than as the organisation a masked note is supposed to name.
    """
    if len(value) < 4 or _is_mojibake(value):
        return True
    letters = [c.casefold() for c in value if c.isalpha()]
    if not any(c in _VOWELS for c in letters):
        return True
    # A single all-caps token that is nearly all consonants is a register
    # code, not a trade name: "APSLLB", "ASTBTP13", "CSTG32". Restricted to
    # one token and to names carrying no lowercase, so an ordinary multi-word
    # establishment name can never reach it.
    #
    # One vowel in five letters, not the three-in-ten first tried: French
    # trade names sit thick against a 0.3 boundary ("ALPHAFORMS", "BERLINGOTS"
    # and "AMBRINVEST" are all exactly 0.3), so that cut removed real names
    # along with the codes. 0.2 clears the codes and nothing else.
    if (
        " " not in value
        and not any(c.islower() for c in value)
        and sum(c in _VOWELS for c in letters) < 0.2 * len(letters)
    ):
        return True
    if (
        _PLACEHOLDER.search(value)
        or _ADMIN_NOTE.search(value)
        or _TEST_RECORD.search(value)
    ):
        return True
    return any(
        character.isalpha() and character.casefold() not in _LATIN
        for character in value
    )


def normalized_name(value: str) -> str | None:
    """Return a safe trade name, excluding obvious personal-name entries."""
    value = _SPACE.sub(" ", value).strip()
    if not 3 <= len(value) <= 80 or _UNSAFE.search(value):
        return None
    if not any(character.isalpha() for character in value):
        return None
    if is_person_name(value) or is_junk_name(value):
        return None
    return value


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def deterministic_sample(names: Iterable[str], size: int) -> list[str]:
    """Return up to *size* names, chosen and ordered by a stable digest sort.

    Sorting by each name's digest rather than the name itself picks a sample
    that does not track alphabetic order or source order, while staying
    reproducible across runs of this script.
    """
    return sorted(
        names, key=lambda name: hashlib.sha256(name.casefold().encode()).digest()
    )[:size]


def build(source: Path, destination: Path, metadata: Path) -> None:
    names: dict[str, str] = {}
    with gzip.open(source, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.reader(stream):
            if len(row) < 3:
                continue
            name = normalized_name(row[2])
            if name is not None:
                names.setdefault(name.casefold(), name)

    selected = deterministic_sample(names.values(), POOL_SIZE)
    if len(selected) != POOL_SIZE:
        raise RuntimeError(
            f"expected at least {POOL_SIZE} usable names, found {len(selected)}"
        )
    selected.sort(key=str.casefold)
    destination.write_text("\n".join(selected) + "\n", encoding="utf-8")
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "purpose": "Dutch organisation-name pseudonymisation pool",
                "source_url": SOURCE_URL,
                "source_snapshot_date": SNAPSHOT_DATE,
                "source_sha256": source_sha256(source),
                "included_name_count": len(selected),
                "selection": "case-insensitive deduplication, unsafe-character and obvious-person-name exclusion, deterministic SHA-256 sample",
                "licence_status": "The historical source archive did not expose a licence statement. Review source terms before redistributing this derived pool outside this deployment.",
                "generated_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "platform/masking-gateway/src/masking_engine/app/core/surrogate_organizations_nl.txt"
        ),
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path(
            "platform/masking-gateway/src/masking_engine/app/core/surrogate_organizations_nl.metadata.json"
        ),
    )
    arguments = parser.parse_args()
    build(arguments.source, arguments.output, arguments.metadata)


if __name__ == "__main__":
    main()
