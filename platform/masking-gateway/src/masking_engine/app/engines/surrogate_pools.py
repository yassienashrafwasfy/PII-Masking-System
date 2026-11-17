"""Load the vocabularies and organisation-name pools a surrogate draws from."""

from __future__ import annotations

import json
from functools import cache
from itertools import islice
from pathlib import Path
from ..core import locales

_CORE_DIR = Path(__file__).resolve().parents[1] / "core"

# How many names are kept from any one organisation pool.
#
# The pools ship with up to 50,000 entries each, and every pool a process
# touches stays resident once loaded, so the full set held 15.4 MB. Keeping
# the first 20,000 brings that to roughly 6.7 MB.
#
# Variety is not the constraint this trades against. A pseudonym is picked by
# a seeded index into the pool, and a document naming a dozen organisations
# still draws those dozen from twenty thousand candidates. What it does change
# is *which* name a given seed selects, so organisation pseudonyms differ
# either side of this cap -- they are stable going forward, not stable against
# output masked before it.
_MAX_POOL_NAMES = 20_000


@cache
def _load_vocabularies(path: Path) -> dict[str, tuple[str, ...]]:
    """Return one locale's clinical term pools, read once per process.

    Cached on the path, the same reason `_load_organization_pool` below is
    cached on its: the pools are static build artefacts and a `MaskingRouter`
    is built per document, so an uncached read would re-parse a checked-in
    file on every span.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    return {label: tuple(values) for label, values in document["vocabularies"].items()}


@cache
def _load_organization_pool(path: Path) -> tuple[str, ...]:
    """Return a checked-in trade-name pool used for organisations.

    Cached on the path, which is what makes `organization_pool_for`'s promise
    of "read once per process" true. Without it this file was re-read and
    re-split for every document, because a `MaskingRouter` is built per
    document -- 13% of masking CPU on the sample corpus, spent re-reading a
    file checked into the image. The pools are static build artefacts, and the
    key set is one path per locale per kind, so the cache is bounded by the
    number of shipped pool files, and each entry by `_MAX_POOL_NAMES`.
    """
    names = (name for name in path.read_text(encoding="utf-8").splitlines() if name)
    return tuple(islice(names, _MAX_POOL_NAMES))


def organization_pool_for(locale: str, kind: str) -> tuple[str, ...]:
    """Return the *kind* organisation pool for *locale*, read once per process.

    Loaded on demand rather than at import: there is one pool file per locale
    per kind, and a deployment that only ever sees Dutch documents should not
    pay to read the French ones. Cached, so a deployment that sees both reads
    each file once.

    Empty when *kind* is a kind this locale has no curated register for.
    English carries a medical pool only, and a general request against it
    falls back to a seeded Faker company name rather than failing the
    document -- a missing register is a gap, not a caller error. A kind no
    locale defines at all still raises, because that one is a caller error.
    """
    profile = locales.profile(locale)
    if kind not in locales.POOL_KINDS:
        raise ValueError("organization_pool must be general or medical")
    filename = profile.organization_pools.get(kind)
    if filename is None:
        return ()
    return _load_organization_pool(_CORE_DIR / filename)


# The Dutch file is the oldest and most complete of the three, so it is the
# fallback every locale draws on for a label its own file has not been built
# for yet (see `vocabulary_for`). The union of every locale's own labels is
# the complete set a surrogate is ever allowed to ask for.
_DEFAULT_VOCABULARY_FILE = locales.profile(locales.DEFAULT_LOCALE).vocabulary_file

VOCABULARY_LABELS: frozenset[str] = frozenset(
    label
    for entry in locales.LOCALES.values()
    for label in _load_vocabularies(_CORE_DIR / entry.vocabulary_file)
)


def vocabulary_for(profile: locales.LocaleProfile, label: str) -> tuple[str, ...]:
    """Return *label*'s surrogate pool for *profile*'s locale.

    Takes the resolved `LocaleProfile` rather than a locale string: unlike
    `organization_pool_for`, which the router calls once per document, this
    is called once per span through `from_vocabulary`, and every caller
    already holds the profile on `SurrogateContext.locale`.

    Mirrors `organization_pool_for`'s fallback rule: a locale need not carry
    every label the taxonomy defines yet, so a label absent from *profile*'s
    own file still falls back to the Dutch pool rather than failing the
    document -- a missing register is a gap, not a caller error. A label no
    locale defines at all still raises, because that one is a caller error.
    """
    if label not in VOCABULARY_LABELS:
        raise KeyError(label)
    own = _load_vocabularies(_CORE_DIR / profile.vocabulary_file).get(label)
    if own is not None:
        return own
    return _load_vocabularies(_CORE_DIR / _DEFAULT_VOCABULARY_FILE)[label]
