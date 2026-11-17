"""The canonical PII entity taxonomy, as the arena needs to display it.

The source of truth is the masking gateway's
`platform/masking-gateway/src/masking_engine/app/core/taxonomy.json` --
schema `qa-pii-v1`, version 4 -- and `test_arena_labels.py` asserts this file
still matches it, the same way the gateway's own
`tests/test_qa_label_schema.py` pins that file.

**Vendored on purpose, not read from disk.** The API image copies only
`leaderboard_api/` and `samples/` (see `leaderboard_api/Dockerfile`), so
nothing under `evaluation/config/` or `platform/` exists at runtime --
`results.CONFIG_DIR` already points at a directory that is absent in
production, which is why `thresholds.yaml` silently falls back to its
hardcoded defaults there. A taxonomy loaded from either place would be
invisible in exactly the deployment that matters. Anyone tidying this into a
config file needs to fix the image first.

Three vocabularies exist in this repository and only this one is canonical:
`evaluation/config/entities.yaml` and the generated dataset manifests both use
an older lowercase set (`person_name`, `phone`, `national_id`, ...), which is
mapped in here rather than being allowed to reach the UI.
"""

from __future__ import annotations

# Display order, not just membership: the per-label view renders labels in this
# order, which is the taxonomy's own.
CANONICAL_LABELS: tuple[str, ...] = (
    "NAME",
    "GENDER",
    "DATE",
    "ORGANIZATION_NAME",
    "ADDRESS",
    "AGE",
    "PHONE_NUMBER",
    "BTW_EENHEID",
    "RIZIV_INAMI",
    "RPPS",
    "INSZ_NISS",
    "NIR",
    "URL",
    "EMAIL",
    "SSN",
    "NPI",
    "NHS_NUMBER",
    "GMC",
)

# Every spelling the taxonomy accepts, plus the legacy lowercase ids used by
# `evaluation/config/entities.yaml` and the generated dataset manifests. Keys
# are already normalised (upper, separators stripped) -- see `normalize`.
ALIASES: dict[str, str] = {
    "ADELI": "RPPS",
    "BUILDINGNUM": "ADDRESS",
    "CITY": "ADDRESS",
    "DATEOFBIRTH": "DATE",
    "DOB": "DATE",
    "DOCTORNAME": "NAME",
    "GIVENNAME": "NAME",
    "GMCNUMBER": "GMC",
    "HOSPITALNAME": "ORGANIZATION_NAME",
    "INAMI": "RIZIV_INAMI",
    "INSEE": "NIR",
    "INSZ": "INSZ_NISS",
    "NATIONALID": "INSZ_NISS",
    "NHS": "NHS_NUMBER",
    "NISS": "INSZ_NISS",
    "NPINUMBER": "NPI",
    "NSS": "NIR",
    "ORG": "ORGANIZATION_NAME",
    "ORGANIZATION": "ORGANIZATION_NAME",
    "PATIENTNAME": "NAME",
    "PHONE": "PHONE_NUMBER",
    "RIZIV": "RIZIV_INAMI",
    "SEX": "GENDER",
    "SOCIALSECURITYNUMBER": "SSN",
    "STREET": "ADDRESS",
    "TELEFOON": "PHONE_NUMBER",
    "TELEPHONENUM": "PHONE_NUMBER",
    "UNITNUMBER": "BTW_EENHEID",
    "VATNUMBER": "BTW_EENHEID",
    "ZIPCODE": "ADDRESS",
    # Not in the gateway taxonomy: the older evaluation vocabulary and the
    # spelling the arena's own masking preview used before it was corrected.
    "PERSON": "NAME",
    "PERSONNAME": "NAME",
}

# Entities whose recall is gated at 98% by `evaluation/config/thresholds.yaml`
# (`critical_entity_recall_min`), mapped onto canonical labels.
#
# `patient_id` is deliberately absent. entities.yaml marks it critical, but it
# has no canonical equivalent -- RIZIV_INAMI is the *prescriber's* number, not
# the patient's, and mapping one onto the other would silently report the wrong
# entity as gated. It needs a human decision, not a guess.
CRITICAL_LABELS: frozenset[str] = frozenset({"PHONE_NUMBER", "INSZ_NISS", "DATE"})

# Acronyms and compound ids that title-casing would mangle: "Insz Niss",
# "Riziv Inami", "Btw Eenheid", "Url".
_DISPLAY_OVERRIDES = {
    "INSZ_NISS": "INSZ / NISS",
    "RIZIV_INAMI": "RIZIV / INAMI",
    "BTW_EENHEID": "BTW eenheid",
    "URL": "URL",
    "RPPS": "RPPS",
    "NIR": "NIR",
    "ORGANIZATION_NAME": "Organisation",
    "PHONE_NUMBER": "Phone number",
}


# Canonical labels keyed the way `normalize` keys everything else, so
# "phone number", "PHONE-NUMBER" and "PhoneNumber" all reach PHONE_NUMBER.
_CANONICAL_BY_KEY = {label.replace("_", ""): label for label in CANONICAL_LABELS}


def normalize(raw: str) -> tuple[str, str | None, bool]:
    """Resolve one reported label to `(canonical, aliased_from, is_canonical)`.

    An unrecognised label is returned **unchanged and is never dropped**: if QA
    starts reporting an entity this taxonomy has not caught up with, it has to
    appear on the board flagged as off-taxonomy rather than silently vanish
    from a view whose whole purpose is showing which entities are weak.
    """
    if not raw:
        return "", None, False
    original = str(raw).strip()
    key = original.upper()
    for separator in (" ", "-", "_"):
        key = key.replace(separator, "")
    canonical = _CANONICAL_BY_KEY.get(key) or ALIASES.get(key)
    if canonical is None:
        return original.upper(), None, False
    # Only report an alias when the reported spelling really differed, so the
    # UI does not annotate every ordinary label with "aliased from itself".
    return canonical, (original if original.upper() != canonical else None), True


def display_name(label: str) -> str:
    """A human label for the UI, without mangling the acronyms."""
    if label in _DISPLAY_OVERRIDES:
        return _DISPLAY_OVERRIDES[label]
    return label.replace("_", " ").capitalize()


def describe(label: str) -> dict:
    """One taxonomy entry as the API publishes it."""
    return {
        "id": label,
        "display": display_name(label),
        "critical": label in CRITICAL_LABELS,
        "canonical": label in CANONICAL_LABELS,
    }


def taxonomy_payload() -> dict:
    """The whole taxonomy, for `GET /api/v1/config`.

    Published so the frontend takes ordering, display names and criticality
    from one place instead of keeping a second copy of the label list in JS.
    """
    return {
        "schema_name": "qa-pii-v1",
        "schema_version": 4,
        "labels": [describe(label) for label in CANONICAL_LABELS],
        "aliases": dict(sorted(ALIASES.items())),
    }
