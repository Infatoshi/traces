# traces/sample.py
from __future__ import annotations

import json
import random
from pathlib import Path


def sample_conversations(in_dir: Path, n: int, seed: int = 0, out_dir: Path | None = None) -> list[dict]:
    rng = random.Random(seed)
    all_records = []
    for src in sorted(in_dir.glob("*.jsonl")):
        with src.open() as f:
            for line in f:
                if line.strip():
                    all_records.append(json.loads(line))
    sample = rng.sample(all_records, min(n, len(all_records)))
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "sample.jsonl").open("w") as f:
            for rec in sample:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return sample
