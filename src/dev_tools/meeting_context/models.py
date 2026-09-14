"""Explicit model provisioning; normal transcription never downloads a model."""

import os
import re
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .files import child_file, local_path, read_json, save_json, sha256_file

MODEL_FILES = (
    "config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.json",
    "vocabulary.txt",
    "preprocessor_config.json",
)


def describe_model(path: Path) -> dict[str, Any]:
    path = local_path(path).resolve()
    if not path.is_dir():
        raise ValueError(
            "Local model directory missing. Provision an approved model with download-model first, or copy one from IT."
        )
    for name in ("config.json", "model.bin", "tokenizer.json"):
        child_file(path, name)
    hashes = {
        name: sha256_file(child_file(path, name)) for name in MODEL_FILES if (path / name).exists()
    }
    provenance = None
    if (path / "model-manifest.json").exists():
        manifest = read_json(path / "model-manifest.json")
        if manifest.get("sha256") != hashes:
            raise ValueError(
                "Model files differ from model-manifest.json; provision or review the model again"
            )
        provenance = {
            "repo": manifest.get("repo"),
            "revision": manifest.get("revision"),
        }
    return {"sha256": hashes, "provenance": provenance}


def download_model(args: Namespace) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", args.repo):
        raise ValueError("Model repo must be an owner/name identifier")
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise ValueError(
            "--revision must be a full 40-character lowercase commit SHA, not a mutable branch or tag"
        )
    args.out.mkdir(parents=True, exist_ok=False)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from huggingface_hub import snapshot_download

    print(
        "Downloading model weights/configuration only; no meeting files are read.",
        flush=True,
    )
    snapshot_download(
        repo_id=args.repo,
        revision=args.revision,
        local_dir=str(args.out),
        allow_patterns=list(MODEL_FILES),
        token=False,
    )
    descriptor = describe_model(args.out)
    save_json(
        args.out / "model-manifest.json",
        {
            "repo": args.repo,
            "revision": args.revision,
            "sha256": descriptor["sha256"],
            "downloaded_utc": datetime.now(UTC).isoformat(),
            "note": "Hashes detect changes against this record; they do not certify publisher trust or model safety.",
        },
    )
    print(f"Model saved to {args.out}. Transcription can now run offline.")
