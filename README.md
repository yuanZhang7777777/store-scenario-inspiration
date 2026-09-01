# Store Scenario Inspiration — Local Hybrid Catalog Retrieval

这部分工程把 ERP 产品列表构建为可回滚的本地混合检索索引。SQLite FTS5 负责关键词召回，FastEmbed 在本机运行 `BAAI/bge-small-zh-v1.5` 生成 512 维中文向量，NumPy 在内存中执行精确余弦检索；不会修改源 Excel，也不依赖外部向量数据库。

## 环境

项目使用 Python 3.12 与 `uv`：

```powershell
uv sync --dev
```

`fastembed` 安装在项目 `.venv` 中；模型、产品向量和索引都位于所选 `--index-root` 下并被 Git 忽略。模型首次使用时下载约 96 MB，后续复用本地缓存。

源工作簿始终位于 Git 仓库之外，并以 openpyxl 的 `read_only=True, data_only=True` 模式读取。构建流程不会保存、复制或重写它。当前已核验源表示例：

```text
E:\download\Chrome下载\产品列表下载20260831093944283.xlsx
```

## 构建与更新

`--index-root` 的相对路径由命令当前工作目录解析。为避免把 runtime 建到意外位置，请先切到仓库根目录，或始终传绝对路径：

```powershell
uv run store-catalog rebuild `
  --source 'E:\download\Chrome下载\产品列表下载20260831093944283.xlsx' `
  --with-vectors `
  --index-root 'E:\Project\store-scenario-inspiration\.worktrees\catalog-retrieval-foundation\var\catalog'
```

只有源文件 SHA-256、源文件 UTC 更新时间、所选 sheet（自动选择为 `none`）、document schema version、cleaning rules version 和 embedding model ID 都与 active manifest 相同，命令才输出 `skipped=true`。任一身份项变化都会新建完整版本，因此同一工作簿切换 Sheet A/Sheet B 不会错误跳过。

产品列表更新采用“全量文档重建 + embedding cache 复用”，而不是维护脆弱的行级增量状态：

1. 读取前计算源文件 SHA-256；
2. 在 `versions/.staging-*` 中全量重建主 SKU 文档和 SQLite FTS；
3. 通过 `embedding model ID + vector text hash` 复用未变化文本的 embedding；
4. 质量检查、源文件二次哈希、制品哈希和 SQLite 一致性检查全部成功后，才原子切换 active state；
5. 失败时 active 版本保持不变，完成但未激活的 orphan 版本不会被检索使用。

使用 `--with-vectors` 时，CLI 通过 FastEmbed 连接本地 BGE provider，成功构建后输出 `vector_status=present`。8,550 级别的商品族使用约 16.7 MiB 的连续 Float32 矩阵即可精确检索，无需安装或运维独立 vector database。每个 index root 自动采用非阻塞的跨进程 single-writer lock：同时运行第二个 rebuild 或 rollback 会清晰失败；只读的 status 与 search 不加锁。

`active.json` 是唯一的恢复权威，且始终包含当前版本及其 previous 版本的完整 pair。`previous.json` 只是为旧工具保留的派生兼容文件；它在崩溃窗口中可能暂时落后，绝不能用于 rollback 或任何恢复决策。

## 状态、检索与回滚

```powershell
uv run store-catalog status --index-root 'var\catalog'

uv run store-catalog search `
  --query '露营椅' `
  --expanded-query '户外折叠椅' `
  --expanded-query '月亮椅' `
  --platform 'Shopee' `
  --top-k 20 `
  --index-root 'var\catalog'

uv run store-catalog rollback --index-root 'var\catalog'
```

`search` 以只读方式打开 active SQLite 和向量矩阵。原始产品名的融合权重为 1.0，每个由 Codex 生成的正向 `--expanded-query` 权重为 0.7；每个查询分别执行关键词 Top 50 和向量 Top 50，再用偏移量 60 的加权 RRF 按主 SKU 去重。最终 `--top-k` 默认为 20，可设为 1–50。结果返回代表产品名称、子 SKU、命中查询词和关键词/向量来源；语义层不使用排除词或硬相似度阈值，平台明确禁售仍按子 SKU 生效。

这些结果是高召回候选，不直接等同于最终推荐。Codex 使用“场景商品名称 + 用途”对候选的中文名称、英文别名和商品目录做四档软重排：`3=直接匹配`、`2=替代或配套`、`1=场景相邻`、`0=仅词面相关`。所有候选继续保留，只按相关性档位降序、原始 RRF 排名升序稳定排序；缺少、重复或越界的 Codex 判断必须报错，不能静默丢商品。

Codex 负责从截图生成场景、场景中的具体产品、正向扩写词和候选软重排判断；本地 CLI 只负责检索，不在本地重复调用生成模型。

`rollback` 交换 active 与 previous，因此再次执行可回到刚才的版本。没有 previous 或两个指针相同会明确报错。

`status` 和 `rebuild` 都输出 `source_sheet_name` 与 `source_modified_at`，用于核对实际读取的工作表与源文件更新时间。

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
  model-cache/               # FastEmbed/BGE 模型文件
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
