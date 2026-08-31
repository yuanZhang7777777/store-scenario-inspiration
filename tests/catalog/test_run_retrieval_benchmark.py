from __future__ import annotations

import json
import importlib.util
from pathlib import Path


_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "run_retrieval_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("run_retrieval_benchmark", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_SCRIPT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SCRIPT)
main = _SCRIPT.main


def _write_benchmark(path: Path, label_status: str) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "query_id": "q1",
                    "query": "query",
                    "relevant_main_skus": ["MATCH"],
                    "expected_status": "has_match",
                    "label_status": label_status,
                    "notes": "note",
                }
            ]
        ),
        encoding="utf-8",
    )


def test_cli_reports_provisional_failure_without_nonzero_exit(
    tmp_path: Path, capsys
) -> None:
    path = tmp_path / "provisional.json"
    _write_benchmark(path, "provisional")

    exit_code = main(["--benchmark", str(path)], search_fn=lambda query, top_k: ())

    assert exit_code == 0
    assert '"enforced": false' in capsys.readouterr().out


def test_cli_returns_nonzero_for_confirmed_gate_failure(tmp_path: Path, capsys) -> None:
    path = tmp_path / "confirmed.json"
    _write_benchmark(path, "confirmed")

    exit_code = main(["--benchmark", str(path)], search_fn=lambda query, top_k: ())

    assert exit_code == 1
    assert '"enforced": true' in capsys.readouterr().out


def test_cli_has_a_minimal_unwired_store_entrypoint(capsys) -> None:
    exit_code = main([])

    assert exit_code == 2
    assert "search dependency" in capsys.readouterr().err
