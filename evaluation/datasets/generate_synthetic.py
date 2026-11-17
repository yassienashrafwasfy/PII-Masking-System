"""Generate the synthetic PII benchmark corpus.

Records are synthetic by construction and name nobody. Two shapes are
available: the original fixed-list records, which every committed manifest was
generated from and which therefore stay the default, and Faker-generated
records selected with `--locale`.

`--locale en_GB` / `en_US` exist because the English surrogate locale needed
fixtures that were *generated* rather than hand-written, and because the
English-language identifiers the taxonomy gained -- SSN, NPI, NHS number, GMC
-- appear in no other corpus. Both English dialects are offered here even
though masking has a single `en` locale: the point of a fixture is to be the
document the engine will meet, and it will meet documents from both countries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

NAMES = ["Nadia Farouk", "Omar Hassan", "Salma Adel", "Youssef Nabil", "Mariam Fawzy"]
STREETS = [
    "14 Nile Street, Giza",
    "8 Lotus Road, Cairo",
    "21 Corniche Avenue, Alexandria",
]
FAKER_LOCALES = ("en_GB", "en_US", "nl_BE", "fr_FR")


def _nhs_number(rng: random.Random) -> str:
    """Return a mod-11-valid NHS number, so the fixture is what it claims."""
    while True:
        body = f"{rng.randint(1, 9)}{rng.randrange(10**8):08d}"
        total = sum(int(char) * (10 - index) for index, char in enumerate(body))
        check = 11 - total % 11
        if check == 11:
            check = 0
        elif check == 10:
            continue
        return f"{body[:3]} {body[3:6]} {body[6:]}{check}"


def _npi(rng: random.Random) -> str:
    """Return a Luhn-valid NPI, checked over the 80840 issuer prefix."""
    body = f"{rng.choice((1, 2))}{rng.randrange(10**8):08d}"
    total = 0
    for index, character in enumerate(reversed(f"80840{body}")):
        digit = int(character)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return f"{body}{(10 - total % 10) % 10}"


def make_faker_record(
    index: int, rng: random.Random, locale: str
) -> tuple[str, list[dict[str, str]]]:
    """Return one generated record and the entities it contains.

    Seeded from *rng* so a run is reproducible from `--seed` alone, the same
    guarantee the fixed-list records give.
    """
    from faker import Faker

    faker = Faker(locale)
    faker.seed_instance(rng.randrange(2**32))
    american = locale == "en_US"

    name = faker.name()
    address = f"{faker.street_address()}, {faker.city()} {faker.postcode()}"
    phone = faker.phone_number()
    email = faker.email()
    patient_id = f"PT-{2026}-{index:05d}"
    born = faker.date_of_birth(minimum_age=18, maximum_age=90)
    # Written the way each country writes it, which is the point of the
    # fixture: the parser has to read both orders.
    dob = born.strftime("%m/%d/%Y") if american else born.strftime("%d/%m/%Y")
    seen = faker.date_between(start_date="-2y")
    consultation = (
        seen.strftime("%B %-d, %Y") if american else seen.strftime("%-d %B %Y")
    )
    organization = faker.company()
    entities = [
        {"type": "person_name", "value": name},
        {"type": "email", "value": email},
        {"type": "phone", "value": phone},
        {"type": "patient_id", "value": patient_id},
        {"type": "date_of_birth", "value": dob},
        {"type": "date", "value": consultation},
        {"type": "address", "value": address},
        {"type": "organization_name", "value": organization},
    ]
    if american:
        identifier, provider = faker.ssn(), _npi(rng)
        identifier_line = f"SSN: {identifier}\nNPI: {provider}\n"
        entities += [
            {"type": "ssn", "value": identifier},
            {"type": "npi", "value": provider},
        ]
    else:
        identifier, provider = _nhs_number(rng), f"{rng.randrange(10**7):07d}"
        identifier_line = f"NHS number: {identifier}\nGMC number: {provider}\n"
        entities += [
            {"type": "nhs_number", "value": identifier},
            {"type": "gmc", "value": provider},
        ]
    text = (
        "SYNTHETIC CLINICAL INTAKE — NOT A REAL PATIENT\n\n"
        f"Patient: {name}\nPatient ID: {patient_id}\nDate of birth: {dob}\n"
        f"{identifier_line}"
        f"Phone: {phone}\nEmail: {email}\n"
        f"Address: {address}\n"
        f"Seen at: {organization} on {consultation}\n\n"
        "Clinical note: Routine synthetic follow-up record."
    )
    return text, entities


def make_record(index: int, rng: random.Random) -> tuple[str, list[dict[str, str]]]:
    name = rng.choice(NAMES)
    first = name.split()[0].lower()
    phone = f"+20 10 {rng.randint(1000, 9999)} {rng.randint(1000, 9999)}"
    national_id = f"30{rng.randint(100000000000, 999999999999)}"
    patient_id = f"PT-{2026}-{index:05d}"
    email = f"{first}.{index}@example.test"
    dob = f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1960, 2004)}"
    address = rng.choice(STREETS)
    entities = [
        {"type": "person_name", "value": name},
        {"type": "email", "value": email},
        {"type": "phone", "value": phone},
        {"type": "national_id", "value": national_id},
        {"type": "patient_id", "value": patient_id},
        {"type": "date_of_birth", "value": dob},
        {"type": "address", "value": address},
    ]
    text = (
        "SYNTHETIC CLINICAL INTAKE — NOT A REAL PATIENT\n\n"
        f"Patient: {name}\nPatient ID: {patient_id}\nDate of birth: {dob}\n"
        f"National ID: {national_id}\nPhone: {phone}\nEmail: {email}\n"
        f"Address: {address}\n\nClinical note: Routine synthetic follow-up record."
    )
    return text, entities


def write_pdf(path: Path, text: str) -> None:
    canvas = Canvas(str(path), pagesize=A4)
    canvas.setTitle("Synthetic PII benchmark")
    text_object = canvas.beginText(48, 790)
    text_object.setLeading(22)
    for line in text.splitlines():
        text_object.textLine(line)
    canvas.drawText(text_object)
    canvas.save()


def build_split(
    root: Path,
    split: str,
    count: int,
    rng: random.Random,
    locale: str | None = None,
) -> None:
    split_dir = root / split
    docs_dir = split_dir / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for index in range(1, count + 1):
        offset = index + (1000 if split == "private" else 0)
        text, entities = (
            make_faker_record(offset, rng, locale)
            if locale
            else make_record(offset, rng)
        )
        pdf_path = docs_dir / f"synthetic-intake-{index:03d}.pdf"
        write_pdf(pdf_path, text)
        digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        records.append(
            {
                "document_id": f"{split}-{index:03d}",
                "path": str(pdf_path.relative_to(split_dir)),
                "sha256": digest,
                "mime_type": "application/pdf",
                "mask_token": "[MASKED]",
                "entities": entities,
            }
        )
    with (split_dir / "manifest.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("datasets/generated"))
    parser.add_argument("--seed", type=int, default=20260803)
    parser.add_argument("--public-count", type=int, default=12)
    parser.add_argument("--private-count", type=int, default=30)
    parser.add_argument(
        "--locale",
        choices=FAKER_LOCALES,
        default=None,
        help=(
            "generate records with Faker in this locale instead of the fixed"
            " lists. Omit it to reproduce the committed manifests exactly."
        ),
    )
    args = parser.parse_args()
    rng = random.Random(args.seed)
    build_split(args.output, "public", args.public_count, rng, args.locale)
    build_split(args.output, "private", args.private_count, rng, args.locale)
    print(f"Generated synthetic benchmark at {args.output}")


if __name__ == "__main__":
    main()
