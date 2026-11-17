"""Clinical values: blood group, BMI, height, weight and dose."""

from __future__ import annotations

import re

from ..core.schemas import MaskSpan
from .regex_rules import Overlap, Rule, collect

# A blood group is written without a space before the sign, which keeps a
# bulleted "O - " out of the match. Spelled and spaced forms need a cue.
_BLOOD_TYPE_RE = re.compile(r"(?<![\w+-])(?:AB|A|B|O)[+-](?![\w+-])")

_BLOOD_TYPE_CONTEXT_RE = re.compile(
    r"\b(?:blood\s*(?:group|type)|bloedgroep|groupe\s+sanguin)\b"
    r"[^\r\n\w]{0,10}(?P<value>(?:AB|A|B|O)\s?"
    r"(?:[+-]|pos(?:itief|itive|itif)?|neg(?:atief|ative|atif)?))(?![\w+-])",
    re.IGNORECASE,
)

# A blood group is not always written as a group. "Universal donor" names O
# negative and "universal recipient" names AB positive as surely as the symbols
# do, and the rhesus factor is routinely split off into its own clause. Neither
# form appears anywhere in the corpora, so the model has no signal for them
# either -- see docs/plan/corpus_rendering_variants.md.
_BLOOD_TYPE_PHRASE_HINT_RE = re.compile(
    r"\b(?:universal|universele|universel|rh|rhesus|group|groep|groupe)\b",
    re.IGNORECASE,
)

_BLOOD_TYPE_PHRASE_RE = re.compile(
    r"\b(?P<value>"
    r"universal\s+(?:donor|recipient|receiver)"
    r"|universele\s+(?:donor|ontvanger)"
    r"|donneur\s+universel|receveur\s+universel"
    r")\b",
    re.IGNORECASE,
)

# "Rhesus positive, group A" / "groep A, rhesus negatief" -- either order.
_BLOOD_TYPE_RHESUS_RE = re.compile(
    r"\b(?P<value>"
    r"(?:rh(?:esus)?\s*[- ]?\s*(?:pos(?:itief|itive|itif)?|neg(?:atief|ative|atif)?|[+-]))"
    r"[,;]?\s*(?:blood\s*)?(?:group|groep|groupe)\s+(?:AB|A|B|O)"
    r"|(?:blood\s*)?(?:group|groep|groupe)\s+(?:AB|A|B|O)[,;]?\s*"
    r"rh(?:esus)?\s*[- ]?\s*(?:pos(?:itief|itive|itif)?|neg(?:atief|ative|atif)?|[+-])"
    r")\b",
    re.IGNORECASE,
)

# The abbreviation alone reached only 45 of 412 spans in the corpus: clinical
# text overwhelmingly spells the index out, and does so per locale
# ("de body mass index bedraagt", "de queteletindex", "l'IMC").
_BMI_RE = re.compile(
    r"\b(?:BMI|IMC|QI|body\s+mass\s+index|queteletindex|quetelet\s*-?\s*index"
    r"|lichaamsmassa\s*-?\s*index|indice\s+de\s+masse\s+corporelle)\b"
    r"[^\r\n\d]{0,26}(?P<value>\d{1,2}(?:[.,]\d{1,2})?)(?!\d)",
    re.IGNORECASE,
)

# The compound unit is definitive on its own -- mass over area is an index, not
# a weight -- and it needs no cue. That matters because French writes the cue
# after the value ("19.0 kg/m2 d'IMC"), which a cue-before-value pattern cannot
# reach, leaving the number unclaimed for the weight cue to mis-grab.
_BMI_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9.,])(?P<value>\d{1,2}(?:[.,]\d{1,2})?)\s?kg\s*/\s*m[²2]",
    re.IGNORECASE,
)

# A measurement carries its own unit, so height and weight need no cue word --
# except where the unit is absent or ambiguous, which is what the *_CUE_RE
# patterns below cover.
#
# Imperial forms are en-GB reality (patients self-report in feet and stones) and
# were entirely undetectable before: "5 foot 9" and "12 stone 4" matched nothing.
_HEIGHT_RE = re.compile(
    r"(?<![A-Za-z0-9.,])(?:"
    # 182,0 cm / 182.0 centimetres -- a French chart writes height in cm with
    # a decimal-tenth fraction, which the plain integer-cm branch below never
    # reaches because it expects the unit right after the digits.
    r"\d{2,3}[.,]\d{1,2}\s?(?:cm\b|centimet(?:re|er)s?\b)"
    # 175 cm / 175 centimetres / 175 centimeter
    r"|\d{2,3}\s?(?:cm\b|centimet(?:re|er)s?\b)"
    # 1.75 m / 1,75 metres
    r"|\d[.,]\d{1,2}\s?(?:m\b|met(?:re|er)s?\b)"
    # 5'9" / 5' 9" / 5 ft 9 in / 5 foot 9 / 5ft9
    r"|\d\s?(?:'|′|ft\b|foot\b|feet\b)\s?\d{1,2}\s?(?:\"|″|in\b|inch(?:es)?\b)?"
    r")(?![A-Za-z0-9])"
)

# Unit-less after a cue: "height 175", "Ht: 175", "lengte 175", "taille 175".
_HEIGHT_CUE_RE = re.compile(
    r"\b(?:height|ht|lengte|grootte|taille)\b[^\r\n\d]{0,12}"
    r"(?P<value>\d{2,3}(?![.,]?\d)|\d[.,]\d{1,2})(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# "/" must be excluded after the unit or the kg of a BMI written "30.9 kg/m2"
# is masked as a body weight -- 412 false positives on the training corpus, one
# for every BMI span in it. Real notes write BMI with that unit routinely.
_WEIGHT_RE = re.compile(
    r"(?<![A-Za-z0-9.,])(?:"
    # 82 kg / 82.4 kg / 82 kilograms / 82 kilo -- "/" excluded so the kg of a
    # BMI written "30.9 kg/m2" is not taken as a body weight.
    r"\d{1,3}(?:[.,]\d)?\s?(?:kg\b|kilogram(?:me)?s?\b|kilo['s]{0,2}\b)(?![/])"
    r")(?![A-Za-z0-9/])"
)

# Imperial weight is cue-gated, not standalone: "st" is also the ordinal suffix
# of a date, so a bare pattern masks "1st December" and "21st May" as body
# weights. A stone reading essentially always sits beside a weight cue anyway.
_WEIGHT_IMPERIAL_CUE_RE = re.compile(
    r"\b(?:weight|weighs|weighing|wt)\b[^\r\n\d]{0,12}"
    r"(?P<value>\d{1,2}\s?(?:st\b|stones?\b)(?:\s?\d{1,2}\s?(?:lb\b|lbs\b|pounds?\b)?)?"
    r"|\d{1,3}\s?(?:lbs?\b|pounds?\b))(?![A-Za-z0-9/])",
    re.IGNORECASE,
)

# Grams are a real body weight in paediatrics ("birth weight 3450 g") but are
# also how a dose is written, so they need the cue -- see the disambiguation
# contract in docs/plan/corpus_rendering_variants.md.
_WEIGHT_GRAMS_CUE_RE = re.compile(
    r"\b(?:birth\s*weight|geboortegewicht|poids\s+de\s+naissance"
    r"|weight|gewicht|poids)\b[^\r\n\d]{0,12}"
    r"(?P<value>\d{3,4}(?:[.,]\d)?\s?(?:g\b|gr\b|gram(?:me)?s?\b))(?![A-Za-z0-9/])",
    re.IGNORECASE,
)

# Unit-less after a cue: "weight 82", "Wt: 82", "gewicht 82", "poids 82".
# Union of every cue word the four cued patterns below can start from. Used
# only as a cheap "is it worth running them" guard, so it needs no groups.
_BIOMETRIC_CUE_HINT_RE = re.compile(
    r"\b(?:height|ht|lengte|grootte|taille|weight|weighs|weighing|wt"
    r"|gewicht|weegt|poids|p[eè]se|geboortegewicht)\b",
    re.IGNORECASE,
)

_WEIGHT_CUE_RE = re.compile(
    r"\b(?:weight|weighs|weighing|wt|gewicht|weegt|poids|p[eè]se)\b[^\r\n\d]{0,12}"
    r"(?P<value>\d{1,3}(?:[.,]\d)?)(?![A-Za-z0-9/])",
    re.IGNORECASE,
)

# A unit followed by "/" and a second unit is a concentration or a rate,
# not a dose: "Hb 14,2 g/dL" is a laboratory result and "CRP 42 mg/L" is
# another. The trailing lookahead below already refused a letter or digit
# after the unit, but a "/" passed it, so the numerator alone -- "14,2 g",
# "42 mg" -- was emitted as a dose. The denominators listed here are the
# ones a clinical letter actually prints; a dose is written with no
# denominator at all ("500 mg", "1 g").
_CONCENTRATION_TAIL = r"(?!/(?:d?[lL]|m[lL]|[muµn]?mol|mmHg|mg|g|IU|U)(?![A-Za-z0-9]))"

_DOSE_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9.,])\d{1,4}(?:[.,]\d{1,2})?\s?"
    r"(?:mg|mcg|µg|ug|ml|IU|IE|g)(?![A-Za-z0-9])" + _CONCENTRATION_TAIL
)

_DOSE_SCHEDULE_RE = re.compile(
    r"(?<![\d.\-])\d{1,2}(?:-\d{1,2}){2,3}(?![\d.\-])"
    r"|\b\d{1,2}\s?dd\s?\d{1,2}\b"
    r"|\b\d{1,2}\s?x\s?(?:per\s?)?(?:dag|daags|week|jour|day)\b"
    r"|\b\d{1,2}\s+fois\s+par\s+(?:jour|semaine|mois)\b"
    r"|(?:\b\d{1,2}\s+(?:tabs?|caps?|comp(?:rim[eé]s?)?|cps?|cp)\s+)?"
    r"\b(?:od|bd|tds|qds|prn|nocte|mane)\b(?:\s+\d{1,3}\s+days?)?",
    re.IGNORECASE,
)


# Order is part of the contract: a rule sees every span the rules above it
# accepted, so the unit-bearing patterns are listed before the cue-only ones.
_CLINICAL_RULES: tuple[Rule, ...] = (
    Rule(_BLOOD_TYPE_RE, "BLOOD_TYPE"),
    Rule(_BLOOD_TYPE_CONTEXT_RE, "BLOOD_TYPE", "value"),
    # Both phrasal blood-group patterns require one of the hint words, so one
    # cheap scan decides whether either is worth running.
    Rule(
        _BLOOD_TYPE_RHESUS_RE,
        "BLOOD_TYPE",
        "value",
        overlap=Overlap.ANY_LABEL,
        hint=_BLOOD_TYPE_PHRASE_HINT_RE,
    ),
    Rule(
        _BLOOD_TYPE_PHRASE_RE,
        "BLOOD_TYPE",
        "value",
        overlap=Overlap.ANY_LABEL,
        hint=_BLOOD_TYPE_PHRASE_HINT_RE,
    ),
    Rule(_BMI_UNIT_RE, "BMI", "value", overlap=Overlap.SAME_LABEL),
    Rule(_BMI_RE, "BMI", "value", overlap=Overlap.SAME_LABEL),
    Rule(_HEIGHT_RE, "HEIGHT"),
    Rule(_WEIGHT_RE, "WEIGHT"),
    # Cue-only forms, emitted after the unit-bearing patterns. A cued value is
    # dropped when *any* measurement span already covers it, not merely one of
    # the same label: French puts the cue after the value ("176 cm de taille,
    # 59 kg de poids"), so a forward-looking height cue otherwise claims the
    # weight that follows it. The unit-bearing match is always the better
    # evidence, so whichever pattern already matched wins.
    #
    # Each is anchored on a cue word, so none can match when no cue word is
    # present. One cheap scan for the bare words decides whether the four
    # expensive ones are worth running -- worth it because the service accepts
    # bodies up to 10 MiB and every pattern otherwise walks the whole of one.
    Rule(
        _WEIGHT_GRAMS_CUE_RE,
        "WEIGHT",
        "value",
        overlap=Overlap.ANY_LABEL,
        hint=_BIOMETRIC_CUE_HINT_RE,
    ),
    Rule(
        _WEIGHT_IMPERIAL_CUE_RE,
        "WEIGHT",
        "value",
        overlap=Overlap.ANY_LABEL,
        hint=_BIOMETRIC_CUE_HINT_RE,
    ),
    Rule(
        _HEIGHT_CUE_RE,
        "HEIGHT",
        "value",
        overlap=Overlap.ANY_LABEL,
        hint=_BIOMETRIC_CUE_HINT_RE,
    ),
    Rule(
        _WEIGHT_CUE_RE,
        "WEIGHT",
        "value",
        overlap=Overlap.ANY_LABEL,
        hint=_BIOMETRIC_CUE_HINT_RE,
    ),
    Rule(_DOSE_UNIT_RE, "DOSAGE"),
    Rule(_DOSE_SCHEDULE_RE, "DOSAGE"),
)


def _clinical_spans(text: str) -> list[MaskSpan]:
    """Detect the measurements, blood groups and dosing notations."""
    return collect(_CLINICAL_RULES, text)
