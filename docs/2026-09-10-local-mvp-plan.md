# 2026-09-10 本机检索基础设施执行记录

范围：按用户“现在推进”先落实当前电脑的数据与检索基础，不连接另一台电脑、不部署公网服务、不改原始 Excel。沿用四列主 SKU 输出和已确认排除规则。

## 本轮交付

- [x] GPT-5.5 修复正式 ERP 读取器：重置错误维度、默认读取全部匹配表、显式选表仍有效。
- [x] GPT-5.5 整理30个真实现货主SKU双语核心名称样本；产物在 `var/outputs/2026-09-10-local-mvp/name-pilot.{json,md}`，尚未应用到正式向量。
- [x] 在项目var运行官方Windows Qdrant v1.19.1，仅监听127.0.0.1；不装Docker/WSL、不注册开机服务。官方zip SHA256：`9b6f69bd85f6abed4bc13f943099f55c6ffd55f5dd90388635320d8fbb569eb0`。
- [x] 完整ERP：613072源行、612960有效身份行、223483主SKU；112行缺主SKU单独保存source-import.json。版本 `20260910T071425Z-f37324b22782`。
- [x] 现货1067主SKU写入Qdrant集合 `stock_20260910_072913_ac9236`，首次库存/embedding建库42.092秒。PH436/TH256/VN282/MY126主SKU，跨国可重复。
- [x] 保留旧索引并实跑PH三类商品批量查询；国家和ERP子集关系通过。首轮main计时3.101秒、命令墙钟5.24秒；每类双范围融合163–272ms，不含生成模型。输出明确为未软重排的高召回候选。

## 已确定的边界

主 SKU 是身份和结果单位；相似产品保留，以排序区分，不追求精确规格配对。后台子 SKU 仅服务库存关联和已确认排除。国家库存按同国任一未排除明细“海外仓可发 > 0”判断，不跨国、不相加重复数量。

基础需求场景、场景产品和正向扩写仍由 Codex 产生。本轮不调用新的付费 API，不将全 ERP 送给生成模型。前一阶段用 BGE 中文向量验证 Qdrant 链路；用户随后批准英文 bge-large-en-v1.5（1024维），执行状态见下方，不再等待 BGE-M3 或384维选型。

Qdrant 的 Windows 发行包由官方 GitHub release 获取并核对 SHA256，数据位于 E:/Project/store-assortment-copilot/var/services/qdrant。生产多人服务、鉴权、调度、自动库存下载均不在本轮。

## 进度

已检查现有代码和旧局部索引。当前机器未发现 Docker 命令或已安装 WSL 发行版；官方 Qdrant 提供原生 Windows 包，因此无需修改系统功能。工作目录已有先前确认规则的未提交变更，全部保留，不提交或推送。

中文基线链路已跑通，不能称为最终选品质量已验收：PH查询“存储卡”能召回真实SD卡，但也出现灯具等无关近邻；PH查询“便携榨汁机”不能因为没有直接匹配而把低相关Top50当成推荐。后续以英文名称检索、接入用途软重排（保留宽召回），再产运营四列结果。当前样本中的主SKU `1A0101` 仅TH有货，PH结果不借用该库存。

方向修订：用户已明确改为英文商品名称和英文查询，不采用双语混合主检索；上述30项双语样本是历史试样。每日库存HTTP文件接口处于已明确需求、未实施状态；以按国家现货选品方案的“最新确认”段落为准。

## 英文1024维切换（已实跑）

- [x] GPT-5.5在english.py派生英文商品文档，复用ERP已去重英文别名，不使用中文名称/目录批量补入向量；1067现货主SKU均有有效英文，`missing_english=[]`。`SH-CW-2339`依据完整ERP中文名“黑色镂空蕾丝短裤 L/M/S/XL”补为`Lace shorts`；`WATOY320`依据完整ERP中文名“迷你古代兵器模型挂件”和原英文别名`1005009609633212`补为`Miniature ancient weapon model pendant`。补名只在源有效英文为空时使用，原始ERP数据库不改。
- [x] 主代理在vectors.py新增bge-large-en-v1.5（1024维）本机provider，复用FastEmbed/小批量CPU；下载路径var/models/fastembed。旧provider不变；官方文件SHA256核对通过。
- [x] storage.py/retrieval.py支持英文别名字段检索；local_catalog.py采用英文文档、英文查询、独立var/catalog-stock-en状态和独立英文FTS关键词库，不覆盖旧中文集合或原始ERP库。英文FTS包含完整ERP旧英文关键词，并用现货主SKU本轮清洗/补名英文覆盖同SKU语料；`keywords_file`指向同collection身份的`.keywords.sqlite3`。
- [x] 当前1067现货池英文向量已写入`stock_en_20260910_084051_ffde49`，Qdrant返回green，vector_count/main_sku_count均为1067，耗时78.356秒；策略为`erp-english-aliases-v2-sourced-fallbacks`。已用真实库限定补名主SKU验证：`lace shorts`经英文`keyword_search`返回`SH-CW-2339`，`miniature ancient weapon model pendant`返回`WATOY320`。历史基线集合`stock_en_20260910_081911_666090`为1065点/1024维，首次建库80.370秒；3类商品+6扩写词PH查询命令墙钟7.935秒，main计时6.247秒，查询编码492.49ms，各类双范围融合201–393ms。存储卡ZF3C0209从第3升至第1；便携榨汁机首项为食品处理器1X1801，但第2雾化器仍无关，不作为最终推荐。

验证限于已有storage/retrieval/vectors定向73项通过、GPT-5.5新增英文投影和英文FTS局部测试通过、真实建库及PH结果国家/ERP子集断言；未重复跑全项目测试。文档同步仅README及本方案/执行记录，规则与个人记忆不改。

协作边界：代理只改english.py及对应最小测试，主代理改provider/英文召回/批量入口；共享ProductFamilyDocument契约不变。按用户要求不做全项目复查或commit，仅最小行为验证和真实查询。库存HTTP接口另续，本轮仍复用原格式CLI快照导入；不关闭用户程序、不安装CUDA、不改变确认排除范围。

Windows Qdrant创建辅助payload索引报gridstore路径错误，已省去可选索引；真实查询仍按main_sku范围先过滤。当前英文集合1067点，旧中文集合1067点，均未达默认HNSW构建阈值，服务内扫描是当前实际执行路径，不声称已经有HNSW加速。

## 光旅单人结果（已发布）

陈韦臻两家TH店7张截图已通过Hermes企业凭证与complexValues资源接口取得。每店6个场景，共61行场景商品；34类商品以102个英文原词/扩写批量召回，main计时19.906秒。Codex完成3146条候选关系软分级，0档保留审计，经理结果区分同类/配套/相邻；703个相关ERP主SKU中89个在TH快照有货，源ERP/库存不改。

[单人在线表](https://alidocs.dingtalk.com/i/nodes/1DKw2zgV2PonxLBqCvzZlEjl8B5r9YAn)已在指定“向量数据库场景匹配结果”folder创建，原生axls、两店页签、四列数据；含表头63行全值回读一致。库存标注2026-09-09 14:56文件更新时间，非实时。执行lxc，使用group1_alidocs.lock，无global/bridge、共享配置或调度改动。产物在var/outputs/2026-09-10-guanglv-pilot；具体运行方式见README。尚未形成无人值守服务。
