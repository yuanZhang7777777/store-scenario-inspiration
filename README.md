# Store Scenario Inspiration — Local Hybrid Catalog Retrieval

> 当前运行基线已经切换为 8,126 个有效主 SKU 的中英文双通道：`BAAI/bge-large-zh-v1.5` 和 `BAAI/bge-large-en-v1.5` 各 1024 维，Qdrant 集合 `product_asset_bilingual_1024_v1` 使用 `cn/en` 命名向量。下方 2026-09-10 英文单通道内容保留为历史兼容说明，不是当前店铺试跑入口。

2026-09-18 已接通 DeepSeek Flash 批量链路：钉钉店铺字段与内嵌截图读取、截图商品识别、店铺与场景分析、正向中英文扩写、中文/英文向量与 FTS5 四路加权 RRF、DeepSeek 软重排、国家库存关联和 Excel 导出。当前交付为光旅 10 店与集团八部 PH/TH/VN/MY 10 店；GPT-5.5 结果只保留为历史对照。运行资产位于 `E:/Project/store-assortment-copilot/var/pilot`，完整业务规划见 [2026-09-18 生产化规划](E:/Project/store-assortment-copilot/docs/2026-09-18-store-analysis-rag-roadmap.md)。

本地前端为 React 工作台：选择国家 + 截图或商品名 → 一键生成经营建议与候选商品 → 按需调整与勾选 → 导出 Excel；没有识别后的人工确认关卡。流程见 [完整流程](docs/2026-09-22-flow-and-branches.md)，尚未接入应用的新 UI 方案见 [导出优先设计](docs/2026-09-22-export-first-design.md)。

```powershell
# 后端：FastAPI，监听 127.0.0.1:8000
# 开发时加 --reload：改完后端代码它会自己重启。不加的话进程还跑着旧代码，
# 而前端已经按新接口取字段，点开店铺会白屏。
uv run uvicorn store_scenario_inspiration.app.main:create_app --factory --port 8000 --reload

# 前端：Vite + React，开发端口 5173，/api 由 Vite 代理到 8000，因此不需要配 CORS
cd frontend
npm install
npm run dev

# 前端检查：类型检查 + 生产构建，以及界面逻辑测试
npm run build
npm test
```

旧的无构建链评审页（`python scripts\serve_pilot_dashboard.py`，:8787）保留作对照；它的页面文件已挪到 `frontend/legacy/index.html`，不再是最前面的入口。

核心新入口：

- `src/store_scenario_inspiration/catalog/pilot.py`：双语 FTS5、命名向量查询、加权 RRF、国家库存标注。
- `scripts/run_store_pilot.py`：读取结构化扩写结果，查询词一次批量编码并缓存，逐场景商品生成审计结果。
- `scripts/assemble_store_pilot.py`：合并模型软重排和国家库存，保留原始召回。
- `scripts/deepseek_prepare_store_batch.py`：DeepSeek 多模态读取每店全部截图并生成结构化商品线索；只接受 PH/TH/VN/MY。
- `scripts/run_deepseek_batches.py`：按 manifest 续跑 DeepSeek 分析、扩写、检索、重排和组装；非四国任务在 API 调用前失败。
- `frontend/`：React + Vite 工作台，也就是现在的应用界面；`src/store_scenario_inspiration/app/` 是它的后端。
- `scripts/serve_pilot_dashboard.py` + `frontend/legacy/index.html`：旧的无构建链评审页，只作对照。

主 SKU 双语名称清洗契约见 [主 SKU 双语名称清洗契约](docs/2026-09-17-main-sku-bilingual-name-cleaning-contract.md)。标准名称已保留原始字段并形成当前产品资产；后续只对新增或证据变化的主 SKU 增量处理。

> 2026-09-10 已修复错误 A1 维度及多表漏读，完整处理 16 表、613,072 行；612,960 行归入 223,483 个主 SKU，112 行缺主 SKU 单独保存在 `source-import.json`。新完整关键词库在 `E:/Project/store-assortment-copilot/var/catalog-full`，旧 8,550 主 SKU 索引未覆盖。四列输出和国家库存规则见 [按国家现货选品方案](docs/2026-09-09-stock-aware-mvp-review.md)。

历史 MVP 结果四列为：场景、相关产品关键词、ERP主SKU、目标国家有货主SKU；具体子款由运营选择。基础需求场景必须覆盖，热点按相关性补充。`scripts/local_catalog.py` 输出高召回候选，不代表已经过模型软重排的最终推荐。2026-09-16 新需求增加店铺经营分析、主SKU与中文分列、国家数量/单销/ERP审核时间；当时尚未实现完整报告，不能把历史四列当成当前交付。

已落实用户确认规则：`1A0000` 整族排除，以及 2026-09-10 确认表内“明确用途待确认”全部、“其他清洗待确认”商品名称含“售后”的精确子 SKU 排除（含原11项共332项，名单为 `src/store_scenario_inspiration/catalog/confirmed_excluded_skus.json`）。同族正常商品保留；新完整构建只用未排除子款生成中英文、目录和向量文本，原始 children 留作审计。清洗规则版本为4（包含完整多表读取与缺主SKU审计），名单未扩大。GPT-5.5负责本轮英文文档整理；先前30个双语核心名称仅是历史样本，未批量应用。

2026-09-10清洗确认已全部定案：332项排除，其余3,627项保留，现货4项是保留项的子集。原待确认表保留表名、确认列改为“保留”；无本轮未决排除项。该确认不改变现货候选集合或清洗规则版本。

## 环境

### 当前本机运行入口

代码位于 `E:/Project/store-scenario-inspiration/.worktrees/catalog-retrieval-foundation`。先在这个目录运行：

```powershell
.\scripts\start-qdrant.ps1

# 每日获得完整库存新文件后，再运行；使用新文件的实际路径。
.\.venv\Scripts\python.exe -X utf8 scripts/local_catalog.py index-stock `
  --stock 'E:\download\Chrome下载\真仓库存明细数据-普通商品-汇总数据-1788915612265.xlsx'

# 同一批英文原词和扩写词共用一次模型加载。scene/product_label可用中文展示。
.\.venv\Scripts\python.exe -X utf8 scripts/local_catalog.py search --country PH `
  --requests 'E:\Project\store-assortment-copilot\var\outputs\2026-09-10-local-mvp\ph-requests-en.json' `
  --output 'E:\Project\store-assortment-copilot\var\outputs\2026-09-10-local-mvp\ph-retrieval-en.json'
```

- 官方 [Qdrant v1.19.1 Windows 包](https://github.com/qdrant/qdrant/releases/tag/v1.19.1) 位于 `E:/Project/store-assortment-copilot/var/services/qdrant/v1.19.1/qdrant.exe`。存储、配置、日志都在同级 `qdrant` 目录；仅监听 `127.0.0.1:6333/6334`，关闭遥测，未装 Docker/WSL、未开公网、未注册开机服务。重启电脑后手动运行启动脚本。
- `catalog-full`：完整ERP关键词库；`catalog-stock-en`：英文库存状态、embedding缓存和每主SKU实际向量文本审计；模型位于`models/fastembed`。这些目录均在`E:/Project/store-assortment-copilot/var`。旧中文`catalog-stock`及代码目录`var/catalog/model-cache`均保留。
- 现货池1,067个主SKU均有有效英文可建向量，`missing_english=[]`。`SH-CW-2339`依据完整ERP中文名“黑色镂空蕾丝短裤 L/M/S/XL”补为`Lace shorts`；`WATOY320`依据完整ERP中文名“迷你古代兵器模型挂件”和原英文别名`1005009609633212`补为`Miniature ancient weapon model pendant`；补名只在源有效英文为空时使用，原始ERP数据库不改。各国库存主SKU仍为PH436、TH256、VN282、MY126，同一主SKU可在多国有货。完整ERP无货商品目前只有旧英文字段关键词召回，不承诺全ERP向量覆盖。
- 模型由FastEmbed运行ONNX CPU后端，2线程、batch=4，未安装CUDA。官方模型文件1,336,854,282字节，SHA256 `523c08e39a645aa3760380dee5d4b432b2f3ce7ba77ff2cd9c02c5a896b6a7c5`；首次下载后离线加载。1024维不保证每条结果更准，仍须看真实候选。
- 原词/扩写词分别召回关键词和向量候选，再以RRF融合。扩写允许同一产品族的材质、形态和用途子类，每种语言最多6个；当前每查询每通道 Top100、融合后全局 Top80，80个候选全部交给 DeepSeek，最多返回40个审计候选。最终只取同类/同产品族/同核心用途的相关度2–3，按产品需求轮流去重后每店最多300个主SKU；国家库存只标记，不删除语义推荐。
- 当前批量脚本会调用 DeepSeek Flash 完成截图识别、场景分析、扩写和软重排，并分别保存 API 回执；API Key 只从环境变量或交互输入读取，不写入产物。
- 库存按同国任一未排除明细“海外仓可发 > 0”判断，不累加销售员数量。成功导入后原子切换国家集合，旧零库存/消失项撤销。按2026-09-16用户确认，个别正库存主SKU缺ERP档案时单列原行证据，不阻断已匹配商品刷新，不伪造商品档案或当成零库存；CLI状态/检索结果返回缺口。表头错误、未知国家、已知SKU归属冲突、国家覆盖改变仍报错保留旧状态。不能据此保证所有业务层漏导都能被发现。
- 当前为单人串行手动导入，CLI 用本机锁协调更新与检索。同模型/文本策略沿用 Qdrant 集合及稳定主SKU点ID，只 upsert 新增/变化英文文本，未变化向量复用；零库存不删除语义向量，国家快照决定是否有货。更新中断会阻止检索混用新旧索引，重新运行 `index-stock` 恢复。未做多人服务、历史结果自动刷新或库存自动下载。
- Windows版创建可选payload字段索引报路径错误，当前省去该辅助索引，国家过滤仍实际生效。当前英文集合1,067点在Qdrant内扫描，未强制建立HNSW（`indexed_vectors_count=0`不代表没有向量）；扩大规模前须重新核验部署和性能。

旧中文512维基线：3类商品加6扩写词从main入口计时3.10秒（含模型加载和全库制品校验），query embedding约22ms，每类双范围融合163–272ms，命令墙钟约5.24秒。该计时不适用于新英文大模型，也不含识图、场景生成、软重排或API，不能作为多人SLA。

英文1024维历史基线（2026-09-10）：首次库存导入/向量构建80.370秒，1065条向量；3类商品+6扩写词单次命令墙钟7.935秒，main计时6.247秒，其中9词编码492.49ms，各类全局+PH融合201–393ms。PH存储卡`ZF3C0209`从旧基线第3升至第1；便携榨汁机首项变为食品处理器`1X1801`，但第2仍是雾化器，说明英文切换有效果但候选仍需软重排。

当前英文1024维实跑（2026-09-10）：`index-stock` 成功生成集合 `stock_en_20260910_084051_ffde49`，vector_count/main_sku_count均为1067，耗时78.356秒；策略为`erp-english-aliases-v2-sourced-fallbacks`，状态文件新增`keywords_file`指向同collection身份的`.keywords.sqlite3`。已用真实库限定补名主SKU验证：`lace shorts`经英文`keyword_search`返回`SH-CW-2339`，`miniature ancient weapon model pendant`返回`WATOY320`。当前仍是候选召回基础，不宣称整体准确率提升或生产性能达标。

### 光旅单人试跑

2026-09-10用Hermes现有企业凭证读取`光旅部门`（sheetId `kgqie6hm`）。图片须请求`select=complexValues`并读取`texts[type=image].resourceId`，再通过`GET /v1.0/doc/docs/resources/{workbookId}/{resourceId}/downloadInfo`下载；纯值为空不代表没截图，无需网页登录。选陈韦臻本人第3/14行，两家TH店7图，其余无图店本轮不生成。

34类商品/102个英文原词与扩写批量检索main计时19.906秒，编码5.476秒；不含识图、场景、Codex软重排和发布。3146条候选关系完整留审计，相关703个去重ERP主SKU中89个在TH快照有货；12个场景、61行四列已发布[陈韦臻单人在线表](https://alidocs.dingtalk.com/i/nodes/1DKw2zgV2PonxLBqCvzZlEjl8B5r9YAn)，两个页签按店分开，全值回读一致。库存文件更新时间2026-09-09 14:56，非实时有货承诺。

同日按用户要求在原在线表两列主SKU后追加`主SKU（ERP中文代表名称）`，不改变主SKU索引、分组或库存；702个有原中文名，HXG26标注“暂无中文名称”。当前在线值对应`results-four-columns-cn-names.json`，原JSON/XLSX保留为更新前版本；`add_cn_names.py`只做显示补名，`publish_online.py update-names`先检查原表、备份再更新C/D列，并回读61行全部四列。

写入指定folder使用官方v2 storage上传+commit，`convertToOnlineDoc=true`及`convertToOnlineDocTargetDocumentType=WORKBOOK`，生成可编辑`axls`。执行身份`hermes-remote-lxc`，锁`group1_alidocs.lock`，凭证不离开Hermes，共享配置/客户端/cron不改。脚本、判断、数据和回执在`E:/Project/store-assortment-copilot/var/outputs/2026-09-10-guanglv-pilot`；接口说明见`source_api_notes.md`和`publish_online.py`。创建使用同名报错、单次提交；已有结果须先回读，不能重复创建。这是人工可控试跑，不是无人值守API验收。

### 原有环境与兼容 CLI

项目使用 Python 3.12 与 `uv`：

```powershell
uv sync --dev
```

`fastembed` 安装在项目 `.venv` 中；模型、产品向量和索引都位于所选 `--index-root` 下并被 Git 忽略。模型首次使用时下载约 96 MB，后续复用本地缓存。

源工作簿始终位于 Git 仓库之外，并以 openpyxl 的 `read_only=True, data_only=True` 模式读取。构建流程不会保存、复制或重写它。当前已核验源表示例：

```text
E:\download\Chrome下载\产品列表下载20260831093944283.xlsx
```

## ERP 构建与更新

### 每日产品表：增量合并，不重新清洗全库

`catalog-full` 保持不可变基线。首次使用会将其 SQLite 备份为 `E:/Project/store-assortment-copilot/var/catalog-daily/catalog.sqlite3`；之后每日仅合并出现的子SKU。既有主SKU名称、人工排除规则和缺英文补名保留，不因部分导出缺行而删除商品。原始每日字段（含审核时间）保存在 `daily_source_rows`，主SKU名称待整理证据在 `daily_name_review`，导入记录在 `daily_imports`。完整操作与边界见[增量更新说明](docs/2026-09-16-incremental-update.md)。

```powershell
# 先预览，再合并。同一内容重导不会重复新增或重算向量。
.\.venv\Scripts\python.exe -X utf8 scripts/local_catalog.py preview-products --source 'E:\download\Chrome下载\产品列表下载20260916075001995.xlsx'
.\.venv\Scripts\python.exe -X utf8 scripts/local_catalog.py update-products --source 'E:\download\Chrome下载\产品列表下载20260916075001995.xlsx'

# 只导出新增/变化主SKU的名称证据，由Codex/人工据实整理后再应用。
.\.venv\Scripts\python.exe -X utf8 scripts/local_catalog.py name-review --output 'E:\Project\store-assortment-copilot\var\name-review.json'
.\.venv\Scripts\python.exe -X utf8 scripts/local_catalog.py apply-names --names 'E:\Project\store-assortment-copilot\var\reviewed-names.json'

# 产品改动后同步必要索引；已有向量按英文文本复用。
.\.venv\Scripts\python.exe -X utf8 scripts/local_catalog.py index-stock --stock '完整库存文件的绝对路径.xlsx'
# 仅库存变化、没有新商品需要建向量时使用此入口，不加载embedding模型。
.\.venv\Scripts\python.exe -X utf8 scripts/local_catalog.py update-stock --stock '完整库存文件的绝对路径.xlsx'
```

日库 `revision` 与索引记录对应；名称/子款改动后未同步索引会明确提示，不静默使用旧版本。`apply-names` 按主SKU校验原证据指纹，防止过期整理结果覆盖更新资料；原始字段不覆盖。没有接入自动名称改写 API。

库存仍按既有 `海外仓可发 > 0` 输出布尔有货集合，不累加销售员行。报表主SKU能精确关联已知ERP主SKU时可以接纳新增子款；缺ERP主SKU进入 `stock.unmatched_positive_rows`（原始空值保留），`erp_mapping_complete=false`，不进入已匹配ERP推荐。库存激活前保存 `.previous-state.json`，`--output` 可保存完整导入回执。检索外层返回 `stock_erp_mapping_complete`、`stock_unmatched_positive_rows` 及有货口径。用户新指定的“库存中心库存+公共池库存”、总库存、单销汇总尚未替代旧指标，跨销售员重复粒度仍需确认，不能将旧有货集合说成新数量口径。

用户确认的缺失处理：保留商品与原始资料，缺ERP字段留空，补档不是继续运行的前置条件。后续数量报表以商品为主体关联最新快照的精确“主SKU+国家”；未匹配库存或字段原本为空时输出空白（JSON为null），不补0、不借用其他国家或旧快照数据；源表明确为0则保留0。无库存不删除商品或已有向量。当前CLI仅输出SKU列表，这条数量显示约定用于后续报表，不代表数量汇总已实现。

### 历史全量构建与兼容 CLI

下面命令用于显式全量基线构建，不用于每日部分产品表更新。带向量命令属于保留的NumPy兼容路径，不是当前Qdrant入口；不要用部分导出覆盖完整基线。

`--index-root` 的相对路径由命令当前工作目录解析。为避免把 runtime 建到意外位置，请先切到仓库根目录，或始终传绝对路径：

```powershell
uv run store-catalog rebuild `
  --source 'E:\download\Chrome下载\产品列表下载20260831093944283.xlsx' `
  --with-vectors `
  --index-root 'E:\Project\store-scenario-inspiration\.worktrees\catalog-retrieval-foundation\var\catalog'
```

只有源文件 SHA-256、源文件 UTC 更新时间、所选 sheet（自动选择为 `none`）、document schema version、cleaning rules version 和 embedding model ID 都与 active manifest 相同，命令才输出 `skipped=true`。任一身份项变化都会新建完整版本，因此同一工作簿切换 Sheet A/Sheet B 不会错误跳过。

历史 `rebuild` 命令仍采用“全量文档重建 + embedding cache 复用”；日常更新改用上方增量入口：

1. 读取前计算源文件 SHA-256；
2. 在 `versions/.staging-*` 中全量重建主 SKU 文档和 SQLite FTS；
3. 通过 `embedding model ID + vector text hash` 复用未变化文本的 embedding；
4. 质量检查、源文件二次哈希、制品哈希和 SQLite 一致性检查全部成功后，才原子切换 active state；
5. 失败时 active 版本保持不变，完成但未激活的 orphan 版本不会被检索使用。

使用兼容路径 `--with-vectors` 时，CLI 通过 FastEmbed 连接本地 BGE provider，成功构建后输出 `vector_status=present`。旧8,550主SKU矩阵约16.7 MiB，这个规模说明不适用于新完整库。每个ERP index root 自动采用非阻塞的跨进程 single-writer lock：同时运行第二个 rebuild 或 rollback 会清晰失败；只读的 status 与 search 不加锁。

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

> 以上四档口径属于**命令行批量链路**（`scripts/deepseek_rerank.py`，被 `scripts/batch_store_pipeline.py` 调用）。2026-09-21 起应用界面走的是另一套：判定单位是**场景**不是商品角色，只有**相关 / 不相关**两档。两套暂时并存，见 [重排改成「按场景判定」两档](docs/2026-09-21-scene-level-rerank.md)。

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
    source-import.json   # 规则v4：原始/归组行数及缺主SKU原始记录
    delta.json
    artifacts.json       # catalog、reports 和可选 vector 文件的 SHA-256
    vectors.npy          # 仅 provider build
    vector-rows.json     # 仅 provider build
```

每个已完成版本都是不可变且自包含的；不会自动删除旧版本。需要清理 runtime 数据时，应先确认 active/previous 指针和回滚需求，再由操作员另行处理。
