# Store Scenario Inspiration — Catalog Retrieval Foundation

这部分工程把 ERP 产品列表构建为可回滚的本地检索索引。当前阶段只提供关键词基线；不会修改源 Excel，也不依赖外部向量数据库。

## 环境

项目使用 Python 3.12 与 `uv`：

```powershell
uv sync --dev
uv run pytest -v
```

源工作簿始终位于 Git 仓库之外，并以 openpyxl 的 `read_only=True, data_only=True` 模式读取。构建流程不会保存、复制或重写它。当前已核验源表示例：

```text
E:\download\Chrome下载\产品列表下载20260831093944283.xlsx
```

## 构建与更新

`--index-root` 的相对路径由命令当前工作目录解析。为避免把 runtime 建到意外位置，请先切到仓库根目录，或始终传绝对路径：

```powershell
uv run store-catalog rebuild `
  --source 'E:\download\Chrome下载\产品列表下载20260831093944283.xlsx' `
  --index-root 'E:\Project\store-scenario-inspiration\.worktrees\catalog-retrieval-foundation\var\catalog'
```

如果源文件 SHA-256、document schema version、cleaning rules version 和 embedding model ID 四项都与 active manifest 相同，命令输出 `skipped=true`，不读取工作簿，也不改动版本或指针。任一身份项变化都会新建完整版本。

产品列表更新采用“全量文档重建 + embedding cache 复用”，而不是维护脆弱的行级增量状态：

1. 读取前计算源文件 SHA-256；
2. 在 `versions/.staging-*` 中全量重建主 SKU 文档和 SQLite FTS；
3. 通过 `embedding model ID + vector text hash` 复用未变化文本的 embedding；
4. 质量检查、源文件二次哈希、制品哈希和 SQLite 一致性检查全部成功后，才原子切换 active state；
5. 失败时 active 版本保持不变，完成但未激活的 orphan 版本不会被检索使用。

当前 CLI 不连接 embedding provider，因此输出 `vector_status=absent`。8,550 级别的商品族可由 SQLite 与可选连续 NumPy 矩阵支持，无需安装或运维独立 vector database。每个 index root 自动采用非阻塞的跨进程 single-writer lock：同时运行第二个 rebuild 或 rollback 会清晰失败；只读的 status 与 search 不加锁。

## 状态、检索与回滚

```powershell
uv run store-catalog status --index-root 'var\catalog'

uv run store-catalog search `
  --query '户外太阳能灯笼' `
  --platform 'Shopee' `
  --top-k 5 `
  --index-root 'var\catalog'

uv run store-catalog rollback --index-root 'var\catalog'
```

`search` 以只读方式打开 active SQLite，使用 `HybridRetriever` 的 keyword-only 路径，并保留平台级子 SKU 风险过滤。`rollback` 交换 active 与 previous，因此再次执行可回到刚才的版本。没有 previous 或两个指针相同会明确报错。

## Keyword baseline

当前 fixture 有 24 条 provisional 查询，处于设计要求的 20–30 条人工标签范围内。运行 active keyword baseline：

```powershell
uv run python scripts\run_retrieval_benchmark.py `
  --benchmark tests\fixtures\retrieval-benchmark-v1.json `
  --index-root 'var\catalog'
```

报告会输出 provisional `Hit@5`，且在标签尚未全部人工确认时保持 `enforced=false`；低分不会让进程强制失败。应先人工复核查询、相关主 SKU 和明显错误类型，再依据 keyword baseline、运行约束以及公司是否已有 approved model API 选择真实 embedding provider。未经确认的 provisional 分数不能作为上线门槛，也不应通过修改 fixture 来迎合实现。

## Runtime artifacts

运行时目录被 `.gitignore` 中的 `var/` 排除，不提交源表、索引、embedding、模型或本地分析产物：

```text
var/catalog/
  active.json
  previous.json
  .writer.lock
  embedding-cache/
    embeddings/*.npy
  versions/<UTC timestamp>-<build identity prefix>/
    catalog.sqlite3
    manifest.json
    quality.json
    delta.json
    artifacts.json       # catalog、reports 和可选 vector 文件的 SHA-256
    vectors.npy          # 仅 provider build
    vector-rows.json     # 仅 provider build
```

每个已完成版本都是不可变且自包含的；不会自动删除旧版本。需要清理 runtime 数据时，应先确认 active/previous 指针和回滚需求，再由操作员另行处理。
