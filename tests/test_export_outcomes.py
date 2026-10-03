from __future__ import annotations

import json

import pytest

from tests.conftest import write_jsonl
from traces.export.outcomes import join_outcomes


def test_join_outcomes_writes_validated_sidecar(tmp_path):
    executions = tmp_path / "execution.jsonl"
    outcomes = tmp_path / "labels.jsonl"
    output = tmp_path / "outcomes.jsonl"
    write_jsonl(executions, [
        {"execution_id": "trace:task:0"},
        {"execution_id": "trace:task:1"},
    ])
    write_jsonl(outcomes, [{
        "execution_id": "trace:task:1",
        "task_id": "task-1",
        "verifier": {"type": "pytest", "passed": True, "exit_code": 0},
        "provenance": {"artifact": "result.json"},
    }])

    result = join_outcomes(executions, outcomes, output)

    assert result == {"executions": 2, "outcomes": 1, "unlabeled": 1}
    assert json.loads(output.read_text()) == {
        "schema_version": "outcome.v1",
        "execution_id": "trace:task:1",
        "task_id": "task-1",
        "verifier": {"type": "pytest", "passed": True, "exit_code": 0},
        "provenance": {"artifact": "result.json"},
    }


def test_join_outcomes_rejects_unknown_execution(tmp_path):
    executions = tmp_path / "execution.jsonl"
    outcomes = tmp_path / "labels.jsonl"
    write_jsonl(executions, [{"execution_id": "known"}])
    write_jsonl(outcomes, [{
        "execution_id": "unknown",
        "verifier": {"type": "tests", "passed": True},
    }])

    with pytest.raises(ValueError, match="unknown execution"):
        join_outcomes(executions, outcomes, tmp_path / "out.jsonl")


def test_join_outcomes_requires_explicit_result(tmp_path):
    executions = tmp_path / "execution.jsonl"
    outcomes = tmp_path / "labels.jsonl"
    write_jsonl(executions, [{"execution_id": "known"}])
    write_jsonl(outcomes, [{
        "execution_id": "known",
        "verifier": {"type": "tests"},
    }])

    with pytest.raises(ValueError, match="no explicit result"):
        join_outcomes(executions, outcomes, tmp_path / "out.jsonl")
