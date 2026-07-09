"""Optional privacy-filter layer (transformers-based PII detection).

Ported from upstream lines 485-822 of export_redacted_traces.py
(PrivacyFilter class + privacy_filter_batch_pass).

The heavy deps (``transformers``, ``torch``) are imported lazily inside
``PrivacyFilter.__init__`` so this module is import-safe without them.
The pipeline imports it freely; only instantiation triggers the imports.
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path
from typing import Any


class PrivacyFilter:
    """Wrap the openai/privacy-filter token-classification model.

    Operates on strings; ``redact_many`` batches candidates and caches
    redactions so each unique string is scored exactly once.
    """

    def __init__(
        self,
        model_name: str = "openai/privacy-filter",
        threshold: float = 0.65,
        max_chars: int = 12000,
        selective: bool = True,
        device: str = "auto",
    ) -> None:
        # --- Lazy imports: must live inside __init__ so the core package
        # stays importable without torch/transformers installed.
        try:
            import torch
            from transformers import pipeline
        except Exception as exc:  # pragma: no cover - exercised only at runtime
            raise SystemExit(
                "Privacy Filter requested, but transformers/torch are not installed. "
                "Use the venv setup described in MANIFEST or run without --privacy-filter."
            ) from exc
        self.torch = torch
        self.threshold = threshold
        self.max_chars = max_chars
        self.selective = selective
        self.requested_device = device
        self.pipeline_device, self.device_description = self.resolve_device(torch, device)
        self.cache: dict[str, tuple[str, collections.Counter[str]]] = {}
        self.cue_pattern = re.compile(
            r"(?i)\b("
            r"name is|my name|i am|i'm|address|live at|phone|call me|contact|"
            r"email|e-mail|passport|ssn|social security|dni|nif|cif|"
            r"customer|client|user|usuario|nombre|direcci[oó]n|tel[eé]fono|"
            r"full name|first name|last name|surname|street|city|zip|postcode"
            r")\b"
        )
        self.pipe = pipeline(
            "token-classification",
            model=model_name,
            aggregation_strategy="simple",
            device=self.pipeline_device,
        )

    @staticmethod
    def resolve_device(torch: Any, requested: str) -> tuple[int, str]:
        normalized = requested.lower().strip()
        if normalized in {"cpu", "-1"}:
            return -1, "cpu"
        if normalized.startswith("cuda") or normalized in {"gpu", "nvidia"}:
            if not torch.cuda.is_available():
                raise SystemExit(
                    "--privacy-filter-device requested CUDA/NVIDIA, "
                    "but torch.cuda.is_available() is false."
                )
            index = 0
            if ":" in normalized:
                try:
                    index = int(normalized.rsplit(":", 1)[1])
                except ValueError as exc:
                    raise SystemExit(f"Invalid CUDA device: {requested}") from exc
            return index, f"cuda:{index} ({torch.cuda.get_device_name(index)})"
        if normalized != "auto":
            raise SystemExit(
                "--privacy-filter-device must be one of: auto, cpu, cuda, cuda:N"
            )
        if torch.cuda.is_available():
            return 0, f"cuda:0 ({torch.cuda.get_device_name(0)})"
        return -1, "cpu"

    def should_process(self, text: str) -> bool:
        if not text or len(text) > self.max_chars:
            return False
        return not (self.selective and not self.cue_pattern.search(text))

    def redact_from_entities(
        self, text: str, entities: Any
    ) -> tuple[str, collections.Counter[str]]:
        spans: list[tuple[int, int, str]] = []
        counts: collections.Counter[str] = collections.Counter()
        for entity in entities or []:
            score = float(entity.get("score", 0.0))
            start = entity.get("start")
            end = entity.get("end")
            label = str(
                entity.get("entity_group") or entity.get("entity") or "PII"
            )
            if start is None or end is None or score < self.threshold or start >= end:
                continue
            spans.append((int(start), int(end), label.upper()))
        if not spans:
            return text, counts
        spans.sort(key=lambda item: (item[0], item[1]))
        merged: list[tuple[int, int, str]] = []
        for start, end, label in spans:
            if merged and start <= merged[-1][1]:
                prev_start, prev_end, prev_label = merged[-1]
                merged[-1] = (prev_start, max(prev_end, end), prev_label)
            else:
                merged.append((start, end, label))
        out = text
        for start, end, label in reversed(merged):
            counts[label] += 1
            out = out[:start] + f"[PII_MODEL:{label}]" + out[end:]
        return out, counts

    def redact(self, text: str) -> tuple[str, collections.Counter[str]]:
        if not self.should_process(text):
            return text, collections.Counter()
        cached = self.cache.get(text)
        if cached is not None:
            return cached
        try:
            entities = self.pipe(text)
        except Exception:
            return text, collections.Counter({"errors": 1})
        result = self.redact_from_entities(text, entities)
        self.cache[text] = result
        return result

    def redact_many(
        self, texts: list[str], batch_size: int = 64
    ) -> dict[str, tuple[str, collections.Counter[str]]]:
        unique: list[str] = []
        seen: set[str] = set()
        for text in texts:
            if text in seen or text in self.cache or not self.should_process(text):
                continue
            seen.add(text)
            unique.append(text)
        for start in range(0, len(unique), batch_size):
            batch = unique[start:start + batch_size]
            try:
                outputs = self.pipe(batch, batch_size=batch_size)
            except Exception:
                for text in batch:
                    try:
                        self.cache[text] = self.redact_from_entities(text, self.pipe(text))
                    except Exception:
                        self.cache[text] = (text, collections.Counter({"errors": 1}))
                continue
            if len(batch) == 1 and (not outputs or isinstance(outputs[0], dict)):
                outputs = [outputs]
            for text, entities in zip(batch, outputs):
                self.cache[text] = self.redact_from_entities(text, entities)
        return {text: self.cache[text] for text in texts if text in self.cache}


# ---------------------------------------------------------------------------
# Tree-walking helpers (ported verbatim from upstream privacy_filter_batch_pass).
# ---------------------------------------------------------------------------

def iter_string_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        values.append(value)
    elif isinstance(value, list):
        for item in value:
            values.extend(iter_string_values(item))
    elif isinstance(value, dict):
        for item in value.values():
            values.extend(iter_string_values(item))
    return values


def apply_string_mapping(
    value: Any, mapping: dict[str, str]
) -> tuple[Any, bool]:
    if isinstance(value, str):
        new = mapping.get(value, value)
        return new, new != value
    if isinstance(value, list):
        changed = False
        items = []
        for item in value:
            new_item, item_changed = apply_string_mapping(item, mapping)
            items.append(new_item)
            changed = changed or item_changed
        return items, changed
    if isinstance(value, dict):
        changed = False
        obj = {}
        for key, item in value.items():
            new_item, item_changed = apply_string_mapping(item, mapping)
            obj[key] = new_item
            changed = changed or item_changed
        return obj, changed
    return value, False


def privacy_filter_pass(
    root: Path, pf: PrivacyFilter, batch_size: int = 64
) -> dict[str, int]:
    """Walk ``root`` (redacted tree), send candidate strings to ``pf``,
    and rewrite files in place. Returns a stats dict.

    This is the standalone port of upstream ``privacy_filter_batch_pass``:
    it does not touch a Redactor's ``counts`` because the deterministic
    rule pass has already run by the time we get here.
    """
    stats = {
        "files_scanned": 0,
        "files_changed": 0,
        "strings_reviewed": 0,
        "strings_changed": 0,
    }
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".jsonl", ".json"}:
            continue
        stats["files_scanned"] += 1
        if path.suffix == ".jsonl":
            records: list[Any] = []
            raw_lines = path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            candidates: list[str] = []
            for line in raw_lines:
                if not line.strip():
                    records.append(None)
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    records.append(line)
                    if pf.should_process(line):
                        candidates.append(line)
                    continue
                records.append(obj)
                candidates.extend(
                    text for text in iter_string_values(obj)
                    if pf.should_process(text)
                )
            results = pf.redact_many(candidates, batch_size=batch_size)
            mapping = {
                text: redacted
                for text, (redacted, _) in results.items() if redacted != text
            }
            stats["strings_reviewed"] += len(results)
            stats["strings_changed"] += len(mapping)
            if not mapping:
                continue
            out_lines: list[str] = []
            changed = False
            for record in records:
                if record is None:
                    out_lines.append("")
                elif isinstance(record, str):
                    new = mapping.get(record, record)
                    changed = changed or new != record
                    out_lines.append(new)
                else:
                    new_obj, item_changed = apply_string_mapping(record, mapping)
                    changed = changed or item_changed
                    out_lines.append(
                        json.dumps(new_obj, ensure_ascii=False, separators=(",", ":"))
                    )
            if changed:
                path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
                stats["files_changed"] += 1
            continue

        # Suffix == ".json"
        try:
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        candidates = [
            text for text in iter_string_values(obj) if pf.should_process(text)
        ]
        results = pf.redact_many(candidates, batch_size=batch_size)
        mapping = {
            text: redacted
            for text, (redacted, _) in results.items() if redacted != text
        }
        stats["strings_reviewed"] += len(results)
        stats["strings_changed"] += len(mapping)
        if not mapping:
            continue
        new_obj, changed = apply_string_mapping(obj, mapping)
        if changed:
            path.write_text(
                json.dumps(new_obj, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            stats["files_changed"] += 1
    return stats
