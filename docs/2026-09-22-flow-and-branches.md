# 店铺经营助手：完整流程与各种情况的走向

本文按当前代码写成，不是设想。每节末尾注明它对应哪个文件，改了代码请一并改这里。

---

## 一、主线：一个按钮，一路跑到底

```
上传页：选国家 + (截图 或 商品名) + 参数
   └─【生成经营建议】一个按钮
        └─ 建店 → 手工商品写进去 → 一个任务从①识别跑到⑨复核
                                          ↓
                                   店铺页出结果
                                          ↓
                        在结果里增删改商品 / 改参数
                                          ↓
                        再按按钮，只重跑落后的那几步
```

- **没有中间站。** 以前「先读图 → 人工核对商品 → 再生成建议」是两段，中间卡一道确认门；现在合成一段，第一轮直接出结果，运营在结果上改。
- **参数在上传页就能调。** 上传页那份只跟着这一次任务走，不落盘；店铺页那份是存下来的，改了就影响之后的重跑。两份都是「选填，默认即可」。
- **没有截图也能跑。** 只要填了商品名，识别这一步会空跑一次（不花钱），商品名单从手工填写开始，后面照常做店铺分析和召回。
- **停止**：这一步做完就停，不走下一步。正在跟模型说话的那一步会跑完。

---

## 二、八个步骤各干什么

| # | 代码名 | 界面上的说法 | 读 | 写 | 花钱吗 |
|---|---|---|---|---|---|
| ① | `recognize` | 识别截图里的商品 | 截图 + 已有读取记录 | `sample_store.json` | **要**（视觉模型）；无截图时**不要** |
| ② | `clues` | 去掉你排除的商品 | ①+排除名单+手工商品 | `clues.json`、`analysis_input.json` | 不要（本地） |
| ③ | `synthesis` | 写店铺结论和人群策略 | `analysis_input.json` | `deepseek_conclusion.json` | **要** |
| ④ | `scenes` | 生成使用场景 | 同上 + ③的结论 | `deepseek_scenes.json` | **要** |
| ⑤ | `products` | 列出每个场景要用的商品 | 场景 + 过滤后的输入 | `products/manifest.json` | **要**（每场景一次，并行 4 个） |
| ⑥ | `expand` | 补充搜索词 | ④的场景 | `expansions.json` | **要**（默认词数 0） |
| ⑦ | `retrieval` | 找商品 | 扩写词 + 产品库 | `retrieval.json` | 不要（本地向量+关键词） |
| ⑧ | `rerank` | 帮你复核一遍 | ⑦的候选 | 写回 `retrieval.json` | **看谁复核** |

### 关于「花不花钱」

```
PAID_STAGES = {recognize, scenes, products, synthesis, expand}
# 复核不在这张表里，因为花不花钱取决于谁回答：
#   Jev 只按输入计费，4.2e-8/词，约等于零；DeepSeek 收费。
```

**Jev 复核不是免费，只是便宜**（实测一家店约 $0.12）；DeepSeek 复核明显更贵；选「不复核」才是真的零。

界面上因此不写「不花钱」或「免费」，统一由 `stageNotice` 判断后写「会调用模型，费用以当前服务配置为准。」或「按已保存的结果更新商品匹配。」

> 代码：[jobs.py:117](../src/store_scenario_inspiration/app/jobs.py#L117)、[operatorUx.ts:41](../frontend/src/operatorUx.ts#L41)

---

## 三、一次任务内部的状态流转

### 3.1 按下按钮到任务排队

```
POST /api/stores/{id}/jobs     body: {stages?, params?}
  │
  ├─ 这家店有东西可读吗？
  │    · 截图和手工商品都没有 → 409「这家店还没有商品」
  │
  ├─ choose_stages(要的步骤)
  │    · 没指定 → 全部八步（上传页就是不带 stages 的那个请求）
  │    · 有没见过的步骤名 → 400「未知的处理步骤：…」
  │
  ├─ 这家店已经有任务在跑？
  │    · 步骤一样 + 参数一样 → 把正在跑的那个直接还给你，不起第二个
  │    · 否则            → 409，让你先等它跑完或者停掉
  │
  ├─ 参数冻结：本次任务跑的就是这一份
  │    （请求里带了参数才写盘；没带就用店铺页存下来的那份）
  ├─ 拿店铺锁 .pipeline.lock
  ├─ 建 Job：每个步骤初始都 = pending
  └─ 丢进线程池（同时最多 2 个店在跑）→ 返回第一份状态给前端
```

> 代码：[jobs.py:183](../src/store_scenario_inspiration/app/jobs.py#L183)（排队）、[jobs.py:87](../src/store_scenario_inspiration/app/jobs.py#L87)（选步骤）、[main.py:307](../src/store_scenario_inspiration/app/main.py#L307)（入口）

### 3.2 任务主循环

```
job.status：pending → running

按顺序遍历本次要跑的步骤：
  │
  ├─ 这个任务被点过「停止」了？ → 立刻跳出，剩下的一步都不跑
  │
  ├─ 跑这一步（见 3.3）
  │
  └─ 这一步失败了？
       · 失败的是「写结论」→ 记一条日志，**继续往下跑**
       · 失败的是别的   → 断掉，剩下的步骤停在 pending
```

**为什么「写结论」失败不打断？** 场景是从原始商品线索写的，结论只是一段前提。结论没了，生意还能照做，所以让任务跑完，把结论单独留给你重试。

> 代码：[jobs.py:284](../src/store_scenario_inspiration/app/jobs.py#L284)

### 3.3 单个步骤

```
stage.status：pending → running
记日志「开始<这一步的中文名>」

① 执行
     │
     ├─ 成功：
     │    · 这一步读参数的话 → 把它这次用的参数值写进 run_state.json
     │      ← 第四节那张表的唯一依据
     │    · stage.status = ready，日志「<结果摘要>（N 秒）」
     │    · 从收据里把这个步骤的 token 消耗折进任务总计
     │
     └─ 失败：
          stage.status = failed，记下「异常类型: 信息」
          界面上这一步变红，日志里写「失败：…」
```

**①「识别」在无截图店铺上是个空跑**：它写一份空的 `sample_store.json` 就返回「没有截图，商品名单从手工填写开始」，不调模型、不花钱。这样后面八步看到的磁盘状态和真有截图时一模一样。

> 代码：[jobs.py:321](../src/store_scenario_inspiration/app/jobs.py#L321)（单步）、[jobs.py:369](../src/store_scenario_inspiration/app/jobs.py#L369)（识别的两条分支）

### 3.4 任务收尾

```
任务结束（正常跑完 / 中途失败 / 被停）
  │
  ├─ 判定整体状态：
  │    · 被停过                          → cancelled
  │    · 有失败且不是「写结论」           → failed
  │    · 一步都没成功（比如只重试结论）   → failed
  │    · 其余                            → ready
  │
  ├─ 记结束时间
  └─ 放掉店铺锁
```

### 3.5 前端怎么跟

```
前端每 2 秒问一次 GET /api/jobs/{id}
  │
  ├─ 有新完成的步骤？
  │    只把**它产出的那一份结果**重新拉一次，不整个店重拉
  │      recognize / clues  → 拉商品名单
  │      synthesis          → 拉经营建议
  │      retrieval / rerank → 拉召回结果（这份有好几 MB）
  │      （其余步骤没有独立结果，只更新进度条）
  │
  └─ 任务不再进行中 → 整份重新加载一次，按钮和提示按最新状态重算
```

> 代码：[StorePage.tsx:40](../frontend/src/pages/StorePage.tsx#L40)（步骤表）、[StorePage.tsx:53](../frontend/src/pages/StorePage.tsx#L53)（完成一步刷新哪一份）

---

## 四、「哪些步骤要重做」是怎么算出来的

后端在 `GET /api/stores/{id}` 里返回 `outdated`，就是这份清单。每个步骤落后只有两个原因：**它的输入在它跑完之后又被写过**，或者**它读的参数被改了**。

```
recognize  ← 有没有截图没被任何一次读取覆盖过？
             └─ 有 → 后面全部落后（后面算的是另一个店）
             └─ 一张截图都没有 → 看 sample_store.json 在不在：
                  不在 → 整条链都落后（这次读取还没做过）
                  在   → 不因为截图而落后（没有图可再读）

clues      ← 名单比读取记录旧？  或  识别已落后
             └─ 是 → 后面全部落后

然后顺着链往下走，一旦某一步落后，它后面的一律落后：

  synthesis ← analysis_input.json 变新 或 温度变了
  scenes    ← 上一步落后 或 场景数 / 温度变了
  products  ← 上一步落后 或 每场景商品数 / 温度变了
  expand    ← 上一步落后 或 补充搜索词数 / 温度变了
  retrieval ← 上一步落后 或 候选数 / 库存范围变了
  rerank    ← 上一步落后 或 复核方式 / 复核模型 / 排除标准变了
```

**温度（`temperature`）同时喂养 ③④⑤⑥ 四步**——这就是为什么改它比改别的贵。

还有一个特性：**命令行跑出来的老店铺没有 `run_state.json`，参数改了不会被标脏**。代码里的说法是「没有意见」，好过凭猜测告诉你需要重跑并让你花钱。

> 代码：[stores.py:141](../src/store_scenario_inspiration/app/stores.py#L141)（判定）、[stores.py:32](../src/store_scenario_inspiration/app/stores.py#L32)（参数→步骤对照表）

---

## 五、页面上那个唯一的按钮，是怎么选出来的

```
点开店铺
 ├─ 有截图没被读过？ → 【识别新增截图】  只读新图 + 重新分桶（会调模型）
 ├─ 还没写过结论？   → 【生成经营建议】  ③~⑧ 全跑
 ├─ ③④⑤⑥ 有落后？  → 【更新经营建议】
 ├─ 只有 ⑦⑧ 落后？  → 【更新推荐商品】
 └─ 都不落后        → 没有按钮
```

按钮悬停的说明由 `stageNotice` 算：会调模型的写「会调用模型，费用以当前服务配置为准。」纯本地的写「按已保存的结果更新商品匹配。」

上传页那个按钮是另一回事：它不带步骤清单，所以后端理解为「跑全部八步」。

> 代码：[StorePage.tsx:344](../frontend/src/pages/StorePage.tsx#L344)、[StoresPage.tsx:96](../frontend/src/pages/StoresPage.tsx#L96)

---

## 六、不同情况的走向

| 你做了什么 | 系统立刻做什么 | 花钱吗 |
|---|---|---|
| **上传新店（有截图）** | 建店 → 存图 → 手工商品（若有）写进去 → 一个任务从①跑到⑨ | 要（读图 + 建议） |
| **上传新店（没截图）** | 建店 → ①空跑一次 → 商品名单从手工填写开始 → ②~⑨照跑 | 只要建议那几步 |
| **又新填/改了商品名** | 本地重算名单和过滤后的输入 | **不花** |
| **加一张截图** | 存图 → 只读**这一张**，把结果折进已有读取，再本地重算名单 | 只花这一张的钱 |
| **删一张截图** | 从已读记录里摘掉这张 + 重算名单 | **不花**（图已经读过了） |
| **勾掉 / 勾回一个商品** | 立刻本地重算名单和过滤后的输入 | **不花** |
| **改参数并保存** | 只写文件。**什么都不跑** | **不花** |
| **按那个按钮** | 把「落后」的步骤按顺序跑 | 看落后到哪一步 |
| **任务跑到一半改了资料** | 后面的步骤照跑：它们读的是任务开始时冻结的那一份参数和盘上最新的产物 | — |
| **某步失败** | 停在那一步，前面成功的保留。再按按钮时**从失败的那一步接着跑** | 只补跑缺的 |
| **「写结论」单独失败** | 不打断任务：场景改从原始商品线索写起，任务继续跑完 | 不重复花 |
| **点停止** | 当前这步做完就停，下一步不走 | 说话中那步照付 |
| **同一店铺重复点开始** | 同样的步骤 + 同样参数 → 直接把正在跑的那个任务还给你，不起第二个 | — |
| **命令行跑出来的老店** | 参数改了不标脏，只能手动指定步骤 | — |

### 每种情况走哪个接口、关键动作是什么

| 情况 | 入口 | 关键动作 |
|---|---|---|
| 建店 | `POST /api/stores` | 截图可以为空；只有国家是必填 |
| 手工商品 | `PUT /api/stores/{id}/products` | 整份名单提交，不做增量 diff；随后本地重算 |
| 加截图 | `POST /api/stores/{id}/images` | 存图；前端随后单独发起「识别 + 分桶」 |
| 删截图 | `DELETE /api/stores/{id}/images/{name}` | 从收据里摘掉这张图 + 立即重算，全程本地 |
| 勾选商品 | `PUT /api/stores/{id}/clues` | 写排除名单，随后本地重算；排除集合真的变了就重算 |
| 保存参数 | `PUT /api/stores/{id}/params` | **只写文件**，不校验、不触发任何步骤 |
| 起任务 | `POST /api/stores/{id}/jobs` | 校验店铺非空 → 冻结参数 → 排队 |

> 代码：全部接口都在 [main.py](../src/store_scenario_inspiration/app/main.py) 里。店铺名单的组装在 [main.py:417](../src/store_scenario_inspiration/app/main.py#L417)（展示）和 [main.py:436](../src/store_scenario_inspiration/app/main.py#L436)（改动后重算）

---

## 七、磁盘上留下的东西

```
var/demo/stores/<店名>/
  store.json                 店名 / 国家 / 截图清单（可以是空的）
  images/*.png               原图
  deepseek_vision.json       读取收据：哪张图读过了、读出了什么、花了多少 token
  sample_store.json          ①的产物（无截图店铺也有一份，内容为空）
  exclusions.json            你勾掉的
  custom_products.json       你手工补的
  clues.json                 ②的产物（名单 + 原始截图用词）
  analysis_input.json        喂给模型的、排除过后的输入
  deepseek_conclusion.json   ③的产物        receipt_analysis.json
  deepseek_scenes.json       ④的产物        receipt_scenes.json
  products/manifest.json     ⑤（每场景一个文件，集齐才算完成）
  expansions.json            ⑥的产物
  retrieval.json             ⑦⑧的产物（复核的判定写在这份里）
  run_state.json             每一步「上次拿什么参数跑的」——第四节那张表靠它
  .pipeline.lock             同一店铺同一时刻只允许一个任务在写
```

### 两个容易踩的点

**复核的判定存在召回结果里面。** 所以重算召回时，会先把上一次的复核判定抹掉——判定是对着旧列表做的，列表都没了，留着它就是误导。

**老店铺里可能还躺着一个没用的 `review_confirmation.json`。** 那是确认门时代留下的，现在的代码不再读它、也不再写它。留着不碍事，重跑一遍就自然过期；不要因为它不在上面的清单里就以为哪里漏了。

> 代码：[jobs.py:658](../src/store_scenario_inspiration/app/jobs.py#L658)（抹掉旧复核判定）

---

## 八、默认参数（后台不改，界面只调）

| 参数 | 默认 | 界面上的说法 |
|---|---|---|
| `scene_count` | 6 | 推荐场景数 |
| `products_per_scene` | 16 | 每个场景至少推荐几类商品 |
| `expansion_terms` | 0 | 补充搜索词数 |
| `recall_limit` | 30 | 每类商品的候选数 |
| `stock_filter` | `all` | 库存范围 |
| `rerank` | `mark_only` | 无关商品处理 |
| `rerank_provider` | `jev` | 复核模型 |
| `rerank_cutoff` | 50 | 排除标准（%） |
| `temperature` | 0.2 | 创意程度 |

参数表只有一个来源：`SearchParams` 这个模型同时驱动后端校验和前端表单，界面文案从它派生，不在前端另抄一份。`GET /api/params/schema` 每个字段都带 `default`，上传页那份面板就靠它把表单填成默认值。

> 代码：[params.py](../src/store_scenario_inspiration/app/params.py)、`GET /api/params/schema`
