#!/usr/bin/env python3
"""Convert the PP-OCRv6_small artifacts to ONNX for the shared OCR engine.

This is the only export script left in the repository. The team graphs under
`teams/<team>/onnx/` are exported from checkpoints held outside it -- see
`docs/decisions/005-onnx-only-serving.md` -- while OCR is platform-owned and
its source artifacts are a published PaddleOCR release, so this one stays.

`paddle2onnx` and `paddlepaddle` are in no dependency group on purpose.
`paddlepaddle` is a ~400 MB wheel with platform-specific distributions, and
adding it would put it in `uv.lock`, which `uv lock --check` (ci/build.yml)
then enforces on every job and every resolution. Run it under `--with`:

    uv run --with "paddlepaddle==3.*" --with "paddle2onnx==2.*" \\
           --with "onnx==1.18.0" --with "onnxruntime==1.28.0" --with pyyaml \\
           python scripts/export_paddle_ocr_onnx.py --component all \\
           --source <dir holding the extracted *_infer directories>

The verification below is the point of the script. Converting is three lines;
what earns its keep is refusing to write a manifest until the artifact has
proved it does what the engine assumes. One of those assumptions was already
wrong: PP-OCRv6 ships DB thresholds of 0.2 / 0.45 / 1.4 where v4 and v5 used
0.3 / 0.6 / 1.5, and nothing but this check would have caught it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from hashlib import sha256
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPO_ROOT / "platform/masking-gateway/src/masking_engine/src/models/ocr/artifacts"
)
OPSET = 19

# What the engine hardcodes. The tarball must agree, or the engine is reading
# the page with the wrong knobs and no test would show it.
EXPECTED_DET_POSTPROCESS = {"thresh": 0.2, "box_thresh": 0.45, "unclip_ratio": 1.4}
EXPECTED_DET_MEAN = [0.485, 0.456, 0.406]
EXPECTED_DET_STD = [0.229, 0.224, 0.225]
EXPECTED_REC_HEIGHT = 48


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:  # pragma: no cover - build-time only
        raise SystemExit(
            "PyYAML is required to read inference.yml; add --with pyyaml"
        ) from None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _transform(config: dict, name: str) -> dict | None:
    """Return the named PreProcess op's parameters, if present."""
    for entry in config.get("PreProcess", {}).get("transform_ops", []):
        if name in entry:
            return entry[name] or {}
    return None


def check_detection_config(source: Path) -> dict:
    """Assert the tarball's detection knobs match what the engine assumes."""
    config = _load_yaml(source / "inference.yml")
    post = config.get("PostProcess", {})
    for key, expected in EXPECTED_DET_POSTPROCESS.items():
        actual = post.get(key)
        if actual != expected:
            raise SystemExit(
                f"detection {key} is {actual!r}, but the engine assumes "
                f"{expected!r}. Update db_postprocess.py and ocr_engine.py "
                "together with this constant, then re-run."
            )
    normalize = _transform(config, "NormalizeImage") or {}
    if normalize.get("mean") != EXPECTED_DET_MEAN:
        raise SystemExit(f"detection mean changed: {normalize.get('mean')!r}")
    if normalize.get("std") != EXPECTED_DET_STD:
        raise SystemExit(f"detection std changed: {normalize.get('std')!r}")
    return post


def extract_charset(source: Path, destination: Path) -> int:
    """Write the recognition dictionary beside the model, one entry per line.

    A plain Git blob, not LFS: no `.gitattributes` rule matches `.txt`, so it
    is present in every checkout and every build context with no `git lfs
    pull`. It also stays diffable in review, which matters -- a silent charset
    change is a silent change to every masked document.
    """
    config = _load_yaml(source / "inference.yml")
    entries = config.get("PostProcess", {}).get("character_dict")
    if not entries:
        raise SystemExit("inference.yml carries no character_dict")
    shape = _transform(config, "RecResizeImg") or {}
    height = (shape.get("image_shape") or [0, 0, 0])[1]
    if height != EXPECTED_REC_HEIGHT:
        raise SystemExit(
            f"recognition height is {height}, but preprocess.REC_HEIGHT is "
            f"{EXPECTED_REC_HEIGHT}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return len(entries)


def convert(source: Path, output: Path) -> None:
    """Run paddle2onnx over one PIR-format inference directory."""
    model = (
        "inference.json"
        if (source / "inference.json").is_file()
        else "inference.pdmodel"
    )
    if not (source / model).is_file():
        raise SystemExit(f"no inference model found under {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "paddle2onnx",
        "--model_dir",
        str(source),
        "--model_filename",
        model,
        "--params_filename",
        "inference.pdiparams",
        "--save_file",
        str(output),
        "--opset_version",
        str(OPSET),
        "--enable_onnx_checker",
        "True",
    ]
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError:
        raise SystemExit(
            "paddle2onnx is not installed; see this script's docstring for the "
            "uv run --with invocation"
        ) from None


def verify_detection(path: Path) -> None:
    """Prove the DB head returns a map at the input's own resolution.

    If it does not, every box coordinate downstream is wrong by the head's
    stride -- a silent four-fold scale error that puts every redaction in the
    wrong place while still producing plausible-looking boxes.
    """
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name
    output = session.run(None, {name: np.zeros((1, 3, 64, 64), np.float32)})[0]
    if output.ndim != 4 or output.shape[2:] != (64, 64):
        raise SystemExit(
            f"detection output is {output.shape}, expected (1, 1, 64, 64): the "
            "head does not upsample to the input resolution"
        )


def verify_recognition(path: Path, charset_entries: int) -> dict:
    """Prove the batch and width axes are dynamic, and pin the class count."""
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name
    narrow = session.run(None, {name: np.zeros((1, 3, 48, 160), np.float32)})[0]
    wide = session.run(None, {name: np.zeros((2, 3, 48, 320), np.float32)})[0]
    if narrow.shape[-1] != wide.shape[-1]:
        raise SystemExit("recognition class count varies with input; graph is wrong")
    if wide.shape[0] != 2 or wide.shape[1] <= narrow.shape[1]:
        raise SystemExit(
            f"recognition axes are not dynamic: {narrow.shape} vs {wide.shape}"
        )
    classes = int(narrow.shape[-1])
    if classes not in (charset_entries + 1, charset_entries + 2):
        raise SystemExit(
            f"{charset_entries} charset entries cannot produce {classes} classes"
        )
    row = narrow[0, 0]
    is_softmax = bool(row.min() >= 0.0 and abs(float(row.sum()) - 1.0) < 1e-3)
    return {
        "output_classes": classes,
        "charset_has_appended_space": classes == charset_entries + 2,
        "rec_output_is_softmax": is_softmax,
    }


def write_manifest(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def export(component: str, source_root: Path, output_root: Path) -> dict:
    source = source_root / f"PP-OCRv6_small_{component}_infer"
    if not source.is_dir():
        raise SystemExit(f"missing {source}; extract the *_infer.tar there first")
    destination = output_root / component
    model = destination / "model.onnx"

    manifest: dict[str, object] = {
        "format": "onnx",
        "precision": "float32",
        "runtime": "onnxruntime-cpu",
        "opset": OPSET,
        "component": component,
        "source_model": f"PP-OCRv6_small_{component}",
    }
    tarball = source_root / f"PP-OCRv6_small_{component}_infer.tar"
    if tarball.is_file():
        manifest["source_model_sha256"] = sha256_file(tarball)

    if component == "det":
        manifest["postprocess"] = check_detection_config(source)
        convert(source, model)
        verify_detection(model)
    else:
        charset = destination / "charset.txt"
        entries = extract_charset(source, charset)
        convert(source, model)
        manifest.update(verify_recognition(model, entries))
        manifest["charset_entries"] = entries
        manifest["charset_sha256"] = sha256_file(charset)

    manifest["model_sha256"] = sha256_file(model)
    write_manifest(destination / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=("det", "rec", "all"), default="all")
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="directory holding the extracted PP-OCRv6_small_*_infer folders",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    components = (
        ("det", "rec") if arguments.component == "all" else (arguments.component,)
    )
    for component in components:
        manifest = export(component, arguments.source, arguments.output)
        print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
