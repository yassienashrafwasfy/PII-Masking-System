"""One coarse vocabulary for every team's labels and every benchmark's tags.

A team emits platform labels; a benchmark has its own tag set. Both are mapped to a
small set of coarse categories so one scorer compares them. Two kinds of label are
deliberately outside it:

* clinical predictions (MEDICATION, DIAGNOSIS, ...) are dropped before scoring: none
  of these benchmarks annotates clinical content as PHI, so keeping them would charge
  a team a false positive for doing what the platform asks of it;
* gold tags no team has a label for (a profession, a title, a sex in MEDDOCAN's case
  is the exception, it maps to GENDER) are category ``OTHER``. They still count in
  the type-agnostic recall, because an unmasked profession is a leak, but not in the
  typed score.
"""

from __future__ import annotations

NAME, DATE, AGE, ADDRESS, PHONE = "NAME", "DATE", "AGE", "ADDRESS", "PHONE"
EMAIL, URL, ORG, ID, GENDER, OTHER = "EMAIL", "URL", "ORG", "ID", "GENDER", "OTHER"

# Identifier labels the platform can emit (identity taxonomy plus the routing-only
# identifier labels): all are personal identifiers, so all score as ID.
_ID_LABELS = (
    "BTW_EENHEID RIZIV_INAMI RPPS INSZ_NISS NIR SSN NPI NHS_NUMBER GMC CNP COLEGIADO "
    "DNI LANR NIE PARAFA STEUER_ID BSN KVNR IBAN MEDICALRECORDNUM NATIONAL_ID"
).split()

PLATFORM_COARSE: dict[str, str] = {
    "NAME": NAME,
    "GENDER": GENDER,
    "DATE": DATE,
    "ORGANIZATION_NAME": ORG,
    "ADDRESS": ADDRESS,
    "AGE": AGE,
    "PHONE_NUMBER": PHONE,
    "EMAIL": EMAIL,
    "URL": URL,
    **{label: ID for label in _ID_LABELS},
}

# Not scored: see the module docstring.
CLINICAL_LABELS = frozenset(
    "MEDICATION SYMPTOM DIAGNOSIS ALLERGIES PRESCRIPTIONINFO TREATMENTINFO "
    "TESTRESULTS PROCEDURE".split()
)

# Labels the regex arbitration emits only to win an overlap against a false positive
# (a weight is not a number to mask); the platform does not mask them, so neither do we.
ARBITRATION_ONLY = frozenset({"HEIGHT", "WEIGHT"})

# gold tag -> (coarse category, predicted categories that count as the right type)
Mapping = dict[str, tuple[str, frozenset[str]]]


def _only(category: str) -> tuple[str, frozenset[str]]:
    return category, frozenset({category})


MEDDOCAN: Mapping = {
    "NOMBRE_SUJETO_ASISTENCIA": _only(NAME),
    "NOMBRE_PERSONAL_SANITARIO": _only(NAME),
    "FAMILIARES_SUJETO_ASISTENCIA": _only(NAME),
    "FECHAS": _only(DATE),
    "EDAD_SUJETO_ASISTENCIA": _only(AGE),
    "TERRITORIO": _only(ADDRESS),
    "CALLE": _only(ADDRESS),
    "PAIS": _only(ADDRESS),
    "SEXO_SUJETO_ASISTENCIA": _only(GENDER),
    "ID_SUJETO_ASISTENCIA": _only(ID),
    "ID_TITULACION_PERSONAL_SANITARIO": _only(ID),
    "ID_ASEGURAMIENTO": _only(ID),
    "ID_CONTACTO_ASISTENCIAL": _only(ID),
    "CORREO_ELECTRONICO": _only(EMAIL),
    "HOSPITAL": _only(ORG),
    "INSTITUCION": _only(ORG),
    "CENTRO_SALUD": _only(ORG),
    "NUMERO_TELEFONO": _only(PHONE),
    "NUMERO_FAX": _only(PHONE),
    "PROFESION": _only(OTHER),
    "OTROS_SUJETO_ASISTENCIA": _only(OTHER),
}

MULTIGRASCCO: Mapping = {
    "NAME_PATIENT": _only(NAME),
    "NAME_DOCTOR": _only(NAME),
    "NAME_RELATIVE": _only(NAME),
    "NAME_EXT": _only(NAME),
    "NAME_USERNAME": _only(NAME),
    "NAME_TITLE": _only(OTHER),
    "DATE": _only(DATE),
    "AGE": _only(AGE),
    "LOCATION_CITY": _only(ADDRESS),
    "LOCATION_ZIP": _only(ADDRESS),
    "LOCATION_STREET": _only(ADDRESS),
    "LOCATION_COUNTRY": _only(ADDRESS),
    "LOCATION_HOSPITAL": _only(ORG),
    "LOCATION_ORGANIZATION": _only(ORG),
    "ID": _only(ID),
    "CONTACT_PHONE": _only(PHONE),
    "CONTACT_FAX": _only(PHONE),
    "CONTACT_EMAIL": _only(EMAIL),
    "PROFESSION": _only(OTHER),
    "UNKNOWN": _only(OTHER),
}

# MedDeID's "Age_Birthdate" holds both ages and dates of birth, and "Contactdetails"
# holds phones, e-mails and URLs, so those two accept every category they contain.
MEDDEID: Mapping = {
    "Date": _only(DATE),
    "Age_Birthdate": (AGE, frozenset({AGE, DATE})),
    "Contactdetails": (PHONE, frozenset({PHONE, EMAIL, URL})),
    "Name:Patient": _only(NAME),
    "Name:Caregiver": _only(NAME),
    "Name:Other": _only(NAME),
    "Organization:Healthcare": _only(ORG),
    "Organization:Other": _only(ORG),
    "ID:Patient": _only(ID),
    "ID:Caregiver": _only(ID),
    "Address_Location:Patient": _only(ADDRESS),
    "Address_Location:Caregiver": _only(ADDRESS),
    "Address_Location:Other": _only(ADDRESS),
    "Profession": _only(OTHER),
}

# MedDeID subannotation categories that are identifying content. The rest
# (formatting, titles, medical or free context) is what the benchmark's
# "non-PII redaction" measures the removal of.
MEDDEID_CORE = frozenset(
    "given family initials internal_id public_id municipality street house_number "
    "postal_code public_phone internal_phone institution company hospital_location "
    "country province region year month day age_year age_month age_week age_day "
    "bus_number postal_office".split()
)
