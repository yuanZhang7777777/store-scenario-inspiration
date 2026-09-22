# METIS 界面改造 · 交接说明

> 2026-09-22 补充：用户将首要目标明确为“最快拿到想要的导出文件”，允许重新设计 UI 与操作流程。新提案见 [导出优先设计](2026-09-22-export-first-design.md)。该提案尚未接入应用；下文仍用于理解原有实现和此前视觉方向，不能把提案当成已上线功能。

这份文件写给接手界面改造的人（或 AI）。目的只有一个：**说清楚哪些能动、哪些不能动。**

- 标【不能动】的，是流程和数据的骨架。改了，功能坏，不是变丑的问题。
- 标【能动的】，全权交给你。想怎么改怎么改。

本文按当前代码写成。代码里改了就回来改这里。

---

## 1. 这是什么

**METIS** —— 店铺经营与选品助手。运营上传店铺截图（或手填商品名），系统读图、写经营判断、生成使用场景、列出每个场景要用的商品，最后去公司商品库里把商品匹配出来。

技术栈，**不许换**：

| 层 | 用什么 |
|---|---|
| 后端 | FastAPI，工厂函数 `create_app(settings)`，在 `src/store_scenario_inspiration/app/` |
| 前端 | React + TypeScript + Vite，在 `frontend/` |
| 样式 | **手写 CSS**，`frontend/src/styles/`（tokens + operator.css） |

**不要再引入 Tailwind / shadcn / 动效库。** 现在是一套手写 CSS，再叠一套原子类会变成两套样式系统并存，以后没人知道该改哪边。样稿里那些效果全部用原生 CSS + SVG + canvas 实现，不需要新依赖。

---

## 2. 【不能动】流程骨架

### 2.1 形态：一个按钮，一路跑到底

```
上传页：选国家 +（截图 或 商品名）+ 参数
   └─【生成经营建议】一个按钮
        └─ 建店 → 手工商品写进去 → 一个任务从①跑到⑧
                                          ↓
                                   店铺页出结果
                                          ↓
                        在结果里增删改商品 / 改参数
                                          ↓
                        再按按钮，只重跑落后的那几步
```

**中间没有确认站。** 以前有过一道「先读图 → 人工核对商品 → 再生成建议」的确认门，已经**删除**（`confirmation.py`、`ConfirmationPanel.tsx`、`/review`、`/review/confirm` 都没了）。**不要把它加回来**，任何形式的「先让运营确认 X，再跑 Y」都与这个形态冲突。

没有截图也能跑：只要填了商品名，识别那一步会空跑一次（不调模型、不花钱），商品名单从手工填写开始，后面照常做。

### 2.2 八个步骤

顺序是**写死的**，存在 `app/jobs.py::STAGE_ORDER`。前端不许自己排。

| # | 代码名 | 界面上的说法 | 读 | 写 | 会调模型吗 |
|---|---|---|---|---|---|
| ① | `recognize` | 识别截图里的商品 | 截图 + 已有读取记录 | `sample_store.json` | **会**；没有截图时**不会** |
| ② | `clues` | 去掉你排除的商品 | ①+排除名单+手工商品 | `clues.json`、`analysis_input.json` | 不会（纯本地） |
| ③ | `synthesis` | 写店铺结论和人群策略 | `analysis_input.json` | `deepseek_conclusion.json` | **会** |
| ④ | `scenes` | 生成使用场景 | 同上 + ③的结论 | `deepseek_scenes.json` | **会** |
| ⑤ | `products` | 列出每个场景要用的商品 | 场景 + 过滤后的输入 | `products/manifest.json` | **会**（每场景一次，并行 4 个） |
| ⑥ | `expand` | 补充搜索词 | ④的场景 | `expansions.json` | **会**（默认词数 0，即不调） |
| ⑦ | `retrieval` | 找商品 | 扩写词 + 产品库 | `retrieval.json` | 不会（本地向量+关键词） |
| ⑧ | `rerank` | 帮你复核一遍 | ⑦的候选 | 写回 `retrieval.json` | **看谁复核** |

中文名存在 `app/jobs.py::STAGE_LABELS`，前端 `StorePage.tsx` 里有一份手抄的 `PIPELINE`。**两份必须一致**，改一处要改另一处。

**关于「花不花钱」——界面上不许出现「免费」「不花钱」这类词。**

```python
PAID_STAGES = {recognize, scenes, products, synthesis, expand}
# rerank 不在这张表里：花不花钱取决于谁回答
#   Jev    只按输入计费（4.2e-8/词），约等于零，但不是零（实测一家店约 $0.12）
#   DeepSeek  收费
#   off    真的零
```

界面上统一由 `frontend/src/operatorUx.ts::stageNotice` 决定写哪句，**不许自己另写一套**：

- 会调模型的 → 「会调用模型，费用以当前服务配置为准。」
- 纯本地的 → 「按已保存的结果更新商品匹配。」

### 2.3 一次任务内部的状态流转

```
POST /api/stores/{id}/jobs   body: {stages?, params?}
  ├─ 这家店有东西可读吗？截图和手工商品都没有 → 409「这家店还没有商品」
  ├─ choose_stages(要的步骤)
  │    · 不传 stages → 全部八步（上传页就是这种）
  │    · 有没见过的步骤名 → 400「未知的处理步骤：…」
  ├─ 这家店已经有任务在跑？
  │    · 步骤一样 + 参数一样 → 把正在跑的那个直接还给你，不起第二个
  │    · 否则 → 409，让你先等它跑完或者停掉
  ├─ 参数冻结：本次任务跑的就是这一份
  ├─ 拿店铺锁 .pipeline.lock
  ├─ 建 Job：每步初始都 = pending
  └─ 丢线程池（同时最多 2 个店在跑）
```

单个步骤的状态：`pending → running → ready | failed`

**③「写结论」失败不打断任务**：记一条日志继续往下跑，场景改从原始商品线索写起。别的步骤失败就断在那里，剩下的停在 `pending`，下次按按钮从失败那步接着跑。

**进度是轮询，不是推流。** 前端每 2 秒问一次 `GET /api/jobs/{id}`。步骤少，轮询和推流对运营来说没区别，还省掉重连逻辑。**不要改成 SSE/WebSocket。**

### 2.4 「哪些步骤要重做」怎么算

后端在 `GET /api/stores/{id}` 里返回 `outdated`。每个步骤落后只有两个原因：**它的输入在它跑完之后又被写过**，或**它读的参数被改了**。

- `temperature` 同时喂养 ③④⑤⑥ 四步 —— 这就是为什么改它比改别的贵。
- 没有 `run_state.json` 的老店铺（命令行跑出来的），参数改了**不标脏**。代码里的说法是「没有意见」，好过凭猜测让你花钱。

**前端不许自己算 `outdated`**，只读后端返回的。`operatorUx.ts::mergeStages` 是唯一允许的加工：把后端给的落后步骤并进这次要跑的范围。

### 2.5 唯一那个按钮，怎么选

```
点开店铺
 ├─ 有截图没被读过？ → 【识别新增截图】  只读新图 + 重新分桶
 ├─ 还没写过结论？   → 【生成经营建议】  ③~⑧ 全跑
 ├─ ③④⑤⑥ 有落后？  → 【更新经营建议】
 ├─ 只有 ⑦⑧ 落后？  → 【更新推荐商品】
 └─ 都不落后        → 没有按钮
```

按钮悬停的说明由 `stageNotice` 算，见 2.2。

上传页那个按钮是另一回事：它不带步骤清单，后端理解为「跑全部八步」。

---

## 3. 【不能动】接口与数据

### 3.1 接口清单（全部在 `app/main.py`）

```
GET    /api/health
POST   /api/stores                              建店（国家必填；截图可以为空）
GET    /api/stores                              店铺列表
GET    /api/stores/{id}                         阶段状态 + outdated + 商品名单
POST   /api/stores/{id}/images                  加截图
DELETE /api/stores/{id}/images/{name}           删截图（从已读记录里摘掉 + 立即重算，不花钱）
GET    /api/stores/{id}/images/{name}           取原图
GET    /api/stores/{id}/clues                   商品名单（已应用排除）
PUT    /api/stores/{id}/clues                   写排除名单，随后本地重算
PUT    /api/stores/{id}/products                整份提交手工商品，随后本地重算
GET    /api/params/schema                       参数表 + 中文说明（驱动前端表单）
GET    /api/stores/{id}/params                  这家店存下来的参数
PUT    /api/stores/{id}/params                  只写文件，不校验、不触发任何步骤
POST   /api/stores/{id}/jobs                    起任务
GET    /api/jobs/{job_id}                       任务快照
POST   /api/jobs/{job_id}/cancel                停止（当前这步做完就停）
GET    /api/stores/{id}/analysis                店铺结论 + 场景
GET    /api/stores/{id}/retrieval               场景 → 商品角色 → 候选 SKU
POST   /api/stores/{id}/adoption/export         picks + dedupe → XLSX；复制货号由前端独立完成
```

### 3.2 磁盘上留下的东西

```
var/demo/stores/<店名>/
  store.json                 店名 / 国家 / 截图清单（可以是空的）
  images/*.png               原图
  deepseek_vision.json       读取收据：哪张图读过了、读出了什么、花了多少 token
  sample_store.json          ①的产物（无截图店铺也有一份，内容为空）
  exclusions.json            你勾掉的
  custom_products.json       你手工补的
  clues.json                 ②的产物
  analysis_input.json        喂给模型的、排除过后的输入
  deepseek_conclusion.json   ③的产物        receipt_analysis.json
  deepseek_scenes.json       ④的产物        receipt_scenes.json
  products/manifest.json     ⑤（每场景一个文件，集齐才算完成）
  expansions.json            ⑥的产物
  retrieval.json             ⑦⑧的产物（复核的判定写在这份里）
  run_state.json             每一步「上次拿什么参数跑的」——2.4 那张表靠它
  .pipeline.lock             同一店铺同一时刻只允许一个任务在写
```

**复核的判定存在召回结果里面。** 所以重算召回时会先把上一次的判定抹掉——判定是对着旧列表做的，列表都没了，留着它就是误导。

### 3.3 参数表

**唯一来源是 `app/params.py::SearchParams`。** 这个 Pydantic 模型同时驱动后端校验和前端表单，界面文案（`description`）也从它派生。`GET /api/params/schema` 每个字段都带 `default`、`minimum`、`maximum`、`step`、`options`、`labels`。

**前端不许另抄一份默认值和范围。** 表单必须读 schema 渲染。

| 参数 | 默认 | 范围 |
|---|---|---|
| `scene_count` | 6 | 3–8 |
| `products_per_scene` | 16 | 8–40 |
| `expansion_terms` | 0 | 0–12 |
| `recall_limit` | 30 | 5–200 |
| `stock_filter` | `all` | `all` / `in_stock` |
| `rerank` | `mark_only` | `mark_only` / `drop` |
| `rerank_provider` | `jev` | `jev` / `deepseek` / `off` |
| `rerank_cutoff` | 50 | 50–95 |
| `temperature` | 0.2 | 0–1 |

参数存两份，**不要合并**：上传页那份只跟本次任务走，不落盘；店铺页那份存下来，改了影响之后的重跑。

### 3.4 结果页的信息层级（三层，写死的）

```
场景  →  商品角色  →  候选 SKU
```

- **场景**：一个生活情境 + 为什么用
- **商品角色**：这个场景需要的东西 + 一句话说明它的用途
- **候选 SKU**：公司商品库里匹配到的具体货，带库存状态和相关度标记

**长列表默认折叠。** 场景、角色、候选三层默认都是收起的，展开只渲染点开的那一个，展开时页面不许跳动。唯一允许的滚动是从已选清单跳回对应行。

---

## 4. 【不能动】文案红线

这几条是踩过坑定下来的，**不是风格偏好**：

1. **不写市场趋势、不写数字承诺、不写空话。** 不出现「市场正在增长」「建议保持 5:8 比例」「一定能提升转化」这类。分析只写判断、问题、下一步。
2. **「边界声明」和「依据」不上界面。** 数据里可能还留着这些字段（导出契约要用），但**不要渲染出来**。
3. **不写「免费」「不花钱」。** 见 2.2，统一用 `stageNotice` 的两句话。
4. **复核只标不剔除，也不做「捞回来」。** 默认 `mark_only`，模型只是打标记，一条都不删。被剔除候选的「捞回」「把握度」「理由」这类功能**一律不做**。
5. **分析要写判断，不许复述截图。** 未来产品结构要写厚（3-5 个方向 × 3-4 句），不是把识别到的商品名重排一遍。

---

## 5. 【能动】视觉与布局

这以下是**你的地盘**。骨架不动，其余随便。

### 5.1 现状

现在的界面能用，但「没有质感」：扁平色块按钮、纯文字步骤条、结果页是一堆表格。视觉语言是深蓝 `#153e5b` + 手写 CSS。

### 5.2 已经定下来的方向

有一份独立的样稿 `docs/2026-09-22-style-preview.html`（自包含、零依赖、**不碰应用代码**）。它演示了四件事，方向已经认可：

**表面**
- 主按钮用胶囊 + 渐变 + 外发光，不是扁平色块。**主次靠光影分，不靠颜色深浅分。**
- 圆形图标按钮，悬停浮起、按下凹陷、点击水波纹。
- 纸白底 + 头发丝边框 + 三层表面（普通 / 浮起 / 下沉）。

**动效**（这是重点，之前那版做得不对）
- **一条线的光点必须是固定像素长度的彗星**，不是按路径比例算的。按比例算的话，长线会变成一条糊掉的长道子。
  做法：`path.getTotalLength()` 量出真实长度，写进 CSS 变量 `--len`，`stroke-dasharray: var(--dash) var(--len)`，`stroke-dashoffset` 从 `-dash` 动到 `len`。
- 深色面板上，两侧的东西**收进中间**（这一阶段读进来的、写出去的，汇到核心）。
- 核心呼吸，节点逐个淡入上浮。
- 「找商品」那一步单独用**粒子从两侧汇聚**，因为那是四路召回。
- 全部动效必须在 `prefers-reduced-motion` 下停掉。

**布局**
- **一屏跑完，不许上下滑。** 左边一条竖排八步，右边动画面板，底下说明 + 按钮。整页锁 `100vh`。
- 步骤状态用颜色区分就够了，**不要配文字说明**（「正在跑」「已跑完」这类都是废话）。

**字体**：正文 15px 起，深色面上的字要够亮（浅灰配深底看不清）。

### 5.3 完全自由的

- 具体色值、圆角、阴影强度、间距
- 每个步骤用什么图形/动画表达
- 卡片里放多少信息
- 步骤条放左边还是上面
- 结果页怎么展示三层

只要不碰第 2、3、4 节的东西，怎么改都行。

---

## 6. 交付标准

**能自动验的：**

```bash
uv run pytest -q                          # 全绿，0 error
cd frontend && npx tsc --noEmit           # 类型必须过
```

**必须人眼过的：**

1. 一屏之内能看完从上传到结果的全过程，**不出现纵向滚动条**。
2. 每条连线上的光点在动，且**每一段长度一致**（不长不短、不糊成一条）。
3. 系统开「减少动态效果」后，所有动画停下，页面依然可读可用。
4. 长列表默认折叠，展开不跳动。
5. 界面上搜不到「免费」「不花钱」「市场趋势」这类词。

---

## 7. 相关文件

| 想了解 | 看 |
|---|---|
| 完整流程与各种情况走向（最详细） | `docs/2026-09-22-flow-and-branches.md` |
| 视觉与动效样稿 | `docs/2026-09-22-style-preview.html` |
| 步骤定义 | `src/store_scenario_inspiration/app/jobs.py` |
| 参数定义 | `src/store_scenario_inspiration/app/params.py` |
| 接口 | `src/store_scenario_inspiration/app/main.py` |
| 界面文案规则 | `frontend/src/operatorUx.ts` |
