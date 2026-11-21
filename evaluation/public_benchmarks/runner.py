"""Run a team's real detector over benchmark documents and score the spans.

`predict` is the worker: one process per team and configuration, because the shared
engine reads its configuration from the environment once and a second team in the
same process would inherit the first one's. It calls what `platform/model-service`
calls, in the order it calls it (`prepare_document`, `document_verdict`,
`_detected_spans`), with the environment its `compose.yaml` service sets, so the spans
are the ones a gateway request would have produced. No locale override is passed:
the document language is detected, and recorded, so a team that falls back to another
checkpoint for a language shows up in the results.

Predictions are written as offsets and labels only, never text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEAMS = ("octo", "synthify", "berts", "mdeberta")
# configuration name -> (team, extra environment). `model_only` switches the
# regex layer and arbitration off, which is what a model card describes.
CONFIGS: dict[str, tuple[str, dict[str, str]]] = {
    **{team: (team, {}) for team in TEAMS},
    "mdeberta-model-only": ("mdeberta", {"DETECTION_MODE": "model"}),
}


def team_environment(team: str) -> dict[str, str]:
    """The environment `compose.yaml` gives this team's model service, host paths resolved."""
    import yaml

    service = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"][
        f"{team}-model"
    ]
    mounts = {}
    for volume in service.get("volumes", []):
        host, container = volume.split(":")[:2]
        mounts[container] = (ROOT / host).resolve()
    env = {}
    for key, value in service["environment"].items():
        value = re.sub(r"\$\{[A-Z_0-9]+:-([^}]*)\}", r"\1", str(value))
        if not value:
            continue
        env[key] = (
            str(mounts.get(value, value)) if value.startswith("/models") else value
        )
    env.update(
        TEAM_ID=team,
        MODEL_IDLE_TIMEOUT_SECONDS="0",  # never unload between documents
        LOG_LEVEL="WARNING",
        PDF_OCR="off",
    )
    return env


def predict(config: str, documents: list, out: Path) -> None:
    team, extra = CONFIGS[config]
    os.environ.update(team_environment(team))
    os.environ.update(extra)
    for path in (
        ROOT,
        ROOT / "platform",
        ROOT / "platform/masking-gateway/src",
        ROOT / "platform/model-service/src",
    ):
        sys.path.insert(0, str(path))
    import service  # noqa: PLC0415 (after the environment is set, on purpose)
    from masking_engine import document_verdict, prepare_document  # noqa: PLC0415

    rows = []
    started = time.monotonic()
    for index, doc in enumerate(documents, 1):
        begin = time.monotonic()
        prepared = prepare_document(doc.text.encode("utf-8"), "text/plain")
        # The platform may normalise text; offsets are only valid if it did not.
        same_text = len(prepared.texts) == 1 and prepared.texts[0].text == doc.text
        verdict = document_verdict(prepared, None)
        spans, _ = service._detected_spans(
            prepared, "text/plain", "onnx", None, verdict.locale
        )
        rows.append(
            {
                "doc_id": doc.doc_id,
                "dataset": doc.dataset,
                "language": doc.language,
                "locale": verdict.locale,
                "text_unchanged": same_text,
                "seconds": round(time.monotonic() - begin, 3),
                "spans": [[s.label, s.start, s.end] for s in spans],
            }
        )
        if index % 25 == 0:
            print(
                f"{config}: {index}/{len(documents)} ({time.monotonic() - started:.0f}s)",
                flush=True,
            )
    out.write_text(json.dumps({"config": config, "documents": rows}))


def _documents(args: argparse.Namespace) -> list:
    from . import loaders

    documents = loaders.load_all(Path(args.data))
    if args.datasets:
        documents = [
            d for d in documents if f"{d.dataset}-{d.language}" in args.datasets
        ]
    if args.limit:
        by_group: dict[str, list] = {}
        for doc in documents:
            by_group.setdefault(f"{doc.dataset}-{doc.language}", []).append(doc)
        documents = [d for group in by_group.values() for d in group[: args.limit]]
    return documents


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser(
        "predict", help="run one configuration; writes <out>/<config>.json"
    )
    run.add_argument("--config", choices=sorted(CONFIGS), required=True)
    run.add_argument(
        "--data",
        required=True,
        help="folder holding meddocan/, multigrascco/, meddeid-nl/",
    )
    run.add_argument("--out", required=True)
    run.add_argument(
        "--limit", type=int, default=0, help="documents per benchmark (0 = all)"
    )
    run.add_argument("--datasets", nargs="*", help="e.g. meddocan-es multigrascco-de")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    predict(args.config, _documents(args), out / f"{args.config}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
