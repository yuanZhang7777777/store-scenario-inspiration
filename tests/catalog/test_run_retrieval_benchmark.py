from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest
from openpyxl import Workbook

from store_scenario_inspiration.catalog.versioning import CatalogIndexManager


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


def test_cli_rejects_top_k_configuration(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--top-k", "1"], search_fn=lambda query, top_k: ())

    assert exit_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_cli_script_bootstraps_local_src_from_an_unrelated_working_directory(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [sys.executable, "-S", str(_SCRIPT_PATH)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "search dependency" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_wires_active_keyword_index_and_keeps_provisional_report_non_enforcing(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "source.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(
        (
            "sku",
            "主SKU",
            "商品名称",
            "英文名称",
            "英文关键字",
            "销售状态",
            "商品目录",
            "商品一级目录",
            "商品二级目录",
            "商品三级目录",
            "商品四级目录",
        )
    )
    worksheet.append(
        ("SKU-1", "MATCH", "户外露营灯", "", "", "", "户外灯", "运动", "露营", "照明", "户外灯")
    )
    workbook.save(source)
    root = tmp_path / "catalog"
    CatalogIndexManager(root).rebuild(source, sheet_name=None, provider=None)
    benchmark = tmp_path / "provisional.json"
    benchmark.write_text(
        json.dumps(
            [
                {
                    "query_id": "q1",
                    "query": "户外露营灯",
                    "relevant_main_skus": ["MATCH"],
                    "expected_status": "has_match",
                    "label_status": "provisional",
                    "notes": "pending review",
                }
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(["--benchmark", str(benchmark), "--index-root", str(root)])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["hit_at_5"] == 1.0
    assert report["enforced"] is False
    assert report["passes_threshold"] is None


def test_cli_index_root_requires_an_active_index_without_traceback(
    tmp_path: Path, capsys
) -> None:
    exit_code = main(["--index-root", str(tmp_path / "missing")])

    error = capsys.readouterr().err
    assert exit_code == 2
    assert "active" in error
    assert "Traceback" not in error
