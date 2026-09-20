# Catalog Retrieval Foundation Implementation Plan

> **状态（2026-09-17）：已被产品资产新口径取代，不应继续照此执行。** 当前权威口径见设计规格第 18 节：活跃商品范围改为库存中心 `平销款/利润款/引流款`，唯一事实表为 `E:\Project\store-assortment-copilot\var\product-asset\catalog.sqlite3`。本文的 8,550 主 SKU、中文单路向量文本和全量 ERP 构建步骤仅保留为历史方案。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, versioned ERP catalog indexer with SQLite FTS keyword search, replaceable in-memory vector search, embedding reuse, atomic product-list updates, and rollback.

**Architecture:** Treat each `主SKU` as one retrieval document and each `sku` as child metadata. A build reads the external Excel file without modifying it, normalizes and validates rows, writes a complete staged SQLite/FTS index, reuses embeddings by normalized vector-text hash, and atomically activates the version only after quality gates pass. Vector search uses a contiguous NumPy matrix and exact cosine similarity; no external vector database is part of this subsystem.

**Tech Stack:** Python 3.12, uv, openpyxl, NumPy, Python `sqlite3` with FTS5, pytest.

**Spec:** `docs/superpowers/specs/2026-08-31-store-scenario-inspiration-mvp-design.md`

## Global Constraints

- The source Excel workbook is read-only and remains outside Git.
- `mainSKU`, child `sku`, sales status, and internal identifiers never enter vector text.
- Vector text v1 contains only cleaned Chinese product names, at most 8 names and 120 characters per name.
- English names and keywords are low-weight keyword fields only.
- Product availability in ERP does not constrain scene generation; this subsystem only reports retrieval results.
- A failed build never changes the active index.
- A successful update retains the previous successful version for rollback.
- No external vector database, web UI, scheduler, screenshot recognition, or scene generation is added by this plan.
- Runtime indexes, source workbooks, model files, and local analysis outputs are not committed.

## File Map

```text
pyproject.toml                                      project metadata and dependencies
.python-version                                    Python 3.12 selection
.gitignore                                         ignore runtime catalog artifacts
src/store_scenario_inspiration/catalog/models.py   immutable source/document/result contracts
src/store_scenario_inspiration/catalog/normalize.py deterministic cell and name cleaning
src/store_scenario_inspiration/catalog/workbook.py  read-only Excel row extraction
src/store_scenario_inspiration/catalog/build.py     main-SKU grouping and document construction
src/store_scenario_inspiration/catalog/quality.py   build invariants and version comparison
src/store_scenario_inspiration/catalog/benchmark.py benchmark validation and Hit@5 reporting
src/store_scenario_inspiration/catalog/storage.py   SQLite schema, FTS writes, and keyword search
src/store_scenario_inspiration/catalog/vectors.py   provider port, cache, matrix, cosine search
src/store_scenario_inspiration/catalog/retrieval.py keyword/vector fusion and platform filtering
src/store_scenario_inspiration/catalog/versioning.py staged build, activation, and rollback
src/store_scenario_inspiration/catalog/cli.py        rebuild, status, search, and rollback commands
tests/catalog/                                      focused unit and integration tests
tests/fixtures/retrieval-benchmark-v1.json           human-reviewable retrieval ground truth
scripts/run_retrieval_benchmark.py                   Hit@5 and obvious-error evaluation
README.md                                           operator commands and update workflow
```

Runtime layout:

```text
var/catalog/
  active.json
  previous.json
  embedding-cache/
  versions/<version-id>/
    catalog.sqlite3
    vectors.npy
    vector-rows.json
    manifest.json
    quality.json
```

---

### Task 1: Python package and catalog contracts

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Modify: `.gitignore`
- Create: `src/store_scenario_inspiration/__init__.py`
- Create: `src/store_scenario_inspiration/catalog/__init__.py`
- Create: `src/store_scenario_inspiration/catalog/models.py`
- Create: `tests/catalog/test_models.py`

**Interfaces:**
- Produces: `SourceRow`, `ChildVariant`, `ProductFamilyDocument`, `BuildManifest`, `SearchHit` dataclasses.
- Produces: `ProductFamilyDocument.to_index_dict() -> dict[str, object]` for deterministic serialization.

- [ ] **Step 1: Write the failing contract test**

```python
from store_scenario_inspiration.catalog.models import ProductFamilyDocument


def test_index_dict_never_places_ids_in_vector_text() -> None:
    doc = ProductFamilyDocument(
        doc_id="main:ZXOD3713",
        main_sku="ZXOD3713",
        searchable=True,
        cn_names=("户外太阳能灯笼",),
        en_aliases=("solar lantern",),
        leaf_categories=("户外灯",),
        category_paths=(("运动及娱乐", "野营及徒步旅行", "户外灯"),),
        vector_text_v1="商品名称：户外太阳能灯笼",
        children=(),
        quality_flags=(),
    )

    payload = doc.to_index_dict()

    assert payload["main_sku"] == "ZXOD3713"
    assert "ZXOD3713" not in payload["vector_text_v1"]
```

- [ ] **Step 2: Run the test and verify the package does not exist**

Run: `uv run pytest tests/catalog/test_models.py -v`

Expected: FAIL during import because `store_scenario_inspiration.catalog.models` does not exist.

- [ ] **Step 3: Add the minimal package configuration**

Use these dependency bounds in `pyproject.toml`:

```toml
[project]
name = "store-scenario-inspiration"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "numpy>=2.1,<3",
  "openpyxl>=3.1,<4",
]

[project.scripts]
store-catalog = "store_scenario_inspiration.catalog.cli:main"

[dependency-groups]
dev = ["pytest>=8.3,<9"]

[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Set `.python-version` to `3.12`. Add `var/`, `.pytest_cache/`, `__pycache__/`, and `.venv/` to `.gitignore` without removing `.analysis-work/`.

- [ ] **Step 4: Implement the immutable contracts**

Use frozen dataclasses and tuples so a built document cannot change after validation. `to_index_dict()` must sort keys during JSON serialization tests and must expose IDs only as metadata fields.

- [ ] **Step 5: Run the contract test**

Run: `uv sync --dev && uv run pytest tests/catalog/test_models.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the package foundation**

```bash
git add pyproject.toml .python-version .gitignore src tests/catalog/test_models.py
git commit -m "build: add catalog package foundation"
```

---

### Task 2: Deterministic text normalization

**Files:**
- Create: `src/store_scenario_inspiration/catalog/normalize.py`
- Create: `tests/catalog/test_normalize.py`

**Interfaces:**
- Consumes: raw Excel cell values.
- Produces: `normalize_compare(value: object) -> str`.
- Produces: `clean_optional_text(value: object) -> str | None`.
- Produces: `prepare_vector_names(names: Iterable[str]) -> tuple[str, ...]`.
- Produces: `make_vector_text(names: Iterable[str]) -> str`.

- [ ] **Step 1: Write failing tests for exact placeholders and real specifications**

```python
from store_scenario_inspiration.catalog.normalize import (
    clean_optional_text,
    make_vector_text,
    prepare_vector_names,
)


def test_only_whole_cell_placeholders_are_removed() -> None:
    assert clean_optional_text(" １ ") is None
    assert clean_optional_text("N/A") is None
    assert clean_optional_text("40L防水袋") == "40L防水袋"
    assert clean_optional_text("16寸风扇") == "16寸风扇"


def test_vector_names_are_stable_bounded_and_deduplicated() -> None:
    names = ["蓝色40L防水袋", " 蓝色40L防水袋 ", "8L防水袋"] * 5
    prepared = prepare_vector_names(names)

    assert prepared == ("8L防水袋", "蓝色40L防水袋")
    assert make_vector_text(names) == "商品名称：8L防水袋；蓝色40L防水袋"
    assert all(len(name) <= 120 for name in prepared)
    assert len(prepared) <= 8
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `uv run pytest tests/catalog/test_normalize.py -v`

Expected: FAIL because the normalization functions do not exist.

- [ ] **Step 3: Implement the exact normalization rules**

Apply Unicode NFKC, trim, and collapse repeated whitespace. Treat only whole-cell values in `{0, 1, 无, n-a, n/a, na, none, null, unknown, 未知}` as placeholders after lowercase comparison. Preserve digits, capacities, dimensions, colors, and quantities inside valid names.

Truncate a vector name only when valid product text precedes one of these operational markers: `链接随便拍`, `链接拍`, `采购看`, `仓库看`, `下单备注`, `供应商按照`, `供应商打包`, `控制在`, `备货量`, `市场分析`, `头程费用`, `本地上传`. Sort unique names by `(len(normalized_name), normalized_name)` and keep the first 8.

- [ ] **Step 4: Run normalization tests**

Run: `uv run pytest tests/catalog/test_normalize.py -v`

Expected: PASS.

- [ ] **Step 5: Commit deterministic normalization**

```bash
git add src/store_scenario_inspiration/catalog/normalize.py tests/catalog/test_normalize.py
git commit -m "feat: add deterministic catalog normalization"
```

---

### Task 3: Read-only workbook loader and main-SKU document builder

**Files:**
- Create: `src/store_scenario_inspiration/catalog/workbook.py`
- Create: `src/store_scenario_inspiration/catalog/build.py`
- Create: `tests/catalog/test_workbook.py`
- Create: `tests/catalog/test_build.py`

**Interfaces:**
- Consumes: an external `.xlsx` path and optional sheet name.
- Produces: `iter_source_rows(path: Path, sheet_name: str | None = None) -> Iterator[SourceRow]`.
- Consumes: `Iterable[SourceRow]`.
- Produces: `build_documents(rows: Iterable[SourceRow]) -> tuple[ProductFamilyDocument, ...]`.

- [ ] **Step 1: Write a failing loader test**

Create a temporary workbook in the test with the required headers plus one extra column. Assert auto-discovery selects the only sheet containing all required headers, extra columns are accepted, SKU values remain strings, and a missing `主SKU` header raises `CatalogSchemaError` naming the missing header.

Required headers are exactly:

```python
REQUIRED_HEADERS = {
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
}
```

- [ ] **Step 2: Run the loader test and verify failure**

Run: `uv run pytest tests/catalog/test_workbook.py -v`

Expected: FAIL because `iter_source_rows` and `CatalogSchemaError` do not exist.

- [ ] **Step 3: Implement read-only row extraction**

Open with `load_workbook(path, read_only=True, data_only=True)`. If no sheet is supplied, choose the single sheet whose first row contains every required header. Reject zero or multiple matching sheets with an actionable error. Never call workbook save methods.

- [ ] **Step 4: Write failing builder tests for real edge-case shapes**

Construct synthetic `SourceRow` values representing:

- `ZXOD3713`: duplicate English name and keyword;
- `ZXOD2149`: 40L, 70L, and 8L children;
- `LSLFBA579A`: English fields equal to `1`;
- `ZXNXK0726-N2`: Chinese hat name with mismatched English underwear text;
- `test1123`: no usable name or category.

Assert one document per main SKU, all child variants remain attached, English duplicates collapse, vector text contains Chinese names only, placeholder English disappears, and the empty document has `searchable=False`.

- [ ] **Step 5: Run builder tests and verify failure**

Run: `uv run pytest tests/catalog/test_build.py -v`

Expected: FAIL because `build_documents` does not exist.

- [ ] **Step 6: Implement document construction**

Group rows by `主SKU`; reject rows with empty `sku` or `主SKU`. Determine each child leaf category from the last non-empty value in fourth, third, second, then first category, falling back to `商品目录`. Store the full non-empty category path once. Keep sales status at child level. Add `mixed_leaf_category`, `placeholder_english`, `operational_text`, and `unsearchable` flags only when their exact conditions occur.

- [ ] **Step 7: Run workbook and builder tests**

Run: `uv run pytest tests/catalog/test_workbook.py tests/catalog/test_build.py -v`

Expected: PASS.

- [ ] **Step 8: Commit the loader and builder**

```bash
git add src/store_scenario_inspiration/catalog/workbook.py src/store_scenario_inspiration/catalog/build.py tests/catalog/test_workbook.py tests/catalog/test_build.py
git commit -m "feat: build main sku catalog documents"
```

---

### Task 4: Quality gates and build comparison

**Files:**
- Create: `src/store_scenario_inspiration/catalog/quality.py`
- Create: `tests/catalog/test_quality.py`

**Interfaces:**
- Consumes: source rows and product-family documents.
- Produces: `validate_build(rows: Sequence[SourceRow], documents: Sequence[ProductFamilyDocument]) -> QualityReport`.
- Produces: `compare_quality(previous: QualityReport | None, current: QualityReport) -> QualityDelta`.
- Produces: `QualityGateError` containing every blocking invariant failure.

- [ ] **Step 1: Write failing invariant tests**

```python
def test_quality_gate_reconciles_every_child_once(rows, documents) -> None:
    report = validate_build(rows, documents)

    assert report.source_row_count == len(rows)
    assert report.child_count == len(rows)
    assert report.document_count == len({row.main_sku for row in rows})
    assert report.errors == ()


def test_duplicate_child_sku_blocks_activation(rows, documents) -> None:
    duplicated = [*rows, rows[0]]

    with pytest.raises(QualityGateError, match="duplicate child sku"):
        validate_build(duplicated, documents)
```

Also assert no searchable vector text contains its main SKU or child SKU, unsearchable documents do not request embeddings, and two builds from the same rows produce identical canonical JSON SHA-256 hashes.

- [ ] **Step 2: Run quality tests and verify failure**

Run: `uv run pytest tests/catalog/test_quality.py -v`

Expected: FAIL because the report and validation functions do not exist.

- [ ] **Step 3: Implement quality reporting**

The report must include source rows, child rows, documents, searchable documents, multi-variant groups, missing/placeholder counts, mixed-category documents, exact document collisions, canonical document hash, warnings, and errors. Version comparison reports absolute and percentage deltas but does not reject a legitimate catalog size change solely because it is large.

- [ ] **Step 4: Run quality tests**

Run: `uv run pytest tests/catalog/test_quality.py -v`

Expected: PASS.

- [ ] **Step 5: Commit quality gates**

```bash
git add src/store_scenario_inspiration/catalog/quality.py tests/catalog/test_quality.py
git commit -m "feat: validate catalog build quality"
```

---

### Task 5: Human-reviewable retrieval benchmark

**Files:**
- Create: `src/store_scenario_inspiration/catalog/benchmark.py`
- Create: `tests/fixtures/retrieval-benchmark-v1.json`
- Create: `tests/catalog/test_benchmark_fixture.py`
- Create: `scripts/run_retrieval_benchmark.py`

**Interfaces:**
- Defines: benchmark items with `query_id`, `query`, `relevant_main_skus`, `expected_status`, `label_status`, and `notes`.
- Produces: `load_benchmark(path: Path) -> tuple[BenchmarkItem, ...]`.
- Produces: `run_benchmark(search_fn: Callable[[str, int], Sequence[SearchHit]], items: Sequence[BenchmarkItem], top_k: int = 5) -> BenchmarkReport` with `hit_at_5`, `misses`, and returned IDs.
- Consumes later: `CatalogStore.keyword_search` directly, or a small callable that supplies a query vector to `HybridRetriever.search`.

- [ ] **Step 1: Write a failing benchmark-schema test**

```python
def test_benchmark_has_unique_confirmable_queries() -> None:
    items = load_benchmark(BENCHMARK_PATH)

    assert 20 <= len(items) <= 30
    assert len({item.query_id for item in items}) == len(items)
    assert all(item.expected_status in {"has_match", "no_reliable_match"} for item in items)
    assert all(item.label_status in {"provisional", "confirmed"} for item in items)
    assert all(item.relevant_main_skus for item in items if item.expected_status == "has_match")
```

- [ ] **Step 2: Create the 24-item provisional benchmark**

Use these exact query-to-family seeds, derived from the current workbook sampling. Keep `label_status="provisional"` until a human reviews the workbook names; the benchmark runner reports provisional results but refuses to enforce the 75% gate until every item is confirmed.

```json
[
  ["q01", "遮阳棚或天幕", ["3G1101", "3G1112", "3G1113"]],
  ["q02", "户外帐篷", ["3G1101", "3G1112", "3G1113"]],
  ["q03", "canopy tent", ["3G1101", "3G1112", "3G1113"]],
  ["q04", "折叠户外桌", ["3G0801", "3G0802", "3G0803"]],
  ["q05", "camping folding table", ["3G0801", "3G0802", "3G0803"]],
  ["q06", "露营折叠椅", ["3G0305", "3G0306", "3G0308"]],
  ["q07", "portable camping chair", ["3G0305", "3G0306", "3G0308"]],
  ["q08", "户外锂电风扇", ["2H1201"]],
  ["q09", "lithium battery fan", ["2H1201"]],
  ["q10", "太阳能露营灯笼", ["ZXOD3713"]],
  ["q11", "solar camping lantern", ["ZXOD3713", "2B0404", "2B0502"]],
  ["q12", "充气泳池", ["ZXOD3984", "ZXOD4039", "ZXOD4064"]],
  ["q13", "inflatable pool", ["ZXOD3984", "ZXOD4039", "ZXOD4064"]],
  ["q14", "充气泵或打气筒", ["HY0426", "ZXOD3200", "3G1901"]],
  ["q15", "野餐防潮垫", ["ZXOD2701"]],
  ["q16", "picnic mat", ["ZXOD2701"]],
  ["q17", "防水收纳袋", ["3X0901", "ZXOD2149", "ZXOD3071"]],
  ["q18", "40L waterproof bag", ["ZXOD2149"]],
  ["q19", "折叠手推车", ["2H1401", "4D0103", "GYLCW-015-02"]],
  ["q20", "folding wagon", ["2H1401", "4D0103", "GYLCW-015-02"]],
  ["q21", "户外工作灯", ["HCA034", "2B0201"]],
  ["q22", "extension cord storage reel", ["CNFBA195"]],
  ["q23", "商品展示架", ["X1MZCLJ"]],
  ["q24", "wooden display rack", ["X1MZCLJ"]]
]
```

Convert each compact seed to the full object schema. Set `expected_status="has_match"`, add a short workbook-grounded note, and do not invent negative examples before inspecting whether ERP truly lacks a candidate.

- [ ] **Step 3: Run the schema test and verify it fails before the loader exists**

Run: `uv run pytest tests/catalog/test_benchmark_fixture.py -v`

Expected: FAIL because the benchmark loader and report types do not exist.

- [ ] **Step 4: Implement the benchmark loader and runner**

Validate every field, reject duplicate query IDs, and compute Hit@5 only over `expected_status="has_match"`. Return both aggregate metrics and every miss. If any label remains provisional, print metrics with `enforced=false`; when all labels are confirmed, exit non-zero if Hit@5 is below `0.75`.

- [ ] **Step 5: Run the benchmark contract test**

Run: `uv run pytest tests/catalog/test_benchmark_fixture.py -v`

Expected: PASS with 24 provisional queries.

- [ ] **Step 6: Commit the benchmark contract**

```bash
git add src/store_scenario_inspiration/catalog/benchmark.py tests/fixtures/retrieval-benchmark-v1.json tests/catalog/test_benchmark_fixture.py scripts/run_retrieval_benchmark.py
git commit -m "test: add catalog retrieval benchmark"
```

---

### Task 6: SQLite document store and FTS keyword index

**Files:**
- Create: `src/store_scenario_inspiration/catalog/storage.py`
- Create: `tests/catalog/test_storage.py`

**Interfaces:**
- Produces: `CatalogStore.create(path: Path, documents: Sequence[ProductFamilyDocument], manifest: BuildManifest) -> CatalogStore`.
- Produces: `CatalogStore.open_readonly(path: Path) -> CatalogStore`.
- Produces: `CatalogStore.keyword_search(query: str, limit: int) -> tuple[SearchHit, ...]`.
- Produces: `CatalogStore.get_document(main_sku: str) -> ProductFamilyDocument | None`.

- [ ] **Step 1: Write a failing FTS test**

```python
def test_keyword_search_prefers_chinese_name_over_english_alias(tmp_path, documents) -> None:
    store = CatalogStore.create(tmp_path / "catalog.sqlite3", documents, manifest())

    hits = store.keyword_search("户外太阳能灯笼", limit=5)

    assert hits[0].main_sku == "ZXOD3713"
    assert hits[0].sources == ("keyword",)
```

Add tests that `main_sku` is an unindexed FTS metadata column, child `sku` is absent from FTS, duplicate English name/keyword is stored once, and child sales status is returned without being indexed.

- [ ] **Step 2: Run storage tests and verify failure**

Run: `uv run pytest tests/catalog/test_storage.py -v`

Expected: FAIL because `CatalogStore` does not exist.

- [ ] **Step 3: Implement the static SQLite schema**

Create ordinary tables `documents`, `children`, and `build_meta`, plus FTS5 table `product_fts(main_sku UNINDEXED, cn_names, categories, en_aliases)`. Insert Chinese names into the highest-weight FTS column, categories next, and English aliases last. Store the full canonical document JSON in `documents` for lossless reconstruction.

- [ ] **Step 4: Implement safe query construction**

Normalize the query, quote individual tokens for FTS, reject an empty query, and use `bm25(product_fts, 0.0, 8.0, 4.0, 1.0)` so the unindexed ID contributes no score and Chinese/category evidence outranks English-only evidence.

- [ ] **Step 5: Run storage tests**

Run: `uv run pytest tests/catalog/test_storage.py -v`

Expected: PASS.

- [ ] **Step 6: Commit SQLite and FTS storage**

```bash
git add src/store_scenario_inspiration/catalog/storage.py tests/catalog/test_storage.py
git commit -m "feat: add sqlite catalog keyword index"
```

---

### Task 7: Embedding reuse and exact in-memory vector search

**Files:**
- Create: `src/store_scenario_inspiration/catalog/vectors.py`
- Create: `tests/catalog/test_vectors.py`

**Interfaces:**
- Defines: `EmbeddingProvider.model_id: str`, `EmbeddingProvider.dimension: int`, and `EmbeddingProvider.embed(texts: Sequence[str]) -> numpy.ndarray` protocol.
- Produces: `build_vector_matrix(documents, provider, cache_dir) -> VectorArtifact`.
- Produces: `ExactVectorIndex.load(matrix_path, rows_path) -> ExactVectorIndex`.
- Produces: `ExactVectorIndex.search(query_vector: numpy.ndarray, limit: int) -> tuple[SearchHit, ...]`.

- [ ] **Step 1: Write a deterministic fake provider and failing cache test**

```python
class RecordingProvider:
    model_id = "test-embedding-v1"
    dimension = 3

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        self.calls.append(tuple(texts))
        return np.asarray([[len(text), text.count("户外"), 1.0] for text in texts], dtype=np.float32)


def test_unchanged_vector_text_reuses_embedding(tmp_path, documents) -> None:
    provider = RecordingProvider()

    first = build_vector_matrix(documents, provider, tmp_path / "cache")
    second = build_vector_matrix(documents, provider, tmp_path / "cache")

    assert len(provider.calls) == 1
    np.testing.assert_array_equal(first.matrix, second.matrix)
```

- [ ] **Step 2: Run vector tests and verify failure**

Run: `uv run pytest tests/catalog/test_vectors.py -v`

Expected: FAIL because the vector module does not exist.

- [ ] **Step 3: Implement the embedding cache**

Compute each cache key as SHA-256 of `model_id + "\0" + document.vector_text_v1`. Batch only cache misses through the provider. Validate returned shape and finite Float32 values. Do not request embeddings for `searchable=False` documents. Save the final matrix with `numpy.save` and save the row-to-main-SKU mapping as deterministic JSON.

- [ ] **Step 4: Implement exact cosine search**

Normalize stored rows once on load, normalize the query, use one matrix multiplication, sort descending with a stable secondary main-SKU order, and map cosine similarity from `[-1, 1]` to `[0, 1]`. Reject a query vector with the wrong dimension or non-finite values.

- [ ] **Step 5: Run vector tests**

Run: `uv run pytest tests/catalog/test_vectors.py -v`

Expected: PASS, including cache reuse, changed-text re-embedding, dimension errors, unsearchable exclusion, and deterministic tie order.

- [ ] **Step 6: Commit vector infrastructure**

```bash
git add src/store_scenario_inspiration/catalog/vectors.py tests/catalog/test_vectors.py
git commit -m "feat: add cached exact vector search"
```

---

### Task 8: Hybrid retrieval and platform-aware child filtering

**Files:**
- Create: `src/store_scenario_inspiration/catalog/retrieval.py`
- Create: `tests/catalog/test_retrieval.py`

**Interfaces:**
- Defines: `RetrievalQuery(text: str, platform: str | None, country: str | None)`.
- Produces: `HybridRetriever.search(query: RetrievalQuery, query_vector: numpy.ndarray | None, limit: int = 5) -> tuple[SearchHit, ...]`.
- Consumes: `CatalogStore` and optional `ExactVectorIndex`.

- [ ] **Step 1: Write failing hybrid ranking tests**

Create fixed keyword ranks and vector ranks. Assert reciprocal-rank fusion uses:

```python
fused_score = sum(weight / (60 + rank) for each_source_rank)
```

Use weight `1.0` for keyword and `1.0` for vector. Assert the same main SKU returned by both channels outranks a candidate returned by only one channel at the same rank, results deduplicate by main SKU, and `query_vector=None` produces a keyword-only result without error.

- [ ] **Step 2: Write failing platform-filter tests**

Use `YNFBA997` with child status `Shopee违禁品` and `ZXMO528` with only one child marked `Ali-高退款(其他)`. Assert Shopee filters the explicitly prohibited child, an omitted platform retains it with a warning, and a child-level Ali warning never removes the whole main-SKU family.

- [ ] **Step 3: Run retrieval tests and verify failure**

Run: `uv run pytest tests/catalog/test_retrieval.py -v`

Expected: FAIL because `HybridRetriever` does not exist.

- [ ] **Step 4: Implement retrieval fusion**

Fetch at least `max(50, limit * 10)` candidates from each available channel, fuse ranks, attach keyword/vector evidence, then apply explicit child-level platform status rules. Keep risk tokens such as infringement, high refunds, and quality warnings visible without treating them as global bans.

- [ ] **Step 5: Run retrieval tests**

Run: `uv run pytest tests/catalog/test_retrieval.py -v`

Expected: PASS.

- [ ] **Step 6: Commit hybrid retrieval**

```bash
git add src/store_scenario_inspiration/catalog/retrieval.py tests/catalog/test_retrieval.py
git commit -m "feat: add hybrid catalog retrieval"
```

---

### Task 9: Versioned rebuild, atomic activation, rollback, and CLI

**Files:**
- Create: `src/store_scenario_inspiration/catalog/versioning.py`
- Create: `src/store_scenario_inspiration/catalog/cli.py`
- Create: `tests/catalog/test_versioning.py`
- Create: `tests/catalog/test_cli.py`
- Create: `README.md`

**Interfaces:**
- Produces: `CatalogIndexManager.rebuild(source: Path, sheet_name: str | None, provider: EmbeddingProvider | None) -> BuildManifest`.
- Produces: `CatalogIndexManager.active_manifest() -> BuildManifest | None`.
- Produces: `CatalogIndexManager.rollback() -> BuildManifest`.
- CLI commands: `rebuild`, `status`, `search`, and `rollback`.

- [ ] **Step 1: Write failing activation and rollback tests**

```python
def test_failed_rebuild_keeps_active_version(tmp_path, valid_source, invalid_source) -> None:
    manager = CatalogIndexManager(tmp_path / "catalog")
    first = manager.rebuild(valid_source, sheet_name=None, provider=None)

    with pytest.raises(QualityGateError):
        manager.rebuild(invalid_source, sheet_name=None, provider=None)

    assert manager.active_manifest().version_id == first.version_id


def test_rollback_swaps_active_and_previous(tmp_path, source_v1, source_v2) -> None:
    manager = CatalogIndexManager(tmp_path / "catalog")
    first = manager.rebuild(source_v1, sheet_name=None, provider=None)
    manager.rebuild(source_v2, sheet_name=None, provider=None)

    restored = manager.rollback()

    assert restored.version_id == first.version_id
```

- [ ] **Step 2: Run versioning tests and verify failure**

Run: `uv run pytest tests/catalog/test_versioning.py -v`

Expected: FAIL because `CatalogIndexManager` does not exist.

- [ ] **Step 3: Implement staged version creation**

Calculate source SHA-256 before reading. Skip only when source hash, document schema version, cleaning-rules version, and embedding model ID all equal the active manifest; a code-rule or model change must rebuild even when Excel is unchanged. Otherwise build under `versions/.staging-<uuid>`, write SQLite, optional vectors, manifest, and quality report, fsync files, rename the staging directory to its final version ID, then replace `active.json` through `active.json.tmp` using `os.replace`. Update `previous.json` only after the new version directory is complete.

Build identity is SHA-256 of source hash, schema version, cleaning-rules version, and embedding model ID. Version ID format is UTC `YYYYMMDDTHHMMSSZ-<first 12 build-identity characters>`. A provider-free build uses embedding model ID `none`, is valid, and reports `vector_status="absent"`; this allows the keyword baseline to be measured before choosing a real embedding provider.

- [ ] **Step 4: Implement the CLI**

Supported commands and exit behavior:

```text
store-catalog rebuild --source <xlsx> [--sheet <name>] [--index-root var/catalog]
store-catalog status [--index-root var/catalog]
store-catalog search --query <text> [--platform <name>] [--top-k 5] [--index-root var/catalog]
store-catalog rollback [--index-root var/catalog]
```

`rebuild` prints version ID, source hash, document/child counts, changes from the previous version, quality warnings, and whether vectors are present. Schema or quality failure returns a non-zero exit and prints that the previous index remains active. `search` performs keyword-only retrieval until a production embedding provider is connected in the next subsystem plan.

- [ ] **Step 5: Write and run CLI integration tests**

Run: `uv run pytest tests/catalog/test_cli.py -v`

Expected: PASS for successful rebuild/status/search, unchanged-source skip, invalid-source non-zero exit, and rollback.

- [ ] **Step 6: Document the operator workflow**

Write README commands using the current source path as an example, state that the workbook remains read-only, explain full document rebuild plus embedding reuse, and state explicitly that no vector database installation is required. Document that a real embedding provider is selected only after the keyword baseline and 20–30 labeled retrieval queries are measured.

- [ ] **Step 7: Run the complete automated suite**

Run: `uv run pytest -v`

Expected: all tests PASS.

- [ ] **Step 8: Run the current-workbook smoke build**

Run:

```powershell
uv run store-catalog rebuild --source 'E:\download\Chrome下载\产品列表下载20260831093944283.xlsx' --index-root 'var\catalog'
```

Expected:

```text
source_rows=13072
documents=8550
children=13072
quality_errors=0
vector_status=absent
active_version=<UTC timestamp>-<12-character build hash>
```

Run the same command again and verify it reports the same source hash and skips rebuilding. Then run a keyword search for `户外太阳能灯笼` and verify `ZXOD3713` appears in the first five results.

- [ ] **Step 9: Review the final diff and commit the operable foundation**

Run: `git diff --check && git status --short`

Expected: no whitespace errors and only the intended catalog foundation files changed.

```bash
git add src/store_scenario_inspiration/catalog/versioning.py src/store_scenario_inspiration/catalog/cli.py tests/catalog/test_versioning.py tests/catalog/test_cli.py README.md
git commit -m "feat: add versioned catalog rebuild workflow"
```

---

## Completion Gate

This plan is complete only when:

- the synthetic edge cases and all automated tests pass;
- the current external workbook reconciles to 13,072 children and 8,550 main-SKU documents;
- rebuilding an unchanged file is skipped;
- a failed build leaves the active version unchanged;
- rollback restores the previous successful version;
- keyword search returns representative known products;
- the 24-query benchmark fixture passes schema validation and reports provisional Hit@5 without enforcing unreviewed labels;
- the vector engine and embedding cache pass deterministic tests without an external database;
- runtime artifacts remain ignored by Git.

After this gate, have the provisional benchmark labels reviewed, then create a separate implementation plan for the real embedding provider, screenshot recognition, unified 9-scene generation, and the one-page MVP UI. The embedding provider decision is made from keyword-baseline quality, operating constraints, and whether the company already has an approved model API; it is not coupled to the vector storage design.
