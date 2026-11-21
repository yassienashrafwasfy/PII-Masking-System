"""Score the predictions `runner predict` wrote against the benchmarks' gold spans.

    uv run python -m evaluation.public_benchmarks.score --data ~/benchmark-data \
        --predictions out/ --out results.json [--markdown results.md]

Writes aggregates only: no document text, no per-document spans. The results file
records what produced it (git commit, dataset file digests) so a number can be traced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

from . import compare, loaders, scoring

ROOT = Path(__file__).resolve().parents[2]
HEADLINE = ("merged", "recall"), ("merged", "f1")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provenance(data: Path) -> dict:
    files = {
        "meddocan/meddocan.zip": data / "meddocan" / "meddocan.zip",
        "multigrascco/MultiGraSCCo_annotations_v2.zip": data
        / "multigrascco"
        / "ann_v2.zip",
        "meddeid-nl/data/test.jsonl": data / "meddeid-nl" / "data" / "test.jsonl",
    }
    try:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    return {
        "git_commit": commit,
        "dataset_sha256": {name: _sha256(p) for name, p in files.items() if p.exists()},
    }


def score(data: Path, predictions: Path, rounds: int) -> dict:
    documents = {(d.dataset, d.language, d.doc_id): d for d in loaders.load_all(data)}
    results: dict = {"provenance": provenance(data), "configs": {}}
    triples: dict[str, dict[str, dict[str, compare.Triple]]] = {}
    for file in sorted(predictions.glob("*.json")):
        run = json.loads(file.read_text())
        by_group: dict[str, list[Counter]] = {}
        unmapped: Counter[str] = Counter()
        locales: Counter[str] = Counter()
        seconds: dict[str, list[float]] = {}
        for row in run["documents"]:
            doc = documents[(row["dataset"], row["language"], row["doc_id"])]
            if not row["text_unchanged"]:
                raise ValueError(
                    f"{row['doc_id']}: the platform changed the text; offsets are void"
                )
            counts, missing = scoring.count_document(
                doc, [tuple(span) for span in row["spans"]]
            )
            group = f"{doc.dataset}-{doc.language}"
            by_group.setdefault(group, []).append(counts)
            triples.setdefault(run["config"], {}).setdefault(group, {})[
                row["doc_id"]
            ] = (
                counts["merged_tp"],
                counts["merged_fp"],
                counts["merged_fn"],
            )
            unmapped.update(missing)
            locales[f"{group}:{row['locale']}"] += 1
            seconds.setdefault(group, []).append(row["seconds"])
        groups = {}
        for group, per_document in sorted(by_group.items()):
            groups[group] = {
                **scoring.metrics(scoring.aggregate(per_document)),
                "ci95": scoring.bootstrap(per_document, rounds),
                "seconds_per_document": sum(seconds[group]) / len(seconds[group]),
            }
        results["configs"][run["config"]] = {
            "groups": groups,
            "resolved_locales": dict(locales),
            "unmapped_platform_labels": dict(unmapped),
        }
    results["comparison"] = compare.compare(triples, rounds)
    return results


def markdown(results: dict) -> str:
    """One table per benchmark group: headline recall and F1 first, strict typed beside."""
    groups = sorted({g for c in results["configs"].values() for g in c["groups"]})
    lines = []
    for group in groups:
        lines += [
            f"### {group}",
            "",
            "| Configuration | Docs | Recall (merged) | Precision (merged) | F1 (merged) | Char recall | Typed F1 (strict) |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for name, config in results["configs"].items():
            if group not in config["groups"]:
                continue
            m = config["groups"][group]
            ci = m["ci95"]["merged_recall"]
            lines.append(
                f"| {name} | {m['documents']} | {m['merged']['recall']:.3f} "
                f"({ci[0]:.3f}-{ci[1]:.3f}) | {m['merged']['precision']:.3f} | "
                f"{m['merged']['f1']:.3f} | {m['char_recall']:.3f} | {m['typed']['f1']:.3f} |"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args(argv)
    results = score(args.data, args.predictions, args.bootstrap)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    if args.markdown:
        args.markdown.write_text(markdown(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
