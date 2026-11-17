#!/usr/bin/env python3
"""Build the Dutch healthcare-organisation surrogate pool from CIBG releases."""

from __future__ import annotations

import argparse
import json
import re
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from xml.etree.ElementTree import iterparse

from build_dutch_organization_pool import (
    deterministic_sample,
    normalized_name,
    source_sha256,
)

POOL_SIZE = 10_000
_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_TABLE = f"{{{_TABLE_NS}}}table"
_ROW = f"{{{_TABLE_NS}}}table-row"
_CELL = f"{{{_TABLE_NS}}}table-cell"
_NAME = f"{{{_TABLE_NS}}}name"
_REPEAT = f"{{{_TABLE_NS}}}number-columns-repeated"
_MEDICAL_KEYWORDS = (
    "apotheek",
    "ambul",
    "begeleid",
    "care",
    "coach",
    "fysio",
    "gezond",
    "hulp",
    "huisarts",
    "jeugd",
    "kind",
    "kraam",
    "kliniek",
    "logoped",
    # "maatschappelijk werk" is social care; "maatschappij" is the ordinary
    # Dutch word for a company. Matching the shared prefix pulled 274 holding,
    # investment and construction firms into a healthcare pool.
    "maatschappelijk",
    "medisch",
    "ondersteun",
    "ortho",
    "praktijk",
    "psych",
    "revalid",
    "tand",
    "therap",
    "thuiszorg",
    "verpleeg",
    "verlos",
    "welzijn",
    "wonen",
    "zorg",
)
_SOURCE_URLS = {
    "digimv2020.zip": "https://www.jaarverantwoordingzorg.nl/site/binaries/site-content/collections/documents/2023/01/25/digimv-2020-definitieve-dataset/DigiMV2020_tot-en-met_20230121_ODS_MeerdereTabellen.zip",
    "digimv2021.zip": "https://www.jaarverantwoordingzorg.nl/site/binaries/site-content/collections/documents/2023/01/25/dataset-2021-zorg-jeugd-en-veilig-thuis/DigiMV2021_tot-en-met_20230121_ODS_MeerdereTabellen.zip",
    "digimv2023.ods": "https://www.jaarverantwoordingzorg.nl/site/binaries/site-content/collections/documents/2024/10/08/definitieve-dataset-2023/DigiMV2023_MultipleTables_20241001_0927.ods",
}


def _content_xml(source: Path) -> bytes:
    with ZipFile(source) as archive:
        if source.suffix == ".ods":
            return archive.read("content.xml")
        nested = next(name for name in archive.namelist() if name.endswith(".ods"))
        with ZipFile(BytesIO(archive.read(nested))) as ods:
            return ods.read("content.xml")


def organization_names(source: Path):
    """Yield safe organisation names from the RowData_01 sheet in *source*."""
    active = False
    name_column: int | None = None
    for event, element in iterparse(
        BytesIO(_content_xml(source)), events=("start", "end")
    ):
        if event == "start" and element.tag == _TABLE:
            active = element.get(_NAME) == "RowData_01"
        elif event == "end" and element.tag == _ROW and active:
            values: list[str] = []
            for cell in element.findall(_CELL):
                values.extend(
                    ["".join(cell.itertext()).strip()] * int(cell.get(_REPEAT, "1"))
                )
            if name_column is None:
                name_column = next(
                    index
                    for index, value in enumerate(values)
                    if value.endswith("NaamName")
                )
            elif len(values) > name_column:
                name = normalized_name(values[name_column])
                if name is not None:
                    yield name
            element.clear()
        elif event == "end" and element.tag == _TABLE and active:
            break


# Care words that also open the name of something that is not care. The
# keyword list cannot exclude these by itself: "kind" has to keep matching
# "kinderopvang", so a children's choir is turned away here instead.
_NON_MEDICAL_KEYWORDS = (
    "koor",
    "orkest",
    "muziek",
    "fanfare",
    "harmonie",
    "toneel",
    "interior",
    "meubel",
    "autohandel",
)

# Dutch compounds without spaces, so a keyword has to be allowed to match
# inside a word: "Ouderengeneeskundepraktijk" and "degraafzorg.nl" are a
# medical practice and a care provider, and requiring the keyword to open a
# word turned both away along with 800-odd others. Only the false friends get
# a stricter pattern.
_KEYWORD_PATTERNS = tuple(
    re.compile(r"(?<!s)tand" if keyword == "tand" else re.escape(keyword))
    for keyword in _MEDICAL_KEYWORDS
)


def _is_medical(name: str) -> bool:
    """Return True when *name* reads as a healthcare organisation.

    "tand", the stem of "tandarts", also sits inside "stand", which is how
    "By A interiors that stand out" reached a healthcare pool. It is the only
    keyword here that hides inside a common unrelated word, so it is the only
    one that carries a lookbehind.
    """
    lowered = name.casefold()
    if any(keyword in lowered for keyword in _NON_MEDICAL_KEYWORDS):
        return False
    return any(pattern.search(lowered) for pattern in _KEYWORD_PATTERNS)


def build(
    sources: list[Path], generic_pool: Path, destination: Path, metadata: Path
) -> None:
    names: dict[str, str] = {}
    for source in sources:
        for name in organization_names(source):
            names.setdefault(name.casefold(), name)

    official_count = len(names)
    supplements = [
        name
        for name in generic_pool.read_text(encoding="utf-8").splitlines()
        if name.casefold() not in names and _is_medical(name)
    ]
    for name in deterministic_sample(supplements, max(0, POOL_SIZE - official_count)):
        names[name.casefold()] = name
    if len(names) < POOL_SIZE:
        raise RuntimeError(f"expected {POOL_SIZE} usable names, found {len(names)}")

    selected = deterministic_sample(names.values(), POOL_SIZE)
    selected.sort(key=str.casefold)
    destination.write_text("\n".join(selected) + "\n", encoding="utf-8")
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "purpose": "Dutch healthcare-organisation pseudonymisation pool",
                "included_name_count": len(selected),
                "selection": "case-insensitive deduplication and unsafe-character/person-name exclusion; CIBG names supplemented only to the 10,000 floor with healthcare-keyword matches from the existing Dutch trade-name pool",
                "official_cibg_name_count": official_count,
                "source_licence": "CC0-1.0 for the CIBG DigiMV releases (https://data.overheid.nl/dataset/124ca8ff-66f6-4453-bf5d-b873cf411ed9)",
                "sources": [
                    {
                        "url": _SOURCE_URLS.get(source.name, "local source"),
                        "sha256": source_sha256(source),
                    }
                    for source in sources
                ],
                "supplement": "Healthcare-keyword matches from surrogate_organizations_nl.txt; retain its separate provenance and licence review.",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument(
        "--generic-pool",
        type=Path,
        default=Path(
            "platform/masking-gateway/src/masking_engine/app/core/surrogate_organizations_nl.txt"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "platform/masking-gateway/src/masking_engine/app/core/surrogate_medical_organizations_nl.txt"
        ),
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path(
            "platform/masking-gateway/src/masking_engine/app/core/surrogate_medical_organizations_nl.metadata.json"
        ),
    )
    arguments = parser.parse_args()
    build(
        arguments.sources, arguments.generic_pool, arguments.output, arguments.metadata
    )


if __name__ == "__main__":
    main()
