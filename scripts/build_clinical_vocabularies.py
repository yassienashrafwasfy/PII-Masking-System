#!/usr/bin/env python3
"""Build a locale's clinical surrogate vocabulary from Wikipedia categories.

`app/core/surrogate_vocabularies_nl.json` names its own sources as Wikipedia
category listings (`nl.wikipedia Categorie:Aandoening`, `Categorie:Symptoom`,
`Categorie:Geneesmiddel and its subcategories`, ...) rather than a downloadable
register archive, so this script follows the same source and the same method
for French and English: read the category's page titles through the MediaWiki
API, normalise them the way the Dutch file already is, and write the same
schema. Unlike `build_*_organization_pool.py`, there is no archive to fetch
first -- a Wikipedia category is queried live, the way the Dutch file's own
`sources` field already describes.

Usage:
    python scripts/build_clinical_vocabularies.py fr_FR \
        --output platform/masking-gateway/src/masking_engine/app/core/surrogate_vocabularies_fr.json
    python scripts/build_clinical_vocabularies.py en \
        --output platform/masking-gateway/src/masking_engine/app/core/surrogate_vocabularies_en.json
"""

from __future__ import annotations

import argparse
import json
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "pii-masking-clinical-vocab-builder/1.0"
REQUEST_DELAY_SECONDS = 1.5
MAX_RETRIES = 8

# Values matching this cannot survive a CSV field or a JSON string unmarked,
# the same invariant `surrogate_vocabularies_nl.json` already documents.
_UNSAFE_CHARACTERS = frozenset({",", ";"})
_MIN_LENGTH = 3
_MAX_LENGTH = 80
# A page cap per label, so a category whose subcategories run deep (French
# "Maladie" alone carries dozens of subcategories) cannot turn one build into
# an unbounded crawl.
_MAX_PAGES_PER_LABEL = 3000


@dataclass(frozen=True)
class CategorySource:
    """One label's Wikipedia source: a category, optionally one level deep.

    `include_subcategories` mirrors the Dutch file's own note for
    MEDICATION -- "Categorie:Geneesmiddel and its subcategories" -- because a
    medication category is organised by drug class and carries almost nothing
    directly. SYMPTOM and DIAGNOSIS follow the Dutch file's other entries,
    which name one category with no subcategory note.
    """

    label: str
    title: str
    include_subcategories: bool = False


LOCALE_SOURCES: dict[str, tuple[CategorySource, ...]] = {
    "fr_FR": (
        CategorySource(
            "MEDICATION", "Catégorie:Médicament", include_subcategories=True
        ),
        CategorySource("SYMPTOM", "Catégorie:Symptôme"),
        CategorySource("DIAGNOSIS", "Catégorie:Maladie"),
    ),
    "en": (
        CategorySource("MEDICATION", "Category:Drugs", include_subcategories=True),
        CategorySource("SYMPTOM", "Category:Symptoms and signs"),
        CategorySource("DIAGNOSIS", "Category:Diseases and disorders"),
    ),
}

WIKIPEDIA_HOST = {"fr_FR": "fr", "en": "en"}


def _api_call(host: str, params: dict[str, str]) -> dict:
    query = dict(params, format="json")
    url = f"https://{host}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    delay = 2.0
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and attempt < MAX_RETRIES - 1:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise
    raise RuntimeError("unreachable")


def _category_members(host: str, title: str, member_type: str) -> list[str]:
    """Return every member title the API reports for *title*, paginated."""
    titles: list[str] = []
    continuation: dict[str, str] = {}
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": title,
            "cmlimit": "500",
            "cmtype": member_type,
            **continuation,
        }
        data = _api_call(host, params)
        titles.extend(member["title"] for member in data["query"]["categorymembers"])
        if "continue" not in data:
            return titles
        continuation = {"cmcontinue": data["continue"]["cmcontinue"]}
        time.sleep(REQUEST_DELAY_SECONDS)


def _pages_for(host: str, source: CategorySource) -> list[str]:
    """Return the article titles for *source*, one category deep at most."""
    pages = list(_category_members(host, source.title, "page"))
    time.sleep(REQUEST_DELAY_SECONDS)
    if source.include_subcategories:
        for subcat in _category_members(host, source.title, "subcat"):
            if len(pages) >= _MAX_PAGES_PER_LABEL:
                break
            time.sleep(REQUEST_DELAY_SECONDS)
            pages.extend(_category_members(host, subcat, "page"))
            time.sleep(REQUEST_DELAY_SECONDS)
    return pages[:_MAX_PAGES_PER_LABEL]


# An index article ("List of antidepressants", "Liste de médicaments liés à
# la fertilité") names a set of terms rather than being one, so it is not a
# surrogate value in its own right the way "Sertraline" or "Migraine" is.
_INDEX_PAGE_PREFIXES = (
    "list of ",
    "lists of ",
    "liste de ",
    "liste des ",
    "liste modèle",
)


def _normalized(title: str) -> str | None:
    """Return *title* as a safe surrogate value, or None to drop it."""
    value = unicodedata.normalize("NFC", title).strip()
    if not _MIN_LENGTH <= len(value) <= _MAX_LENGTH:
        return None
    if any(character in _UNSAFE_CHARACTERS for character in value):
        return None
    if not any(character.isalpha() for character in value):
        return None
    if value.casefold().startswith(_INDEX_PAGE_PREFIXES):
        return None
    return value


def build_vocabulary(locale: str) -> dict[str, list[str]]:
    host = WIKIPEDIA_HOST[locale]
    vocabularies: dict[str, list[str]] = {}
    for source in LOCALE_SOURCES[locale]:
        seen: dict[str, str] = {}
        for title in _pages_for(host, source):
            normalized = _normalized(title)
            if normalized is not None:
                seen.setdefault(normalized.casefold(), normalized)
        vocabularies[source.label] = sorted(seen.values(), key=str.casefold)
    return vocabularies


def _source_lines(locale: str) -> list[str]:
    host = WIKIPEDIA_HOST[locale]
    lines = []
    for source in LOCALE_SOURCES[locale]:
        suffix = " and its subcategories" if source.include_subcategories else ""
        lines.append(f"{host}.wikipedia {source.title}{suffix}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("locale", choices=sorted(LOCALE_SOURCES))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    vocabularies = build_vocabulary(args.locale)
    document = {
        "schema_version": 1,
        "locale": args.locale,
        "note": (
            "Surrogate values for labels that Faker cannot generate. No value "
            "carries a comma or a semicolon, so a replacement survives a CSV "
            "field and a JSON string unchanged."
        ),
        "sources": _source_lines(args.locale),
        "vocabularies": vocabularies,
    }
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for label, values in vocabularies.items():
        print(f"{label}: {len(values)} values")


if __name__ == "__main__":
    main()
