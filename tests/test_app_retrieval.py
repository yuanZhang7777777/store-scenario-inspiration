"""The candidate list the operator reads top-down.

Four channels fuse here — Chinese and English, each in a keyword and a vector
flavour — and there is no model rerank behind them. These tests pin the fusion,
the cut-off and the payload the browser has to carry, all without loading a
76MB vector index or two torch models.
"""

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from store_scenario_inspiration.app import retrieval as retrieval_module
from store_scenario_inspiration.app.params import SearchParams
from store_scenario_inspiration.app.retrieval import flatten_expansions, retrieve_store


CATALOGUE = {
    "A": ("露营折叠椅", "Camping Chair"),
    "B": ("户外折叠桌", "Outdoor Folding Table"),
    "C": ("遮阳伞", "Sun Umbrella"),
    "D": ("便携榨汁机", "Portable Juicer"),
    "E": ("保温水壶", "Thermos Bottle"),
    "F": ("野餐垫", "Picnic Mat"),
    "G": ("手电筒", "Flashlight"),
    "H": ("折叠水桶", "Folding Bucket"),
}

# Which SKUs each query vector ranks, keyed by model and the term's vector slot.
VECTOR_RANKING = {
    "cn-model": {1: ["A", "B", "C", "D", "E", "F", "G", "H"], 2: ["B", "A", "D"], 3: ["C"]},
    "en-model": {1: ["A", "C", "E", "G"], 2: ["B", "A"], 3: ["C", "A"]},
}
SLOT = {"露营折叠椅": 1, "Camping Chair": 1, "露营椅": 2, "Folding Chair": 2, "遮阳伞": 3}


class FakeEncoder:
    def encode(self, model_id: str, texts: list[str]) -> np.ndarray:
        return np.array([[SLOT[text]] for text in texts], dtype=np.float32)


class FakeIndex:
    model_ids = {"cn": "cn-model", "en": "en-model"}

    def search(self, language: str, vector: np.ndarray, limit: int):
        ranked = VECTOR_RANKING[f"{language}-model"][int(vector[0])]
        return [(sku, 1.0 - rank / 100) for rank, sku in enumerate(ranked, start=1)][:limit]


def write_catalogue(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """CREATE TABLE products (
                   main_sku TEXT, standard_name_cn TEXT, standard_name_en TEXT,
                   inventory_metadata_json TEXT, active INTEGER, indexable INTEGER)"""
        )
        connection.executemany(
            "INSERT INTO products VALUES (?, ?, ?, ?, 1, 1)",
            [(sku, cn, en, json.dumps({"child_skus": [f"{sku}-1"]}))
             for sku, (cn, en) in CATALOGUE.items()],
        )
        connection.commit()
    finally:
        connection.close()


EXPANSIONS = {
    "scenes": [{"scene_name": "庭院遮阳", "products": [
        {"product_cn": "露营折叠椅", "product_en": "Camping Chair",
         "canonical_cn": "露营折叠椅", "canonical_en": "Camping Chair",
         "expanded_cn": ["露营椅"], "expanded_en": ["Folding Chair"]},
    ]}],
}


@pytest.fixture
def asset_db(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    write_catalogue(path)
    return path


@pytest.fixture
def run(asset_db, tmp_path, monkeypatch):
    monkeypatch.setattr(retrieval_module, "load_vector_index", lambda _: FakeIndex())
    monkeypatch.setattr(retrieval_module, "load_encoder", lambda _: FakeEncoder())

    def call(**overrides):
        params = SearchParams(**{"scene_count": 8, "products_per_scene": 10,
                                 "expansion_terms": 6, "recall_limit": 30, **overrides})
        return retrieve_store(
            asset_db=asset_db,
            vector_cache=tmp_path / "vectors.sqlite3",
            model_cache=tmp_path / "models",
            stock_path=tmp_path / "missing.xlsx",
            country="PH",
            products=flatten_expansions(EXPANSIONS),
            params=params,
        )
    return call


def test_every_expanded_term_reaches_the_query_list(run) -> None:
    """The operator asked for the expansion terms to be visible, so the ones
    that actually went to the catalogue are reported back, not just the name."""
    scene = run()["scenes"][0]

    assert scene["queries"] == {
        "cn": ["露营折叠椅", "露营椅"],
        "en": ["Camping Chair", "Folding Chair"],
    }


def test_a_sku_hit_by_more_channels_outranks_one_hit_by_fewer(run) -> None:
    """A is on top of the Chinese vector channel and second on the English one;
    B is the reverse. Both are found, and neither is ranked by a model."""
    scene = run()["scenes"][0]
    ranks = {row["main_sku"]: row["rank"] for row in scene["candidates"]}

    assert ranks["A"] < ranks["B"]
    assert scene["candidates"][0]["rank"] == 1


def test_the_payload_carries_no_per_candidate_evidence(run) -> None:
    """Eight scenes of ten products is 2,400 rows; per-channel evidence would
    multiply that for something the operator never reads."""
    first = run()["scenes"][0]["candidates"][0]

    assert "evidence" not in first
    assert set(first) == {
        "rank", "main_sku", "standard_name_cn", "standard_name_en", "rrf_score",
        "channels", "matched_queries", "country_available", "country_available_quantity",
    }


def test_the_recall_limit_is_what_the_operator_set(run) -> None:
    assert len(run(recall_limit=5)["scenes"][0]["candidates"]) == 5
    assert len(run(recall_limit=30)["scenes"][0]["candidates"]) == len(CATALOGUE)


def test_the_top_of_the_list_does_not_move_when_the_cut_gets_longer(run) -> None:
    """The operator's "select this row and everything above it" only means what
    they think it means if a longer list does not reshuffle the short one."""
    short = [row["main_sku"] for row in run(recall_limit=5)["scenes"][0]["candidates"]]
    long = [row["main_sku"] for row in run(recall_limit=30)["scenes"][0]["candidates"]]

    assert long[:5] == short
    assert [row["rank"] for row in run(recall_limit=30)["scenes"][0]["candidates"]] == list(
        range(1, len(CATALOGUE) + 1))


def test_without_a_stock_file_the_candidates_say_so_instead_of_guessing(run) -> None:
    """Inventing an availability answer is worse than admitting there is none:
    the operator can check stock themselves, but not a number we made up."""
    result = run()

    assert result["inventory"] == "unavailable"
    assert all(row["country_available"] is None for row in result["scenes"][0]["candidates"])
    assert all(row["country_available_quantity"] is None
               for row in result["scenes"][0]["candidates"])


@pytest.fixture
def stock(tmp_path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "汇总表格"
    sheet.append(["子SKU", "主SKU", "国家", "库存中心库存", "公共池库存"])
    sheet.append(["A-1", "A", "菲律宾", 0, 0])
    sheet.append(["B-1", "B", "菲律宾", 4, 0])
    path = tmp_path / "stock.xlsx"
    workbook.save(path)
    return path


def _with_stock(asset_db, tmp_path, monkeypatch, stock, **overrides):
    monkeypatch.setattr(retrieval_module, "load_vector_index", lambda _: FakeIndex())
    monkeypatch.setattr(retrieval_module, "load_encoder", lambda _: FakeEncoder())
    return retrieve_store(
        asset_db=asset_db, vector_cache=tmp_path / "v.sqlite3", model_cache=tmp_path / "m",
        stock_path=stock, country="PH", products=flatten_expansions(EXPANSIONS),
        params=SearchParams(recall_limit=30, **overrides),
    )


def test_looking_at_everything_keeps_the_whole_list_and_marks_availability(
    asset_db, tmp_path, monkeypatch, stock,
) -> None:
    """Out-of-stock locally is a fact about a row, not a reason to delete it.
    Dropping it would hide the semantically closest SKUs and leave a short page
    of near-misses with no explanation."""
    result = _with_stock(asset_db, tmp_path, monkeypatch, stock, stock_filter="all")

    assert result["inventory"] == "available"
    rows = {row["main_sku"]: row for row in result["scenes"][0]["candidates"]}
    assert len(rows) == len(CATALOGUE)
    assert rows["A"]["country_available"] is False
    assert rows["A"]["country_available_quantity"] is None
    assert rows["B"]["country_available"] is True
    assert rows["B"]["country_available_quantity"] == 4.0


def test_filtering_to_in_stock_shortens_the_list_without_leaving_gaps(
    asset_db, tmp_path, monkeypatch, stock,
) -> None:
    result = _with_stock(asset_db, tmp_path, monkeypatch, stock, stock_filter="in_stock")

    kept = result["scenes"][0]["candidates"]
    assert [row["main_sku"] for row in kept] == ["B"]
    assert [row["rank"] for row in kept] == [1]


def test_flattening_expansions_keeps_each_product_with_its_scene() -> None:
    products = flatten_expansions({"scenes": [
        {"scene_name": "庭院遮阳", "products": [{"product_cn": "遮阳伞"}]},
        {"scene_name": "亲子露营", "products": [{"product_cn": "儿童帐篷"},
                                                {"product_cn": "野餐垫"}]},
    ]})

    assert [(item["scene_name"], item["product_cn"]) for item in products] == [
        ("庭院遮阳", "遮阳伞"), ("亲子露营", "儿童帐篷"), ("亲子露营", "野餐垫"),
    ]


def test_flattening_an_empty_expansion_is_an_empty_list() -> None:
    assert flatten_expansions({}) == []
