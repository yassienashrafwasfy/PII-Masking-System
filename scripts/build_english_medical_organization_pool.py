#!/usr/bin/env python3
"""Build the English healthcare-organisation surrogate pool.

There is one English locale rather than a British and an American one, so
there is one English pool, merged from both countries' open registers:

* **NHS ODS** -- the Organisation Data Service directory published by NHS
  England, reachable without a key and licensed under the Open Government
  Licence v3.0. It is the largest of the three registers this repository
  draws on: about 217,000 active organisations, from acute trusts to
  single-site GP practices.
* **CMS provider-data** -- the Centers for Medicare & Medicaid Services
  facility listings (hospitals, hospices, dialysis facilities, ambulatory
  surgical centres), a US Government work in the public domain.

It is the English counterpart of `build_dutch_medical_organization_pool.py`
and `build_french_medical_organization_pool.py`, so an English document's
`ORGANIZATION_NAME` surrogate reads like a hospital rather than like a
Flemish care team.

**Why both registers rather than the larger one.** The two English-speaking
populations this engine sees overlap: a US citizen treated in Britain and a
British citizen treated in the States both produce an English document, and
neither should be masked with an organisation that could only exist in the
other country. Drawing from the union costs nothing and means neither kind of
document looks out of place.

**Why the halves are capped rather than pooled.** NHS ODS outnumbers the CMS
listings roughly nine to one, so a straight sample of the union would be
essentially British. Each source therefore contributes at most half the pool
and a shortfall on one side is filled from the other, which keeps American
names present in useful numbers. Both halves are chosen by the same
deterministic digest sample as every other pool in this repository, and the
per-source counts are recorded in the metadata beside the pool.

The source snapshots are deliberately kept outside the repository. `--fetch`
writes them; the build then reads them by path, so a rebuild is reproducible
from a recorded snapshot rather than from whatever the registers say today.

Usage:
    python scripts/build_english_medical_organization_pool.py \
        --fetch ~/.agent-reach/en-pools

    python scripts/build_english_medical_organization_pool.py \
        ~/.agent-reach/en-pools/nhs-ods-organisations.json.gz \
        ~/.agent-reach/en-pools/cms-provider-facilities.csv.gz
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from build_dutch_organization_pool import (
    deterministic_sample,
    normalized_name,
    source_sha256,
)

POOL_SIZE = 50_000
# Half each, so the larger register cannot crowd the other out. A shortfall
# on either side is filled from the other in `build`.
PER_SOURCE_LIMIT = POOL_SIZE // 2

NHS_SOURCE_URL = (
    "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations"
    "?Status=Active&Limit=1000"
)
NHS_SOURCE_DATASET = "https://digital.nhs.uk/services/organisation-data-service"
NHS_LICENCE = "Open Government Licence v3.0 (NHS England, Organisation Data Service)"

CMS_METASTORE_URL = (
    "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items"
)
CMS_SOURCE_DATASET = "https://data.cms.gov/provider-data/"
CMS_LICENCE = "U.S. Government work, public domain (CMS provider data)"
# The facility listings that carry an establishment name. Matched on the
# dataset title the metastore publishes, so a renamed dataset fails loudly at
# fetch time rather than silently dropping a country's worth of names.
CMS_DATASETS = (
    "Hospital General Information",
    "Hospice - General Information",
    "Dialysis Facility - Listing by Facility",
    "Ambulatory Surgical Center Quality Measures - Facility",
)
# The name column, whichever of these a listing happens to use.
CMS_NAME_FIELDS = ("Facility Name", "facility_name", "Provider Name", "provider_name")

_TITLES = frozenset(
    {
        "DR",
        "DR.",
        "DOCTOR",
        "MR",
        "MR.",
        "MRS",
        "MRS.",
        "MS",
        "MS.",
        "MISS",
        "PROF",
        "PROF.",
        "PROFESSOR",
    }
)
# A practice registered under its partners keeps the partnership wording and
# is still, underneath it, somebody's name: "DR A B SMITH & PARTNERS".
_PARTNERSHIP_TAILS = (
    "& PARTNERS",
    "AND PARTNERS",
    "& PARTNER",
    "AND PARTNER",
    "& SONS",
    "AND SONS",
    "& SON",
    "AND SON",
)
# A word that makes an entry an institution rather than a person, however many
# given names it also contains: "MARY SEACOLE CENTRE" is a building and
# "STELLA ZWANENBERG" is somebody. Only the presence of one of these rescues a
# name-shaped entry, so the list can stay short without over-rejecting -- an
# entry of four words or more is never tested for being a person at all.
_INSTITUTION_WORDS = frozenset("""
    hospital hospitals hospice hospices surgery surgeries clinic clinics
    practice practices medical medicine health healthcare care caring centre
    center centres centers house lodge manor court hall grange villa home
    homes trust trusts foundation nhs unit units ward wards wing pharmacy
    pharmacies dental dentist opticians laboratory laboratories diagnostics
    imaging radiology dialysis kidney oncology cancer maternity nursing
    rehabilitation therapy therapies ambulance services service group holdings
    partnership associates consultants clinical community county district
    regional national international university college school academy
    institute society charity trustees limited ltd plc llp llc inc corp
    corporation company co pllc pa
    """.split())
# CMS publishes some facility names with the register's own code in front of
# them -- "032314 PHOENIX CHILDRENS HOSPITAL". The code is not part of the
# name, and a surrogate that opens with one reads as an identifier rather than
# as an organisation, so it is stripped rather than carried through.
_LEADING_CODE_RE = re.compile(r"^[0-9][0-9_-]{2,}[ _-]+")
# Entries the register has annotated rather than named. Both registers park a
# retired facility by prefixing its name -- "zz_closed_BRADENTON ARTIFICIAL
# KIDNEY CENTER", "zzz closed- DaVita New Kensington Dialysis" -- and the
# annotation is not part of any organisation's name.
_REGISTER_MARKER_RE = re.compile(
    # `z*closed` rather than `closed`: the marker arrives as "zz_closed",
    # "zzz closed" and "zCLOSED" in the same two registers.
    r"\bzz+\b|zz_|_closed|closed_|\bz*closed\b|\bdo not use\b"
    r"|\bnot in use\b|\bduplicate\b|\bdo not select\b",
    re.IGNORECASE,
)


@cache
def _english_given_names() -> frozenset[str]:
    """Return the English given names used to spot a person-shaped entry.

    Every English Faker locale, not just the two the pool is built from.
    `en_GB` and `en_US` between them carry 778 given names and do not include
    "Stella", which is how "ZWANENBERG STELLA VAN" -- a practitioner, not a
    practice -- reached a committed pool. All ten `en_*` providers carry 8,633
    and do, and a wider list only ever rejects more, which is the safe
    direction for a pool whose whole purpose is that it names nobody.
    """
    import importlib
    import pkgutil

    import faker.providers.person as person

    names: set[str] = set()
    for module in pkgutil.iter_modules(person.__path__):
        if not module.name.startswith("en"):
            continue
        provider = importlib.import_module(
            f"faker.providers.person.{module.name}"
        ).Provider
        names.update(
            unicodedata.normalize("NFKD", name.casefold())
            .encode("ascii", "ignore")
            .decode()
            for name in getattr(provider, "first_names", ())
        )
    return frozenset(names)


def _is_person_name(value: str) -> bool:
    """Return True when *value* reads as a person rather than an institution.

    The Dutch builder's `is_person_name` is nearly a no-op on these registers
    for the reason it was on SIRENE -- it wants Title Case, and NHS ODS writes
    every name in capitals -- and it does not know the two shapes an English
    register uses for a practice held by one practitioner: a title in front of
    the name ("DR J SMITH"), and initials in place of a given name
    ("A B SMITH", written without the dots the Dutch pattern expects).

    A pool whose whole purpose is that it names nobody must not carry either.
    As in the Dutch and French builders, only a *whole* name is rejected:
    "SMITH LANE MEDICAL CENTRE" is an institution that happens to contain a
    surname and stays.
    """
    stripped = value.upper().strip()
    for tail in _PARTNERSHIP_TAILS:
        if stripped.endswith(tail):
            stripped = stripped[: -len(tail)].strip()
            break
    tokens = stripped.split()
    if tokens and tokens[0] in _TITLES:
        # A title in front of anything at all is a practitioner's practice.
        return len(tokens) > 1
    if not 2 <= len(tokens) <= 3:
        return False
    if not all(token.isalpha() for token in tokens):
        return False
    folded = [
        unicodedata.normalize("NFKD", token.casefold())
        .encode("ascii", "ignore")
        .decode()
        for token in tokens
    ]
    if any(token in _INSTITUTION_WORDS for token in folded):
        return False
    if all(len(token) == 1 for token in tokens[:-1]):
        # Initials and a surname: "A B SMITH", "J JONES".
        return True
    # Any position, not just the first. NHS ODS also registers a practitioner
    # surname-first -- "ZWANENBERG STELLA VAN" -- so testing only the head
    # copied a real person's name into a pool whose whole purpose is that it
    # names nobody.
    return any(token in _english_given_names() for token in folded)


def _read_json(url: str):
    """Return the parsed body and the response headers.

    The headers come back as the mail message urllib builds rather than a
    plain dict, because the ODS pagination header arrives as `next-page` over
    HTTP/2 and `Next-Page` over HTTP/1.1, and only that object looks a header
    up without regard to case. A dict copy silently paginated once and
    stopped, which produced a thousand-name snapshot of a 217,000-name
    register.
    """
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "pii-pool-build/1"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        return json.load(response), response.headers


def fetch(directory: Path) -> tuple[Path, Path]:
    """Write a snapshot of both registers into *directory*, and name the files.

    Kept in this script rather than in a note somebody has to follow, so the
    pool can be rebuilt from the same two registers a year from now without
    guessing which endpoints were used.
    """
    directory.mkdir(parents=True, exist_ok=True)
    nhs_path = directory / "nhs-ods-organisations.json.gz"
    cms_path = directory / "cms-provider-facilities.csv.gz"

    print(f"NHS ODS -> {nhs_path}", file=sys.stderr)
    names: list[str] = []
    url: str | None = NHS_SOURCE_URL
    while url:
        payload, headers = _read_json(url)
        names.extend(
            entry["Name"] for entry in payload.get("Organisations", []) if entry["Name"]
        )
        url = headers.get("next-page")
        if len(names) % 20_000 < 1000:
            print(f"  {len(names)} organisations", file=sys.stderr)
    with gzip.open(nhs_path, "wt", encoding="utf-8") as stream:
        json.dump({"names": names}, stream)
    print(f"  {len(names)} organisations written", file=sys.stderr)

    print(f"CMS provider-data -> {cms_path}", file=sys.stderr)
    catalogue, _ = _read_json(CMS_METASTORE_URL)
    by_title = {item.get("title"): item for item in catalogue}
    rows: list[tuple[str, str]] = []
    for title in CMS_DATASETS:
        item = by_title.get(title)
        if item is None:
            raise RuntimeError(f"CMS metastore no longer publishes {title!r}")
        # The metastore returns a distribution either flat or wrapped in a
        # `data` object, depending on whether reference ids are expanded.
        distribution = item["distribution"][0]
        download = distribution.get("data", distribution)["downloadURL"]
        request = urllib.request.Request(
            download, headers={"User-Agent": "pii-pool-build/1"}
        )
        with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
            text = response.read().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(text.splitlines())
        field = next(
            (f for f in CMS_NAME_FIELDS if f in (reader.fieldnames or [])), None
        )
        if field is None:
            raise RuntimeError(f"no facility-name column in {title!r}")
        found = 0
        for row in reader:
            value = (row.get(field) or "").strip()
            if value:
                rows.append((title, value))
                found += 1
        print(f"  {title}: {found} facilities", file=sys.stderr)
    with gzip.open(cms_path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("dataset", "name"))
        writer.writerows(rows)
    print(f"  {len(rows)} facilities written", file=sys.stderr)
    return nhs_path, cms_path


def nhs_names(source: Path):
    """Yield every active organisation name in the NHS ODS snapshot."""
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        for name in json.load(stream)["names"]:
            yield name


def cms_names(source: Path):
    """Yield every facility name in the CMS snapshot."""
    with gzip.open(source, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            yield row["name"]


def _usable(raw_names) -> dict[str, str]:
    """Return the publishable names in *raw_names*, keyed case-insensitively."""
    names: dict[str, str] = {}
    for raw in raw_names:
        candidate = _LEADING_CODE_RE.sub("", raw.strip())
        if _REGISTER_MARKER_RE.search(candidate):
            continue
        name = normalized_name(candidate)
        if name is not None and not _is_person_name(name):
            names.setdefault(name.casefold(), name)
    return names


def build(nhs: Path, cms: Path, destination: Path, metadata: Path) -> None:
    british = _usable(nhs_names(nhs))
    american = _usable(cms_names(cms))
    # The same name in both registers stays on the American side, which is
    # the smaller one and therefore the one a duplicate would cost most.
    for key in american:
        british.pop(key, None)

    from_cms = deterministic_sample(american.values(), PER_SOURCE_LIMIT)
    from_nhs = deterministic_sample(british.values(), POOL_SIZE - len(from_cms))
    if len(from_nhs) + len(from_cms) < POOL_SIZE:
        # One side came up short, so the other fills the gap.
        from_cms = deterministic_sample(american.values(), POOL_SIZE - len(from_nhs))
    selected = from_nhs + from_cms
    if len(selected) != POOL_SIZE:
        raise RuntimeError(
            f"expected {POOL_SIZE} usable names, found {len(selected)} "
            f"({len(from_nhs)} NHS ODS, {len(from_cms)} CMS)"
        )
    selected.sort(key=str.casefold)
    destination.write_text("\n".join(selected) + "\n", encoding="utf-8")
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "purpose": "English healthcare-organisation pseudonymisation pool",
                "sources": [
                    {
                        "name": "NHS ODS (Organisation Data Service)",
                        "source_url": NHS_SOURCE_URL,
                        "source_dataset": NHS_SOURCE_DATASET,
                        "source_sha256": source_sha256(nhs),
                        "included_name_count": len(from_nhs),
                        "licence_status": NHS_LICENCE,
                    },
                    {
                        "name": "CMS provider-data facility listings",
                        "source_url": CMS_METASTORE_URL,
                        "source_dataset": CMS_SOURCE_DATASET,
                        "source_datasets": list(CMS_DATASETS),
                        "source_sha256": source_sha256(cms),
                        "included_name_count": len(from_cms),
                        "licence_status": CMS_LICENCE,
                    },
                ],
                "source_snapshot_date": datetime.now(UTC).date().isoformat(),
                "included_name_count": len(selected),
                "selection": (
                    "active organisation and facility names, leading "
                    "register-code strip, case-insensitive deduplication "
                    "across both registers, unsafe-character exclusion, "
                    "register-artefact exclusion (mojibake, administrative "
                    "notes, placeholders, bare acronyms, closed-facility "
                    "markers), English person-name exclusion including "
                    "title+name, initials+surname, partnership forms and a "
                    "given name in any position, deterministic SHA-256 "
                    "sample capped at half the pool per register"
                ),
                "generated_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    core = Path("platform/masking-gateway/src/masking_engine/app/core")
    parser = argparse.ArgumentParser()
    parser.add_argument("nhs", type=Path, nargs="?")
    parser.add_argument("cms", type=Path, nargs="?")
    parser.add_argument(
        "--fetch",
        type=Path,
        metavar="DIRECTORY",
        help="download a snapshot of both registers into DIRECTORY and exit",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=core / "surrogate_medical_organizations_en.txt",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=core / "surrogate_medical_organizations_en.metadata.json",
    )
    arguments = parser.parse_args()
    if arguments.fetch is not None:
        fetch(arguments.fetch)
        return
    if arguments.nhs is None or arguments.cms is None:
        parser.error("both source paths are required unless --fetch is given")
    build(arguments.nhs, arguments.cms, arguments.output, arguments.metadata)


if __name__ == "__main__":
    main()
