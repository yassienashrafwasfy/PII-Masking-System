#!/usr/bin/env python3
"""Build the French healthcare-organisation surrogate pool from FINESS.

FINESS is the Fichier National des Etablissements Sanitaires et Sociaux, the
French national register of health and social-care establishments, published
by the Agence du Numerique en Sante under the Licence Ouverte v2.0. It is the
French counterpart of the Dutch CIBG/DigiMV source behind
`build_dutch_medical_organization_pool.py`, so a French document's
`ORGANIZATION_NAME` surrogate reads like a French clinic rather than a
Flemish one.

The source archive is deliberately kept outside the repository. This script
retains only a fixed-size, deduplicated set of establishment names suitable
for deterministic pseudonymisation, plus a provenance record beside the
generated pool.

Names come from `nomEgeLong`, the establishment's own long name ("CLINIQUE
DOCTEUR CONVERT", "MAISON DE RETRAITE RESIDENCE DES ANCIENS"), falling back
to `nomEgeCourt`. Both are institution names rather than people, so the
person-name exclusion inherited from the general builder rarely fires here --
it is applied anyway, because a sole-practitioner practice is registered
under its practitioner's name and must not become a surrogate.

Usage:
    python scripts/build_french_medical_organization_pool.py \
        ~/.agent-reach/fr-pools/finess-structures.json.gz
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from build_dutch_organization_pool import (
    deterministic_sample,
    normalized_name,
    source_sha256,
)
from build_french_organization_pool import _is_person_name

POOL_SIZE = 50_000
SOURCE_URL = (
    "https://static.data.gouv.fr/resources/finess-structures-1/"
    "20260901-021627/finess-structures-mensuel-202608.json.gz"
)
SOURCE_DATASET = "https://www.data.gouv.fr/datasets/finess-structures-1"
SNAPSHOT_DATE = "2026-08"
LICENCE = "Licence Ouverte / Open Licence v2.0 (Etalab)"

# The export is one large JSON document, not newline-delimited, and holds
# well over a hundred thousand establishments. Streaming the name fields out
# with a regex keeps peak memory to the pool itself rather than to a parsed
# copy of a 400 MB document, and the fields are flat strings with no nesting
# for a parser to disambiguate.
_NAME_FIELD_RE = re.compile(
    r'"(nomEgeLong|nomEgeCourt)"\s*:\s*"((?:[^"\\]|\\.){2,120})"'
)
_CHUNK = 1 << 22
# A name can straddle a chunk boundary; keep enough tail to hold the longest
# match the pattern can produce.
_OVERLAP = 256


def establishment_names(source: Path):
    """Yield every establishment name in *source*, in file order."""
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        carry = ""
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                break
            text = carry + chunk
            for match in _NAME_FIELD_RE.finditer(text):
                yield json.loads(f'"{match.group(2)}"')
            carry = text[-_OVERLAP:]


def build(source: Path, destination: Path, metadata: Path) -> None:
    names: dict[str, str] = {}
    for raw in establishment_names(source):
        name = normalized_name(raw)
        if name is not None and not _is_person_name(name):
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
                "purpose": "French healthcare-organisation pseudonymisation pool",
                "source_url": SOURCE_URL,
                "source_dataset": SOURCE_DATASET,
                "source_snapshot_date": SNAPSHOT_DATE,
                "source_sha256": source_sha256(source),
                "included_name_count": len(selected),
                "selection": (
                    "nomEgeLong/nomEgeCourt, case-insensitive deduplication, "
                    "unsafe-character exclusion, uppercase-aware person-name "
                    "exclusion, personal-name exclusion including "
                    "initials+surname, register-artefact exclusion (mojibake, "
                    "administrative notes, placeholders, bare acronyms, "
                    "non-Latin spellings), deterministic SHA-256 sample"
                ),
                "licence_status": LICENCE,
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
    core = Path("platform/masking-gateway/src/masking_engine/app/core")
    parser.add_argument(
        "--output",
        type=Path,
        default=core / "surrogate_medical_organizations_fr.txt",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=core / "surrogate_medical_organizations_fr.metadata.json",
    )
    arguments = parser.parse_args()
    build(arguments.source, arguments.output, arguments.metadata)


if __name__ == "__main__":
    main()
