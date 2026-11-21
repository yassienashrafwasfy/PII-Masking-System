"""Readers for the three open benchmarks. They read files you downloaded yourself.

The datasets are not in this repository: MEDDOCAN is built on real case reports, and
all three are CC BY 4.0 data with their own citations (see README.md). Every loader
checks that each gold span's text sits at its offsets in the document it read, and
fails loudly if one does not: a loader that shifts offsets silently turns every score
after the first mismatch into noise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import labels


@dataclass(frozen=True)
class Gold:
    start: int
    end: int
    tag: str  # the benchmark's own tag, for the per-type table
    category: str  # coarse category, labels.OTHER when no team has a label for it
    accept: frozenset[str]  # predicted categories that count as the right type


@dataclass
class Document:
    doc_id: str
    dataset: str
    language: str
    text: str
    gold: list[Gold]
    # (start, end) of identifying subannotations; only MedDeID has them.
    core: list[tuple[int, int]] = field(default_factory=list)


def _read_raw(path: Path) -> str:
    """Read without newline translation: brat offsets count the \\r of \\r\\n."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _gold(start: int, end: int, tag: str, mapping: labels.Mapping) -> Gold:
    if tag not in mapping:
        raise ValueError(f"gold tag {tag!r} has no mapping; add it to labels.py")
    category, accept = mapping[tag]
    return Gold(start, end, tag, category, accept)


def _check(doc_id: str, text: str, start: int, end: int, expected: str) -> None:
    if text[start:end] != expected:
        raise ValueError(
            f"{doc_id}: gold offsets {start}-{end} do not hold the gold text"
        )


def load_meddocan(root: Path, split: str = "test") -> list[Document]:
    """MEDDOCAN brat files, read with raw newlines (see `_read_raw`)."""
    folder = root / split / "brat"
    documents = []
    for annotation in sorted(folder.glob("*.ann")):
        text = _read_raw(annotation.with_suffix(".txt"))
        gold = []
        for line in _read_raw(annotation).splitlines():
            if not line.startswith("T"):
                continue
            _, meta, surface = line.split("\t", 2)
            tag, offsets = meta.split(" ", 1)
            if ";" in offsets:
                raise ValueError(f"{annotation.name}: discontinuous span")
            start, end = (int(part) for part in offsets.split())
            _check(annotation.stem, text, start, end, surface)
            gold.append(_gold(start, end, tag, labels.MEDDOCAN))
        documents.append(Document(annotation.stem, "meddocan", "es", text, gold))
    return documents


def load_multigrascco(root: Path, language: str) -> list[Document]:
    """MultiGraSCCo v2 `<Language>_PHI.json`: the direct identifiers, not the IPI file."""
    code = {"German": "de", "English": "en", "French": "fr"}[language]
    rows = json.loads((root / f"{language}_PHI.json").read_text(encoding="utf-8"))
    documents = []
    for row in rows:
        gold = []
        for entity in row["entities"]:
            _check(
                row["filename"],
                row["text"],
                entity["start"],
                entity["end"],
                entity["text"],
            )
            gold.append(
                _gold(
                    entity["start"], entity["end"], entity["type"], labels.MULTIGRASCCO
                )
            )
        documents.append(
            Document(row["filename"], "multigrascco", code, row["text"], gold)
        )
    return documents


def load_meddeid(path: Path) -> list[Document]:
    """MedDeID Dutch synthetic benchmark `data/test.jsonl`: 300 documents."""
    documents = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        gold, core = [], []
        for span in row["spans"]:
            _check(
                row["document_id"],
                row["text"],
                span["begin"],
                span["end"],
                span["text"],
            )
            gold.append(
                _gold(span["begin"], span["end"], span["label"], labels.MEDDEID)
            )
            for part in span.get("subannotations", []):
                if part["category"] in labels.MEDDEID_CORE:
                    core.append((part["begin"], part["end"]))
        documents.append(
            Document(row["document_id"], "meddeid", "nl", row["text"], gold, core)
        )
    return documents


def load_all(data_root: Path) -> list[Document]:
    """Every benchmark under *data_root*, laid out as the README describes."""
    documents = load_meddocan(data_root / "meddocan" / "meddocan")
    for language in ("German", "English", "French"):
        documents += load_multigrascco(
            data_root / "multigrascco" / "MultiGraSCCo_v2", language
        )
    documents += load_meddeid(data_root / "meddeid-nl" / "data" / "test.jsonl")
    return documents
