#!/usr/bin/env python3
"""Build the French organisation surrogate pool from the SIRENE stock file.

SIRENE is the French national business register, published by INSEE under the
Licence Ouverte v2.0. It is the French counterpart of the Dutch KVK trade-name
source behind `build_dutch_organization_pool.py`, so a French document's
`ORGANIZATION_NAME` surrogate reads like a French company rather than a Dutch
one.

The source archive is deliberately kept outside the repository. This script
retains only a fixed-size, deduplicated set of trade names suitable for
deterministic pseudonymisation, plus a provenance record beside the generated
pool.

Two SIRENE-specific exclusions matter and neither is optional:

* Only `denominationUniteLegale` is read. That column carries the registered
  name of a *personne morale* -- a company. A sole trader is registered under
  `nomUniteLegale`/`prenom1UniteLegale`, which is a real person's name, and
  the whole point of a surrogate pool is that nothing in it names anybody.
* Only rows whose `statutDiffusionUniteLegale` is `O` are read. INSEE marks a
  record `P` when the registrant restricted its publication, and a restricted
  record must not be copied into a file this repository ships.

Usage:
    python scripts/build_french_organization_pool.py \
        ~/.agent-reach/fr-pools/stock-unitelegale.zip
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import unicodedata
import zipfile
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from build_dutch_organization_pool import (
    deterministic_sample,
    normalized_name,
    source_sha256,
)

POOL_SIZE = 50_000
SOURCE_URL = (
    "https://static.data.gouv.fr/resources/"
    "base-sirene-des-entreprises-et-de-leurs-etablissements-siren-siret/"
    "20260901-083236/stock-stockunitelegale-csv.zip"
)
SOURCE_DATASET = (
    "https://www.data.gouv.fr/datasets/"
    "base-sirene-des-entreprises-et-de-leurs-etablissements-siren-siret"
)
SNAPSHOT_DATE = "2026-09-01"
LICENCE = "Licence Ouverte / Open Licence v2.0 (Etalab)"

_NAME_COLUMN = "denominationUniteLegale"
_DIFFUSION_COLUMN = "statutDiffusionUniteLegale"
_DIFFUSIBLE = "O"


@cache
def _french_given_names() -> frozenset[str]:
    """Return the French given names used to spot a person-shaped entry."""
    from faker.providers.person.fr_FR import Provider

    return frozenset(
        unicodedata.normalize("NFKD", name.casefold())
        .encode("ascii", "ignore")
        .decode()
        for name in Provider.first_names
    )


def _is_person_name(value: str) -> bool:
    """Return True when *value* reads as a person rather than a business.

    The general builder's `_PERSON_LIKE` is a no-op here: it requires Title
    Case, and SIRENE writes every denomination in capitals, so "JULES
    LARDIERE" sailed through a filter meant to stop exactly that. A surrogate
    pool that names a real person defeats its own purpose, so the check is
    redone on the shape SIRENE actually publishes.

    Only a *whole* name is rejected. "SARL AMBULANCE PHILIPPE ARDOUIN" is a
    business that contains a person's name and stays, the same way the Dutch
    filter only rejects an entry that is nothing but a name.
    """
    tokens = value.split()
    if not 2 <= len(tokens) <= 3:
        return False
    if not all(token.isalpha() for token in tokens):
        return False
    head = (
        unicodedata.normalize("NFKD", tokens[0].casefold())
        .encode("ascii", "ignore")
        .decode()
    )
    return head in _french_given_names()


def trade_names(source: Path):
    """Yield every publishable company name in the SIRENE stock archive."""
    with zipfile.ZipFile(source) as archive:
        member = next(
            info for info in archive.infolist() if info.filename.endswith(".csv")
        )
        with archive.open(member) as handle:
            stream = io.TextIOWrapper(handle, encoding="utf-8", newline="")
            reader = csv.reader(stream)
            header = next(reader)
            try:
                name_at = header.index(_NAME_COLUMN)
                diffusion_at = header.index(_DIFFUSION_COLUMN)
            except ValueError as exc:
                raise RuntimeError(
                    f"SIRENE export is missing an expected column: {exc}"
                ) from exc
            width = max(name_at, diffusion_at) + 1
            for row in reader:
                if len(row) < width or row[diffusion_at] != _DIFFUSIBLE:
                    continue
                if row[name_at]:
                    yield row[name_at]


def build(source: Path, destination: Path, metadata: Path) -> None:
    names: dict[str, str] = {}
    for raw in trade_names(source):
        name = normalized_name(raw)
        if name is not None and not _is_person_name(name):
            names.setdefault(name.casefold(), name)
        if len(names) % 500_000 == 0 and names:
            print(f"  {len(names):,} distinct names so far", file=sys.stderr)

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
                "purpose": "French organisation-name pseudonymisation pool",
                "source_url": SOURCE_URL,
                "source_dataset": SOURCE_DATASET,
                "source_snapshot_date": SNAPSHOT_DATE,
                "source_sha256": source_sha256(source),
                "included_name_count": len(selected),
                "selection": (
                    "denominationUniteLegale where statutDiffusionUniteLegale "
                    "is O, case-insensitive deduplication, unsafe-character "
                    "exclusion, uppercase-aware person-name exclusion, "
                    "personal-name exclusion including initials+surname, "
                    "register-artefact exclusion (mojibake, administrative "
                    "notes, placeholders, bare acronyms, non-Latin spellings), "
                    "deterministic SHA-256 sample"
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
        "--output", type=Path, default=core / "surrogate_organizations_fr.txt"
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=core / "surrogate_organizations_fr.metadata.json",
    )
    arguments = parser.parse_args()
    build(arguments.source, arguments.output, arguments.metadata)


if __name__ == "__main__":
    main()
