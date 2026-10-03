"""Strictly join external verifier outcomes to exported execution identities."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "outcome.v1"


def _rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from error
            if not isinstance(value, dict):
                raise TypeError(f"{path}:{line_number}: row is not an object")
            rows.append(value)
    return rows


def join_outcomes(executions: Path, outcomes: Path, output: Path) -> dict[str, int]:
    """Write a validated outcome sidecar keyed by existing execution IDs.

    Outcome rows must contain ``execution_id`` and a verifier object with a
    non-empty ``type`` plus at least one explicit result field. Unknown or
    duplicate execution IDs are errors; no success is inferred from prose.
    """
    execution_rows = _rows(executions)
    execution_ids = [row.get("execution_id") for row in execution_rows]
    if any(not isinstance(value, str) or not value for value in execution_ids):
        raise ValueError("every execution row must have a non-empty execution_id")
    execution_id_set = set(execution_ids)
    if len(execution_id_set) != len(execution_ids):
        raise ValueError("execution file contains duplicate execution_id values")

    indexed: dict[str, dict[str, Any]] = {}
    for row in _rows(outcomes):
        execution_id = row.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError("every outcome row must have a non-empty execution_id")
        if execution_id in indexed:
            raise ValueError(f"duplicate outcome for {execution_id}")
        if execution_id not in execution_id_set:
            raise ValueError(f"outcome references unknown execution {execution_id}")
        verifier = row.get("verifier")
        if not isinstance(verifier, dict) or not isinstance(verifier.get("type"), str):
            raise TypeError(f"{execution_id}: verifier.type is required")
        if not any(key in verifier for key in ("passed", "reward", "status")):
            raise ValueError(f"{execution_id}: verifier has no explicit result")
        indexed[execution_id] = row

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as stream:
        for execution_id in execution_ids:
            source = indexed.get(execution_id)
            if source is None:
                continue
            row = {
                "schema_version": SCHEMA_VERSION,
                "execution_id": execution_id,
                "verifier": source["verifier"],
            }
            for key in ("task_id", "teacher", "provenance"):
                if key in source:
                    row[key] = source[key]
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {
        "executions": len(execution_ids),
        "outcomes": len(indexed),
        "unlabeled": len(execution_ids) - len(indexed),
    }
