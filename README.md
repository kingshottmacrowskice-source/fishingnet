# FishingNet · AI 多来源信息聚合平台

## 2026-10-21 01:10（UTC+8）· M4 Phase 2：事实核查建议路由、版本过期封顶与 AC32–45 阻断数据集

**本轮完成 M4 Phase 2 的第一部分。没有新增迁移，没有真实模型调用，没有外部检索，也没有真实事实核查烟测。** 事实核查仍由确定性规则分档；新增的 `fact_check` 模型路由只读取**库内材料元数据**给出建议，不改变档位、状态或发布门槛，人工接受仍是唯一提升表达的路径。

- **`fact_check` 建议路由**（`app/ai/factcheck.py`，新增）：有界提示只含主张与材料元数据（作用、命题范围、直接性、上游组、独立性、完整性、条件、限制），不发送原文；模型只能返回 `supports/refutes/conflicts/insufficient/unclear`、理由、限制和已发送材料引用。服务端拒绝编造材料引用、非法结论，以及任何“读过外部文档/调用过工具”的自述（`MODEL_CLAIMED_EXTERNAL_READS`）；置信度不存储、不参与分档。建议写入核查记录的 `model_runs`，`checker_type` 仍为 `rule`，记录保持 `pending`。调用仍走“短事务预留 → 无事务 HTTP → 结算/释放”，预算耗尽、策略阻断、上游失败只记为“建议不可用”，**不降低门槛**。
- **过期来源版本封顶**（AC34）：来源出现新版本后，旧版本材料仍可用于解释历史，但 `material_tier` 封顶为 E1（`SOURCE_VERSION_SUPERSEDED`），不能参与 E2/E3；保留期到期降为 E0。快照哈希纳入 `superseded`/`retention_expired`，新版本出现后旧的待复核记录在人工接受时返回 409，不能把旧结论带到新版本。
- **断章取义拦截**（AC33）：引文在同一句的逗号/分号/冒号处截断、且后半句含明确限定或转折（但、仅、不支持、尚未、正式版、beta 等）时，主张保存为 `candidate` 并记录 `CONTEXT_TRUNCATED_LIMITATION`，不写证据关系、不发布；完整句子和并列补充句不误伤。
- **发布门槛收紧**（AC45）：`scoped_fact` 只允许由**人工接受**的当前记录产生，历史 `checker_type='rule'` 的 accepted 记录不能继承确认资格。
- **AC32–45 阻断数据集**：新增 `tests/fixtures/ac32-45.json`（48 个案例，虚假宣传/断章取义/过期版本/循环转载/伪造测评/真假批评各 8 例，另 12 个对照）与 `tests/test_m4_ac_dataset.py`；每个案例声明档位、状态、接受门槛、七个读取面允许的表达与必须阻断的表述。详见 `docs/AC32-45-dataset.md`。
- **界面**：主张详情与核查工作台显示模型建议结论、是否与规则一致、材料是否截断，并明确“建议不改变分档、状态或发布门槛”；模型管理说明新增路由用途。

**验证**：`pytest -q` **385 passed / 93 warnings（45.72s）**（隔离 PostgreSQL schema）；新增 `tests/test_m4_ac_dataset.py` 86 项（60 个数据集案例 + 8 个断章取义表达案例 + 18 项单元/集成），网络全部为 `httpx.MockTransport` 或完全离线；`npm.cmd --prefix apps/web run build` 通过；`alembic current` 仍为 **0008 (head)**；`docs/openapi.json` 重新导出 **70 路径 / 0.10.0**；`launch.ps1 -NoBrowser` 就绪、`/health/ready` ok/ok、5173 返回 200，`stop.ps1` 退出码 0 且三端口释放（详见 `docs/ACCEPTANCE.md`）。正式库保持 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水 / 6 条既有核查记录**，`fact_*` 关系 0 行。

**明确未做**：真实模型事实核查、真实外部检索、`claim_comparison` 路由、来源画像评估与记录、纠错表达式生成、批量复核、真实扫码确认、分P批量、凭证恢复与定时调度。AC32–45 数据集通过**不等于** AC01–AC45 全部签收，也不等于 M4 完成；`insufficient` 不是“虚假”，`corroborated` 也不是外部已查证。供应商/模型默认停用、任务路由默认未配置、预算默认 0、数据策略默认 `local_only` 的项目默认值未改。

## 2026-10-20 23:45（UTC+8）· M4 Phase 1：库内事实材料、版本化核查记录与人工复核

**本轮完成 M4 Phase 1。** 事实核查现在是一条独立、可解释、只追加的闭环：用户把库内来源版本中的逐字引文登记为“支持 / 反驳 / 背景”事实材料（事实关系，`proposed`），排队一次 `fact_check` 任务，由**确定性规则**做 `kb_only` 库内检索与分档评估（E0–E3），生成待人工复核的**版本化核查记录**；只有人工接受并满足硬门槛后，主张的 `fact_check_status` 才改变，服务端发布门槛才可能放行 `scoped_fact`。**没有外部网页检索、没有模型调用、没有真实事实核查烟测。**

- `app/domain/fact_evidence.py`（新增）：事实材料载荷按 `fact_evidence.v1` 校验；引文必须逐字出现在所选片段且唯一，`char_start/char_end/quote_hash` 全部由服务端按 Unicode code point 计算；条件未知不得当作匹配；同一来源同时作表达溯源与事实证据时必须填写用途说明。
- `app/domain/verification.py`（新增）：E0（不可用）/E1（有限间接）/E2（实质支持：直接 + 五项条件匹配 + 完整性已核验）/E3（两个不同上游组、确认独立的适用直接证据）分档；`corroborated / conflicted / refuted / insufficient` 结论；`accept_gate` 拒绝“重要事实只有 E2”“corroborated 只有 E1”“refuted 只有 E1”等越权接受；快照哈希用于发现证据变化。
- `app/jobs/worker.py`：新增 `fact_check` 任务（`kind='fact_check'`，初始阶段 `retrieve`）。只读库内材料与同实体候选主张；不调用模型、不联网；在同一 fence 事务里追加 `review_status='pending'` 的核查记录并结束任务；主张状态保持不变。
- `app/main.py` 新增 7 条路径：`POST /claims/{id}/evidence-links`、`POST|GET /claims/{id}/fact-checks`、`GET /verification-records/{id}`、`POST /verification-records/{id}/review`、`GET /verification-capabilities`、`GET /sources/{id}/reliability`（真实返回 `unknown`）、`POST /updates/{id}/publication-preview`。`GET /claims/{id}` 返回事实材料分档、当前记录、待复核记录；`GET /verification-queue` 增加最新记录状态、待复核数量与 `status`/`review` 筛选；读取端的发布检查同时约束标题与正文，并允许“已存储的更弱表达”继续展示、拒绝升级。
- 迁移 **0008**：事实证据载荷/锚点触发器、`verification_records` 的 review/checker/trigger 约束与索引、`claims.fact_check_status` 索引。未改 0001–0007、未删库、未覆盖 `.env`。
- 前端：主张详情新增“库内事实核查”面板（事实材料、待复核记录、接受/拒绝理由、发起核查、添加待确认材料）；核查工作台每行可直接发起库内核查；模式横幅更新为 M4 Phase 1。

**验证**：`pytest -q` **299 passed / 93 warnings（43.84s）**；新增 `tests/test_m4_verification.py` 24 项（12 单元 + 12 集成，全部隔离 schema、无网络、无模型）；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0008 (head)**；`docs/openapi.json` 重新导出 **70 路径 / 0.9.0**；`launch.ps1 -NoBrowser` 就绪，`/health/ready` ok/ok、5173 返回 200，新端点未登录均 401，停止入口退出码 0 且三端口释放（详见 `docs/ACCEPTANCE.md`）。

**明确未做**：事实核查模型路由与 `claim_comparison` 接入、外部检索连接器、来源画像评估、纠错传播与专项数据集（AC32–45 尚未整体通过）、批量人工复核；真实模型抽取/语义复核/实体消歧仍未执行。自动测试与库内规则不能代替真实外部核查，也不能把 `insufficient` 写成“虚假”。

## 2026-10-20 21:25（UTC+8）· M3 收尾：表达语义复核与实体候选人工确认

本轮把 `evidence_review` 与 `entity_disambiguation` 两个任务路由接进 `process_source`。**仍然没有用真实模型处理任何材料**；自动测试的网络全部由 `httpx.MockTransport` 替身。新增迁移 **0007**（`claim_expression_reviews`、待复核关系索引），未改 0001–0006、未删库、未覆盖 `.env`。

- **表达语义复核（`evidence_review`）**：抽取与锚点校验后，若该路由显式启用为 `model`，发送有界 JSON（主张、服务端已校验的引文、限定上下文）；模型只能返回 `supports` / `partially_supports` / `unsupported` / `unclear` 与理由、限制。只有前两种可进入发布门槛；不支持、上下文不足、复核未完成或未被覆盖的主张保存为 `candidate`，不建核查记录、不发布。复核记录写入追加型 `claim_expression_reviews`，`fact_check_status` 永不由此改变，语义支持不等于事实确认。
- **实体消歧（`entity_disambiguation`）**：只发送主张与库内已有实体（名称、类型、有限别名），模型只能返回请求中出现过的 `node_id`；服务端拒绝编造节点、未知主张、重复或超限链接，**绝不自动创建或合并节点**。通过的候选写为 `relations(status='proposed', asserted_by='model')`，在主张详情人工接受后才影响实体视图；接受会为已发布动态补 `update→entity` 关系，拒绝保持不可见。实体关联不改变事实核查状态。
- **预算与失败顺序不变**：每个模型调用仍是“短事务预留最坏费用 → 无事务 HTTP → 结算/释放 + 调用账本”；复核或实体调用失败不会把未复核主张发布出去。抽取阶段零预算仍是 `blocked_budget` 且可重试；复核阶段预算不足会保留锚点主张为 `candidate`、任务 `needs_review`（不自动重试，需新任务或后续回填）。
- **API**：新增 `POST /api/v1/relations/{relation_id}/review`（人工接受/拒绝模型实体候选）；`GET /claims/{id}` 返回 `expression_reviews`、`expression_review`、`about_nodes`；`GET /verification-queue` 返回最新 `expression_verdict`。OpenAPI 为 **63 路径 / 0.8.0**。
- **界面**：核查工作台显示表达复核结论；任务页显示表达复核计数与实体候选数；主张详情显示复核理由/限制与实体候选，可接受或拒绝；模型管理说明新路由用途。
- **实测**：`pytest -q` **275 passed / 93 warnings（61.43s）**（新增 `tests/test_m3_wrapup.py` 20 项：10 单元 + 10 集成），`npm.cmd --prefix apps/web run build` 通过，`alembic current` = **0007 (head)**，OpenAPI **63 路径 / 0.8.0**。`launch.ps1 -NoBrowser` 就绪、`/api/v1/health/ready` 为 ok/ok、5173 返回 200、新端点和 `/capabilities` 未登录均为 401；`stop.ps1` 退出码 0、8000/5173/55432 全部释放。正式库保持 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**，新表为 0 行。

**未执行**：真实模型抽取、真实语义复核、真实实体消歧；真实扫码确认、分P批量、凭证恢复、定时调度。事实核查/主张比较接入、跨进程配额、多节点预算、Anthropic/Gemini 原生协议仍未完成。

## 2026-10-20 20:20（UTC+8）· M3 Phase 2：路由化主张抽取接入来源处理

把 M3 Phase 1 的模型网关接进 `process_source`，并给模型输出加上服务端原文锚点校验。代码与自动测试全部使用 `httpx.MockTransport` 替身；**没有用真实模型处理任何材料**。本轮末尾用户在本机自行配置 MIMO 2.6，执行了 **1 次真实连接测试**（固定合成提示，不发送导入材料），结果见下。

- **新增 `app/ai/extraction.py`**：有界抽取提示只包含路由输入上限内的片段（超出部分标记 `truncated`，其不可见尾部不可被引用）；输出必须符合受控词表，否则整条主张被拒绝而不是静默改写。
- **引用由服务端决定**：模型只能引用请求中出现过的 `segment_id`；`quote` 必须逐字（允许空白折行归一化）出现在该片段文本中，字符偏移与哈希一律由服务端按 Unicode code point 计算；模型自带的 `char_start/char_end` 只有与服务端结果完全一致才接受，否则记 `OFFSET_MISMATCH` 并拒绝该证据。
- **未通过锚点校验的主张不会发布**：保存为 `review_status='candidate'` 待复核，不写 `evidenced_by` 关系、不生成动态；通过锚点的才按既有发布门槛发布，`fact_check_status` 始终为 `insufficient`，抽取模型不能设置核查状态。
- **预算顺序不变**：短事务预留最坏费用 → 无事务 HTTP → 短事务结算；worker 用 `LeaseKeeper` 心跳续租，写入仍受 `fence AND state='running'` 守卫。
- **分派是显式的**：合成示例（`metadata.demo=true`）始终走本地 Mock；只有 `claim_extraction` 路由显式启用为 `model` 时才处理真实材料；未配置路由的真实材料仍停在 `needs_review`，不会伪装成已分析。`blocked_budget` 任务可直接重试。
- **可解释结果**：任务结果新增 `validated/candidates/rejected/anchors/invocation_id/cost`，并明确 `fact_confirmation=false`、`semantic_review=not_performed`；前端动态卡片标注“模型抽取 · 未事实确认”，任务页显示锚点通过/待复核/拒绝。
- **真实连接测试（用户在本机执行，2026-10-20 20:35）**：用户在应用内填写 MIMO 2.6 端点与密钥，点击“测试连接”。`openai_compatible` 端点 `https://token-plan-cn.xiaomimimo.com/v1` 返回成功：模型 `mimo-v2.6-pro`，输入 37 / 输出 26 token，延迟 1625 ms，估算并结算 0.0000445 USD（预留 0.000256 → 结算 0.0000445），调用账本与预算流水各 1 行；没有 claims/updates，没有发送导入材料。这只证明端点与密钥当时可用，不证明持续可用、抽取质量或引用校验在真实模型输出上的表现。
- **实测**：`pytest -q` **255 passed / 93 warnings（30.71s）**（新增 21 项提取单元/集成测试），`tsc -b && vite build` 通过，OpenAPI **62 路径 / 0.7.0**（无端点结构变化）。正式库为 1 用户 / 7 来源 / 10 任务；连接测试后新增 1 供应商 / 1 模型 / 1 调用 / 1 预算流水，任务路由仍为 0。

**仍未完成**：真实模型抽取烟测（连接测试成功，但尚未用真实模型处理材料）；语义复核（判断引用是否支持主张）与事实核查；实体消歧/主张比较路由尚未接入流水线（模型抽取的动态暂无实体归属）；跨进程并发上限/多节点预算；Anthropic/Gemini 原生协议；供应商账单级“已确认费用”。

## 2026-10-20 19:55（UTC+8）· M3 Phase 1：模型供应商、网关、预算与费用账本

在真实 B站 烟测通过后进入 M3。本轮新增**迁移 0006**（供应商连接、模型配置、任务路由、调用账本、预算预留账本；`workspace_settings.budget_currency` 与 `routing_revision`），实现统一模型网关与费用控制：

- **供应商**：OpenAI Chat Completions 原生、通用 OpenAI 兼容、本地兼容端点、Mock 四种协议。密钥使用部署 `MASTER_KEY` 派生的 Fernet 密钥加密，接口只返回 `****` + 末 4 位掩码，不回显、不写日志、不进入调用快照。Anthropic/Gemini 原生协议**未实现**。
- **模型与路由**：每个模型保存真实模型 ID、等级、上下文/输出上限、是否支持 JSON 输出、输入/输出单价与币种；8 类任务（初筛、主张抽取、实体消歧、主张比较、表达证据复核、事实核查、动态生成、日报润色）可分别配置规则/Mock/模型模式、主模型、最多 3 个备用模型、token 上限、超时与尝试次数。保存是整组原子操作，使用单独的 `routing_revision` 做 If-Match。
- **默认零预算**：供应商、模型、任务路由默认关闭，工作区日/月预算默认 0、数据策略默认仅本地。只有同时满足“路由启用 + 模型与供应商启用 + 数据策略允许云端 + 路由允许云端 + 保存时显式 `confirm_cloud` + 预算足够”才可能发起云端调用；Mock 提供器不联网、费用为 0。
- **预算与费用**：每次调用前按路线 token 上限预留最坏费用（短事务，`FOR UPDATE` 锁工作区行，并发预留不会穿透预算），调用后按供应商返回用量结算；供应商未返回用量时保留全部预留并标记 `unknown`，只会高估不会低估。预算窗口按 UTC 日/自然月，工作区单一币种、无隐式汇率。
- **调用账本**：记录任务、供应商/模型、状态、尝试日志、token、估算与结算费用、费用状态、配置快照（不含密钥与材料正文）；被预算/策略阻断的调用也会记录为 `blocked`，不会伪装成成功。
- **实测**：`pytest -q` **234 passed / 93 warnings（26.02s）**，`tsc -b && vite build` 通过，迁移 `0006 (head)`，OpenAPI **62 路径 / 0.6.0**。新增 53 项 M3 测试（35 单元 + 18 集成），网络全部由 `httpx.MockTransport` 替身；**没有执行任何真实模型调用**，`live_model_protocol_verified=false`。

**Phase 1 时仍未完成**：真实模型协议烟测与把抽取结果接进 `process_source`。这两项在 M3 Phase 2 已补齐（抽取接入 + 1 次真实连接测试），但真实抽取、跨进程并发上限/多节点预算、供应商账单级“已确认费用”、浏览器端模型面板点击实测、Anthropic/Gemini 原生协议仍未完成。

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


实施基线：`AI多来源信息聚合平台_开发需求文档.md` v1.2。

**当前烟测进度（2026-10-20，UTC+8）：** B站 元数据/字幕（`BV1MLJe6fEmH`，6 片段）、WBI 签名关键词搜索（`"AI"` 两页各 20 条）与平台连接检查已通过；**真实扫码确认、分P批量、凭证过期/风控恢复、定时调度运行仍未验证**。M3 Phase 1/2 与 M3 收尾的表达语义复核、实体候选人工确认已实现；自动测试使用 `httpx.MockTransport` 替身，**没有用真实模型处理材料**。用户另在本机完成 **1 次真实模型连接测试**（MIMO 2.6，固定合成提示，成功，37/26 token，0.0000445 USD），但真实抽取、语义复核与实体消歧仍未执行，真实 B站 材料也未经真实模型处理。详见 [验收记录](docs/ACCEPTANCE.md)。

继续开发或新开 CODEX 对话时，请先阅读 [CODEX交接文档](CODEX交接文档.md)，其中包含当前实现边界、下一工作包与可直接复制的交接提示。

本项目从零开发，按 M0 → M5 交付。当前完成 **M1 核心范围**（M1.1 闭环加固 + 主题管理、主题/实体范围筛选、时间范围与主题页动态）、**M2 Phase 1 + Phase 2 + 收尾**（B站 单链接导入、账号能力检查、元数据/字幕状态、采集器契约与测试替身、网页扫码登录；关键词来源配置、排除词/语言/每轮上限、限速与退避、数据库调度、增量 watermark、分P定位与跨阶段持久恢复；分P批量需确认、凭证过期/风控恢复、发现结果与日报联动、发现明细保留策略），以及 **M3 Phase 1**（模型供应商、模型配置、任务路由与回退、预算预留/结算、调用与费用账本、模型管理界面）、**M3 Phase 2**（路由化主张抽取接入 `process_source`、服务端原文锚点/偏移/哈希校验、无锚点主张保持待复核）与 **M3 收尾**（表达语义复核、实体候选人工确认）。真实 B站 元数据/字幕与 WBI 关键词搜索已在 2026-10-20 完成短时烟测；**真实模型连接测试已完成 1 次（MIMO 2.6），但真实模型抽取/复核/实体消歧没有执行**，Mock 与测试替身只用于合成数据。**完整 M1 签收与完整 MVP 均未完成**。按用户决定，批量导入无限期推后、部署端密码恢复暂时完全推迟、语言偏好和完整自动 E2E 推迟。事实确认与生产部署均未验收。

## 本机启动

**直接双击根目录的 `启动FishingNet.cmd`**：自动启动数据库、后端、任务处理和前端，等待 API、数据库、worker 与页面全部就绪后才打开默认浏览器。启动成功后窗口自动关闭；失败会保留窗口显示具体阶段、端口冲突或日志位置。重复打开会校验进程归属并复用已运行的服务，不会重复启动；PID 被其他程序复用时不会误停。

**停止请双击 `停止FishingNet.cmd`**（或运行 `infra/stop.ps1`）：只结束命令行、项目路径和启动时间都匹配本项目的进程树，再停止项目内 PostgreSQL，并检查 8000/5173/55432 是否已释放。它不会停止端口上的其他程序，也不会删除 `.runtime/pgdata`。

PostgreSQL 不允许以管理员权限运行。`infra/init-local.py` 在检测到当前 shell 已提升时会通过 Windows“基本用户”信任级别启动 `pg_ctl`；这是本机双击启动能重复工作的前提，不要把该进程改成管理员启动。

此工作目录已安装前后端依赖和项目内 PostgreSQL，也可执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra/launch.ps1 -NoBrowser
```

打开 <http://127.0.0.1:5173>。已有正式账号时直接登录；**不要为“重置”而删除数据库、`.runtime/pgdata` 或覆盖 `.env`**。仅空实例需要首次设置，口令在本地 `.env` 的 `SETUP_TOKEN` 中，不提供默认管理员密码。创建账号需至少12位密码。

创建账号后点击“载入合成示例”，在“任务与费用”查看处理状态，再回到“信息动态”查看引用。样例共6份来源：三条观点形成2条动态（同观点增加引用），三条重要功能/测试主张因未核实而暂缓发布。所有样例明确标注虚构内容。

任意文本/Markdown/SRT/VTT 可导入并保留原始片段；当前 Mock 不分析真实材料，未配置 `claim_extraction` 路由时任务会显示“待复核”。默认零预算、供应商/模型默认停用、任务路由未配置；用户已在本机配置 1 个供应商与 1 个模型并完成 1 次固定合成提示的连接测试，但**没有用真实模型处理任何材料**。

停止服务：双击 `停止FishingNet.cmd`，或执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra/stop.ps1
```

运行日志仅保存在 `.runtime/`，无访问日志正文。不要提交 `.env`、数据库或运行目录。

## 在新环境安装

需要 Python 3.12+、Node.js 22.12+/24、PostgreSQL 17。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
npm --prefix apps/web ci
```

Windows 本地脚本预期 PostgreSQL 二进制位于 `.runtime/pgsql/bin`；使用 [EDB 官方二进制下载页](https://www.enterprisedb.com/download-postgresql-binaries) 获取 Windows x64 zip，解压到 `.runtime`。`infra/init-local.py` 创建本地数据库，生成随机部署密钥，仅监听127.0.0.1:55432，不注册系统服务。

已有 PostgreSQL 时复制 `.env.example` 为 `.env`，设置数据库连接和随机初始化口令，再运行 Alembic 迁移。不要覆盖现有 `.env`。

容器方案：配置 `.env` 中 `POSTGRES_PASSWORD`、`SETUP_TOKEN`、`MASTER_KEY` 后执行 `docker compose up --build`。本机无 Docker，此路径**尚未实际验证**。容器数据库不向宿主发布端口，Web仅绑定回环地址；scheduler 运行在 worker 进程内（`FISHINGNET_SCHEDULER=0` 可关闭），未做多进程/多节点调度验证。

## 验证

先启动本地 PostgreSQL（`infra/init-local.py` 或双击启动），再执行：

```powershell
.venv\Scripts\python -m pytest -q
npm --prefix apps/web run build
```

2026-10-20 的实际结果：**299 项后端测试通过**（真实 PostgreSQL 隔离 schema，没有 SQLite 替代锁/约束），`tsc -b && vite build` 通过；启动→健康检查→停止已复测（脚本输出“已就绪”，`/health/ready` 返回数据库 `ok`，5173 返回 200；停止后 8000/5173/55432 全部释放）。测试构成：M1 既有 48 项；M2 Phase 1 的 36 项契约/凭证单元测试、14 项采集流程集成测试、8 项扫码登录集成测试；M2 Phase 2 的 40 项关键词规则/适配器单元测试与 19 项发现流程集成测试；M2 收尾的 5 项集成测试；M3 Phase 1 的 35 项单元测试与 18 项集成测试（供应商/模型/路由/预算并发/回退/费用账本）；M3 Phase 2 的 11 项提取单元测试与 10 项提取集成测试（提示边界、引用偏移/哈希、拒绝原因、候选不发布、零预算阻断与重试、fence 丢失零写入）；M3 收尾的 10 项表达复核/实体候选单元测试与 10 项集成测试；M4 Phase 1 的 12 项事实材料/分档/门槛单元测试与 12 项集成测试（材料锚点与角色说明、材料上限、`fact_check` 任务、接受/拒绝/过期输入/硬门槛、发布门槛隐藏与放行、队列筛选、能力声明）。M1 轮次的隔离 QA 无头 Edge 检查覆盖了登录后的首页、证据抽屉、导入弹窗、个人设置、会话页、任务页，以及主题创建/父子关系/关联实体/主题动态筛选；**M2/M3/M4 各轮都没有重新执行完整浏览器检查，也没有新增截图**。真实网络只出现过：用户触发的二维码生成失败（域名写错，已修）与修正后的生成 + 轮询、`BV1MLJe6fEmH` 元数据/字幕（6 片段）、WBI 关键词搜索两页、一次被 HTTP 412 阻断的关键词尝试，以及用户本机 1 次真实模型连接测试（MIMO 2.6，固定合成提示，37/26 token）；**没有用真实模型处理任何材料**。

测试需要 PostgreSQL；`init-local.py` 会在缺少 `postgres` 服务时启动项目内实例。测试自动建立并移除独立测试 schema，不改变正式工作区。API 契约见 `docs/openapi.json`（70 条路径，版本 0.9.0），运行时文档为 <http://127.0.0.1:8000/api/docs>。测试、实现范围和剩余阻断项见 [验收记录](docs/ACCEPTANCE.md)；B站 接口与政策核验记录见 [B站采集政策](docs/bilibili-api-policy.md)；关键词发现流水线取舍见 [ADR 0003](docs/adr/0003-discovery-pipeline.md)；模型网关与预算取舍见 [ADR 0004](docs/adr/0004-model-gateway-budget.md)；事实证明材料与人工复核取舍见 [ADR 0005](docs/adr/0005-fact-verification.md)。

## M1 核心范围（2026-10-20）

### M1.1 闭环加固

- **启动/停止**：PID 文件记录进程启动时间与命令归属；重复启动校验后复用；PID 被其他程序复用时只清理记录、不结束该进程；端口被占用时明确报错且不误停；服务未全部就绪不显示“已就绪”；新增 `停止FishingNet.cmd`。PostgreSQL 在提升 shell 下通过基本用户信任级别启动，避免“管理员不能运行 PostgreSQL”与共享内存权限错误。
- **API 契约**：统一 `{error:{code,message,retryable,details},request_id}` 与 `X-Request-Id`；`If-Match` 缺失 428、过期 409、格式错误 422；非法游标 400 且不回显 SQL；`updates/sources/nodes/jobs/verification-queue` 使用不透明 cursor，筛选条件跨页保持；导入必须提供 `Idempotency-Key`；新增 `GET /jobs/{id}`、`POST /auth/sessions/revoke-others`、`GET /health/ready`。
- **界面流程**：导入重试只在内容未变化时复用幂等键；页面切换时清理上一页数据（修复返回动态页时的未捕获渲染错误）；列表“加载更多”保留筛选；个人资料冲突刷新后重试；会话页可注销其他全部会话；错误提示提供重试。
- **队列回归**：并发 worker 用 `SKIP LOCKED` 只领取一次；过期租约重新领取并递增 fence；旧 fence 不能写入；取消排队/运行中安全检查点、幂等取消、失败/取消重试；尝试次数耗尽标记 `ATTEMPTS_EXHAUSTED`；worker 异常记录脱敏错误码。

### M1 核心：主题管理与阅读范围

- **主题/实体管理**：`POST /nodes` 创建主题或实体；`PATCH /nodes/{id}` 使用 `If-Match` 修改名称/简介/别名；主题页和实体页提供创建与“管理”入口。
- **主题关系**：`POST /topic-links` 添加父子主题或实体归属，服务端与数据库触发器双重拒绝环；`DELETE /topic-links/{id}` 移除关系并写审计。节点详情返回父主题、子主题、关联实体、所属主题、相关动态和来源主张。
- **阅读范围**：动态支持 `topic_id`/`entity_id`/`since`/`until`/`update_type` 筛选，默认最近 7 天；从主题或实体详情“查看动态”会带入范围标签，标签可清除；筛选跨游标分页保持。
- **界面**：主题/实体页可新建；主题管理抽屉可加/删父主题、子主题和关联实体；错误（如环）显示在抽屉内，不白屏。

**仍未完成/已推迟**：批量20文件导入无限期推后；个人语言偏好、部署端密码恢复（生产发布前仍需补）、完整前端自动 E2E 推迟；`sort=changed` 等剩余筛选/端点推迟；M1 完整签收因此仍未完成。后续轮次已完成 M2 Phase 1/2/收尾与 M3 Phase 1/2/收尾；真实 B站 元数据/字幕与 WBI 关键词搜索已烟测，但真实扫码确认、分P批量、凭证恢复、定时调度未验证，真实模型抽取/复核/实体消歧未执行，M4–M5 未实现。当前 Mock 仅识别内置合成语法，真实导入与 B站 字幕只保存原文并进入待复核，不能宣称完成 AI 分析或事实确认。

## M2 Phase 1：B站 单链接导入（2026-10-20）

- **核验先行**：接口与使用政策核对记录在 [docs/bilibili-api-policy.md](docs/bilibili-api-policy.md)，包含 URL、核对日期、证据等级（官方条款/社区文档）、频率与重试上限、禁止事项和“真实烟测未执行”的声明。本机 `web_fetch` 被 DNS/hosts 拦截，条款全文待用户本机复核。
- **采集器契约**：`app/collectors/base.py` 定义 `Capabilities / Locator / RemoteSource / SubtitleResult / AccountCapability / CollectorError`；`app/collectors/fake.py` 是只用于测试的确定性替身，输出明确标注“合成示例”；`app/collectors/__init__.py` 每次调用返回新实例，请求预算不跨任务泄漏。
- **B站 适配器**：只接受含 BV 号的单条视频链接（含 `?p=N`）；`b23.tv` 短链、`av` 号、番剧、合集返回 `UNSUPPORTED_LOCATOR` 并提示替代做法。元数据走公开接口；字幕清单需要用户在本机提供的 `SESSDATA`，匿名时返回 `login_required` 而不是误报“缺字幕”。单任务上游请求 ≤4 次，最小间隔 1.5 秒（`FISHINGNET_BILIBILI_MIN_INTERVAL` 可调高），仅超时/5xx 重试一次，`412/-352/-101` 立即停止。
- **长期调用安全**：`collect_source` 任务拆成 `fetch_metadata → fetch_subtitles → store_version` 三个短事务阶段；网络调用期间由 `LeaseKeeper` 心跳续租，所有写入带 `fence AND state='running'` 守卫，fence 丢失只回滚、不发布。
- **状态诚实**：字幕可用→保存不可变版本并另建 `process_source` 任务，Mock 对真实材料不做抽取，任务停在“待复核”；缺字幕→`content_status=missing` + `subtitle_status=missing`；需要登录→`blocked_credentials` + `CREDENTIAL_REQUIRED`，提供凭证后可原地恢复；风控/网络错误→失败并记录具体错误码，不生成任何主张或动态。
- **凭证安全**：只保存 `SESSDATA`，使用 `MASTER_KEY` 派生的 Fernet 密钥加密落库；API 不回显、不写日志、不进错误信息；断开只删除本地密文，并明确说明未调用平台撤权接口。
- **扫码登录（实验）**：平台账号页可生成 B站 登录二维码（`qrcode.react` 本机渲染，ISC 许可），轮询等待扫码/已扫码/过期/风控状态；成功后只提取 `SESSDATA` 并加密保存。`qrcode_key` 只存在 API 进程内存，不返回浏览器、不落库、不写日志；生成冷却 10 秒、单会话 180 秒、最多 60 次轮询。这是网页登录流程，**不是开放平台 OAuth**，且真实扫码尚未验收。
- **界面**：导入弹窗新增“B站链接”页签（授权勾选、幂等键、无 AI 分析提示）；“平台账号”页可查看连接状态、保存凭证、手动“检查连接”、移除凭证，并显示“真实网络验收未执行”。
- **明确未做**：定时/轮询采集（Phase 2 已补）、增量 watermark（Phase 2 已补）、分P批量（M2 收尾已补，需确认）、本地转写、视频下载、自动评论/@/回复、官方开放平台 OAuth。

## M2 Phase 2：关键词来源、调度、增量与恢复（2026-10-20）

- **关键词来源配置**：新增 `discovery_sources` 表与 `/discovery-sources` 系列端点。每个来源可配置 1–5 个关键词、最多 20 个排除词、优先字幕语言、any/all 匹配、每轮候选 ≤20、每轮页数 ≤3、每轮请求 ≤4、重扫窗口、60–10080 分钟的定时间隔，以及**两个独立开关**：`enabled`（参与调度）与 `network_authorized`（允许在本机发起真实请求）。默认都关闭。
- **有界发现**：采集器契约新增 `discover(DiscoveryQuery)`；B站 适配器每次只读一页搜索结果（`x/web-interface/search/type`，不签名、不伪装浏览器），最多 3 页；本地再做关键词/排除词/上限/语言过滤，并把每个候选的决定（enqueued / refreshed / excluded_term / keyword_miss / duplicate / over_limit / skipped_deleted）写入 `discovery_run_items`，被排除的候选不会静默消失。
- **限速与重试**：沿用 1.5 秒最小间隔的进程内 pacer；超时/5xx 只重试一次，412/429/-352/-403 立即停止；失败写入 `failure_streak` 并从 15 分钟起指数退避，不会静默停用来源。
- **调度**：`app/jobs/scheduler.py` 在 worker 进程内的独立 tick 中运行，只用短事务 `FOR UPDATE SKIP LOCKED` 选取到期来源并插入 `discover_keywords` 任务，不做任何网络调用；`FISHINGNET_SCHEDULER=0` 可整体关闭。
- **增量 watermark**：去重权威是 `sources (workspace_id, platform, external_id)`；`pending` 或最近检查过的来源记为 duplicate，超过 `rescan_after_hours` 的记为 refreshed；`watermark` 记录最近入队候选与最新发布时间，整页都早于水位时停止翻页。
- **分P定位**：元数据阶段保存 ≤100 个分P的序号/cid/标题/时长；`POST /sources/{id}/parts/{p}/import` 可单独排队一个分P；批量导入见下一节（需明确确认）。
- **跨阶段持久恢复**：`jobs.checkpoint` 保存阶段快照（元数据、字幕结果、已读搜索页）；租约过期或崩溃后重领时从第一个未完成阶段继续，不再重复请求平台；手动 retry 与凭证恢复会清空 checkpoint。被取消或尝试耗尽的发现任务会在 `claim()` 清理阶段关闭运行记录并退避。
- **界面**：“采集与调度”页可新建/停用/删除关键词来源、授权或撤销联网、立即运行、查看运行记录与候选决定，并明确标注“真实关键词发现未验证”。
- **明确未验证**：真实关键词搜索请求**未执行**，上游可能因签名/登录态返回 `-403`（映射为 `DISCOVERY_DENIED`，不做绕过）；真实元数据/字幕/扫码仍未烟测。

## M2 收尾：分P批量、凭证恢复、日报联动与明细保留（2026-10-20）

- **分P批量（需确认）**：新增 `POST /sources/{id}/parts/import`，必须提交 `confirmed: true`，一次最多 20 个分P；重复序号去重，任一所选分P不存在则整批 404 `SOURCE_PART_NOT_FOUND` 且不排队；每个分P仍是独立采集任务，不抓取未选分P、不下载视频。来源详情抽屉可勾选分P、单独导入或批量确认导入。
- **凭证过期/风控恢复**：采集遇到 `-101` 将连接标记为 `expired`，遇到 412/-352/429 标记为 `risk_control` 并写入 15 分钟本地冷却；`POST /platforms/{platform}/recover` 只在凭证已替换、冷却结束后重新排队被阻塞的采集任务（≤20），不会自动重试、不会换账号/换 IP，也不修改来源或联网授权。成功采集写 `last_success_at`，不代表平台授权。
- **发现结果与日报联动**：`GET /digests/today` 增加 `discovery` 结构（totals/runs/decisions/collected_sources），Markdown 增加“关键词发现（本日）”与“关键词来源采集（本日）”小节；明确入队不等于已采集/已分析/事实确认，下载结果一致。
- **发现明细保留**：`discovery_run_items` 默认保留 30 天（`FISHINGNET_DISCOVERY_ITEM_RETENTION_DAYS`，1–3650），worker 约每 6 小时有界清理已结束运行的旧明细；`discovery_runs` 计数与来源 watermark 不删除。
- **迁移 0005**：`platform_connections` 增加 `risk_control` 状态、`cooldown_until`、`last_success_at`，并为 `discovery_runs.finished_at` 建索引；只向前追加，不改六表语义。
- **仍未验证**：真实分P批量采集、真实凭证过期/风控恢复、真实日报数据都**未执行**；本轮没有真实网络请求，全部行为用测试替身验证。

## M3 Phase 1：模型供应商、网关、预算与费用账本（2026-10-20）

- **供应商与密钥**：`provider_connections` 支持 `openai`（Chat Completions 原生）、`openai_compatible`、`local`、`mock` 四种协议；端点必须是 http(s) 且不含账号信息/查询串/片段，不跟随重定向。API 密钥使用 `MASTER_KEY` 派生的 Fernet 密钥加密（与平台凭证使用不同派生标签），只保存密文和 `****` + 末 4 位；写入接口不回显，删除供应商只删本地密文，不声称已撤销平台授权。
- **模型**：`model_profiles` 保存真实模型 ID、显示名、经济/标准/高质量等级、上下文与最大输出、是否支持 JSON 结构化输出、输入/输出单价与计价货币、价格版本。新模型**默认停用**；模型计价货币必须与工作区预算货币一致，不做汇率换算。
- **任务路由**：`task_routes` 覆盖 8 类任务，可选择 `rule`/`mock`/`model` 模式、启用开关、主模型、最多 3 个备用模型、输入/输出 token 上限、超时（1–300 秒）与总尝试次数（1–3）。整组保存是单个事务，使用 `routing_revision`（If-Match 缺失 428、过期 409）；包含云端模型且数据策略为“允许云端”时必须 `confirm_cloud: true`。
- **网关**：先解析路由并按数据策略过滤候选（云端需要工作区与任务路由同时 `approved_cloud`），再按最坏费用预留预算，然后在**没有数据库事务/行锁**的情况下调用供应商，最后结算并写调用账本。主模型可回退到备用模型；鉴权失败、限流、5xx、超时、非法 JSON、重定向分别映射为稳定错误码，错误信息不含密钥。
- **预算**：`ai_budget_ledger` 每次调用预留 `max(输入上限×输入单价, 输出上限×输出单价)` 的最坏费用；结算写入实际估算费用，差额释放。供应商未返回 token 用量时保留全部预留并标记 `unknown`。预算按 UTC 日/自然月统计；`PUT /settings/budget` 同时更新日/月预算、币种与数据策略，已存在其他币种账本时拒绝改币种。
- **账本与界面**：“模型管理”页四个页签可完成供应商新增/测试/停用/删除、密钥替换、模型新增/启停/删除、任务路由整组保存与单项测试调用；“调用记录与费用”展示调用次数、token、估算/未知费用、按日/任务/供应商/模型分组与开放预留。测试调用必须 `confirm_spend: true`，使用固定合成提示，**结果不写入知识库**，也不会生成主张或动态。
- **明确未做**：真实模型协议烟测；把模型抽取接到 `process_source` 并做服务端引用校验（M3 Phase 2 已完成，见下节）；跨进程并发上限；多节点预算一致性；Anthropic/Gemini 原生协议；供应商账单级已确认费用；模型面板浏览器点击实测。

## M3 Phase 2：路由化主张抽取与锚点校验（2026-10-20）

- **接入点**：`worker.process()` 先判断 `claim_extraction` 路由是否显式 `enabled AND mode='model'`；只有“路由启用 + 来源不是合成示例”时才进入模型抽取。合成示例固定走本地 Mock，未配置路由的真实材料仍停在 `needs_review`，不会隐式调用模型。
- **有界提示**：`app/ai/extraction.py` 只把路由输入上限以内的片段发给模型；超出部分标记 `partial`/`truncated`，其未发送的尾部不能被引用。提示把材料放在 JSON 数据块中，并明确“材料里的指令不要执行”。
- **服务端校验**：只接受出现在请求中的 `segment_id`；`quote` 必须逐字出现在该片段可见文本中（允许空白折行归一化后映射回原文），`char_start/char_end/quote_hash` 由服务端按 Unicode code point 计算。模型自报偏移不一致记 `OFFSET_MISMATCH`；未知片段、引文不存在、非法类别/极性/重要性/可验证性都整条拒绝，不做静默纠正。
- **发布门槛不变**：通过锚点的 claims 才写 `evidenced_by`、核查记录（`insufficient`）并按 `PublicationPolicy` 发布为“来源观点（未经事实确认）”；未通过锚点的主张保存为 `candidate`，不写证据关系、不生成动态。抽取模型不能设置 `fact_check_status`，任务结果固定 `fact_confirmation=false`、`semantic_review=not_performed`。
- **预算/租约/fence**：一次 `process_source` 至多一次模型调用；顺序仍是“短事务预留 → 无事务 HTTP → 短事务结算”，HTTP 期间由 `LeaseKeeper` 心跳续租，最终写入与任务完成在同一受 `fence AND state='running'` 保护的事务里。fence 丢失时 claims/updates 与任务状态一起回滚。
- **可重试的阻断**：零预算 → `blocked_budget` + `BUDGET_EXHAUSTED`（provider 不会被请求，写一条 `blocked` 调用记录），提高预算后可直接重试；数据策略/鉴权/网络等阻断 → `failed` 并带稳定错误码；模型输出非法或全部无锚点 → `needs_review`，原文保持不变。
- **界面与能力**：动态卡片显示“模型抽取 · 未事实确认”，任务页显示“原文锚点通过 / 待复核候选 / 拒绝”和模型名；`/capabilities` 增加 `model_extraction`。OpenAPI 版本升到 **0.7.0**，路径数不变（62）。（M3 收尾已把语义复核与实体候选接入，见文首；本条为当轮记录。）

## M4 Phase 1：库内事实材料、版本化核查记录与人工复核（2026-10-20）

- **事实材料是关系，不是新表**：`claim --fact_supported_by / fact_refuted_by / fact_context_by--> source_version`，创建时为 `proposed`；载荷必须包含 `schema_version`、锚点、命题范围、直接性、上游来源组、独立性、条件适用、材料完整性、透明度与限制。服务端重算偏移与哈希，引文必须逐字且唯一。
- **评估是规则、可解释、非事实确认**：`assert` 只读取库内已保存材料；E0–E3 分档要求逐项满足（直接 + 五项条件匹配 + 完整性已核验；E3 还要求两个不同上游组且独立性已确认）。数量、抽取置信度、作者身份、热度都不参与分档。
- **记录只追加**：每次核查追加一条 `verification_records`（`pending`），人工接受/拒绝再追加一条带 `supersedes_id` 的版本；原记录不可改、不可删。接受时会一并接受该记录使用的事实材料并写入 `assessment_record_id`。
- **硬门槛**：`corroborated` 至少 E2；重要事实需 E3 且必须人工接受；`refuted` 需 E3 反向材料且不能同时存在 E2/E3 支持材料；`insufficient` 可以接受，但它不会提升表达。
- **发布门槛覆盖标题与正文**：读取端对每个句子评估策略，允许“已存储的更弱表达”（例如已发布为 attributed_unverified）继续展示，但拒绝把句子升级为更强的 scoped_fact；`POST /updates/{id}/publication-preview` 只预览、不发布。
- **能力如实声明**：`GET /verification-capabilities` 明确 `kb_only`、无外部连接器、事实核查模型路由未接入、`auto_accept_allowed=false`；`GET /sources/{id}/reliability` 返回 `unknown`，来源画像评估尚未实现。

## 技术栈

React + TypeScript + Vite；Python 3.12 + FastAPI + Pydantic；PostgreSQL；SQLAlchemy + Alembic。独立 worker 共享 PostgreSQL 持久队列（scheduler 在 worker 进程内运行短事务），不依赖 DSH、Redis 或向量数据库。

## 实施顺序

1. M0：架构、数据字典、六表迁移、契约与合成样例。
2. M1：初始化与会话、授权手动导入、不可变片段、Mock 处理、动态与证据闭环。
3. M2：B站适配与平台账号、增量、调度及恢复。
4. M3：模型配置、统一网关、隐私路由与并发预算（Phase 1 供应商/路由/预算/账本已完成；Phase 2 路由化抽取与服务端锚点校验已完成；收尾的表达语义复核与实体候选人工确认已完成；真实模型抽取/复核/消歧、跨进程配额、事实核查与主张比较接入仍待做）。
5. M4：事实核查、人工复核、统一发布门槛及纠错传播（Phase 1 已完成库内事实材料、版本化核查记录、人工接受/拒绝与发布门槛；`claim_comparison`/`fact_check` 模型路由、外部检索、来源画像、纠错传播与 AC32–45 专项数据集仍待做）。
6. M5：安全、性能、备份恢复与完整验收。

真实模型和平台烟测必须单独记录，Mock 不作为真实验收。默认预算为零，禁止隐式付费或自动采集。

## 开发约束

- 六张核心表：sources、source_versions、claims、nodes、relations、updates；运行支撑表独立。
- 所有引用按工作区隔离；Unicode code point 偏移；证据验证与事实判断分开。
- 重要且未经核实的事实默认暂缓发布；所有展示及导出执行同一服务端策略。
- 下载受阻即暂停并向用户提供原始下载链接，不自行切换不明镜像。

当前环境与进度见 [开发记录](docs/PROGRESS.md)，数据约束见 [数据字典](docs/DATA_DICTIONARY.md)。
