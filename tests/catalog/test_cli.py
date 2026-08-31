from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from openpyxl import Workbook

from store_scenario_inspiration.catalog.cli import main
import store_scenario_inspiration.catalog.cli as cli


HEADERS = (
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


def _write_source(
    path: Path,
    rows: tuple[tuple[str, str, str, str], ...] = (
        ("ZXOD3713-A", "ZXOD3713", "户外太阳能灯笼", ""),
    ),
) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "产品列表"
    worksheet.append(HEADERS)
    for sku, main_sku, name, status in rows:
        worksheet.append(
            (
                sku,
                main_sku,
                name,
                "solar lantern",
                "solar lantern",
                status,
                "户外灯",
                "运动",
                "露营",
                "照明",
                "户外灯",
            )
        )
    workbook.save(path)
    return path


def _lines(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.strip().splitlines())


def test_rebuild_status_search_skip_and_rollback_commands(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "catalog"
    first_source = _write_source(tmp_path / "first.xlsx")

    assert main(["rebuild", "--source", str(first_source), "--index-root", str(root)]) == 0
    first_output = _lines(capsys.readouterr().out)
    assert first_output["source_rows"] == "1"
    assert first_output["documents"] == "1"
    assert first_output["children"] == "1"
    assert first_output["quality_errors"] == "0"
    assert first_output["vector_status"] == "absent"
    assert first_output["source_sheet_name"] == "none"
    assert first_output["source_modified_at"].endswith("Z")
    assert first_output["skipped"] == "false"
    first_version = first_output["active_version"]

    assert main(["status", "--index-root", str(root)]) == 0
    status = _lines(capsys.readouterr().out)
    assert status["active_version"] == first_version
    assert status["source_sha256"] == first_output["source_sha256"]

    assert (
        main(
            [
                "search",
                "--query",
                "户外太阳能灯笼",
                "--top-k",
                "5",
                "--index-root",
                str(root),
            ]
        )
        == 0
    )
    search = json.loads(capsys.readouterr().out)
    assert search["results"][0]["main_sku"] == "ZXOD3713"
    assert search["results"][0]["sources"] == ["keyword"]
    assert search["results"][0]["eligible_child_skus"] == ["ZXOD3713-A"]

    assert main(["rebuild", "--source", str(first_source), "--index-root", str(root)]) == 0
    skipped = _lines(capsys.readouterr().out)
    assert skipped["active_version"] == first_version
    assert skipped["skipped"] == "true"

    second_source = _write_source(
        tmp_path / "second.xlsx",
        (("SECOND-A", "SECOND", "太阳能露营灯", ""),),
    )
    assert main(["rebuild", "--source", str(second_source), "--index-root", str(root)]) == 0
    second = _lines(capsys.readouterr().out)
    assert second["active_version"] != first_version

    assert main(["rollback", "--index-root", str(root)]) == 0
    rollback = _lines(capsys.readouterr().out)
    assert rollback["active_version"] == first_version
    assert rollback["rolled_back"] == "true"


def test_rebuild_failure_is_nonzero_and_explicitly_keeps_previous_active(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "catalog"
    valid = _write_source(tmp_path / "valid.xlsx")
    invalid = tmp_path / "invalid.xlsx"
    workbook = Workbook()
    workbook.active.append(("sku", "商品名称"))
    workbook.save(invalid)
    assert main(["rebuild", "--source", str(valid), "--index-root", str(root)]) == 0
    first = _lines(capsys.readouterr().out)["active_version"]

    exit_code = main(["rebuild", "--source", str(invalid), "--index-root", str(root)])

    error = capsys.readouterr()
    assert exit_code != 0
    assert "previous index remains active" in error.err
    assert "Traceback" not in error.err
    assert main(["status", "--index-root", str(root)]) == 0
    assert _lines(capsys.readouterr().out)["active_version"] == first


def test_rebuild_output_failure_does_not_claim_the_old_version_is_active(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    source = _write_source(tmp_path / "source.xlsx")
    monkeypatch.setattr(cli, "_print_active", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("output failed")))

    assert main(["rebuild", "--source", str(source), "--index-root", str(tmp_path / "catalog")]) != 0

    assert "new version may already be active; run status" in capsys.readouterr().err


def _fail_writer_unlock(monkeypatch) -> None:
    if os.name == "nt":
        import msvcrt

        original = msvcrt.locking
        monkeypatch.setattr(
            msvcrt,
            "locking",
            lambda fd, mode, size: (_ for _ in ()).throw(OSError("unlock unavailable"))
            if mode == msvcrt.LK_UNLCK
            else original(fd, mode, size),
        )
    else:
        import fcntl

        original = fcntl.flock
        monkeypatch.setattr(
            fcntl,
            "flock",
            lambda fd, mode: (_ for _ in ()).throw(OSError("unlock unavailable"))
            if mode == fcntl.LOCK_UN
            else original(fd, mode),
        )


def test_cli_rebuild_succeeds_when_post_commit_unlock_fails(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    source = _write_source(tmp_path / "source.xlsx")
    _fail_writer_unlock(monkeypatch)

    assert main(["rebuild", "--source", str(source), "--index-root", str(tmp_path / "catalog")]) == 0

    output = _lines(capsys.readouterr().out)
    assert output["active_version"] != "none"


def test_cli_rollback_succeeds_when_post_commit_unlock_fails(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    root = tmp_path / "catalog"
    first = _write_source(tmp_path / "first.xlsx")
    second = _write_source(
        tmp_path / "second.xlsx", (("SECOND-A", "SECOND", "太阳能灯", ""),)
    )
    assert main(["rebuild", "--source", str(first), "--index-root", str(root)]) == 0
    first_version = _lines(capsys.readouterr().out)["active_version"]
    assert main(["rebuild", "--source", str(second), "--index-root", str(root)]) == 0
    capsys.readouterr()
    _fail_writer_unlock(monkeypatch)

    assert main(["rollback", "--index-root", str(root)]) == 0

    assert _lines(capsys.readouterr().out)["active_version"] == first_version


def test_status_without_active_is_clean_but_invalid_searches_are_nonzero(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "catalog"
    assert main(["status", "--index-root", str(root)]) == 0
    assert _lines(capsys.readouterr().out) == {"active_version": "none"}

    for args in (
        ["search", "--query", "灯", "--index-root", str(root)],
        ["search", "--query", "", "--index-root", str(root)],
        ["search", "--query", "灯", "--top-k", "0", "--index-root", str(root)],
    ):
        assert main(args) != 0
        error = capsys.readouterr().err
        assert "error=" in error
        assert "Traceback" not in error


def test_search_applies_platform_child_filtering(tmp_path: Path, capsys) -> None:
    root = tmp_path / "catalog"
    source = _write_source(
        tmp_path / "source.xlsx",
        (
            ("BANNED", "FAMILY", "户外灯笼", "Shopee 违禁"),
            ("SAFE", "FAMILY", "户外灯笼安全款", ""),
        ),
    )
    assert main(["rebuild", "--source", str(source), "--index-root", str(root)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "search",
                "--query",
                "户外灯笼",
                "--platform",
                "Shopee",
                "--index-root",
                str(root),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["eligible_child_skus"] == ["SAFE"]
    assert result["warnings"] == []


def test_installed_console_script_runs_from_unrelated_working_directory(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path / "source.xlsx")
    root = tmp_path / "runtime" / "catalog"
    executable = Path(sys.executable).with_name("store-catalog.exe")

    result = subprocess.run(
        [
            str(executable),
            "rebuild",
            "--source",
            str(source),
            "--index-root",
            str(root),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0
    assert "documents=1" in result.stdout
    assert "Traceback" not in result.stderr
