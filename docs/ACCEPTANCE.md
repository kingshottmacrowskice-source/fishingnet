# 验收记录（M1 核心 + M2 Phase 1 / Phase 2 / 收尾 + M3 Phase 1 / Phase 2 / 收尾 + M4 Phase 1 / Phase 2，2026-10-21）

## 2026-10-21 01:55（UTC+8）· M4 Phase 2 补充：模型自动建实体 + 按实体分组的动态

**本轮范围**：按用户指示，`entity_disambiguation` 现在在实体库为空时也调用，模型可返回 `new_entities` 由服务端自动创建（`provisional`、按名称去重、不自动合并）；同一来源的主张按实体分组成一条动态。未新增迁移，未删库，未覆盖 `.env`。

### 实际执行

- 基线：`pytest -q` **385 passed / 93 warnings（45.72s）**；改动后 **390 passed / 93 warnings（47.89s）**。
- `app/ai/entities.py`：允许空实体库调用；新增 `new_entities` 校验（字段长度、同名去重、`claim_ref` 必须已发送、上限）。
- `app/jobs/worker.py`：`_suggest_entities` 不再因无节点跳过；`_store_model_claims` 解析 `new_entities` → 去重后自动建 `provisional` 节点 + `accepted` claim→node `about`；动态改为按（来源版本 + 实体）分组，未归类仍一条一动态；既有实体链接仍 `proposed`。
- `app/main.py`：`/capabilities` 的 `entity_disambiguation` 更新；API **0.11.0**（端点仍 70）。
- 前端：feed 卡片新增“关于 {实体}”标签与多句预览；任务页显示“新建实体 N 个”。
- 测试：新增 `tests/test_m4_entities.py` 4 项（双实体各一条动态、单实体多句合并、同名复用、无实体逐条）；`tests/test_m3_wrapup.py` 更新空库/去重语义。网络全部为 `httpx.MockTransport`。
- `npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0008 (head)**；`docs/openapi.json` 重导出 **70 路径 / 0.11.0**。

### 未执行/未验证

- 真实模型实体消歧与自动建实体：仍只用 `httpx.MockTransport`，未做真实烟测。
- 主题（topic）自动创建：本轮只实现实体（entity）自动创建，主题仍须手动创建。
- 自动建的实体为 `provisional`，可在实体管理里改名/合并/删除；模型不自动合并两个已有实体。

## 2026-10-21 01:10（UTC+8）· M4 Phase 2：事实核查建议路由、版本过期封顶与 AC32–45 阻断数据集

**本轮范围**：把 `fact_check` 模型路由接成**只给建议**的可选环节；来源版本被取代时封顶旧材料；抽取阶段确定性拦截断章取义；收紧 `scoped_fact` 必须人工接受；建立 AC32–45 专项阻断数据集（48 案例 + 12 对照）与运行器。代码与自动测试**没有真实外部检索、没有真实模型调用、没有真实事实核查烟测**。

### 本轮实际执行

- 基线复现（改动前）：`infra/init-local.py` 复用本地 PostgreSQL；`pytest -q` **299 passed / 93 warnings（42.42s）**；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0008 (head)**；`docs/openapi.json` = 0.9.0 / 70 路径；正式库 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水 / 6 条核查记录**。
- 未新增迁移（head 仍 `0008`）；未改 0001–0008、未删库、未覆盖 `.env`。
- 后端测试：`pytest -q` → **385 passed / 93 warnings（45.72s）**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_m4_ac_dataset.py` 86 项与 `tests/m4_support.py` 辅助；网络全部为 `httpx.MockTransport` 或完全离线。
- 前端：`npm.cmd --prefix apps/web run build`（`tsc -b && vite build`）通过；主张详情/核查记录显示模型建议与一致性，模型管理补充说明；**没有做浏览器点击实测**。
- API：端点结构不变（70 路径），版本升至 **0.10.0**；`/verification-capabilities` 与 `/capabilities` 更新能力声明；`docs/openapi.json` 重新导出。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”；`/api/v1/health/ready` = `{"status":"ok","database":"ok"}`、5173 返回 200、OpenAPI 0.10.0 / 70 路径；`/verification-capabilities`、`/claims/{id}/fact-checks` 未登录 401；`stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- 正式库计数保持 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水 / 6 条既有核查记录 / 0 条事实关系**；未新增调用、预算流水或核查记录。

### 本轮验证到的行为（全部为隔离 schema、合成材料与 MockTransport）

| 范围 | 实际结果 | 不能据此声称 |
|---|---|---|
| AC32 虚假功能宣传 | 无材料/间接/单条 E2 的重要宣传不能得到事实确认；7 个读取面按门槛显示未核实或隐藏；两个独立组确认后才允许 `scoped_fact` | 真实产品功能已被核查 |
| AC33 断章取义 | 引用在限定分句前截断时保存为 candidate（`CONTEXT_TRUNCATED_LIMITATION`），不建证据关系、不发布；完整句与并列补充句正常通过 | 模型不会漏掉所有截断 |
| AC34 过期版本 | 来源出现新版本后旧材料封顶 E1；两个旧版本不能拼 E3；旧的待复核记录因快照变化返回 409；保留期到期降 E0 | 旧版本材料已失效删除（仍保留用于历史） |
| AC35 循环转载 | 10 个同源转载仍为 E2；同一组即使标 confirmed 也不升级；E3 需要不同上游组且至少一组确认独立 | 系统能识别所有隐藏同源关系 |
| AC36 伪造测评 | 疑似篡改 E0、未核验完整性 E1；无法判断时只标未核实，不指控造假；一条疑似不否定另一条可核验材料 | 已做真实取证或水印检测 |
| AC37 真假批评 | 4 组真实限制与错误指控分别评估：前者 corroborated、后者 refuted，互不牵连 | 已生成纠错表达式 |
| AC38 低可靠来源 | 来源可靠度 `unknown` 不构成一票否决；当前命题有直接可检查证据时允许发布 | 已完成来源画像 |
| AC39 无外部工具 | 模型声称读过外部文档/调用工具 → `MODEL_CLAIMED_EXTERNAL_READS`，建议被拒绝，检索清单仍 `kb_only`、`external_reads=[]` | 存在外部检索连接器 |
| AC40 多来源/置信度 | 抽取置信度 1.0 与 10 条同源材料都不改变档位；数量与分数不是事实输入 | 多来源已交叉验证 |
| AC41 不对称争议 | E2 支持 + E1 反证 → `conflicted`，`dispute_balance=support_stronger` 且缺口写明不对称 | 争议已被裁决 |
| AC42 纠错传播 | 接受 `conflicted` 记录后，feed/detail/日报/导出同一读取路径立即不再显示旧的确定性句子；历史记录与版本链仍可查 | 已生成 correction 动态或统一重算缓存 |
| AC43 全面门槛 | 重要事实待复核时，标题/正文/日报/导出/概览全部 hold，`publication-preview` 逐面返回 blocked | 搜索/导出是独立缓存 |
| AC44 事实核查预算 | 路由启用后一次建议调用：预算 0 时不请求 provider、写 `blocked` 调用行、记录仍待复核；成功调用计入账本并结算；模型结论（refutes/supports）不改变规则分档 | 真实模型事实核查已验证 |
| AC45 并发与迁移 | revision/指纹/材料快照变化 → 409；历史 `checker_type='rule'` 的 accepted 记录不能把句子提升为 `scoped_fact` | 多进程/多节点并发已覆盖 |

### 本轮未执行/未验证

- **真实模型事实核查**：`fact_check` 路由只在本轮用 `httpx.MockTransport` 验证；没有用真实模型处理任何材料，`live_fact_check_verified=false`。
- **外部检索**：没有外部网页/文档连接器；`kb_only` 空结果不是反证，`corroborated` 不是外部已查证。
- **`claim_comparison`**：未接入流水线；同实体候选主张只作为 manifest 列表出现，没有模型比较或关系结论。
- **来源画像与纠错表达式**：`GET /sources/{id}/reliability` 仍返回 `unknown`；没有 `source_reliability` 评估记录、纠错动态生成或缓存重算。
- **界面**：只有生产构建与接口回归，**没有浏览器点击实测**，没有 375px/1280px 自动 UI 覆盖。
- **B站/调度**：本轮没有新增 B站 请求；真实扫码确认、分P批量、凭证恢复、定时调度仍未验证。
- **数据集边界**：60 个案例全部合成，不代表真实平台材料分布；通过数据集不等于 AC01–AC45 全部通过或 M4 整体签收。

### 最终验证

- `python -m pytest -q`：**385 passed / 93 warnings（45.72s）**。
- `npm.cmd --prefix apps/web run build`：通过；无新增前端/Python 依赖。
- `alembic current`：**0008 (head)**；未新增迁移。
- `infra/launch.ps1 -NoBrowser` 与 `infra/stop.ps1`：见上；停止后三端口全部释放。
- OpenAPI：**70 路径 / 0.10.0**；端点结构未变。
- 数据集：`tests/fixtures/ac32-45.json`（48 + 12，全部合成）；说明见 `docs/AC32-45-dataset.md`。

## 2026-10-20 23:45（UTC+8）· M4 Phase 1：库内事实材料、版本化核查记录与人工复核

**本轮范围**：把事实核查从“只有一条 insufficient 记录”扩展为库内事实材料（支持/反驳/背景）、独立 `fact_check` 任务、E0–E3 分档、只追加的核查记录、人工接受/拒绝、硬门槛与服务端发布预览。代码与自动测试**没有外部检索、没有模型调用、没有真实事实核查烟测**；材料均为隔离 schema 中的合成文本。

### 本轮实际执行

- 基线复现（改动前）：`infra/init-local.py` 复用本地 PostgreSQL；`pytest -q` **275 passed / 93 warnings（34.35s）**；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0007 (head)**；正式库 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水 / 6 条既有 insufficient 记录**。
- 迁移 0008：事实证据载荷/锚点约束触发器、`verification_records` review/checker/trigger 约束与索引、事实关系与 fact status 索引；`alembic current` = **0008 (head)**；未改 0001–0007、未删库、未覆盖 `.env`。
- 后端测试：`pytest -q` → **299 passed / 93 warnings（43.84s）**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_m4_verification.py` 24 项（12 单元 + 12 集成），无网络、无模型调用。
- 前端：`npm.cmd --prefix apps/web run build`（`tsc -b && vite build`）通过；新增主张详情核查面板与工作台发起入口，**没有做浏览器点击实测**。
- API：新增 7 条路径（`POST /claims/{id}/evidence-links`、`POST|GET /claims/{id}/fact-checks`、`GET /verification-records/{id}`、`POST /verification-records/{id}/review`、`GET /verification-capabilities`、`GET /sources/{id}/reliability`、`POST /updates/{id}/publication-preview`）；`docs/openapi.json` 重新导出 **70 路径 / 0.9.0**。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”；`/api/v1/health/ready` = `{"status":"ok","database":"ok"}`、5173 返回 200、OpenAPI 0.9.0 / 70 路径；新端点未登录时 `POST` 为 401（仅 GET 的路由用 GET 校验同为 401）；`api.error.log` 只有 Uvicorn 启动 INFO，`worker.error.log` 与 `web.error.log` 均 0 字节；`stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- 正式库计数保持 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**，`verification_records` 仍为既有 6 行、`fact_*` 关系 0 行；未改写任何既有来源、主张或核查记录。

### 本轮验证到的行为（全部为隔离 schema 与合成材料）

| 范围 | 实际结果 | 不能据此声称 |
|---|---|---|
| 事实材料锚点 | 引文必须在所选片段的存储文本中逐字出现且唯一；模型/用户自报偏移不被信任，`char_start/char_end/quote_hash` 由服务端计算 | 外部材料已被读取 |
| 载荷校验 | `fact_evidence.v1` 必含锚点、命题范围、直接性、上游组、独立性、条件、完整性、透明度、限制；非法枚举/控制字符/未知片段/不存在的引文拒绝 | 材料内容真实或充分 |
| 角色说明 | 同一来源版本同时作表达溯源与事实证据时必须填写用途说明（≥10 字） | 该材料足以支持命题 |
| 材料上限/去重 | 每个主张最多 20 条非拒绝材料；相同锚点重复提交返回同一关系（deduplicated） | 有数量就等于有证据 |
| 分档 | 间接或条件未知/完整性未核验 → E1；直接 + 五项条件匹配 + 完整性已核验 → E2；两个不同上游组且确认独立 → E3；疑似篡改或条件不匹配 → E0 | 分档是真实外部评估 |
| `fact_check` 任务 | 只读库内材料与同实体候选主张；`retrieval_manifest` 记录 `kb_only`、`external_reads=[]`、`model_route=not_wired_in_this_phase`；不调用模型、不联网；fence 事务内追加 `pending` 记录 | 已做外部检索或模型核查 |
| 状态不变 | 核查任务结束后 `claims.fact_check_status` 仍为 `unchecked`（或原值）；指针不动 | 任务完成即事实确认 |
| 接受 | 追加一条 `checker_type='human'`、`review_status='accepted'` 且 `supersedes_id` 指向原记录的版本；原子接受该记录列出的 `proposed` 材料（写 `assessment_record_id`）并移动 `current_verification_id`；写审计 | 接受等于外部已查证 |
| 拒绝 | 追加 rejected 版本；主张状态与指针不变；材料保持 `proposed` | 拒绝等于命题为假 |
| 过期/重复 | 输入指纹、claim revision 或材料快照哈希不一致 → 409 `STALE_VERIFICATION_INPUT`；已有复核版本或非 pending 记录 → 409 `VERIFICATION_ALREADY_REVIEWED` | 旧输入可以覆盖新结论 |
| 硬门槛 | E1 的 `corroborated`、重要事实的 E2 `corroborated`、E1 的 `refuted`、有 E2/E3 支持却 `refuted` 的评估 → 422 `PUBLICATION_GATE_BLOCKED`，主张不变 | 人工点击可以绕过门槛 |
| 记录不可变 | 直接 UPDATE/DELETE `verification_records` 被数据库触发器拒绝 | 记录可以原地修改 |
| 发布门槛 | 记录为 `scoped_fact` 但主张只有 insufficient 时，动态对 feed 与 detail 均隐藏；接受 E2 corroborated 后同一条动态出现；已存储的 attributed_unverified 在记录被接受后仍显示（更弱表达不被隐藏），不会被自动升级 | 接受后历史句子自动变成事实 |
| 预览 | `POST /updates/{id}/publication-preview` 只返回逐句 action/mode/reason 与 `mode_still_allowed`，不改数据；`expected_revision` 过期返回 409 | 预览等于发布 |
| 能力声明 | `/verification-capabilities` 明确 `kb_only`、无外部连接器、模型路由未接入、`auto_accept_allowed=false`、`live_fact_check_verified=false`；`/sources/{id}/reliability` 返回 `unknown` | 来源画像或外部核查已实现 |
| 队列 | `GET /verification-queue` 返回最新记录状态与待复核数量，支持 `status`/`review` 过滤；非法值 400；重复发起核查复用活动任务 | 队列即已复核 |

### 本轮未执行/未验证

- **真实模型**：本轮没有模型调用；`fact_check`/`claim_comparison` 模型路由未接入。真实模型抽取、语义复核、实体消歧仍未执行。
- **外部检索**：没有外部网页/文档检索连接器；`kb_only` 的空结果不是反证。没有真实事实核查烟测。
- **来源画像与纠错传播**：`GET /sources/{id}/reliability` 只返回 `unknown`；评估记录、纠错 propagation、日报/导出/搜索的统一重算尚未实现。
- **AC32–45 专项数据集**：没有新增 48 个合成/授权案例与 12 个对照数据集，也没有整体门槛签收；本轮仅覆盖其中的材料分档、重要事实 E3、循环转载不能自动升 E3、无外部工具降级、发布门槛与并发输入过时等基础行为。
- **界面**：主张详情面板与工作台按钮只有生产构建和接口回归，**没有浏览器点击实测**；未做 375px/1280px 自动 UI 覆盖。
- **B站/调度**：本轮没有新增 B站 请求；真实扫码确认、分P批量、凭证恢复、定时调度仍未验证。

### 最终验证

- `python -m pytest -q`：**299 passed / 93 warnings（43.84s）**。
- `npm.cmd --prefix apps/web run build`：通过；无新增依赖。
- `alembic current`：**0008 (head)**；未改 0001–0007。
- `infra/launch.ps1 -NoBrowser` 与 `infra/stop.ps1`：见上；停止后三端口全部释放。
- OpenAPI：**70 路径 / 0.9.0**；新增 7 条路径。

## 2026-10-20 21:25（UTC+8）· M3 收尾：表达语义复核与实体候选人工确认

**本轮范围**：把 `evidence_review` 与 `entity_disambiguation` 两个任务路由接进 `process_source`，并给模型实体候选增加人工接受/拒绝。代码与自动测试全部使用 `httpx.MockTransport` 替身；**没有用真实模型处理任何材料**。

### 本轮实际执行

- 基线复现（改动前）：`infra/init-local.py` 复用本地 PostgreSQL；`pytest -q` **255 passed / 93 warnings（34.85s）**；`npm.cmd --prefix apps/web run build` 通过；已有实例 `/api/v1/health/ready` = `{"status":"ok","database":"ok"}`、5173 返回 200、OpenAPI 0.7.0 / 62 路径；正式库 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**。
- 迁移 0007：`claim_expression_reviews` 与 proposed 关系索引；`alembic current` = **0007 (head)**；未改 0001–0006、未删库、未覆盖 `.env`。
- 后端测试：`pytest -q` → **275 passed / 93 warnings（61.43s）**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_m3_wrapup.py` 20 项（10 单元 + 10 集成），网络全部为 `httpx.MockTransport`。
- 前端：`npm.cmd --prefix apps/web run build`（`tsc -b && vite build`）通过；核查工作台、任务页、主张详情与模型管理说明已更新。
- API：新增 1 条路径 `POST /relations/{relation_id}/review`；`docs/openapi.json` 重新导出为 **63 路径 / 0.8.0**。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”；`/api/v1/health/ready` ok/ok、5173 返回 200、OpenAPI 0.8.0 / 63 路径；`/api/v1/relations/{id}/review`、`/claims/{id}`、`/capabilities`、`/task-routes` 未登录均 401；`stop.ps1` 退出码 0，8000/5173/55432 全部释放。`api.error.log` 仅 Uvicorn 启动 INFO，`worker.error.log` 与 `web.error.log` 均为 0 字节。
- 正式库计数保持 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**；`claim_expression_reviews` 0 行、proposed 关系 0 行。

### 本轮验证到的行为（模型为 MockTransport 替身）

| 范围 | 实际结果 | 不能据此声称 |
|---|---|---|
| 复核分派 | `evidence_review` 路由未配置时不调用第二模型，锚点通过的主张按原门槛发布；启用为 `model` 后才发送复核提示 | 真实模型已复核 |
| 有界复核提示 | 只发送带服务端锚点的主张、引文和限定上下文；超出预算的主张标记 `truncated`、不发送；路由输入上限过小返回 `ROUTE_INPUT_TOO_SMALL` | 模型看到了完整长文 |
| 复核输出校验 | 只接受请求中出现过的 `claim_ref` 和 supports/partially_supports/unsupported/unclear；未知、重复、非法条目拒绝；缺失条目记为 missing | 模型可以自证语义支持 |
| 发布门槛 | 只有 supports/partially_supports 的锚点主张写 `validated` 并进入 `PublicationPolicy`；unsupported/unclear/未复核主张写 `candidate`，不建核查记录、不生成动态 | 语义支持等于事实确认 |
| 复核记录 | 每条复核结果写入追加型 `claim_expression_reviews`（verdict/rationale/limitations/quote_count/model/invocation）；`fact_check_status` 始终 `insufficient`，任务结果 `fact_confirmation=false` | 已做事实核查 |
| 复核失败 | 上游 503 → 锚点主张保留为 candidate，任务 `needs_review` + `EXPRESSION_REVIEW_UNAVAILABLE`，不发布；抽取调用仍 `succeeded`、复核调用 `failed` | 失败被当成成功 |
| 复核预算 | 复核预留不足 → 不请求 provider，写一条 `blocked` 调用记录；主张保留为 candidate，任务 `needs_review`，不自动重试 | 预算不足仍发布 |
| 部分支持 | partially_supports 可发布，动态 `structured_body` 带 `expression_review` 与 `expression_limitations` | 限制被忽略 |
| 实体候选 | 只发送已有实体；模型只能返回请求中的 node_id；编造节点/未知主张/重复/超限/非法角色拒绝，不创建或合并节点 | 模型可以新建实体 |
| 实体落库 | 通过的候选写 `relations(status='proposed', asserted_by='model')`；接受前不出现在实体详情或实体筛选动态；接受后补 update→node 关系并可见；拒绝保持不可见 | 候选等于已确认归属 |
| 实体失败 | 实体调用 503 不影响主张发布；任务结果记录 `unavailable` 与错误码 | 实体消歧已完成 |
| API | `POST /relations/{id}/review` 只允许模型提出的 claim→node `about` 候选；非候选/已复核返回 409；接受/拒绝写审计 | 可以绕过人工确认 |
| 读时边界 | `GET /claims/{id}` 返回复核记录与 `about_nodes`；`GET /verification-queue` 返回最新 `expression_verdict`；节点详情的来源主张只统计已接受关系 | 未接受候选被展示为已归属 |

### 本轮未执行/未验证

- **真实模型抽取、真实语义复核、真实实体消歧**：连接测试只证明端点与密钥当时可用；没有用真实模型处理任何材料。
- **真实 B站 网络**：本轮没有新请求；沿用 2026-10-20 的元数据/字幕与 WBI 搜索烟测记录。真实扫码确认、分P批量、凭证恢复、定时调度仍未验证。
- **事实核查/主张比较**：`claim_comparison`、`fact_check` 路由尚未接入流水线；语义复核只判断“引文是否支持表达”，不判断事实真假。
- **运行与规模**：跨进程并发配额、多节点预算一致性、供应商账单级“已确认费用”、Anthropic/Gemini 原生协议、浏览器端模型面板自动点击回归、375px/1280px 自动 UI 覆盖。
- **复核预算重试**：复核阶段预算不足时任务为 `needs_review` 并保留 candidate，不会自动重试；抽取阶段零预算仍是可重试的 `blocked_budget`。

### 最终验证

- `python -m pytest -q`：**275 passed / 93 warnings（61.43s）**。
- `npm.cmd --prefix apps/web run build`：通过；无新增前端/Python 依赖。
- `alembic current`：**0007 (head)**；未改 0001–0006。
- `infra/launch.ps1 -NoBrowser` 与 `infra/stop.ps1`：见上；停止后三端口全部释放。
- OpenAPI：**63 路径 / 0.8.0**；新增 `POST /relations/{relation_id}/review`。

## 2026-10-20 20:20（UTC+8）· M3 Phase 2：路由化主张抽取与服务端锚点校验

**本轮范围**：把 M3 Phase 1 的模型网关接进 `process_source`，由 `claim_extraction` 任务路由决定是否调用模型，并对模型输出做服务端引用/锚点校验。代码与自动测试全部使用 `httpx.MockTransport` 替身；**没有用真实模型处理任何材料**。本轮末尾用户在本机自行配置 MIMO 2.6 并执行了 **1 次真实连接测试**（固定合成提示，不发送导入材料），结果见下文。

### 本轮实际执行

- 基线复现（改动前）：`infra/init-local.py` 复用本地 PostgreSQL；`pytest -q` **234 passed / 93 warnings（25.45s）**；`npm.cmd --prefix apps/web run build` 通过；`infra/launch.ps1 -NoBrowser` 就绪，`/health/ready` = `{"status":"ok","database":"ok"}`、5173 返回 200、OpenAPI 0.6.0 / 62 路径；`infra/stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- 后端测试：`pytest -q` → **255 passed / 93 warnings（30.71s）**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_m3_extraction.py` 21 项：11 项无数据库单元测试 + 10 项集成测试；**除用户显式执行的 1 次真实连接测试外，所有网络均由 `httpx.MockTransport` 替身**。
- 前端：`npm.cmd --prefix apps/web run build`（`tsc -b && vite build`）通过；动态卡片、任务页与任务路由说明已更新。
- API：无新增/删除端点，`docs/openapi.json` 重新导出为 **62 路径 / 0.7.0**；`/capabilities` 增加 `model_extraction` 说明与真实连接测试记录。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”；`/health/ready` ok/ok、5173 返回 200、OpenAPI 0.7.0 / 62 路径；`/model-providers`、`/task-routes`、`/ai/invocations`、`/capabilities` 未登录均 401；`api/worker/web` 错误日志均为 0 字节；`stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- 未改迁移（仍 `0006 (head)`）、未删库、未覆盖 `.env`、未改写既有用户数据。连接测试前正式库计数为 **1 用户 / 7 来源 / 10 任务**，供应商/模型/路由/调用/流水均为 0；用户完成测试后为 **1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**，来源与任务数未变。

### 本轮验证到的行为（模型为 MockTransport 替身）

| 范围 | 实际结果 | 不能据此声称 |
|---|---|---|
| 分派 | `claim_extraction` 路由未配置或未启用时，真实材料仍停在 `needs_review`，claims/updates 为 0、无调用记录；合成示例 `metadata.demo=true` 即使路由启用也走本地 Mock，不产生模型调用 | 真实材料已被模型处理 |
| 有界提示 | 只发送路由 `max_input_tokens` 以内的片段；超出部分 `partial=true`、`truncated=true`，其未发送尾部不能被引用；上限过小返回 `ROUTE_INPUT_TOO_SMALL`，不发请求 | 模型看到了完整长文 |
| 锚点校验 | 只接受请求中出现过的 `segment_id`；引文必须逐字存在（空白折行归一化后映射回原文）；偏移按 Unicode code point 计算，emoji 前的引文偏移正确；模型自报偏移不一致 → `OFFSET_MISMATCH`；未知片段/不存在引文/非法枚举整条拒绝 | 模型可以自证引用 |
| 无锚点候选 | 无有效引文的主张写为 `review_status='candidate'`，不建 `evidenced_by`、不写核查记录、不生成动态；`verification-queue` 可见，`updates` 为空 | 候选主张已被接受或发布 |
| 部分校验 | 2 条主张中 1 条通过锚点、1 条失败：只发布 1 条动态，另 1 条为 candidate；任务 `succeeded` 且结果含 `validated/candidates/rejected` | 失败主张被静默丢弃 |
| 发布门槛 | anchored claim 写 `insufficient` 核查记录后按 `PublicationPolicy` 发布为“来源观点（未经事实确认）”；`fact_check_status` 不会被模型设置；任务结果 `fact_confirmation=false`、`semantic_review=not_performed` | 已完成语义复核或事实确认 |
| 预算顺序 | 调用前预留最坏费用 0.0028（2000 输入 + 400 输出 @ 1/2 USD per Mtok），HTTP 调用时无数据库事务；结算 0.0005（300/100 token），账本 `settled`，调用记录 `estimated` | 供应商账单已对账 |
| 零预算 | 日/月预算为 0 时 `blocked_budget` + `BUDGET_EXHAUSTED`，provider 未被请求，写一条 `blocked` 调用记录、无预算流水、无 claims；提高预算后 `POST /jobs/{id}/retry` 可重新执行并成功 | 未确认也能调用 |
| 策略/失败映射 | `local_only` 下云端模型 → `failed` + `DATA_POLICY_BLOCKED`，未发请求；非法 JSON 输出 → `needs_review` + `MODEL_OUTPUT_INVALID`，claims/updates 为 0 | 非法输出被当作成功 |
| fence | 写入前 fence 被抢：claims/updates 均为 0，任务保持新 fence 的 `running`，不发布部分结果 | 旧尝试可以越过 fence 写入 |
| 结果可解释 | 任务结果只含计数、错误码与模型名，不含材料正文；调用账本 `summary` 只含片段数/字符数/截断标记与来源版本 ID | 日志或账本保存了导入原文 |

### 真实连接测试（用户在本机执行，2026-10-20 20:35，UTC+8）

用户在应用界面内自行填写 MIMO 2.6 的端点和密钥（密钥只进入本机应用，未出现在聊天、日志或本文档），把数据策略改为“允许云端”并设置预算后，点击一次“测试连接”。请求只包含固定合成探测提示，不含任何导入材料。

| 项目 | 实际结果 |
|---|---|
| 供应商 | 协议 `openai_compatible`，端点 `https://token-plan-cn.xiaomimimo.com/v1`，状态 `ok`，`last_tested_at=2026-10-20 20:35:10 +08`，无最近错误 |
| 模型 | 真实 ID `mimo-v2.6-pro`，显示名 `MIMO26`，启用；上下文 8192 / 最大输出 1024 |
| 调用 | `connection_test`，`succeeded`，1 次尝试，延迟 1625 ms |
| token | 输入 37 / 输出 26 / 缓存 0 |
| 费用 | 估算并结算 `0.0000445 USD`，`cost_status=estimated`（未经供应商账单确认） |
| 预算 | 预留 `0.000256 USD` → 结算 `0.0000445 USD`，流水 `settled` |
| 数据写入 | 无 claims/updates；未发送任何导入材料 |

该结果证明**该端点与密钥在 2026-10-20 当时可完成一次 OpenAI 兼容 Chat Completions 调用**，不证明持续可用性、模型能力匹配、抽取质量，也不证明真实模型能返回可锚定引用。`/capabilities` 的 `live_model_protocol_verified` 现为 true 并带日期/范围说明；`model_extraction.live_model_extraction_verified` 仍为 false。

### 本轮未执行/未验证

- **真实模型抽取**：连接测试已成功，但**没有用真实模型处理任何材料**，没有生成真实 claims/updates；长文本上的提示裁剪、模型输出能否通过服务端锚点校验、真实 token 与费用规模均未验证。`model_extraction.live_model_extraction_verified=false`。
- **语义复核**：不调用第二模型判断引文是否支持主张；只校验“原文确实这样说”，不校验“这句话支持该主张”。事实核查仍为 `insufficient`，核查工作台仍只读。
- **实体消歧/主张比较**：这两个路由尚未接入流水线，模型抽取产生的动态没有 `about` 实体关系，主题/实体筛选不会包含它们。
- 跨进程并发配额、多节点预算一致性、Anthropic/Gemini 原生协议、供应商账单级已确认费用、浏览器端模型面板点击实测、375px/1280px 自动化回归。
- 真实 B站 网络本轮**没有新请求**；沿用 2026-10-20 的烟测记录，不重复声明。

### 最终验证

- `python -m pytest -q`：**255 passed / 93 warnings（30.71s）**。
- `npm.cmd --prefix apps/web run build`：通过；无新增前端/Python 依赖。
- `alembic current`：**0006 (head)**；未新增迁移。
- `infra/launch.ps1 -NoBrowser` 与 `infra/stop.ps1`：见上；停止后三端口全部释放。
- 真实模型连接测试（用户执行）：1 次 `connection_test` 成功（输入 37 / 输出 26 token，估算 0.0000445 USD）；正式库现有 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水。
- OpenAPI：**62 路径 / 0.7.0**；端点结构未变。

## 2026-10-20 19:55（UTC+8）· M3 Phase 1：模型供应商、网关、预算与费用账本

**本轮范围**：在真实 B站 烟测通过后进入 M3。实现模型供应商与密钥管理、四种协议（OpenAI 原生 / OpenAI 兼容 / 本地兼容 / Mock）、模型配置、8 类任务路由与回退、调用前预算预留/调用后结算、调用与费用账本、模型管理界面与预算设置。**没有配置真实模型供应商密钥，也没有发起任何真实模型请求。**

### 本轮实际执行

- 基线复现（改动前）：`infra/init-local.py` 复用本地 PostgreSQL；`pytest -q` **181 passed / 93 warnings（18.36s）**；`npm.cmd --prefix apps/web run build` 通过；`infra/launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”，`/health/ready` 返回 `{"status":"ok","database":"ok"}`、5173 返回 200、`/openapi.json` 为 0.5.0 / 50 路径；`infra/stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- 迁移 0006：新增 `provider_connections`、`model_profiles`、`task_routes`、`ai_invocations`、`ai_budget_ledger`，并给 `workspace_settings` 增加 `budget_currency`、`routing_revision`。`alembic current` = **0006 (head)**；未改 0001–0005、未删库、未覆盖 `.env`。
- 后端测试：`python -m pytest -q` → **234 passed / 93 warnings（26.02s）**（真实 PostgreSQL 17 隔离 schema）。新增 35 项无数据库单元测试与 18 项集成测试；**全部网络请求由 `httpx.MockTransport` 替身，没有任何真实模型请求**。
- 前端：`npm.cmd --prefix apps/web run build`（`tsc -b && vite build`）通过；“模型管理”页新增四个页签与费用摘要，“系统设置”页新增预算/币种/数据策略表单，任务页显示今日估算费用。
- API：新增 12 条路径（供应商 CRUD/密钥/测试、模型 CRUD、任务路由整组保存、测试调用、调用列表与详情、费用汇总、预算设置）；OpenAPI 重新导出为 **62 路径 / 0.6.0**。
- 启动/停止（改动后复测）：见下方“最终验证”。

### 本轮验证到的行为（M3，测试替身）

| 范围 | 实际结果 | 不能据此声称 |
|---|---|---|
| 供应商与密钥 | 创建时密钥加密保存，响应只含 `****`+末 4 位与 `configured=true`；列表/调用详情不含密钥；替换与清除可用；清除后数据库为 NULL；协议/端点变更重置为“未测试” | 供应商真实可用；平台授权已撤销 |
| 端点校验 | 拒绝 ftp、内嵌账号、查询串、空白、空端点；接受本地 http 与 Mock；不跟随重定向，3xx 映射为 `UPSTREAM_REJECTED` | 能访问任意内网端点 |
| 模型 | 计价货币必须等于工作区币种（否则 422 `CURRENCY_MISMATCH`）；输出>上下文 422；重复模型 409；负价 422 `INVALID_MODEL_PRICE`；新模型默认停用；被路由引用时删除返回 409 `MODEL_IN_USE` | 真实模型存在或价格正确 |
| 任务路由 | 8 类任务默认 `mock`/未配置；整组保存是单事务；未知模型/未知任务整组 422 且不落库；If-Match 缺失 428、过期 409；云端模型 + 允许云端但未 `confirm_cloud` 时 422 `ROUTE_CLOUD_CONFIRM_REQUIRED` | 真实路由调用了模型 |
| 默认关闭 | 路由未启用时测试调用 409 `TASK_ROUTE_DISABLED` 且不产生调用记录；`confirm_spend` 缺失 422 `CALL_CONFIRM_REQUIRED` | 未确认也能调用 |
| 数据策略 | 工作区 `local_only` 时云端路由调用被 409 `DATA_POLICY_BLOCKED` 阻断，且不创建调用行 | 云端发送已获用户同意 |
| 零预算 | 日/月预算为 0 时云端调用 409 `BUDGET_EXHAUSTED`；provider 未被请求；写入一条 `blocked` 调用记录、无预算流水 | 阻断被写成成功 |
| 预算预留/结算 | 预留 = (1000×1.0 + 500×2.0)/1e6 = 0.002；供应商返回 100/50 token 时结算 0.0002，流水 `settled`，`/settings` 与 `/costs/summary` 一致 | 供应商账单已确认 |
| 未知用量 | 响应无 usage 时按 0.002 结算并标记 `unknown`，费用只会高估 | 实际费用等于估算 |
| 回退 | 主模型 503 后按顺序使用 Mock 备用模型成功；尝试日志记录 `UPSTREAM_FAILED`；预留释放为 0 | 真实回退已在生产验证 |
| 并发预算 | 两个线程同时预留 0.0005、日预算 0.0005 时只有一笔成功，另一笔 `BUDGET_EXHAUSTED`，账本恰好 1 行 | 多节点/多进程预算一致 |
| 币种变更 | 已有 USD 流水时改为 CNY 返回 409 `CURRENCY_CHANGE_BLOCKED` | 支持多币种账本 |
| 供应商测试 | Mock 供应商测试成功并显示“不发起真实请求”；云端供应商在 `local_only` 下 409 `DATA_POLICY_BLOCKED`；探测提示固定且不含导入材料 | 真实供应商连接测试通过 |
| 调用账本 | 列表按 task/status 过滤，非法过滤 400；详情含 ledger；Mock 调用 `cost_status=none` 且金额 0；测试调用不写 claims/updates | 已用模型完成 AI 分析 |

### 本轮未执行/未验证

- **真实模型协议烟测**：没有配置任何真实供应商密钥，没有向 OpenAI 或任何兼容端点发出请求；`/capabilities` 中 `live_model_protocol_verified=false`。
- **模型抽取写入知识库**：`process_source` 仍使用内置 Mock 语法；测试调用只返回结果，不生成 claims/updates/relations。
- 跨进程并发配额、多节点预算一致性、Anthropic/Gemini 原生协议、供应商账单级已确认费用、浏览器端模型面板点击实测、375px/1280px 自动化回归。
- 真实 B站 网络本轮**没有新请求**；沿用 2026-10-20 的烟测记录（元数据/字幕/关键词搜索），不重复声明。

### 最终验证

- `python -m pytest -q`：**234 passed / 93 warnings（26.02s）**（真实 PostgreSQL 17 隔离 schema）。
- `npm.cmd --prefix apps/web run build`：TypeScript（`tsc -b`）与 Vite 构建通过；无新增前端依赖。
- `alembic current`：**0006 (head)**；正式库只读计数未变：**1 用户 / 7 来源 / 10 任务**；`provider_connections` / `model_profiles` / `task_routes` / `ai_invocations` / `ai_budget_ledger` 均为 0（未配置真实供应商，未产生调用）。
- `infra/launch.ps1 -NoBrowser`：输出“FishingNet 已就绪”，`/health/ready` 返回 `{"status":"ok","database":"ok"}`，5173 返回 200，`/openapi.json` 为 **0.6.0 / 62 路径**；`/model-providers`、`/task-routes`、`/costs/summary`、`/capabilities` 未登录均返回 401；`worker.error.log` 为空。
- `infra/stop.ps1`：退出码 0，8000/5173/55432 全部释放。
- 未改写 `.env`、未删除数据库、未修改迁移 0001–0005、未改写既有用户数据。

## 2026-10-20 16:10（UTC+8）· WBI 签名关键词搜索真实烟测通过

**用户已授权关键词搜索使用 WBI 签名**（扩展自此前"仅字幕查询允许标准 WBI"的授权，同一库 `bilibili-api-python` 的 OE 表与 `_enc_wbi` 函数）。

实现变更：`bilibili.py` 新增 `_get_wbi_key()`（从 `/x/web-interface/nav` 获取并缓存 30 分钟 mixin key）、`_sign_wbi()`（调用库的 `_enc_wbi`）、`_get_buvid()`（从 `/x/frontend/finger/spi` 获取并缓存 buvid3/buvid4 设备指纹）；`discover()` 在搜索前获取 buvid 并签名参数。`worker.discover()` 从 `platform_connections` 加载凭证并传递给 `collector.discover(credential=...)`。`FakeBilibiliCollector.discover()` 签名同步更新。测试 helper 预设 `_wbi_key`/`_buvid3`/`_buvid4` 跳过真实请求。

真实烟测结果（`_smoke_wbi.py`，2026-10-20 16:10 UTC+8）：

| 步骤 | 结果 |
|---|---|
| 加载 SESSDATA | OK（从 `platform_connections` 解密） |
| WBI key 获取 `/x/web-interface/nav` | OK（`ea1db124...4ff8`，len=32） |
| WBI 签名关键词搜索 `"AI"` 第 1 页 | **OK**：status=ok, items=20, has_more=True |
| WBI 签名关键词搜索 `"AI"` 第 2 页 | **OK**：status=ok, items=20, has_more=True |

- 搜索端点：`api.bilibili.com/x/web-interface/wbi/search/type`（WBI 签名）
- buvid 设备指纹：通过 `/x/frontend/finger/spi` 获取，搜索必需（无 buvid 返回 HTTP 412）
- WBI 签名使用 `bilibili-api-python` 的 `OE` 表与 `_enc_wbi`，与字幕适配器相同
- 未使用 WBI2、浏览器伪装、设备激活或额外重试
- 全量测试 181 passed（含更新后的 discovery flow 测试），前端构建通过

BV1MLJe6fEmH 状态确认：source `BV1MLJe6fEmH:p1`，content_status=ready，availability=available，版本 1（ai-zh，6 片段）。B站平台连接 status=connected，last_success_at=2026-10-20 12:30:37 CST。

## 2026-10-20 12:33（UTC+8）· 最终运行验证与数据库就绪修复

最终业务回归179 passed / 33 warnings（21.05s），依赖检查通过，前端构建通过。启停验收时既有停止脚本在数据库关机checkpoint等待超时后执行了其已有的强制停止分支；数据目录保留，下一次启动进入恢复。启动脚本原先仅检查TCP端口，误把恢复中的数据库当作就绪，已修正为pg_isready协议就绪检查；已有实例也等待协议就绪后再创建/检查数据库。新增2项独立回归通过（0.03s），覆盖端口开放但恢复未完成、恢复超时不误报就绪。未删除数据库或重写.env。

修复后 launch.ps1 -NoBrowser 成功：API/worker/web均启动，API健康检查HTTP200、前端HTTP200。本次进程环境FISHINGNET_SCHEDULER=0、最小请求间隔2秒；没有修改.env，双击重启按原环境配置。关键词测试来源的enabled与network_authorized已关闭。字幕TXT可直接读取 `.runtime/exports/BV1MLJe6fEmH-subtitles.txt`。

## 2026-10-20 12:32（UTC+8）· 字幕库接入完成、真实字幕取得；关键词被HTTP 412阻断

**覆盖旧的依赖未安装/字幕未取得状态：** bilibili-api-python 17.4.1及全部运行依赖已安装，pip check通过。官方wheel均与PyPI摘要核对，qrcode-terminal 0.8官方源码包经摘要和setup.py检查后本地构建安装。requirements.lock与docs/python-dependencies.json已更新；离线缓存保留。许可证边界与私有API固定说明见docs/subtitle-library-notice.md（GPL-3.0-or-later，非DSH MIT）。

受限适配器 `apps/api/app/collectors/bilibili_subtitles.py` 继承库的Video.get_subtitle，覆盖get_player_info，使用库内置标准WBI签名和OE参数表；所有实际HTTP仍走现有采集器。没有使用库的默认传输、WBI2、设备激活、ticket、浏览器UA或额外重试，没有修改库全局配置。搜索仍不签名。完整采集正常4请求（元数据/nav/清单/正文），重试消耗同一总预算。字幕CDN无凭证，持久Cookie亦不继承。worker短事务、心跳、fence保持原流程。

真实样本 `BV1MLJe6fEmH`：任务 `585521f4-14e8-4921-9c3c-fc040510b626` succeeded、error_code=null、subtitle_status=available；新版本 `f54d2445-c6ca-418d-8d70-e999ef52afb2`，6片段。原文TXT已导出到 `.runtime/exports/BV1MLJe6fEmH-subtitles.txt`（418字符）。未改写旧任务和不可变证据；未调用模型，未完成AI分析或事实确认。

真实关键词 `AI DEEPSEEK`：创建单轮验收来源，enabled=false，单轮授权network_authorized=true，上限2页/2请求/1条结果；实际首个请求HTTP412，任务failed / RISK_CONTROL，requests_used=1，pages=0，发现/入队均0。立即停止，没有重试或绕过风控。运行后已撤销network_authorized且enabled保持false并记审计。未继续M3：按既定顺序，关键词真实烟测尚未通过。

验证：全量首次运行受数据库恢复期影响（141 passed/38连接错误），日志确认恢复完成后重跑179 passed/33 warnings（22.42s）；前端生产构建通过；迁移仍0005(head)，OpenAPI重新导出50路径/0.5.0，无端点结构变化。新增回归覆盖标准签名请求、无库默认传输/浏览器UA/设备Cookie/WBI2，以及-403/-352/-101不重试、无效签名参数停止。既有CDN隔离重试回归仍通过。账号、.env、迁移未删除或覆盖；后续真实关键词请求需遵守平台风控处理，不能因本轮字幕成功宣称整个MVP完成。

## 2026-10-20 12:26（UTC+8）· PyYAML 校验成功，后续下载及元数据查询被代理阻塞

用户提供的 `pyyaml-6.0.3-cp312-cp312-win_amd64.whl` 已复制至 `.runtime/subtitle-wheels/`，与官方 PyPI SHA-256 比对通过；BeautifulSoup、lxml 本地包再次校验通过。Brotli 1.2.0 cp312 win_amd64 官方 wheel 下载遇到 ProxyError，按约定立即停止。为避免用户逐包往返，随后尝试仅查询官方元数据整理剩余依赖链接，首个 Brotli 元数据查询也遇到 ProxyError，未再尝试。准确 wheel 链接和未完成清单见 `docs/subtitle-offline-downloads.md`，不得宣称清单完整。

本轮未安装新包，未更改业务代码、requirements.lock、数据库或 .env，未修改代理；未运行新测试或真实B站请求。上轮81项通过仅为已有凭证隔离回归。字幕库适配仍待依赖齐备；M3未开始。

## 2026-10-20 12:23（UTC+8）· 兼容性核对、依赖校验及字幕凭证隔离

已核对本机 Python 3.12.14 / AMD64。官方 PyPI JSON 返回匹配的 lxml 6.1.3 cp312 win_amd64 wheel，已下载至 `.runtime/subtitle-wheels/`，SHA-256 `3e9a00d1c2c30936f7add097c41afc5da6556c580909104aafd382cac92a855c` 与官方一致。用户提供的 BeautifulSoup 4.15.0 亦已与官方摘要比对一致。此前 lxml “from versions: none”不能解释为没有兼容包。

继续以官方 JSON 查询依赖、通过官方 wheel 直链下载，PyYAML 阶段发生 ProxyError 后立即停止，无重试、无代理修改。失败且匹配当前环境的安装包链接：[pyyaml-6.0.3-cp312-cp312-win_amd64.whl](https://files.pythonhosted.org/packages/86/bf/899e81e4cce32febab4fb42bb97dcdf66bc135272882d1987881a4b519e9/pyyaml-6.0.3-cp312-cp312-win_amd64.whl)。未执行安装，requirements.lock 未变，不能宣称全部依赖已齐。

在不依赖新增安装包的现有采集器上修复字幕正文请求附带平台凭证的问题：正文请求不传入 Credential；每次发出请求前清空客户端持久 Cookie，防止接口 Set-Cookie 被同域 CDN 或重试继承。字幕清单仍明确传入本机凭证。新增两项合成回归覆盖外部及同域 CDN、503 重试、平台和 CDN Set-Cookie 均不外带；请求预算仍为4。`pytest -q tests/test_collectors.py tests/test_discovery_rules.py`：81 passed / 15 warnings（0.13s），警告为既有弃用提示。未执行全量数据库测试、前端构建或真实B站复测；API无变化。

数据库、用户账号、.env、迁移未变；标准WBI字幕库适配尚未完成，关键词烟测与M3仍未开始。接续先补齐PyYAML及其余依赖，然后完成库适配与完整回归；已有下载文件可复用。启动入口保持 `启动FishingNet.cmd`。

## 2026-10-20 12:14（UTC+8）· 使用用户提供的 wheel 接续，代理握手超时

用户提供的 `C:/Users/Administrator/Downloads/beautifulsoup4-4.15.0-py3-none-any.whl` 已核验包名、版本和 ZIP 完整性，SHA-256 为 `d6f88de62e1d4e38ecb1077eb9724cd0eff29d2a08ca16a401e9b9e93f117cf9`（尚未与官方摘要比对）。继续开发时将该文件与已有 bilibili-api-python wheel 一并交给 pip download 收集离线依赖，官方 PyPI、timeout=15、retries=0、总时限45秒。

本次下载退出码1，明确报错：`ProxyError: Cannot connect to proxy`，底层 `_ssl.c:993: The handshake operation timed out`。失败URL：[colorama 0.4.6 wheel metadata](https://files.pythonhosted.org/packages/d1/d6/3965ed04c63042e047cb6a3e6ed1a63a35087b6a609aa3a15ed8ac56c221/colorama-0.4.6-py2.py3-none-any.whl.metadata)。已停止，未再次下载、未切镜像、未改代理或证书校验。

colorama 0.4.6 本机已安装，此次是 pip download 收集离线包时重新查询，不能把它写为缺失依赖。当前未安装的依赖包括 bilibili-api-python、beautifulsoup4、lxml、pyyaml、brotli、qrcode、APScheduler、pillow、yarl、pycryptodomex、qrcode_terminal、PyJWT、soupsieve；此清单不是完整传递依赖锁定结果。两个主 wheel 已有本地文件，但库及其依赖尚未完成安装。

业务代码、requirements.lock、数据库、.env、迁移保持原状。未运行本轮测试和真实B站烟测；字幕适配与M3仍未完成。下一步先由用户处理代理连接问题或提供完整离线依赖，再继续安装和实现，不把本轮下载检查视为功能完成。

## 2026-10-20 12:10（UTC+8）· 接续检查：字幕依赖下载失败，已停止

已读取用户粘贴的前轮对话，确认继续接入字幕库及“仅字幕查询允许库内置标准 WBI”的授权；搜索路径保持原约束。

本轮仅尝试一次依赖下载（pip download，官方 https://pypi.org/simple，timeout=15、retries=0，总时限45秒），使用已存在的 bilibili-api-python 17.4.1 wheel。约6秒后退出码1：`Could not find a version that satisfies the requirement beautifulsoup4 (from versions: none)` / `No matching distribution found for beautifulsoup4`。这只能证明本次索引解析未取得可用分发，不能据此断言该包不存在或不兼容。按用户要求停止，未重试、未切镜像、未改代理。

本轮没有执行安装；bilibili-api-python 与 beautifulsoup4 均未安装，requirements.lock 未变。原 wheel SHA-256 仍为 `3054b824f0f7ac81917329f19909f39d24fb3b7d9de1672c6dd4cb9fa6e5757d`。

供用户查看的先前记录的官方直链：[BeautifulSoup 4.15.0 wheel](https://files.pythonhosted.org/packages/88/c6/92fcd42f1ba33e1184263f25bfabf3d27c383410470f169e4b8163bf9c17/beautifulsoup4-4.15.0-py3-none-any.whl)。本次未请求此直链；它只是当前首个缺失依赖，不代表全部依赖。

业务代码、数据库、凭证、.env 和迁移0001–0005未改。本轮未运行测试、启动/停止复现或真实B站请求；此前173项通过仅为历史结果。受限字幕适配层、字幕正文获取、关键词发现烟测仍未完成，M3未开始，API未变化。等待依赖下载问题解决后再继续安装、实现适配层、回归与已授权样本复测。启动入口仍为根目录 `启动FishingNet.cmd`。

## 2026-10-20 07:35（UTC+8）· 字幕库接入准备，依赖安装中止

**用户已明确授权：仅字幕查询允许使用 bilibili-api-python 内置标准 WBI 签名。** 搜索路径不变；不伪装浏览器、不绕过验证码/付费/风控。此授权覆盖旧的全面禁止 WBI 限制中的字幕查询部分，无需再次索取同一授权；不代表字幕已接入或真实复测通过。

已完成：修复 `ai-zh/ai-en` 与语言偏好的匹配，默认中文优先且严格中文筛选不会排除 `ai-zh`；字幕空清单提示改为“本次接口未返回可用字幕轨”，来源标签改为“未取得字幕”，不再断言视频没有字幕。保留原状态字段和不可变历史，不改写此前任务结果。新增 3 项合成回归，**全量 173 passed / 20 warnings（18.75s）**；前端生产构建通过。没有 API 结构变化，OpenAPI 无需变更。

核对发行包 `bilibili-api-python 17.4.1`：`get_subtitle` 调用 `/x/player/wbi/v2`，默认 WBI/WBI2 开启，默认浏览器 UA、自动 buvid 与 WBI 错误重试不能直接带入本项目。拟接入方式必须只保留获准的标准 WBI，关闭 WBI2 模拟行为、自动设备激活、ticket 与额外重试；所有实际请求统一经过现有限速、4 次请求预算、错误映射与短事务/心跳/fence。字幕 CDN 请求不应附带平台凭证。依赖许可证为 GPL-3.0-or-later，不能把 DSH 包装层的 MIT 误作其依赖许可证。尚未实现此适配层、尚未安装该库。

发行 wheel 已成功下载到本地 `.runtime/bilibili_api_python-17.4.1-py3-none-any.whl`，SHA-256 `3054b824f0f7ac81917329f19909f39d24fb3b7d9de1672c6dd4cb9fa6e5757d` 校验一致。随后 pip（官方 PyPI、timeout=20、retries=0）输出 `Downloading beautifulsoup4-4.15.0-py3-none-any.whl.metadata (3.8 kB)` 后持续无进展，按用户要求人工中止，退出码 1；没有收到具体 HTTP/网络异常，不能推断为某个域名故障。未重试、未切镜像、未改代理。核对后该库及其新增依赖均未安装，requirements.lock 未变。

下载信息：
- [bilibili-api-python 17.4.1 wheel（已取得）](https://files.pythonhosted.org/packages/07/50/520f98ef607687d686c9170b3ff2db944152bb0dc43bef0df7b4face8c5b/bilibili_api_python-17.4.1-py3-none-any.whl)
- [BeautifulSoup 4.15.0 wheel 元数据（最后可见下载项）](https://files.pythonhosted.org/packages/88/c6/92fcd42f1ba33e1184263f25bfabf3d27c383410470f169e4b8163bf9c17/beautifulsoup4-4.15.0-py3-none-any.whl.metadata)
- [BeautifulSoup 4.15.0 wheel](https://files.pythonhosted.org/packages/88/c6/92fcd42f1ba33e1184263f25bfabf3d27c383410470f169e4b8163bf9c17/beautifulsoup4-4.15.0-py3-none-any.whl)

下一步先处理依赖下载，再完成受限适配层和对应测试，最后对已授权 `BV1MLJe6fEmH` 复测。不能以语言选择修复冒充库接入，也不能宣称字幕已取得。本轮未新增真实 B站请求；关键词 `AI DEEPSEEK` 一轮发现仍未执行，M3 未开始。数据库、凭证、迁移 0001–0005 保留。


### 2026-10-20 · 用户提交样本后的真实采集核对

用户确认本机 B站登录态有效，并提供 `BV1MLJe6fEmH`、关键词 `AI DEEPSEEK`。随后用户在本机提交导入。只读核对发现：该来源在 **2026-10-20 07:28:40（UTC+8）** 完成采集任务，元数据已保存，字幕状态 `missing`，内容版本为空；任务 `6cd3e074-9209-40cc-a737-0339808026f7` 为 `succeeded`，`error_code=null`，结果说明字幕清单为空。该状态只表示采集流程结束，**不表示取得字幕或完成 AI 分析**。连接状态为 `connected`、无最近错误，但 `last_checked_at=null`，不能据此声称独立账号能力检查通过。

用户截图显示播放器提供“中文 AI”等字幕轨，与接口空清单存在差异：当前证据只能得出“本次接口未返回可用字幕”，不能断言视频没有字幕或账号没有权限。字幕正文与导出仍未验证；关键词发现仍未执行，未进入 M3。本次核对没有重新请求 B站，也没有读取或输出凭证明文。

对照 DSH 源码（commit `9a3e0e67d82feedf755c763807c806c4d125a245`）：`vendor/bilibili-mcp/mcp_server.py` 的 `bili_subtitle` 调用 `video.Video(...).get_subtitle(cid)`，优先 `ai-zh/zh-CN/zh`，下载返回的字幕 JSON 并拼接 `body[].content`；依赖声明 `bilibili-api-python>=17.4.1,<18`。未安装或复制其代码，尚未核实该依赖实际采用的上游协议，不能把库调用不同直接认定为本次空字幕的原因。


## 2026-10-20 07:13（UTC+8）· 端口已释放，本地复现通过

此前 Clash 出站连接已自然释放；重新检查 55432 无占用且实际绑定成功。未结束代理进程、未修改代理设置或数据库端口。

- `infra/init-local.py` 成功；`alembic current` 为 **0005 (head)**。正式库只读计数为 **1 用户 / 6 来源 / 6 任务 / 0 关键词来源 / 0 发现运行**，待处理联网任务为 0。
- 全量 `.venv\Scripts\python.exe -m pytest -q`：**170 passed，14 warnings，19.29s**；警告仍为 TestClient/httpx 弃用提示。此前前端构建已通过，本次没有修改代码，无需重复构建。
- `infra/launch.ps1 -NoBrowser` 成功；健康检查返回 `status=ok/database=ok`，应用已初始化，前端 HTTP 200；`infra/stop.ps1` 退出码 0，报告全部停止。
- 停止验证后重新启动成功，供用户在本机登录和扫码；本次服务进程环境设置 `FISHINGNET_SCHEDULER=0`、`FISHINGNET_BILIBILI_MIN_INTERVAL=2`，没有改写 `.env`。此设置仅作用于本次启动，普通双击重启会恢复原有环境行为。
- 本轮烟测授权继续有效；仍待用户亲自扫码及提供自有 BV 完整链接、关键词。**本轮真实 B站烟测尚未执行**，没有上游错误码，未进入 M3。历史 07:06 的端口阻塞已经解除。
- 业务代码、迁移 0001–0005 和 API 未改，OpenAPI 无需重新生成；没有下载新依赖或修改用户账号与数据。


## 最新轮次：已授权烟测，尚被本地启动阻塞（2026-10-20 07:06，UTC+8）

本节为当前状态；下方 M2 收尾的“未获授权”和 170 passed 是历史轮次记录。用户本轮已明确授权亲自扫码、1 条自有链接元数据/字幕、1 个关键词来源手动 1 轮（≤2 页）；仍待自有 BV 链接、关键词与扫码操作。**本轮真实请求为零，未进入 M3。**

| 检查 | 本轮实际结果 |
|---|---|
| `.venv\Scripts\python.exe infra\init-local.py` | 失败，退出码 1；07:04:57 日志：`could not bind IPv4 address "127.0.0.1": Permission denied`，未接受数据库连接 |
| 端口诊断 | 独立 socket 绑定 55432 得到 Windows `10013`；8000/5173 可绑定。07:05:57 查明 `verge-mihomo.exe` PID 25048 使用 55432 的 Bound/Established 出站连接；无监听者不代表端口可用。未终止代理、未改代理配置 |
| 无数据库测试 | `python -m pytest -q tests/test_policy.py tests/test_collectors.py tests/test_discovery_rules.py`：**104 passed，14 warnings**；均不触网，警告为既有 TestClient/httpx 弃用提示 |
| 前端构建 | `npm.cmd --prefix apps/web run build`：通过（TypeScript + Vite） |
| 全量 pytest / 迁移 current / 正式库计数 | **尚未执行**：先恢复数据库；不以本轮单元测试或历史 170 passed 冒充全量复现 |
| `launch.ps1 -NoBrowser` / 停止入口 | **尚未执行**：数据库启动前置失败，待端口释放后继续 |
| API / 编码 / Git | OpenAPI 与当前代码完全一致，50 路径 / 0.5.0；4 个 PowerShell 脚本 UTF-8 BOM 完好；master 无提交，未跟踪成果保留 |
| 本轮真实扫码、账号检查、元数据、字幕、关键词发现 | **未执行**；已获授权，但本机服务未启动且自有链接/关键词未提供。没有 B站响应，故无上游错误码，Windows 10013 不是 B站错误码 |

未修改业务代码、迁移 0001–0005、`.env`、用户账号与数据。待用户释放占用连接后按原顺序继续；真实记录只保存日期、阶段、结果与脱敏错误码，不保存 Cookie、二维码 URL/key 或字幕原文到验收日志。发现配置保持 `enabled=false`，联网授权单独设置；只手动一轮、≤2 页；搜索 -403 记录为拒绝并按现有政策评估，不实现 WBI/浏览器伪装。

当前交付为 **M0/M3 过渡开发预览**：M1 核心（M1.1 加固、主题管理、范围/时间筛选）、M2 Phase 1/2/收尾（B站 单链接导入与采集、账号能力检查、元数据/字幕状态、采集器契约与测试替身、网页扫码登录、关键词来源与调度、增量 watermark、分P定位/批量、凭证与风控恢复、日报联动、明细保留）、M3 Phase 1（模型供应商、模型配置、任务路由与回退、预算预留/结算、调用与费用账本、模型管理界面）与 M3 Phase 2（路由化主张抽取接入 `process_source`、服务端原文锚点/偏移/哈希校验、无锚点主张保持待复核）已完成。批量导入、语言偏好、密码恢复、完整 E2E 已按用户决定推迟。**不是完整 MVP**。本记录只列出实际执行过的内容。

真实网络方面：B站 元数据/字幕与 WBI 关键词搜索已于 2026-10-20 完成短时烟测（见本节上方记录）；扫描登录仍只验证到生成 + 轮询，**没有完成真实扫码**。M3 Phase 1/2 的自动测试**没有任何真实模型请求**，全部由 `httpx.MockTransport` 与隔离 schema 验证；用户另在本机执行了 **1 次真实模型连接测试**（固定合成提示，成功），但**没有用真实模型处理材料**。关键词发现的上游代码已通过真实 WBI 烟测，但定时运行、分P批量、凭证恢复的真实请求仍未验证。

## 真实网络执行记录（2026-10-20）

| 时间 | 操作 | 请求 | 结果 | 数据写入 |
|---|---|---|---|---|
| 02:13 前（用户点击“生成登录二维码”） | 生成二维码 | `GET https://api.bilibili.com/x/passport-login/web/qrcode/generate` | **HTTP 404**，界面提示“B站返回 HTTP 404，已停止本次采集” | 无；未保存任何凭证 |
| 02:13 | 修正域名后复测 | `GET https://passport.bilibili.com/x/passport-login/web/qrcode/generate` 与 `GET .../qrcode/poll` | 生成成功；轮询返回 `86101`（`waiting_scan`）；共 2 次请求 | 无；未扫码，未保存凭证 |
| M2 Phase 2 轮次 | 关键词来源、调度、分P、恢复 | **无真实请求** | 未执行；仅在自动测试中使用确定性测试替身 | 无 |
| M2 收尾本轮（2026-10-20） | 分P批量、凭证/风控恢复、日报联动、明细保留 | **无真实请求** | 未执行：本轮用户未明确授权本机真实 B站 烟测；实现只用测试替身验证 | 无 |
| 12:30 前后（用户本机 SESSDATA） | `BV1MLJe6fEmH` 元数据、账号/导航、字幕清单/正文 | 4 次受限请求（元数据/nav/清单/正文） | **成功**：元数据已保存，字幕 6 片段（ai-zh），`subtitle_status=available` | 来源/版本/任务与证据记录；不代表事实确认 |
| 16:10 | WBI 关键词搜索 `"AI"` 两页 | 每页 20 条，`has_more=True` | **成功**：WBI key + buvid + 签名搜索全链路 | 无持久来源（烟测脚本只读） |
| M3 Phase 1 / Phase 2 自动测试（2026-10-20） | 模型供应商测试、测试调用、路由化抽取 | **无真实请求** | 自动测试全部由 `httpx.MockTransport` 验证 | 无 |
| M3 收尾自动测试（2026-10-20） | 表达复核、实体消歧、实体候选接受/拒绝 | **无真实请求** | 自动测试全部由 `httpx.MockTransport` 验证；没有新增 B站 请求 | 无 |
| 20:35（用户在本机点击“测试连接”） | 固定合成探测提示 | `POST https://token-plan-cn.xiaomimimo.com/v1/chat/completions` | **200 成功**；37 输入 / 26 输出 token；估算并结算 0.0000445 USD；延迟 1625 ms；未发送导入材料 | 调用账本 1 行、预算流水 1 行；无 claims/updates |

诊断：首次失败是**本项目实现错误**——扫码接口应使用 `passport.bilibili.com`，代码误用了 `api.bilibili.com`；不是风控、封禁或账号问题。已修复，并新增回归测试 `test_qr_endpoints_use_passport_host` 防止再次写错域名。

仍未验证：真实扫码确认与真实 `SESSDATA` 扫码落库、分P批量、凭证过期/风控恢复、定时调度、真实模型抽取/复核/实体消歧。

## 本轮实际执行（M2 收尾，2026-10-20）

- 基线复现（改动前）：`infra/init-local.py` 复用本地 PostgreSQL；`python -m pytest -q` **165 passed**；`npm.cmd --prefix apps/web run build` 通过；已运行实例 `/health/ready` 返回 `{"status":"ok","database":"ok"}`、5173 返回 200；`infra/stop.ps1` 退出码 0 并释放 8000/5173/55432。随后按要求重新启动数据库再跑测试（先停服务会让测试连接失败）。
- 迁移：新增 `0005_recovery_retention`（`platform_connections.status` 增加 `risk_control`、`cooldown_until`、`last_success_at`、`discovery_runs` 完成时间索引）。`alembic current` = `0005 (head)`；未改 0001–0004、未删库、未覆盖 `.env`；正式库计数不变：**1 用户 / 6 来源 / 6 任务 / 0 关键词来源 / 0 发现运行**。
- 后端测试：`python -m pytest -q` → **170 passed**（真实 PostgreSQL 17 隔离 schema，新增 5 项集成测试）。全部使用测试替身，**没有任何真实网络请求**。
- 前端：`npm.cmd --prefix apps/web run build`（`tsc -b && vite build`）通过；来源抽屉新增分P勾选/批量确认，平台账号页新增恢复按钮与冷却显示，日报页新增发现摘要。无新增依赖。
- API：新增 `POST /sources/{id}/parts/import`、`POST /platforms/{platform}/recover` 两条路径；OpenAPI 重新导出为 **50 路径 / 0.5.0**；`/capabilities` 与 `/platforms` 增加批量、恢复、保留期与日报联动声明，`live_*_verified` 全部保持 false。
- 启动/停止（改动后复测）：`infra/launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”，`/health/ready` 正常、5173 返回 200、worker 错误日志为空；`infra/stop.ps1` 退出码 0，8000/5173/55432 全部释放。

## 本轮验证到的行为（M2 收尾，测试替身）

| 范围 | 实际结果 | 不能据此声称 |
|---|---|---|
| 分P批量 | `POST /sources/{id}/parts/import` 缺少 `confirmed` 返回 422 `PART_BATCH_CONFIRM_REQUIRED`；重复序号去重；>20 个返回 422 `PART_BATCH_TOO_LARGE`；任一不存在分P整批 404 `SOURCE_PART_NOT_FOUND` 且不新增来源/任务；成功时每个分P独立建来源与采集任务，并返回预计上游请求数 | 真实分P批量采集可用；未选分P会被自动抓取 |
| 凭证过期恢复 | 采集遇到 `CREDENTIAL_EXPIRED` 时连接状态置为 `expired` 并记录错误码；恢复端点返回 409 `CREDENTIAL_EXPIRED`，不自动重试；替换凭证后可恢复 1 个被阻塞任务，任务 attempts 归零、checkpoint 清空；成功采集后写 `last_success_at` | 平台授权已恢复；凭证永久有效 |
| 风控恢复 | `RISK_CONTROL` 后连接状态 `risk_control`、`cooldown_until` 约 15 分钟；冷却内恢复返回 429 `RISK_COOLDOWN` 与 `retry_after_seconds`；冷却结束后可重新排队 | 平台已解除风控；可换账号/换 IP 绕过 |
| 日报联动 | `/digests/today` 返回 `discovery.totals/runs/decisions/collected_sources`；Markdown 含“关键词发现（本日）”与“关键词来源采集（本日）”，列出失败错误码与入队候选；下载结果一致 | 入队即采集完成；采集结果等于事实确认 |
| 明细保留 | 新明细不被清理；把运行 `finished_at` 回拨 40 天后，`cleanup_discovery_items(days=30)` 删除 1 条候选明细，`discovery_runs` 与来源 watermark 保留 | 运行账本会被级联删除 |



## 上一轮实际执行（M2 Phase 2）

- 基线复现（先于改动）：`init-local.py` 复用本地 PostgreSQL；`python -m pytest -q` **106 passed**；`npm --prefix apps/web run build` 通过；`infra/launch.ps1 -NoBrowser` 输出“已就绪”，`/health/ready` 返回 `{"status":"ok","database":"ok"}`，5173 返回 200；`infra/stop.ps1` 退出码 0 且 8000/5173/55432 全部释放。同时确认 `/capabilities` 中 `live_collection_verified=false`、`keyword_discovery` 此前为 false，没有把测试替身当成真实采集。
- 迁移：新增 `0004_discovery`（`discovery_sources`、`discovery_runs`、`discovery_run_items`、`jobs.checkpoint`、`sources.discovery_source_id`）。`alembic current` 为 `0004 (head)`；**未改 0001–0003，未删除数据库，未覆盖 `.env`**。迁移前后正式库计数不变：1 用户、6 来源、6 任务。
- 后端测试：`.venv\Scripts\python.exe -m pytest -q` → **165 passed**（真实 PostgreSQL 17 隔离 schema）。构成：M1 既有 48 项 + M2 Phase 1 的 36 项契约/凭证单元测试、14 项采集流程集成测试、8 项扫码集成测试 + **M2 Phase 2 的 40 项关键词规则/适配器单元测试与 19 项发现流程集成测试**。全部使用测试替身，没有任何真实网络请求。
- 前端：`npm --prefix apps/web run build`（`tsc -b && vite build`）通过；“采集与调度”页新增可操作的关键词来源面板（新建、启用/停用、授权/撤销联网、立即运行、运行记录、删除），无新增前端依赖。
- API：新增 6 条路径；OpenAPI 重新导出为 **48 路径 / 0.4.0**。`/capabilities` 增加 `live_keyword_discovery_verified=false`、`keyword_discovery` 说明、`scheduling`、`incremental_watermark`、`page_positioning` 与 `deferred` 列表。
- 启动/停止（改动后复测）：`infra/launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”，API/worker/web 均启动，worker 错误日志为空；`infra/stop.ps1` 退出码 0，三个端口全部释放。

## 本轮验证到的行为（M2 Phase 2，测试替身）

| 范围 | 实际结果 | 不能据此声称 |
|---|---|---|
| 关键词来源配置 | 创建/读取/`PATCH`（If-Match 428/409）/逻辑删除；关键词 1–5、排除词 ≤20、上限 1–20、页数 1–3、请求 1–4、间隔 60–10080 的越界均返回 422 `DISCOVERY_CONFIG_INVALID` 并带字段名；同名 409 | 配置在真实平台上的效果 |
| 联网授权 | `enabled` 与 `network_authorized` 独立；未授权时手动运行 422 `NETWORK_NOT_AUTHORIZED`，调度器不选取，且测试断言 collector 调用为空、任务表为 0 | 平台授权或 OAuth |
| 有界发现 | 每次只读一页搜索结果，页数/请求预算按配置截断；`-403` → `DISCOVERY_DENIED`（不重试、不签名绕过），`-400` → `INVALID_QUERY`，412 → `RISK_CONTROL` 且只 1 次请求 | 真实搜索可用 |
| 本地过滤 | Unicode NFKC+casefold 的 any/all 关键词匹配；排除词命中标题或作者即记录 `excluded_term`；每轮上限之外的候选记录 `over_limit`；未命中记录 `keyword_miss`；每个候选都写入 `discovery_run_items` 与原因 | 上游排序或平台去重行为 |
| 增量 watermark | 同一候选第二次运行记为 `duplicate`；存在但超过 `rescan_after_hours` 的记录为 `refreshed`；`pending` 来源视为已排队；`watermark.seen` 有 500 条上限并保留 `newest_published_at` | 平台发布时间永久稳定 |
| 调度 | 只选取 `enabled AND network_authorized` 且 `next_run_at` 到期的来源；一次 tick 最多 3 个；同一时刻重复调用不会重复入队；失败退避 15/30/60… 分钟且不超过配置间隔 | 多进程或多节点调度 |
| 分P定位 | 元数据保存 ≤100 个分P；`POST /sources/{id}/parts/{p}/import` 只排队指定分P并可选 `created`；不存在的分P 返回 404 `SOURCE_PART_NOT_FOUND` | 分P批量采集 |
| 语言 | 字幕轨按配置语言优先；`require` 且无匹配时返回 `LANGUAGE_MISMATCH`，来源标记 `missing` 且不生成版本、不额外请求字幕正文 | 平台字幕语言集合完整 |
| 跨阶段恢复 | 采集任务带 `checkpoint.remote` 时不再请求元数据（断言 collector 只被调用 `fetch_subtitles`）；发现任务带第 1 页 checkpoint 时只请求第 2 页；手动 retry 清空 checkpoint | 断电级恢复与多节点一致性 |
| fence 与清理 | 发现网络阶段被抢 fence：无运行项、无来源、运行仍 queued、任务保持新 fence 的 running；被取消或 `ATTEMPTS_EXHAUSTED` 的发现任务在 `claim()` 清理时关闭运行记录并给来源退避 | 运行中取消总能立即生效 |

## 既有 M1 核心与 M2 Phase 1 验证（保留）

- 错误契约：401/400/409/422/428 统一 `{error:{code,message,retryable,details},request_id}`，响应头含 `X-Request-Id`；校验错误不回显密码。
- If-Match：缺失 428、过期 409、格式错误 422；删除来源重复调用返回 `already_deleted`。
- 游标：updates/sources/nodes/jobs/verification-queue/discovery-runs 使用不透明 cursor；非法游标 400 且不回显 SQL；筛选跨页保持。
- 幂等键：缺失/过短 400；同键不同内容 409；重复导入与重复示例不新增来源/动态。
- 账号/会话：首次设置只成功一次；批量注销并轮换当前会话；退出后旧 cookie 401。
- 队列：4 线程并发只领取一次；过期租约重领 fence+1；旧 fence 不写入；取消/重试/尝试耗尽/异常失败均覆盖。
- 主题/实体：`POST /nodes`、`PATCH /nodes/{id}`、`POST/DELETE /topic-links`；环返回 409 `TOPIC_CYCLE`；节点详情含父/子/实体/更新。
- 阅读筛选：`topic_id`/`entity_id`/`since`/`until`/`update_type`，默认最近 7 天，主题筛选沿成员实体匹配。
- 证据/发布：Unicode 锚点、跨工作区关系、删除后所有 surface 撤下、重要未核实事实阻断测试继续通过。
- 采集（Phase 1）：链接解析与拒绝；导入不联网；元数据/字幕状态映射；缺字幕 `missing`；需要登录 `blocked_credentials` 并可原地恢复；风控失败不产生内容；fence 丢失零写入；凭证 Fernet 加密、不回显；扫码状态机 `waiting_scan → scanned → connected`，`qrcode_key` 不返回浏览器、不落库。
- 启动/停止：重复启动复用、端口冲突、伪 PID、失败不就绪、双击停止均实测（M1.1 轮次）。
- 隔离 QA 无头 Edge：登录后首页、证据抽屉、导入弹窗、个人设置、会话页、任务页、主题创建/管理/关联/主题动态筛选（M1.1/M1.2 轮次，合成数据）。

## 环境说明

- PostgreSQL 不能在管理员令牌下运行；`infra/init-local.py` 在提升 shell 中通过 `runas /trustlevel:0x20000` 启动 `pg_ctl`。不要用管理员身份直接运行 `postgres.exe`，也不要删除 `pgdata`。
- 迁移为 `0001`–`0007`（head=0007）。`docs/openapi.json` 从当前应用重新导出（63 条路径，版本 0.8.0）；M3 收尾新增 `POST /relations/{relation_id}/review`。
- 模型密钥复用 `MASTER_KEY` 但使用独立派生标签与 schema；用户已在本机配置 1 个真实供应商（MIMO，`openai_compatible`）与 1 个模型，密钥只在应用内加密保存，验收记录只保留掩码末 4 位与调用指标。前端未新增依赖。
- 测试库连接设置 5 秒超时；PostgreSQL 未启动时测试快速失败（曾因先执行停止入口而出现连接错误，重启数据库后恢复，未删除数据）。本轮一次把 pytest 放到后台作业时，PostgreSQL 服务进程收到控制台中断并快速关闭；重启数据库、改为前台执行后 170 项全部通过，未删除 `pgdata`。
- 无新增 Python/前端依赖；二维码仍由本机 `qrcode.react` 渲染。

## 当前明确未交付

- **真实网络烟测**：已通过 2026-10-20 的 `BV1MLJe6fEmH` 元数据/字幕（6 片段）、WBI 关键词搜索（`"AI"` 两页各 20 条）与 B站 平台连接检查；**真实扫码确认仍未完成**，分P批量、凭证过期/风控恢复、定时调度运行仍未做真实请求。M3 已完成 1 次用户真实连接测试（固定合成提示），但**没有真实模型抽取、语义复核或实体消歧**。
- **M1 推迟项（用户决定）**：批量 20 文件导入无限期推后；部署端密码恢复暂时完全推迟（发布前仍需补）；语言偏好、完整 E2E、`sort=changed` 等继续推迟。完整 M1 签收仍缺这些项。
- **M2 剩余**：UP 主订阅/合集/番剧展开、本地转写、多进程/多节点调度；单库并发预算已在 M3 Phase 1 实现，跨进程/多节点配额仍未覆盖。
- **M3 剩余**：真实模型抽取/语义复核/实体消歧烟测（连接测试成功，但尚未用真实模型处理材料）；`claim_comparison` 与事实核查模型路由；跨进程并发配额与多节点预算一致性；Anthropic/Gemini 原生协议；供应商账单级“已确认费用”；模型面板浏览器点击实测。供应商/模型/路由/回退/预算预留与结算/调用账本/锚点校验/表达语义复核/实体候选人工确认已实现，连接测试已通过，但**不能写成真实模型抽取或语义复核已通过**。
- **M4**：已完成 Phase 1（库内事实材料、E0–E3 规则分档、版本化核查记录、人工接受/拒绝、重要事实 E3 硬门槛、标题/正文读取门槛与发布预览）。**未完成**：事实核查模型路由与 `claim_comparison`、外部检索连接器、来源画像评估与记录、纠错传播与统一重算、AC32–45 专项数据集与整体签收、批量复核。`GET /sources/{id}/reliability` 只返回 `unknown`；`fact_check_status` 目前只能由“人工接受核查记录”改变，规则任务本身不会改它。
- **M5**：性能基准、完整安全矩阵、备份恢复演练、容器部署验收、许可证逐项复核。Docker Compose 仍未实测。
- 界面：本轮只做生产构建与接口回归；未重新生成截图，也未做 375px/1280px 全量自动 E2E、键盘/焦点回归或新面板的浏览器点击实测（主张详情的事实材料表单、接受/拒绝按钮尚未做浏览器点击实测）。

## 可复现命令

```powershell
.venv\Scripts\python.exe infra\init-local.py
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m pytest -q
npm --prefix apps/web run build
powershell.exe -NoProfile -ExecutionPolicy Bypass -File infra\launch.ps1 -NoBrowser
powershell.exe -NoProfile -ExecutionPolicy Bypass -File infra\stop.ps1
```

## 证据

- `docs/bilibili-api-policy.md`：B站 接口与政策核对记录（URL、日期、限制、关键词发现的边界与未执行声明）。
- `docs/adr/0003-discovery-pipeline.md`：关键词来源、调度、watermark、分P定位/批量、恢复、日报与明细保留的实现取舍。
- `docs/adr/0004-model-gateway-budget.md`：模型协议、路由/回退、预算预留与结算、费用状态与默认关闭的实现取舍。
- `docs/adr/0005-fact-verification.md`：事实证明材料、E0–E3 分档、追加型核查记录、人工复核与发布门槛的实现取舍。
- `tests/test_m2_wrapup.py`：M2 收尾 5 项集成测试（测试替身，无真实网络）。
- `tests/test_ai_gateway.py`、`tests/test_m3_ai_flow.py`：M3 Phase 1 的 53 项单元/集成测试（`httpx.MockTransport`，无真实模型请求）。
- `tests/test_m3_extraction.py`：M3 Phase 2 的 21 项单元/集成测试（提示边界、锚点偏移/哈希、拒绝原因、候选不发布、零预算阻断与重试、fence 丢失零写入；无真实模型请求）。
- `tests/test_m3_wrapup.py`：M3 收尾的 20 项单元/集成测试（表达复核提示/校验/失败/预算与实体候选提议/接受/拒绝/编造节点；无真实模型请求）。
- `tests/test_m4_verification.py`：M4 Phase 1 的 24 项单元/集成测试（事实材料锚点/载荷/角色说明/上限、E0–E3 与状态矩阵、accept gate、过期输入、接受/拒绝与记录只追加、发布门槛隐藏与放行、队列筛选、能力声明；无网络、无模型调用）。
- `migrations/versions/0005_recovery_retention.sql`、`0006_ai_gateway.sql`、`0007_m3_wrapup.sql`、`0008_verification_loop.sql`：恢复/保留、模型/预算支撑表、表达复核与事实证明材料/核查记录约束；head=0008。
- `docs/m1.1-ui-preview.png`、`docs/m1.2-topics-preview.png`：隔离 QA schema 的合成数据截图，不是正式账号、不是真实平台页面、不是事实核查证据。
- 本轮没有新增截图；真实模型连接测试沿用用户在本机执行的 1 次记录（只保留端点域名、状态、token、费用与延迟，不保存密钥、请求/响应正文或导入材料）。299 项测试通过不等于 AC01–AC45 全部通过，也不等于真实模型抽取/复核/实体消歧、真实事实核查、真实采集或完整扫码登录通过。
