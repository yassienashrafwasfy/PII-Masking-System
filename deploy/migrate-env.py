#!/usr/bin/env python3
"""Create a mode-0600 .env.next without exposing or replacing legacy secrets."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path

# How each team spells its model identity. The three teams do not agree, and
# getting it wrong is not obvious from the outside: a `sha256:` prefix on a
# `*_SHA256` key is rejected by the model service's `_digest()` and surfaces
# only as an unlogged HTTP 422. Deriving the values from the committed manifest
# through this table is what keeps that mistake from being typeable at all.
#
# "dir"     -> <releases-root>/<release-sha>/<team>
# "prefix"  -> sha256:<hex> taken from the named manifest field
# "bare"    -> <hex> taken from the named manifest field
MODEL_IDENTITY = {
    # Every key names the exported graph, because the graph is what a service
    # loads. The manifest's `source_model_sha256` still records the checkpoint
    # the graph was exported from, but that checkpoint is not in the repository
    # and never answers a request, so it must not be what /version reports.
    "octo": {
        "OCTO_MODEL_DIR": ("dir", None),
        "OCTO_MODEL_VERSION": ("prefix", "model_sha256"),
        "OCTO_ONNX_MODEL_SHA256": ("bare", "model_sha256"),
    },
    "synthify": {
        "SYNTHIFY_MODEL_DIR": ("dir", None),
        "SYNTHIFY_MODEL_VERSION": ("prefix", "model_sha256"),
        "SYNTHIFY_ONNX_MODEL_DIGEST": ("prefix", "model_sha256"),
    },
    # Berts' version is the upstream revision rather than a digest.
    # deploy-gateway.sh additionally pins the approved berts artifact by value,
    # so a swap here fails preflight unless that pin is updated in the same
    # change -- which is the intent.
    "berts": {
        "BERTS_MODEL_DIR": ("dir", None),
        "BERTS_MODEL_VERSION": ("revision", "source_revision"),
        "BERTS_ONNX_MODEL_SHA256": ("bare", "model_sha256"),
        "BERTS_MODEL_VERSION_EN": ("revision", "source_revision", "onnx-en"),
        "BERTS_ONNX_MODEL_SHA256_EN": ("bare", "model_sha256", "onnx-en"),
        "BERTS_MODEL_VERSION_FR": ("revision", "source_revision", "onnx-fr"),
        "BERTS_ONNX_MODEL_SHA256_FR": ("bare", "model_sha256", "onnx-fr"),
    },
}

SAFE_LEGACY_KEYS = {
    "ACCELERATOR",
    "SERVICE_TOKEN",
    "PRIVATE_GATEWAY_PORT",
    "REQUEST_TIMEOUT_SECONDS",
    "GATEWAY_WEB_THREADS",
    "MODEL_SERVICE_WEB_THREADS",
    "MAX_CONCURRENT_REQUESTS_PER_TEAM",
    "MODEL_IDLE_TIMEOUT_SECONDS",
    "NGINX_SWITCH_WAIT_SECONDS",
    "DRAIN_MAX_SECONDS",
    "DRAIN_POLL_SECONDS",
    "MIN_AVAILABLE_MEMORY_PERCENT",
    "VERIFY_MAX_SECONDS",
    "WATCHDOG_REPAIR_COOLDOWN_SECONDS",
    "WATCHDOG_REPAIR_WAIT_SECONDS",
}
EXPLICIT_KEYS = {
    "ENABLED_TEAMS",
    "NIFI_DOCKER_BRIDGE_IP",
    "NGINX_UPSTREAM_FILE",
}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            values[key] = value
    return values


def secure_input(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"Input environment file is not a regular file: {path.name}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SystemExit(f"Input environment file is not private: {path.name}")
    if path.stat().st_uid not in {0, os.getuid()}:
        raise SystemExit(f"Input environment file has an untrusted owner: {path.name}")


def model_identity_values(
    team: str, release_sha: str, manifest_path: Path, releases_root: str
) -> dict[str, str]:
    """Return one team's identity keys, derived from its committed manifest."""
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        raise SystemExit("--release-sha must be a 40-character commit hash.")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", releases_root) or ".." in releases_root:
        raise SystemExit("--releases-root must be an absolute normalized path.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Berts ships one manifest per language, in sibling directories beside the
    # one named on the command line. Reading them from there keeps this a
    # single --manifest argument, and a language whose manifest is absent
    # fails here rather than at the first document in that language.
    siblings: dict[str, dict[str, object]] = {}

    values: dict[str, str] = {}
    for key, entry in MODEL_IDENTITY[team].items():
        kind, field, subdir = (*entry, None)[:3]
        if kind == "dir":
            values[key] = f"{releases_root.rstrip('/')}/{release_sha}/{team}"
            continue
        source = manifest
        if subdir is not None:
            if subdir not in siblings:
                sibling = manifest_path.parent.parent / subdir / "manifest.json"
                if not sibling.is_file():
                    raise SystemExit(f"Manifest for {subdir} is missing for {team}.")
                siblings[subdir] = json.loads(sibling.read_text(encoding="utf-8"))
            source = siblings[subdir]
        raw = str(source.get(field, ""))
        expected = r"[0-9a-f]{40}" if kind == "revision" else r"[0-9a-f]{64}"
        if not re.fullmatch(expected, raw):
            raise SystemExit(
                f"Manifest field {field} is missing or malformed for {team}."
            )
        values[key] = f"sha256:{raw}" if kind == "prefix" else raw
    return values


def rewrite_env(current_path: Path, overrides: dict[str, str]) -> str:
    """Return *current_path* with only *overrides* changed.

    The live file is edited line by line rather than regenerated from the
    schema: it holds the service token and the object-storage credentials, and
    a regenerated file would silently drop any key the schema does not list.
    """
    remaining = dict(overrides)
    lines: list[str] = []
    for raw in current_path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0]
            if key in remaining:
                lines.append(f"{key}={remaining.pop(key)}")
                continue
        lines.append(raw)
    for key, value in remaining.items():
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(text)
    os.chmod(path, 0o600)


def update_model_identity(args: argparse.Namespace) -> int:
    if args.team not in MODEL_IDENTITY:
        raise SystemExit("--team must be octo, synthify, or berts.")
    for name in ("release_sha", "manifest", "releases_root"):
        if getattr(args, name) is None:
            raise SystemExit(f"--{name.replace('_', '-')} is required with --current.")
    if args.output.exists():
        raise SystemExit("Output already exists; candidates are never overwritten.")
    if args.output.resolve() == args.current.resolve():
        raise SystemExit("Output must differ from the current environment file.")
    secure_input(args.current)

    overrides = model_identity_values(
        args.team, args.release_sha, args.manifest, args.releases_root
    )
    write_private(args.output, rewrite_env(args.current, overrides))
    for key in sorted(overrides):
        print(f"{key}={overrides[key]}")
    print(
        f"Prepared {args.output.name} for {args.team}; "
        "validate it with deploy-gateway.sh preflight before promoting it."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", type=Path)
    parser.add_argument("--values-file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--current", type=Path)
    parser.add_argument("--team")
    parser.add_argument("--release-sha")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--releases-root")
    args = parser.parse_args()

    if bool(args.legacy) == bool(args.current):
        raise SystemExit("Pass exactly one of --legacy or --current.")
    if args.current:
        return update_model_identity(args)

    schema_path = Path(__file__).with_name(".env.example")
    if args.output.exists():
        raise SystemExit(
            "Output already exists; legacy and prepared environments are never overwritten."
        )
    if args.output.resolve() == args.legacy.resolve():
        raise SystemExit("Output must differ from the legacy environment file.")
    secure_input(args.legacy)
    legacy = read_env(args.legacy)
    supplied: dict[str, str] = {}
    if args.values_file:
        secure_input(args.values_file)
        supplied = read_env(args.values_file)

    schema = read_env(schema_path)
    unknown = sorted(set(supplied) - set(schema))
    if unknown:
        print("Unknown environment keys: " + ", ".join(unknown), file=sys.stderr)
        return 2

    values = dict(schema)
    for key in SAFE_LEGACY_KEYS:
        if key in legacy:
            values[key] = legacy[key]
    values.update(supplied)

    unresolved: set[str] = {key for key in EXPLICIT_KEYS if key not in supplied}

    teams = values.get("ENABLED_TEAMS", "") if "ENABLED_TEAMS" not in unresolved else ""
    required_by_team = {
        "octo": {
            "OCTO_MODEL_DIR",
            "OCTO_MODEL_VERSION",
            "OCTO_ONNX_MODEL_SHA256",
        },
        "synthify": {
            "SYNTHIFY_MODEL_DIR",
            "SYNTHIFY_MODEL_VERSION",
            "SYNTHIFY_ONNX_MODEL_DIGEST",
        },
        "berts": {
            "BERTS_MODEL_DIR",
            "BERTS_MODEL_NAME",
            "BERTS_MODEL_VERSION",
            "BERTS_ONNX_MODEL_SHA256",
        },
    }
    # One checkpoint per document language, all staged under the same
    # BERTS_MODEL_DIR. An env carried forward with only the Dutch keys deploys
    # a Berts that cannot read a French or English document -- unless routing
    # is off, which is the documented rollback and reads only the Dutch one.
    if values.get("BERTS_LANGUAGE_ROUTING", "on").strip().lower() != "off":
        required_by_team["berts"] |= {
            "BERTS_MODEL_VERSION_EN",
            "BERTS_ONNX_MODEL_SHA256_EN",
            "BERTS_MODEL_VERSION_FR",
            "BERTS_ONNX_MODEL_SHA256_FR",
        }
    for team in filter(None, teams.split(",")):
        if team not in required_by_team:
            unresolved.add("ENABLED_TEAMS")
        for key in required_by_team.get(team, set()):
            value = values.get(key, "")
            if not value or "replace-with" in value or value == "__UNRESOLVED__":
                unresolved.add(key)
    if "berts" in teams.split(","):
        warm_rss = values.get("BERTS_WARM_RSS_MIB", "")
        if not warm_rss or "replace-with" in warm_rss:
            unresolved.add("BERTS_WARM_RSS_MIB")
    # OCR is deployment-wide rather than per-team, so it hangs off the switch
    # instead of ENABLED_TEAMS. The staged artifact directory has no default
    # worth keeping: the release SHA is part of the path.
    if values.get("PDF_OCR", "").strip().lower() == "on":
        for key in (
            "OCR_MODEL_DIR",
            "OCR_DET_SHA256",
            "OCR_REC_SHA256",
            "OCR_CHARSET_SHA256",
        ):
            value = values.get(key, "")
            if not value or "replace-with" in value or value == "__UNRESOLVED__":
                unresolved.add(key)
    for key in (
        "SERVICE_TOKEN",
        "ENABLED_TEAMS",
        "NIFI_DOCKER_BRIDGE_IP",
        "NGINX_UPSTREAM_FILE",
    ):
        value = values.get(key, "")
        if not value or "replace-with" in value or value == "__UNRESOLVED__":
            unresolved.add(key)

    if unresolved:
        print(
            "Unresolved required keys: " + ", ".join(sorted(unresolved)),
            file=sys.stderr,
        )
        return 2

    lines: list[str] = []
    for raw in schema_path.read_text().splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            lines.append(raw)
            continue
        key = raw.split("=", 1)[0]
        lines.append(f"{key}={values.get(key, '')}")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(args.output, flags, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write("\n".join(lines) + "\n")
    os.chmod(args.output, 0o600)
    print(f"Prepared {args.output.name}; validate it with deploy-gateway.sh preflight.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
