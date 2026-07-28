"""HuggingFace Hub upload."""
from __future__ import annotations

import json
from pathlib import Path

from .dataset_card import render_card


def upload_variant(
    export_dir: Path,
    repo_id: str,
    variant: str = "both",
    private: bool = False,
    token: str | None = None,
) -> str:
    """Upload selected export variants and a dataset card to HF Hub.

    Returns the Hub URL. Requires the `publish` extra: pip install -e .[publish]
    """
    try:
        from huggingface_hub import HfApi, create_repo
    except ImportError as e:
        raise RuntimeError("install with: pip install -e .[publish]") from e

    api = HfApi(token=token)
    create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True, token=token)

    variant_files = {
        "messages": ["messages.jsonl"],
        "sharegpt": ["sharegpt.jsonl"],
        "execution": ["execution.jsonl"],
        "steps": ["execution.jsonl", "assistant_steps.jsonl"],
        "both": ["messages.jsonl", "sharegpt.jsonl"],
        "all": [
            "messages.jsonl",
            "sharegpt.jsonl",
            "execution.jsonl",
            "assistant_steps.jsonl",
        ],
    }
    files = [export_dir / name for name in variant_files[variant]]
    for f in files:
        if not f.exists():
            raise FileNotFoundError(f)

    manifest_path = export_dir.parent / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    card = render_card(manifest, repo_id)
    (export_dir / "README.md").write_text(card)

    api.upload_folder(
        folder_path=str(export_dir),
        repo_id=repo_id,
        repo_type="dataset",
        path_in_repo=".",
    )
    return f"https://huggingface.co/datasets/{repo_id}"
