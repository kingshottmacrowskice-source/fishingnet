# FishingNet · CODEX 开发交接文档

## 2026-10-21 01:55（UTC+8）· M4 Phase 2 补充：模型自动建实体 + 按实体分组的动态 + 自动化需求记录

**本轮**：按用户指示完成“模型可自建实体 + 一个实体一条动态”；并把“每日定时检索与整合（自动化为主）”作为**未实现需求**记录（见 `docs/ROADMAP.md`）。未新增迁移、未改 0001–0008、未删库、未覆盖 `.env`。

- `app/ai/entities.py`：`entity_disambiguation` 允许空实体库调用；模型返回 `links`（仅已有 node_id）与 `new_entities`（name/entity_type/aliases/description/claim_refs）；服务端校验、同名去重、上限。
- `app/jobs/worker.py`：`_suggest_entities` 不再因无节点跳过；`_store_model_claims` 按 `canonical_name` 去重后自动建 `provisional` 实体（`asserted_by='model'`，不自动合并），并建立 `accepted` claim→node `about`；动态改为“按（来源版本 + 实体）分组，一个实体一条动态”，未归类仍逐条；既有实体链接仍 `proposed` 需人工接受。
- `app/main.py`：`/capabilities` 更新；API **0.11.0**（端点仍 70）。
- 前端：feed 卡片“关于 {实体}”标签 + 多句预览；任务页“新建实体 N 个”。
- 验证：`pytest -q` **390 passed / 93 warnings（47.89s）**；新增 `tests/test_m4_entities.py` 4 项；`npm build` 通过；`alembic current` = **0008 (head)**；OpenAPI **70 路径 / 0.11.0**。

**未实现（仅记录，下一步）**：每日固定时间段定时检索与自动整合（见 `docs/ROADMAP.md`）；主题（topic）自动创建；真实模型实体消歧/建实体烟测。

## 2026-10-21 01:10（UTC+8）· M4 Phase 2：事实核查建议路由、版本过期封顶与 AC32–45 阻断数据集

**本轮完成 M4 Phase 2 的第一部分（没有新增迁移、没有真实模型调用、没有外部检索、没有真实事实核查烟测）。** 未改 0001–0008、未删库、未覆盖 `.env`、未改写既有来源/主张/核查记录。

实现要点：
- `app/ai/factcheck.py`（新增）：`build_fact_check_messages` 只发送主张与**库内材料元数据**（作用、范围、直接性、上游组、独立性、完整性、条件、限制），按路由输入上限截断；`validate_advisory` 只接受受控 `supports/refutes/conflicts/insufficient/unclear`、已知 `material_refs`、理由与限制，拒绝编造引用、非法结论与任何“外部读取/工具调用”自述（`MODEL_CLAIMED_EXTERNAL_READS`）；忽略模型置信度；`agreement` 只说明与规则分档是否一致。
- `app/jobs/worker.py`：`_fact_materials` 增加 `superseded`/`retention_expired`/`version_no`；新增 `_fact_check_advisory`（未配置路由直接跳过；否则 `LeaseKeeper` 内 `invoke_messages('fact_check')`，预算/策略/上游/非法输出只记录“建议不可用”）；`fact_check` 把建议写 `model_runs`，manifest 增加 `model_route`/`model_advisory_status`；评估、记录状态与 `claims.fact_check_status` 不变。
- `app/domain/verification.py`：来源出现新版本后旧材料封顶 E1（`SOURCE_VERSION_SUPERSEDED`），保留期到期 E0；`assess` 增加 `stale_*`、`dispute_balance` 与不对称缺口；快照哈希纳入 `superseded`/`retention_expired`，旧记录接受时 409。
- `app/ai/extraction.py`：AC33 截断限定——引文在子句分隔符处截断且后半句含但/仅/不支持/尚未/正式版/beta 等标记时，主张进入 `unverified`（`CONTEXT_TRUNCATED_LIMITATION`），不发布。
- `app/domain/policy.py`：`scoped_fact` 必须来自人工接受（`checker_type='human'` + reviewer）的当前记录；历史规则记录不能继承确认资格。
- `app/main.py`：同步材料 `superseded`；`/verification-capabilities` 与 `/capabilities` 更新能力声明与 deferred；API **0.10.0**（端点仍 70）。
- 数据集：`tests/fixtures/ac32-45.json`（48 案例 + 12 对照，全部合成）、`tests/test_m4_ac_dataset.py`（86 项）、`tools/build_ac_dataset.py`、`docs/AC32-45-dataset.md`。
- 前端：主张详情/核查记录显示模型建议、一致性与截断标记，明确“不改变分档、状态或发布门槛”。

**验证**：基线 299 passed（42.42s）；改动后 `pytest -q` **385 passed / 93 warnings（45.72s）**（真实 PostgreSQL 17 隔离 schema）；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0008 (head)**；`docs/openapi.json` 重导出 **70 路径 / 0.10.0**；`launch.ps1 -NoBrowser` 就绪、`/health/ready` ok/ok、5173 返回 200、新端点未登录 401，`stop.ps1` 退出码 0 且三端口释放。正式库保持 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水 / 6 条核查记录 / 0 条事实关系**。

**未执行/未验证**：真实模型事实核查与真实外部检索；`claim_comparison` 模型路由；来源画像评估与记录；纠错表达式生成与统一重算；批量复核与浏览器点击实测；真实扫码确认、分P批量、凭证恢复、定时调度。AC32–45 数据集通过不等于 AC01–AC45 全部签收，也不等于 M4 完成；`insufficient` 不是“虚假”，`corroborated` 也不是外部已查证。

**建议下一项**：把 `claim_comparison` 接入事实核查任务（同实体候选主张比较，仍只输出建议/候选、不改变分档），或在用户明确授权下用固定合成材料真实执行一次 `fact_check` 路由调用并记录端点、token 与费用（不得发送真实导入材料，不得降低门槛）。

## 2026-10-20 23:45（UTC+8）· M4 Phase 1：库内事实材料、版本化核查记录与人工复核

**本轮完成 M4 Phase 1（没有外部检索、没有模型调用、没有真实事实核查烟测）。** 迁移新增 **0008**（事实证明证据载荷/锚点约束、核查记录状态约束与索引），未改 0001–0007、未删库、未覆盖 `.env`、未改写既有来源/主张/核查记录。

实现要点：
- `app/domain/fact_evidence.py`（新增）：`normalize_link` 生成 `fact_evidence.v1` 载荷；引文必须逐字出现在所选片段且唯一，偏移/哈希由服务端按 Unicode code point 计算；`directness/independence_status/artifact_integrity/applicability/method_transparency/limitations` 受控校验，条件缺省为 `unknown`；同一来源版本同时作表达溯源与事实证据时必须提供用途说明；材料作用映射为 `fact_supported_by/fact_refuted_by/fact_context_by`。
- `app/domain/verification.py`（新增）：确定性分档 E0–E3（E2 = 直接 + 五项条件全匹配 + 完整性已核验；E3 还需至少两条 E2、不同上游组、无同源转载且至少一组确认独立）；`assess` 产出 `insufficient/corroborated/conflicted/refuted`、缺口、逐档计数与 `human_review_required/auto_accept_allowed=false`；`accept_gate` 拦截 E1 的 corroborated、重要事实的 E2、E1 的 refuted 和“有强支持却判 refuted”；`snapshot_hash` 用于复核时检测材料变化。
- `app/jobs/worker.py`：新增 `kind='fact_check'` 任务（初始阶段 `retrieve`）。只读库内材料与同实体候选主张，写 `kb_only` 检索清单（`external_reads=[]`、`model_route=not_wired_in_this_phase`）、证据快照与评估；在 `fence AND state='running'` 的同一事务内追加 `pending` 记录并结束任务；**不改变** `claims.fact_check_status`；claim 不可用时 `CLAIM_UNAVAILABLE` 且不写记录。
- `app/main.py`：新增 7 条路径（`POST /claims/{id}/evidence-links`、`POST|GET /claims/{id}/fact-checks`、`GET /verification-records/{id}`、`POST /verification-records/{id}/review`、`GET /verification-capabilities`、`GET /sources/{id}/reliability`、`POST /updates/{id}/publication-preview`）。复核要求输入指纹、claim revision 与材料快照哈希一致（409 过期/重复），接受时原子接受材料并移动指针（422 硬门槛）；`GET /claims/{id}` 返回 `current_verification/fact_evidence/fact_evidence_groups/pending_verification_records`；`GET /verification-queue` 增加最新记录、待复核数量与过滤；`visible_update` 同时约束标题与正文，并用 `mode_is_within` 允许更弱表达、拒绝升级；API 版本 **0.9.0**，端点 **70**。
- `app/domain/policy.py`：`publication.v1.3.0` + `mode_is_within`（opinion/conditional/attributed < dispute/correction < scoped_fact）。
- 前端：主张详情新增“库内事实核查”面板（材料分档、待复核记录与 gate 提示、接受/拒绝需填理由、发起核查、选择来源与片段添加待确认材料）；核查工作台可直接发起核查；横幅/页脚更新为 M4 Phase 1。
- 文档：新增 `docs/adr/0005-fact-verification.md`；同步 README、PROGRESS、ACCEPTANCE、数据字典、OpenAPI。

**验证**：基线 `pytest -q` **275 passed**；改动后 **299 passed / 93 warnings（43.84s）**（真实 PostgreSQL 17 隔离 schema），新增 `tests/test_m4_verification.py` 24 项（12 单元 + 12 集成，无网络、无模型）；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0008 (head)**；`docs/openapi.json` 重导出 **70 路径 / 0.9.0**；`launch.ps1 -NoBrowser` 就绪、`/api/v1/health/ready` ok/ok、5173 返回 200、新端点未登录 401、`api.error.log` 只有启动 INFO、`worker/web.error.log` 0 字节；`stop.ps1` 退出码 0 且三端口释放。正式库保持 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**；`verification_records` 仍 6 行、`fact_*` 关系 0 行。

**未执行/未验证**：事实核查模型路由与 `claim_comparison`、外部检索连接器、来源画像评估与记录、纠错传播与统一重算、AC32–45 专项数据集与整体签收、批量复核、浏览器点击回归；真实模型抽取/语义复核/实体消歧、真实扫码确认、分P批量、凭证恢复、定时调度仍未验证。`kb_only` 空结果不是反证，`insufficient` 不是“虚假”，`corroborated` 也不是外部已查证。

**建议下一项**：先建立 AC32–45 专项阻断数据集（虚假功能宣传、断章取义、过期版本、循环转载、伪造测评、真假批评、无外部工具、多来源不等于真、不对称争议、纠错传播、全面发布门槛、预算路由、并发与迁移），再把 `claim_comparison`/`fact_check` 模型路由接入并用固定合成材料验证；真实外部检索必须单独授权并记录来源与结果。

## 2026-10-20 21:25（UTC+8）· M3 收尾：表达语义复核与实体候选人工确认

**本轮完成 M3 收尾（自动测试未用真实模型；没有真实模型抽取/复核/实体消歧）。** 迁移新增 **0007**（`claim_expression_reviews`、proposed 关系索引），未改 0001–0006、未删库、未覆盖 `.env`、未改写既有来源/任务数据。

实现要点：
- `app/ai/review.py`（新增）：`build_review_messages` 只发送带服务端锚点的主张、逐字引文和限定上下文（超出路由输入上限的主张标记 `truncated`、不发送）；`validate_review_output` 只接受请求中出现过的 `claim_ref` 与受控 verdict（supports/partially_supports/unsupported/unclear），未知/重复/非法条目拒绝、未覆盖条目记为 missing。复核只判断“引文是否支持表达”，不判断事实真假。
- `app/ai/entities.py`（新增）：`build_entity_messages` 只发送主张与库内已有实体（名称、类型、有限别名）；`validate_entity_output` 只接受请求中出现过的 `node_id`，拒绝编造节点、未知主张、重复/超限/非法角色，不创建或合并节点。
- `app/jobs/worker.py`：`_process_with_model` 在抽取与锚点校验后，按 `evidence_review` 路由执行一次语义复核，再按 `entity_disambiguation` 路由执行一次实体候选；两次调用复用“短事务预留 → 无事务 HTTP → 结算/释放 + 调用账本”和 `LeaseKeeper`。`_store_model_claims` 现在：只有锚点通过且复核为 supports/partially_supports 的主张写 `validated` 并进入 `PublicationPolicy`；不支持、上下文不足、复核未完成/未覆盖的主张写 `candidate`，不建核查记录、不发布；复核结果写 `claim_expression_reviews`；实体候选写 `relations(status='proposed', asserted_by='model')`。`fact_check_status` 仍固定 `insufficient`，任务结果固定 `fact_confirmation=false`。
- `app/main.py`：新增 `POST /relations/{relation_id}/review`（人工接受/拒绝模型实体候选；接受后为已发布动态补 update→node 关系，拒绝保持不可见）；`GET /claims/{id}` 增加 `expression_reviews`、`expression_review`、`about_nodes`；`GET /verification-queue` 增加最新 `expression_verdict`；节点详情的来源主张只统计已接受关系；`/capabilities` 增加 `expression_review` 与 `entity_disambiguation`，更新 B站/WBI 烟测边界与 deferred 列表；API 版本 **0.8.0**，端点 **63**。
- 前端：核查工作台显示表达复核结论；任务页显示复核计数与实体候选；主张详情显示复核理由/限制与实体候选，可接受/拒绝；模型管理说明新路由用途；模式横幅更新为 M3 收尾。

**验证**：基线复现（`init-local.py` 复用库、`pytest -q` **255 passed**、前端构建、已有实例健康检查）通过。改动后 `pytest -q` **275 passed / 93 warnings（61.43s）**（新增 `tests/test_m3_wrapup.py` 20 项：10 单元 + 10 集成，全部 `httpx.MockTransport`）；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0007 (head)**；`docs/openapi.json` 重导出 **63 路径 / 0.8.0**；`launch.ps1 -NoBrowser` 就绪、`/api/v1/health/ready` ok/ok、5173 200、`/relations/{id}/review` `/claims/{id}` `/capabilities` `/task-routes` 未登录均 401、web/worker 错误日志 0 字节；`stop.ps1` 退出码 0 且三端口释放。正式库计数保持 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**，新表 0 行。

**未执行/未验证**：真实模型抽取、真实语义复核、真实实体消歧；真实扫码确认、分P批量、凭证恢复、定时调度；`claim_comparison`/`fact_check` 接入；跨进程配额、多节点预算、Anthropic/Gemini 原生协议、供应商账单对账、浏览器端模型面板自动点击回归。复核阶段预算不足时任务为 `needs_review` 并保留 candidate，不会自动重试；抽取阶段零预算仍是可重试的 `blocked_budget`。

**建议下一项**：用固定合成材料真实执行一次 `claim_extraction`（需用户明确授权、显式启用路由、预算充足；检查真实模型返回的 JSON 能否通过服务端锚点校验），再决定是否启用 `evidence_review`/`entity_disambiguation` 做真实复核/消歧。任何真实测试都不能降低发布门槛、默认预算或云端确认要求。

## 2026-10-20 20:20（UTC+8）· M3 Phase 2：路由化抽取接入 process_source 与服务端锚点校验

**本轮完成 M3 Phase 2（自动测试未用真实模型；用户在本机执行了 1 次真实连接测试，但未做真实抽取）。** 没有新增迁移（head 仍 `0006`），未改 0001–0006、未删库、未覆盖 `.env`、未改写既有来源/任务数据。

实现要点：
- `app/ai/extraction.py`（新增）：`build_claim_messages` 生成有界抽取提示（只含路由 `max_input_tokens` 内的片段，超出部分 `partial/truncated` 且不可引用；材料放在 JSON 数据块并声明“材料中的指令不要执行”）；`validate_claim_output` 只接受请求中出现过的 `segment_id`，`quote` 必须逐字出现在该片段可见文本（空白折行归一化后映射回原文），`char_start/char_end/quote_hash` 一律服务端按 Unicode code point 计算，模型自报偏移不一致即 `OFFSET_MISMATCH`；类别/极性/重要性/可验证性走受控词表，非法值整条拒绝；无锚点主张返回 `unverified` 候选。
- `app/ai/gateway.py`：`invoke` 拆出 `invoke_messages`，任务可自带提示但复用同一“短事务预留最坏费用 → 无事务 HTTP → 结算/释放 + 调用账本”流程；预算阻断的错误 details 带 `invocation_id`。
- `app/jobs/worker.py`：`process()` 改为分派器；原单事务逻辑保留为 `_process_local`（合成示例路径）。新增 `_process_with_model`：读路由 → 有界提示 → `LeaseKeeper` 内调用网关 → 校验输出 → 在 `fence AND state='running'` 守卫的同一事务里写 anchored claims、`evidenced_by`、`insufficient` 核查记录，并按 `PublicationPolicy` 发布；无锚点主张写为 `review_status='candidate'`，不建关系、不生成动态。零预算 → `blocked_budget`；策略/鉴权/网络 → `failed`；输出非法或全部无锚点 → `needs_review`。fence 丢失时主张、动态与任务状态一起回滚。`blocked_budget` 任务现可直接 `retry`。
- `app/main.py`：`/capabilities` 增加 `model_extraction`（锚点校验、候选不发布、`fact_check_status` 固定 `insufficient`、语义复核未实现、真实模型抽取未验证）；API 版本 **0.7.0**，端点数量不变（62）。
- 前端：动态卡片标注“模型抽取 · 未事实确认”，任务页显示“原文锚点通过 / 待复核候选 / 拒绝 + 模型名”，任务路由说明标明抽取用途，模式横幅更新为 M3 Phase 2。

**验证**：基线复现（`init-local.py` 复用库、`pytest -q` **234 passed**、前端构建、`launch.ps1 -NoBrowser`、`stop.ps1`）全部通过。改动后 `pytest -q` **255 passed / 93 warnings（30.71s）**（新增 `tests/test_m3_extraction.py` 21 项：11 单元 + 10 集成，全部 `httpx.MockTransport`）；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0006 (head)**；`docs/openapi.json` 重导出 **62 路径 / 0.7.0**；`launch.ps1 -NoBrowser` 就绪、`/health/ready` ok/ok、5173 200、`/model-providers` `/task-routes` `/ai/invocations` `/capabilities` 未登录均 401、api/worker/web 错误日志 0 字节；`stop.ps1` 退出码 0 且三端口释放。

**真实连接测试（用户在本机执行，2026-10-20 20:35，UTC+8）**：用户在应用内自行配置 MIMO 2.6（`openai_compatible`，`https://token-plan-cn.xiaomimimo.com/v1`，模型 `mimo-v2.6-pro`），设置预算与“允许云端”后点击一次“测试连接”。结果：`succeeded`，输入 37 / 输出 26 token，延迟 1625 ms，估算并结算 **0.0000445 USD**，预留 0.000256 → 结算 0.0000445，账本 `settled`；无 claims/updates、无导入材料外发。正式库计数随之为 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**。`/capabilities` 的 `live_model_protocol_verified` 已改为 true 并带日期/范围说明，`model_extraction.live_model_extraction_verified` 仍为 false。

**没有用真实模型处理任何材料**，也没有新增 B站 请求；模型抽取只在测试替身中跑通。语义复核、事实核查、实体消歧/主张比较路由接入、跨进程配额、多节点预算仍未实现。默认供应商/模型停用、任务路由未配置、日/月预算 0、数据策略 `local_only` 的**项目默认值未改**（用户为本次测试把其工作区调为 `approved_cloud` 并启用该供应商/模型，这是用户本机数据，不要覆盖）。

**建议下一项**：用固定合成材料真实执行一次 `claim_extraction`（需要用户明确授权、`claim_extraction` 路由显式启用、预算充足；检查真实模型返回的 JSON 是否能通过服务端锚点校验），或继续 M3 收尾（把 `evidence_review`/`entity_disambiguation` 接入流水线并做语义复核）。两者都不能降低发布门槛或默认预算。

## 2026-10-20 19:55（UTC+8）· M3 Phase 1：模型供应商、网关、预算与费用账本

**本轮完成 M3 Phase 1（未做真实模型烟测）。** 迁移新增 `0006_ai_gateway.sql`：`provider_connections`、`model_profiles`、`task_routes`、`ai_invocations`、`ai_budget_ledger`，并给 `workspace_settings` 增加 `budget_currency`/`routing_revision`；未改 0001–0005、未删库、未覆盖 `.env`。

实现要点：
- `app/domain/model_secrets.py`：模型密钥独立派生标签的 Fernet 密封，只显示 `****`+末 4 位，不回显/不写日志/不进快照。
- `app/ai/budget.py`：Decimal 计价；调用前按 token 上限预留最坏费用（短事务 + `FOR UPDATE` 锁工作区行），调用后按用量结算；未知用量保留全额预留并标记 unknown；预算窗口 UTC 日/月，单一币种无汇率。
- `app/ai/providers.py`：openai / openai_compatible / local / mock 四协议；固定 UA、无自动重试、不跟随重定向、限制响应大小、错误按 401/403、429、5xx、3xx、非法 JSON 映射；Mock 不触网。
- `app/ai/gateway.py`：保留 `AIGateway.extract` 不变；新增 8 任务路由、资料策略门（云端需工作区与路由都 `approved_cloud`）、主→备回退、超时/尝试次数、调用账本与连接测试。
- API 新增 12 路径（供应商 CRUD/密钥/测试、模型 CRUD、任务路由整组原子保存、`/ai/test-call`、调用列表/详情、费用汇总、`PUT /settings/budget`），OpenAPI **62 路径 / 0.6.0**。
- 前端“模型管理”四个页签 + 费用摘要；“系统设置”预算/币种/数据策略表单；任务页显示今日估算费用。

**验证**：基线 181 passed → 改动后 **234 passed / 93 warnings（26.02s）**；前端构建通过；`alembic current` = 0006；`launch.ps1 -NoBrowser` 就绪、`/health/ready` ok、5173 200、OpenAPI 0.6.0/62 路径、新端点未登录 401、worker 错误日志为空；`stop.ps1` 退出码 0 且三端口释放。正式库计数 1 用户 / 7 来源 / 10 任务；供应商、模型、路由、调用、预算流水均为 0。

**本轮没有任何真实模型请求**（没有配置真实密钥），全部网络用 `httpx.MockTransport` 替身；`live_model_protocol_verified=false`。默认供应商/模型停用、任务路由未配置、日/月预算 0、数据策略 `local_only`；云端调用需要显式 `confirm_cloud` 与足够预算。`process_source` 仍是 Mock，**测试调用不写 claims/updates**。真实 B站 本轮没有新请求。

**建议下一项**：M3 Phase 2 —— 把路由化模型抽取接进 `process_source`（服务端引用/锚点校验、逐句核查依据、失败保持待复核），或先做一次用户授权的真实模型连接测试（固定合成提示，记录端点/状态/费用，不发导入材料）。两个方向都不能降低发布门槛或默认预算。

## 2026-10-20 16:10（UTC+8）· WBI 签名关键词搜索真实烟测通过

用户授权扩展：关键词搜索可使用 bilibili-api-python 库内置标准 WBI 签名（OE 表 + `_enc_wbi`），与字幕适配器相同。

实现变更：
- `bilibili.py`：新增 `_get_wbi_key()`（`/x/web-interface/nav` mixin key，30 分钟缓存）、`_sign_wbi()`、`_get_buvid()`（`/x/frontend/finger/spi` buvid3/buvid4 设备指纹，搜索必需）；`discover()` 搜索前获取 buvid 并签名
- `worker.py`：`discover()` 从 `platform_connections` 加载凭证传递给 `collector.discover(credential=...)`
- `fake.py`：`FakeBilibiliCollector.discover()` 签名同步更新
- `test_discovery_rules.py`：测试 helper 预设 `_wbi_key`/`_buvid3`/`_buvid4`

真实烟测（`_smoke_wbi.py`）：WBI key 获取 OK，关键词 `"AI"` 两页各 20 条结果。搜索端点从 `x/web-interface/search/type`（无签名，HTTP 412）改为 `x/web-interface/wbi/search/type`。buvid 缺失时搜索返回 HTTP 412，已确认。全量 181 passed，前端构建通过。BV1MLJe6fEmH 状态确认：ready/available，6 片段。B站平台连接 connected。

当前状态：M1 核心 + M2 Phase 1/2/收尾 + WBI 签名搜索已全部通过真实烟测。M3（供应商密钥管理、真实模型协议）未开始。

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


> 最新接续检查点（2026-10-20 07:06，UTC+8）：用户**已明确授权本轮**真实低频烟测（本人扫码、1 条自有 BV 链接元数据/字幕、1 个关键词来源手动 1 轮且 ≤2 页），无需重复索取该授权；尚待链接、关键词及扫码。本轮初始化被本机 55432 端口占用阻塞：`verge-mihomo.exe` PID 25048 的出站连接导致 PostgreSQL 绑定失败（Windows 10013）。已请用户释放该连接，未改代理、未结束代理进程、未覆盖 `.env` 或删库。前端构建和 104 项无数据库单元测试通过；全量测试、正式库迁移/计数、启停和本轮真实请求均尚未执行。API 无变化，OpenAPI 一致。此处覆盖下方历史“仍待授权”状态，历史测试不代表本轮复现。先解决端口占用并完成本地复现，再按已授权范围烟测；定时调度保持关闭，M3 未开始。详细结果见 ACCEPTANCE 最新轮次。

交接日期：2026-10-21  
最后更新：2026-10-21（M4 Phase 2 补充：模型自动建实体 + 按实体分组的动态；自动化需求已记录未实现）  
项目目录：`C:\Users\Administrator\Desktop\fishingNet`  
需求基线：`AI多来源信息聚合平台_开发需求文档.md`，v1.2  
当前阶段：**M1 核心已按用户优先级收口（M1.1 加固 + 主题管理/阅读筛选）；M2 Phase 1、Phase 2 与收尾已完成；M3 Phase 1（模型供应商/路由/预算/账本）、M3 Phase 2（路由化抽取与服务端锚点校验）、M3 收尾（表达语义复核、实体候选人工确认）已完成；M4 Phase 1（库内事实材料、E0–E3 规则分档、追加型核查记录、人工接受/拒绝、重要事实 E3 硬门槛、标题/正文发布门槛与发布预览）、M4 Phase 2（`fact_check` 仅建议路由、来源版本过期封顶、断章取义拦截、`scoped_fact` 必须人工接受、AC32–45 阻断数据集）与 M4 Phase 2 补充（模型自动建实体 + 按实体分组的动态）已完成**。批量导入、语言偏好、部署端密码恢复、完整 E2E 已获用户同意推迟。B站 元数据/字幕与 WBI 关键词搜索已通过真实烟测；**真实模型连接测试已由用户在本机执行 1 次（MIMO 2.6，固定合成提示，成功），但真实模型抽取/复核/实体消歧/事实核查均未执行**，不是完整 MVP。**每日固定时间段定时检索与整合为未实现需求，见 `docs/ROADMAP.md`。**

优先级更新：2026-10-20 用户决定把密码恢复暂时完全推迟、批量导入无限期推后，主线转向 B站 自动采集。M2 Phase 1 完成单链接导入与契约；Phase 2 完成发现流水线与调度；M2 收尾完成批量确认、恢复、日报与保留；M3 Phase 1 完成模型网关与费用控制；M3 Phase 2 完成抽取接入与服务端锚点校验；M3 收尾完成表达语义复核与实体候选人工确认；M4 Phase 1 完成事实证明材料、版本化核查记录、人工复核与发布门槛；M4 Phase 2 完成建议路由、过期版本封顶、断章取义拦截与 AC32–45 数据集；M4 Phase 2 补充完成模型自动建实体与按实体分组。**2026-10-21 用户新增“自动化为主、每日固定时间检索与整合”需求，已记录为未实现（`docs/ROADMAP.md`），暂不开发。** 真实上游请求仍应低频、可中止、记录结果。

## 1. 如何把下一项任务交给 CODEX

在 Codex 中打开同一个项目目录，新建一个开发对话，把本文第 12 节的任务提示完整发给它即可。继续当前对话也可以，不必复制整个项目。

下一位 CODEX 应先读取本文件和需求原文，再检查当前代码、数据库迁移与 Git 状态；不要仅依赖历史对话总结。用户可能在交接后登录、导入或修改数据，必须保留这些变化。

**真实 B站 烟测已通过（2026-10-20）：** BV1MLJe6fEmH 元数据/字幕（6 片段，ai-zh）+ WBI 签名关键词搜索（`"AI"` 两页各 20 条）+ B站平台连接（connected）全部验证通过。结果已记入 `docs/ACCEPTANCE.md`。**M3 Phase 2、M3 收尾、M4 Phase 1 与 M4 Phase 2 第一部分已完成（抽取锚点、表达复核、实体候选、库内事实材料、核查记录与人工复核、仅建议的事实核查路由、版本过期封顶、断章取义拦截、AC32–45 数据集；自动测试均为替身且事实核查无外部检索）；用户已完成 1 次真实模型连接测试（MIMO 2.6，固定合成提示，成功），但真实模型抽取/复核/实体消歧/事实核查未执行。建议下一项任务：接入 `claim_comparison` 模型路由，或用固定合成材料在用户授权下真实执行一次 `fact_check` 路由调用。** 分P批量（需确认）、账号过期/风控恢复、日报联动与 `discovery_run_items` 保留策略已在 M2 收尾完成；M3 Phase 1 的供应商/模型/路由/预算/账本已完成；**真实模型相关能力与外部核查均未用真实服务验证**。批量导入、密码恢复、语言偏好、完整 E2E 继续暂停。不要重新搭建项目，不要为了”干净”删除数据库、`.runtime/pgdata` 或覆盖 `.env`，也不要把 M1 核心、M2、Mock、测试替身、M3 Phase 1/2/收尾、M4 写成完整 MVP 或“真实模型/真实事实核查已接入”。

## 2. 用户已经明确的要求

1. 按需求文档从零开发，沿用信息聚合定位，不能改造成视频推荐器。
2. **如果下载出现问题或卡住，先停止，把下载链接交给用户查看。** 不无限重试、不擅自切换不明镜像、不关闭证书校验、不修改用户代理设置。
3. 已要求提供启动入口，根目录的 `启动FishingNet.cmd` 必须保留并维护；2026-10-20 新增了 `停止FishingNet.cmd`。
4. 真实模型调用、平台账号、导入来源和合成示例必须明确区分；未经真实验证的能力不能写成已完成。
5. 需要新依赖但下载失败时停下来报告，不安装不明来源包。
6. **2026-10-20 优先级决定：** 部署端密码恢复暂时完全推迟（发布前仍需补，不能算生产就绪）；批量导入无限期推后；手动导入只作为缺字幕/无授权/非视频来源的兜底，不投入批量扩展。
7. **主线期望是系统自动发现并获取 B站视频。** 参考 `dsh-bili-scroll` 的处理链，但必须独立实现：不依赖 DSH，不照搬代码；如实际复用代码，需逐文件核对许可证与依赖并保留声明；不复制其价格、优惠时段或版本假设；不做自动评论、@、回复。
8. **真实 B站采集必须先核验接口与平台政策。** 只使用平台允许的公开能力或用户授权；不得绕过登录、付费、验证码或风控；无授权/接口不可用时只做契约和测试替身，不能声称真实采集通过。
9. **2026-10-20 用户要求提供类似参考插件的扫码登录。** 已实现 B站 网页登录二维码流程（本机渲染、内存会话、成功后只加密保存 SESSDATA）；真实网络已验证到生成 + 轮询（返回 `waiting_scan`），但**没有用真实 App 完成扫码确认**；它属于网页登录流程，不是官方开放平台 OAuth。真实扫码必须由用户在本机授权并亲自确认。
10. **2026-10-20 Phase 2 要求：** 关键词来源配置、排除词/语言/每轮上限、限速与重试、调度、增量 watermark、分P定位、跨阶段持久恢复。已实现；**WBI 签名关键词搜索已用 `"AI"` 真实烟测两页各 20 条**，但完整定时发现、分P批量、凭证恢复未验证；不得把测试替身结果或接口契约写成“完整关键词发现全流程已通过”。
11. **联网授权与启用是两项独立决定。** `enabled`（参与调度）与 `network_authorized`（允许本机发真实请求）默认都为 false；调度器只处理两者同时为真的来源。未来不要为了“自动化”把默认值改成 true，也不要让一次勾选同时代表启用与联网。

需求约束：保留六张核心表语义；默认本地、单用户、单工作区；默认云调用预算为零；不依赖 DSH；不自动采集、转写或发起付费请求。不得降低事实发布门槛来让演示“看起来有结果”。

## 3. 当前真实交付状态

| 范围 | 当前实现 | 限制 |
|---|---|---|
| 应用账号 | 一次性初始化、密码登录、服务端会话、退出、修改密码、个人名称/时区、注销指定会话、注销其他全部并轮换当前会话 | 密码恢复按用户决定暂时完全推迟到发布前；语言偏好及完整安全验收未完成 |
| 手动导入 | 文本/Markdown/SRT/VTT、授权确认、版本与片段、任务入队、Idempotency-Key、内容修改后新键重试 | 单文件/粘贴导入作为兜底；批量 20 文件按用户决定无限期推后；任意真实材料不做 Mock 伪分析 |
| Mock处理 | 6份虚构Plugin X材料、主张、实体、引用、动态修订、核查记录 | 只识别合成语法，不是通用AI抽取器 |
| 动态与阅读 | 首页游标分页、全部/未读/收藏、主题/实体范围与 7/30/全部时间筛选、实体/来源/证据抽屉、关注、收藏、已读、日报 | `sort=changed`、部分类型/分歧筛选及完整可深链接路由未完成 |
| 主题管理 | 主题/实体创建与 If-Match 编辑、父子主题、实体归属、环冲突 409、主题/实体详情与相关动态、删除关系 | 合并/拆分、传递闭包展示、批量维护未完成 |
| 日报 | 当日模板日报、Markdown下载、统一发布门槛、关键词发现运行/入队候选/当日采集来源摘要 | 多日期/范围、持久日报及完整修订分区未完成；真实发现运行数据未验证 |
| 任务 | PostgreSQL持久队列、并发领取、租约/fence、取消、重试、尝试耗尽、任务详情与历史；`jobs.checkpoint` 支持跨阶段恢复；发现任务被取消/耗尽时清理运行账本；凭证过期/风控后显式恢复被阻塞采集；`blocked_budget` 任务可直接重试；模型抽取后按路由执行一次表达复核与一次实体候选 | `process_source` 的本地 Mock 分支仍单事务持锁；模型分支每次阶段都“预留→无事务 HTTP→结算→fence 写入”；`collect_source` 与 `discover_keywords` 已用短事务+心跳+fence；仍是单进程调度，没有多节点调度；M3 只有单库预算预留，没有跨进程配额；复核阶段预算不足不自动重试 |
| 核查 | **M4 Phase 1 + Phase 2 第一部分**：库内事实材料（`fact_supported_by/refuted_by/context_by`，proposed）、独立 `fact_check` 任务（`kb_only`，无外部检索）、E0–E3 规则分档、追加型核查记录、人工接受/拒绝（输入指纹+revision+材料快照校验）、重要事实 E3 硬门槛、标题/正文读取门槛与发布预览；**Phase 2** 增加 `fact_check` 仅建议模型路由（只读材料元数据、预算预留/结算、拒绝外部读取自述、不改变分档/状态/门槛）、来源版本被取代封顶 E1、断章取义确定性拦截、`scoped_fact` 必须人工接受、AC32–45 阻断数据集（48 案例 + 12 对照）；`evidence_review` 语义复核与 `entity_disambiguation` 实体候选仍可用 | **没有真实外部检索、真实模型事实核查或真实烟测**；来源画像只返回 `unknown`；纠错表达式生成、`claim_comparison` 路由、批量复核与 M4 整体签收未完成；`insufficient` 不等于虚假 |
| 模型/平台 | **M3 Phase 1**：供应商连接（openai / openai_compatible / local / mock）、密钥 Fernet 密封与掩码、连接测试；模型配置（真实 ID、等级、限制、结构化输出、单价/币种，默认停用）；8 任务路由（模式/启用/主模型 + ≤3 备用/上限/超时/尝试次数/数据策略）、整组原子保存与回退；调用前最坏费用预留 + 调用后结算、`unknown` 保守计费；调用与费用账本、`/costs/summary`、预算设置；模型管理四页签。**M3 Phase 2**：`claim_extraction` 路由接入 `process_source`；有界片段提示；服务端核对 `segment_id/quote/偏移/哈希`；无锚点主张存 `candidate` 不发布；任务结果记录 `validated/candidates/rejected/anchors/cost`。**M3 收尾**：`evidence_review` 有界语义复核、`entity_disambiguation` 仅限已有实体、候选 `proposed` 关系与人工接受/拒绝、`claim_expression_reviews` 追加记录。**M4 Phase 2**：`fact_check` 路由接入事实核查任务，仅作建议 | **真实模型协议与真实抽取/复核/实体消歧/事实核查均未验证**；`process_source` 合成示例仍为本地 Mock；测试调用不写知识库；无跨进程配额/多节点预算；无 Anthropic/Gemini 原生协议；费用未经供应商账单确认 |
| B站单链接采集 | **Phase 1**：单 BV 链接导入、公开元数据、账号能力检查、字幕清单/正文（需本机 SESSDATA 或扫码登录）、缺字幕与需要登录分别标记；凭证 Fernet 加密；网页扫码登录（内存会话、本机渲染二维码） | **真实元数据/字幕已通过短时烟测（2026-10-20，6 片段）**；**真实扫码只验证到生成/轮询，未完成扫码**；b23.tv 短链/av 号/番剧/合集明确不支持；扫码不是官方 OAuth |
| 关键词来源 | **Phase 2 + 收尾 + WBI 签名**：`discovery_sources` 配置（关键词 1–5、排除词 ≤20、语言、any/all、每轮 ≤20 条 / ≤3 页 / ≤4 请求、重扫窗口、定时间隔）、两个独立开关（enabled / network_authorized）、`discovery_runs` 账本与 `discovery_run_items` 逐候选决定、本地再过滤、增量 watermark、失败退避、worker 内短事务调度、分P定位、跨阶段 checkpoint；**收尾新增**分P批量（`confirmed: true`、≤20）、发现运行/入队候选接入日报、明细默认保留 30 天；**WBI 签名**（OE 表 + `_enc_wbi` + buvid 设备指纹）| **真实搜索通过（2026-10-20）**：WBI 签名 + buvid + SESSDATA，关键词 `"AI"` 两页各 20 条；分P批量、凭证恢复、定时调度未真实烟测；不做 UP 主遍历/合集展开；运行账本不清理，只清理明细 |
| API | 统一错误 envelope、`X-Request-Id`、428/409 If-Match、不透明游标、导入幂等键、主题/节点/关系端点、平台账号/B站导入/扫码登录端点、关键词来源与运行记录端点、分P单P/批量端点、凭证恢复端点、模型供应商/模型/任务路由/测试调用/调用记录/费用汇总/预算端点、模型实体候选复核端点、事实材料/核查任务/核查记录/人工复核/核查能力/来源画像/GW发布预览端点；OpenAPI **70 路径（0.10.0）** | 仍缺部分需求 12.2/12.4 的端点（feedback、digests 范围、来源画像评估、reliability-assessments、claim_comparison、批量复核等） |
| 运行 | Windows项目内PostgreSQL、独立API/worker/web、worker 内调度 tick、双击启动/停止、PID归属校验、就绪检查 | Docker配置未实测；不是生产部署；PostgreSQL不能以管理员令牌运行；调度未做多进程验证 |

### 合成示例的正确预期

- 6份来源，6条主张。
- 两份相同的界面观点归为1条动态，增加引用及revision，而非产生重复动态。
- 1份批评形成另一条动态，批评被保留。
- 3条重要功能/测试主张缺乏事实证据，被发布门槛暂缓。
- 来源独立性显示未知，不因为作者不同就假定独立。
- 普通导入材料原文保存后进入待复核，不能说已经完成真实AI分析。
- 关键词来源在测试中使用替身，产出的来源带“合成示例”标题与 `metadata.synthetic=true`；真实运行时不会自动改写为成功。

## 4. 先读哪些文件

建议阅读顺序：

1. 本文件。
2. `AI多来源信息聚合平台_开发需求文档.md`：特别是5、6、7、11、12、15、17节。
3. `docs/ACCEPTANCE.md`：已测内容与未完成项。
4. `docs/DATA_DICTIONARY.md`、`docs/adr/0001-architecture.md`、`docs/adr/0002-collector-contract.md`、`docs/adr/0003-discovery-pipeline.md`、`docs/adr/0004-model-gateway-budget.md`、`docs/adr/0005-fact-verification.md`。
5. `README.md`、`docs/DEVELOPMENT.md`、`docs/PROGRESS.md`。
6. 下表的实际实现文件。代码与说明冲突时，先记录差异、修复或修正文档，不能扩大完成声明。

| 文件 | 用途 |
|---|---|
| `apps/api/app/main.py` | 主要API、错误/游标/If-Match契约、导入、读时发布检查（标题+正文）、日报、个人资料、关键词来源与分P端点、模型实体候选复核端点、事实证明材料/核查任务/核查记录/人工复核/核查能力/来源画像/发布预览端点、OpenAPI覆盖 |
| `apps/api/app/auth.py` | Argon2密码、会话哈希、CSRF及认证 |
| `apps/api/app/config.py` | 本地环境配置 |
| `apps/api/app/storage/db.py` | SQLAlchemy连接（5秒连接超时）与参数化查询 |
| `apps/api/app/domain/evidence.py` | 文本/字幕解析、Unicode偏移与引用校验 |
| `apps/api/app/domain/policy.py` | PublicationPolicy + `mode_is_within`；发布资格必须继续由服务端控制，已存储的更弱表达可展示、更强表达必须隐藏 |
| `apps/api/app/domain/fact_evidence.py` | M4 Phase 1 事实材料载荷：逐字引文/服务端偏移与哈希、受控枚举、条件与透明度、来源角色说明 |
| `apps/api/app/domain/verification.py` | M4 E0–E3 分档、状态推导、接受门槛、复查期限与材料快照哈希；Phase 2 增加来源版本被取代封顶 E1、保留期到期 E0、`stale_*`/`dispute_balance`（纯函数，不联网/不调用模型） |
| `apps/api/app/domain/discovery.py` | 关键词配置校验、匹配/排除、每轮上限、watermark 合并与退避（纯函数） |
| `apps/api/app/ai/gateway.py` | M1 合成语法 `AIGateway.extract` + M3 路由解析、数据策略门、主→备回退、预算预留/结算、调用账本、连接测试 |
| `apps/api/app/ai/providers.py` | 四种模型协议适配（openai/openai_compatible/local/mock）、端点校验、错误映射、不跟随重定向、响应上限 |
| `apps/api/app/ai/budget.py` | Decimal 计价、最坏费用预留、结算/释放、UTC 日/月预算、并发锁 |
| `apps/api/app/ai/extraction.py` | M3 Phase 2 有界主张抽取提示、服务端 anchor/偏移/哈希校验、候选与拒绝原因 |
| `apps/api/app/ai/review.py` | M3 收尾有界表达语义复核提示、受控 verdict/理由/限制校验，不判断事实真假 |
| `apps/api/app/ai/entities.py` | M3 收尾实体候选提示与校验 + M4 Phase 2 补充：允许空实体库调用、`new_entities` 提案与校验；链接已有 node_id 拒绝编造/重复/超限，不自动合并节点 |
| `tests/test_m4_entities.py` | M4 Phase 2 补充：模型自动建实体、按实体分组动态、同名复用、无实体逐条（隔离 schema，`httpx.MockTransport`） |
| `docs/ROADMAP.md` | 用户最新产品方向与待办：每日定时检索与整合（未实现）、实体/主题自动创建、动态分组与安全边界 |
| `apps/api/app/ai/factcheck.py` | M4 Phase 2 `fact_check` 仅建议路由：有界材料元数据提示、受控 verdict/引用校验、拒绝外部读取自述、忽略置信度、与规则分档一致性说明 |
| `apps/api/app/domain/model_secrets.py` | 模型 API 密钥的独立 Fernet 密封、校验与掩码 |
| `apps/api/app/jobs/worker.py` | 领取、短事务、checkpoint、fence、租约、取消/重试、关系写入、聚合、核查记录；`collect()` 分阶段采集 + `LeaseKeeper`；`discover()` 分页发现与入队；`_process_with_model` 抽取 → 表达复核 → 实体候选 → fenced 写入/发布；`fact_check()` 只读库内材料、追加 pending 核查记录（不调用模型） |
| `apps/api/app/jobs/scheduler.py` | 到期来源的短事务调度（只写行、不联网），`FISHINGNET_SCHEDULER`/`FISHINGNET_SCHEDULER_TICK` |
| `apps/api/app/collectors/` | `base.py` 契约与错误码（含 DiscoveryQuery/Item/Result、checkpoint 序列化）、`bilibili.py` 适配器（URL/元数据/字幕/账号/搜索/限速/预算）、`fake.py` 测试替身、`__init__.py` 注册表 |
| `apps/api/app/domain/secrets.py` | SESSDATA 解析、Fernet 密封/解密、主密钥缺失与不可解密错误 |
| `apps/api/app/domain/qr_sessions.py` | 扫码登录短期会话（内存、TTL/冷却/轮询上限），`qrcode_key` 不外泄 |
| `tests/test_discovery_rules.py` | 关键词规则、上限、watermark、退避、搜索解析/拒绝映射、语言选择、checkpoint 往返（不触网、不连库） |
| `tests/test_discovery_flow.py` | 关键词来源 CRUD、授权门、发现→入队、增量复制/刷新、排除/上限、显式分P、语言不匹配、fence、跨阶段恢复、尝试耗尽/取消清理、调度 |
| `tests/test_bilibili_flow.py` | 单链接导入→采集→缺字幕/需登录/风控/fence 的隔离 schema 集成测试 |
| `tests/test_collectors.py` | 契约、URL 解析、上游错误映射、字幕分段、凭证加密（不触网） |
| `tests/test_qr_login.py` | 扫码状态机、凭证密封、取消/断开/过期/限流（隔离 schema，测试替身） |
| `tests/test_m2_wrapup.py` | M2 收尾：分P批量确认与整批失败、凭证过期恢复、风控冷却、日报联动、明细保留（隔离 schema，不触网） |
| `tests/test_ai_gateway.py` | M3 单元：计价/预算、用量归一化、密钥密封与掩码、端点校验、请求构造、错误映射、超时、非法载荷、Mock 不触网（不连库、不触网） |
| `tests/test_m3_ai_flow.py` | M3 集成：供应商/模型/路由 CRUD、If-Match、云端确认、零预算阻断、预算并发、结算、未知用量、回退、费用汇总（隔离 schema，`httpx.MockTransport`） |
| `tests/test_m3_extraction.py` | M3 Phase 2 提取单元/集成：提示边界、锚点偏移/哈希、拒绝原因、候选不发布、零预算阻断与重试、fence（`httpx.MockTransport`） |
| `tests/test_m3_wrapup.py` | M3 收尾单元/集成：表达复核支持/部分/不支持/缺失/失败/预算、实体候选提议/接受/拒绝/编造节点/失败（`httpx.MockTransport`） |
| `tests/test_m4_verification.py` | M4 Phase 1 单元/集成：事实材料锚点/载荷/角色说明/上限与去重、E0–E3 与状态矩阵、accept gate、快照哈希、过期输入、接受/拒绝与记录只追加、发布门槛隐藏与放行、队列筛选、能力声明（无网络、无模型） |
| `tests/test_m4_ac_dataset.py` | M4 Phase 2：AC32–45 阻断数据集运行器（60 案例 + 8 断章取义表达案例 + 18 项单元/集成），含版本过期、截断引用、建议路由预算/失败/空材料跳过、纠错后读取面联动（全部合成，`httpx.MockTransport`） |
| `tests/m4_support.py` | M4 数据集测试的隔离 schema 辅助（seed、材料、更新、复核、模型路由与 MockTransport） |
| `tools/build_ac_dataset.py` | 生成 `tests/fixtures/ac32-45.json` 的离线脚本（不联网、不读写数据库） |
| `docs/AC32-45-dataset.md` | AC32–45 阻断数据集的构成、运行方式、AC 覆盖与未证明边界 |
| `docs/bilibili-api-policy.md` | B站 接口与政策核对记录：URL、日期、证据等级、限制、关键词发现边界、真实烟测执行状态 |
| `apps/web/src/main.tsx` | React界面（仍是大单文件）：分页、导入、会话、错误恢复、关键词来源面板、模型管理与预算/费用面板、表达复核与实体候选复核、库内事实核查面板（材料/记录/接受拒绝/发起核查） |
| `apps/web/src/style.css` | 中文桌面/移动端样式 |
| `migrations/versions/0001_baseline.*`、`0002_integrity.*`、`0003_platform_connections.*`、`0004_discovery.*`、`0005_recovery_retention.*`、`0006_ai_gateway.*`、`0007_m3_wrapup.*`、`0008_verification_loop.*` | 六表、M1支撑表、关系/证据约束、平台连接、关键词发现配置/运行/决定与 checkpoint、恢复状态与发现明细保留索引、模型供应商/模型/任务路由/调用/预算账本、表达语义复核与 proposed 关系索引、事实证据载荷/锚点与核查记录约束；当前head=**0008** |
| `tests/test_policy.py` | 发布门槛和片段校验测试 |
| `tests/test_integration.py` | 独立PostgreSQL schema的集成与队列回归 |
| `tests/fixtures/plugin-x.json` | 6份合成材料；不是完整评测集 |
| `docs/openapi.json` | 当前API契约（70路径，版本0.11.0） |
| `启动FishingNet.cmd` / `停止FishingNet.cmd` | 双击启动/停止 |
| `infra/launch.ps1` / `infra/start.ps1` / `infra/stop.ps1` | 就绪检查、进程归属、启动/停止编排 |
| `infra/runtime.ps1` | PID JSON记录、归属校验、端口/日志辅助函数 |
| `infra/init-local.py` / `infra/stop-local.py` | 项目内PostgreSQL初始化/启动/停止（提升shell下用基本用户令牌） |
| `infra/qa-preview.py` | 隔离QA实例；已修复`sys.path`，仅用于开发验证 |

## 5. 本机环境与启动方式

已使用环境：Windows + PowerShell（Windows PowerShell 5.1），Node.js 24.16.0、npm 11.13.0、Python 3.12.14、PostgreSQL 17。优先复用项目现有 `.venv` 和依赖，不要仅为了交接重新下载安装。`npm` 在本机 PowerShell 可能被执行策略拦截，验证时用 `npm.cmd`。

| 服务 | 地址/位置 |
|---|---|
| 正式前端 | `http://127.0.0.1:5173` |
| 后端 | `http://127.0.0.1:8000` |
| API文档 | `http://127.0.0.1:8000/api/docs` |
| PostgreSQL | `127.0.0.1:55432`，数据库名 `fishingnet` |
| PostgreSQL二进制 | `.runtime/pgsql/bin` |
| 数据库数据 | `.runtime/pgdata`；**不可删除或覆盖** |
| 敏感配置 | `.env`；**不可提交或打印内容** |
| 运行日志、PID | `.runtime/` |
| 临时QA地址 | `http://127.0.0.1:8001`，仅运行QA脚本时存在 |

用户启动：双击 `启动FishingNet.cmd`；停止：双击 `停止FishingNet.cmd`。

开发检查（项目根目录）：

```powershell
# 启动或复用数据库，不启动 Web 服务
.venv\Scripts\python.exe infra\init-local.py

# 运行后端测试：需要数据库已启动
.venv\Scripts\python.exe -m pytest -q

# 检查类型并构建前端
npm.cmd --prefix apps/web run build

# 启动并检查服务，不打开浏览器
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File infra/launch.ps1 -NoBrowser

# 停止正式本地服务
powershell.exe -NoProfile -ExecutionPolicy Bypass -File infra/stop.ps1
```

**调度开关：** 调度器运行在 worker 进程内，默认开启；`FISHINGNET_SCHEDULER=0` 关闭自动调度（手动“立即运行”仍可用），`FISHINGNET_SCHEDULER_TICK` 调整 tick 间隔（最小 5 秒）。它只处理 `enabled AND network_authorized` 的来源，因此默认库不会有任何定时网络请求。

**PostgreSQL 运行约束：** PostgreSQL 拒绝以管理员权限运行。当前 `init-local.py` 在检测到提升 shell 时通过 `runas /trustlevel:0x20000` 启动 `pg_ctl`；不要改用管理员直接启动 `postgres.exe`，否则会出现共享内存 `Permission denied` 或无法停止。停止脚本会校验 `postmaster.pid` 的数据目录和进程镜像，再走同一基本用户路径。

**PowerShell 文件编码：** Windows PowerShell 5.1 读取 UTF-8 脚本需要 BOM。`infra/runtime.ps1`、`start.ps1`、`stop.ps1`、`launch.ps1` 均保留 UTF-8 BOM；用 `Set-Content`/`Out-File` 改写后必须重新加 BOM，否则中文会变成乱码并导致解析错误。

**正式工作区状态：** 已存在用户账号和数据；不要再次初始化、重置密码或删除 pgdata。接手后先查询 `/api/v1/auth/status` 和只读计数，不要把“已有用户”误判为空库。本轮迁移后正式库仍为 1 用户 / 6 来源 / 6 任务。

**测试与数据库：** 测试需要 PostgreSQL 正在运行；先执行停止入口再跑测试会得到大量连接错误（不是测试失败）。测试自动建立并删除独立 schema，不改变正式工作区。

QA 脚本建立独立 schema 并使用仅用于合成测试的账号；该账号不能作为正式登录方式。该脚本没有完善的自动清理，检查遗留进程/schema 时须确认归属，不能清空整个数据库。

## 6. 本轮实际验证（2026-10-20，M4 Phase 1）

- 基线复现（改动前）：`init-local.py` 复用本地 PostgreSQL；`pytest -q` **275 passed / 93 warnings（34.35s）**；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0007 (head)**；正式库 1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 路由 / 1 调用 / 1 预算流水 / 6 条既有 insufficient 核查记录。
- 迁移：新增 `0008_verification_loop`（事实证据载荷/锚点约束触发器、事实证据状态约束、`verification_records` review/checker/trigger 约束与索引、`claims.fact_check_status` 索引）；`alembic current` = **0008 (head)**；未改 0001–0007、未删库、未覆盖 `.env`。
- 后端测试：**299 passed / 93 warnings（43.84s）**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_m4_verification.py` 24 项（12 单元 + 12 集成），**无网络、无模型调用**；既有 275 项无回归。
- 前端：`tsc -b && vite build` 通过；新增主张详情事实核查面板与工作台发起入口；无新增依赖。
- API：`docs/openapi.json` 重导出 **70 路径 / 0.9.0**；新增 7 条路径。`/claims/{id}`、`/verification-queue`、`/capabilities` 扩展。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”，`/api/v1/health/ready` ok/ok，5173 返回 200，OpenAPI **0.9.0 / 70 路径**；`POST /claims/{id}/evidence-links`、`POST /claims/{id}/fact-checks`、`POST /updates/{id}/publication-preview` 未登录 401，GET 端点（`/verification-records/{id}`、`/verification-capabilities`、`/sources/{id}/reliability`、`/capabilities`、`/claims/{id}`）未登录 401；`api.error.log` 仅 Uvicorn 启动 INFO，`worker.error.log` 与 `web.error.log` 均 0 字节；`stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- 正式库只读计数：**1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**；`verification_records` 仍为既有 6 行、`fact_*` 关系 0 行。
- 真实网络：自动测试**没有任何真实外部检索或模型请求**；本轮没有新增 B站 请求。真实模型连接测试、B站 元数据/字幕与 WBI 搜索沿用 2026-10-20 的历史记录。

**未验证/未执行：真实事实核查（含外部检索与模型核查）、真实模型抽取/语义复核/实体消歧、`claim_comparison`/`fact_check` 模型路由、来源画像与纠错传播、AC32–45 专项数据集与整体签收、真实 B站 扫码确认/分P批量/凭证恢复/定时调度、本地转写、多进程/多节点调度、跨进程并发预算、Docker 启动、375px/1280px 完整自动 UI 覆盖、新面板浏览器点击实测、完整安全测试、生产规模性能、备份恢复、断电级恢复演练。不得把 299 项测试解释为 AC01–AC45 全部通过，也不得称为真实事实核查/真实抽取/复核已完成、真实 B站 采集全流程或完整 MVP 完成。**

### 上一轮（2026-10-20，M3 收尾）

- 基线复现（改动前）：`init-local.py` 复用本地 PostgreSQL；`pytest -q` **255 passed / 93 warnings（34.85s）**；`npm.cmd --prefix apps/web run build` 通过；已有实例 `/api/v1/health/ready` ok/ok、5173 返回 200、OpenAPI 0.7.0 / 62 路径；正式库 1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 路由 / 1 调用 / 1 预算流水。
- 迁移：新增 `0007_m3_wrapup`（`claim_expression_reviews` + proposed 关系索引）；`alembic current` = **0007 (head)**；未改 0001–0006、未删库、未覆盖 `.env`。
- 后端测试：**275 passed / 93 warnings（61.43s）**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_m3_wrapup.py` 20 项（10 单元 + 10 集成），网络全部为 `httpx.MockTransport`，**没有真实模型请求**。
- 前端：`tsc -b && vite build` 通过；无新增前端/Python 依赖。
- API：`docs/openapi.json` 重导出 **63 路径 / 0.8.0**；新增 `POST /relations/{relation_id}/review`。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”，`/api/v1/health/ready` ok/ok，5173 返回 200，OpenAPI **0.8.0 / 63 路径**，`/relations/{id}/review`、`/claims/{id}`、`/capabilities`、`/task-routes` 未登录均 401，web/worker 错误日志 0 字节；`stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- 正式库只读计数：**1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**；`claim_expression_reviews` 0 行、proposed 关系 0 行。
- 真实网络：自动测试**没有任何真实模型请求**；本轮没有新增 B站 请求。真实模型连接测试、B站 元数据/字幕与 WBI 搜索沿用 2026-10-20 的历史记录。

**未验证/未执行：真实模型抽取、真实语义复核、真实实体消歧、事实核查、claim_comparison/fact_check 接入、真实 B站 扫码确认/分P批量/凭证恢复/定时调度、本地转写、多进程/多节点调度、跨进程并发预算、Docker 启动、375px/1280px 完整自动 UI 覆盖、完整安全测试、生产规模性能、备份恢复、断电级恢复演练。不得把 275 项测试或 1 次连接测试解释为 AC01–AC45 全部通过，也不得称为真实抽取/复核已完成、真实 B站 采集全流程或完整 MVP 完成。**

### 上一轮（2026-10-20，M3 Phase 2）

- 基线复现（改动前）：`init-local.py` 复用本地 PostgreSQL；`pytest -q` **234 passed / 93 warnings（25.45s）**；`npm.cmd --prefix apps/web run build` 通过；`launch.ps1 -NoBrowser` 就绪（`/health/ready` ok/ok、5173 返回 200、OpenAPI 0.6.0 / 62 路径）；`stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- 后端测试：**255 passed / 93 warnings（30.71s）**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_m3_extraction.py` 21 项（11 项无数据库单元 + 10 项集成），网络全部为 `httpx.MockTransport`，**没有真实模型请求**。
- 前端：`tsc -b && vite build` 通过；无新增前端/Python 依赖。
- 迁移：未新增，`alembic current` = **0006 (head)**；未改 0001–0006、未删库、未覆盖 `.env`。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”，`/health/ready` ok/ok，5173 返回 200，OpenAPI **0.7.0 / 62 路径**，`/model-providers`、`/task-routes`、`/ai/invocations`、`/capabilities` 未登录均 401，api/worker/web 错误日志均为 0 字节；`stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- 正式库只读计数（连接测试前）：**1 用户 / 7 来源 / 10 任务**，供应商、模型、路由、调用、流水均为 0。用户完成真实连接测试后：**1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**，来源与任务数未变。
- 真实网络：自动测试**没有任何真实模型请求**；用户在本机另执行 1 次真实连接测试（`openai_compatible` / MIMO 2.6，固定合成提示）：`succeeded`，37 输入 / 26 输出 token，延迟 1625 ms，估算并结算 0.0000445 USD。没有新增 B站 请求；B站 烟测沿用 2026-10-20 16:10 的记录。

**该轮未验证：真实模型抽取、语义复核、事实核查、实体消歧/主张比较接入、真实 B站 扫码确认/分P批量/凭证恢复/定时调度等。**

### 上一轮（2026-10-20，M3 Phase 1）

- 基线复现（改动前）：`init-local.py` 复用本地 PostgreSQL；`pytest -q` **181 passed / 93 warnings（18.36s）**；`npm.cmd --prefix apps/web run build` 通过；`infra/launch.ps1 -NoBrowser` 就绪，`/health/ready` 返回 `{"status":"ok","database":"ok"}`、5173 返回 200、OpenAPI 0.5.0/50 路径；`infra/stop.ps1` 退出码 0，三端口释放。
- 迁移：新增 `0006_ai_gateway`（5 张支撑表 + `budget_currency`/`routing_revision`）；`alembic current` = **0006 (head)**；未改 0001–0005、未删库、未覆盖 `.env`。
- 后端测试：**234 passed / 93 warnings（26.02s）**；新增 `tests/test_ai_gateway.py`（35 项单元）与 `tests/test_m3_ai_flow.py`（18 项集成），网络全部为 `httpx.MockTransport`，**没有真实模型请求**。
- 前端：`tsc -b && vite build` 通过；模型管理四页签、预算设置、今日费用显示；无新增依赖。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 就绪，`/health/ready` ok/ok，5173 返回 200，OpenAPI **0.6.0 / 62 路径**，`/model-providers`、`/task-routes`、`/costs/summary`、`/capabilities` 未登录均 401，`worker.error.log` 为空；`stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- 正式库只读计数：**1 用户 / 7 来源 / 10 任务**；供应商、模型、任务路由、调用、预算流水均为 0（未配置真实供应商）。
- 真实网络：**本轮没有任何真实模型请求**，也没有新增 B站 请求；B站 烟测沿用 2026-10-20 16:10 的记录。

**未验证/未执行：真实模型协议与真实供应商连接、模型抽取写入知识库、真实 B站 扫码确认/分P批量/凭证恢复/定时调度、本地转写、多进程/多节点调度、跨进程并发预算、Docker 启动、375px/1280px 完整自动 UI 覆盖、真实事实核查、完整安全测试、生产规模性能、备份恢复、断电级恢复演练。不得把 234 项测试解释为 AC01–AC45 全部通过，也不得称为真实模型已接入、真实 B站 采集全流程或完整 MVP 完成。**

## 6.1 上一轮实际验证（2026-10-20，M2 收尾）

- 基线复现（改动前）：`init-local.py` 复用本地 PostgreSQL；`python -m pytest -q` **165 passed**；`npm.cmd --prefix apps/web run build` 通过；已在运行的实例 `/health/ready` 返回 `{"status":"ok","database":"ok"}`、5173 返回 200；`infra/stop.ps1` 退出码 0 并释放 8000/5173/55432。
- 迁移：新增 `0005_recovery_retention`（`platform_connections` 的 `risk_control` 状态、`cooldown_until`、`last_success_at` 与 `discovery_runs.finished_at` 索引）。`alembic current` = `0005 (head)`；未改 0001–0004、未删库、未覆盖 `.env`；正式库计数不变：**1 用户 / 6 来源 / 6 任务 / 0 关键词来源 / 0 发现运行**。
- `python -m pytest -q`：**170 passed**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_m2_wrapup.py` 5 项集成测试；全部使用测试替身，没有真实网络请求。
- `npm.cmd --prefix apps/web run build`：TypeScript 与 Vite 构建通过；无新增前端/Python 依赖。
- 启动/停止（改动后复测）：`infra/launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”；`/api/v1/health/ready` 返回 `{"status":"ok","database":"ok"}`，5173 返回 200，`/openapi.json` 版本 0.5.0、**50 路径**，`/api/v1/platforms` 与新的分P批量端点未登录均返回 401，`worker.error.log` 为空；`infra/stop.ps1` 退出码 0，8000/5173/55432 全部释放。工具包装的启动命令因服务进程继承输出句柄而未自行返回，已确认服务就绪后用停止入口结束；不是启动失败。
- OpenAPI：`docs/openapi.json` 重新导出（50 路径 / 0.5.0）；新增 `/sources/{id}/parts/import` 与 `/platforms/{platform}/recover`，`/digests/today` 扩展发现区块。
- 真实网络（用户触发）：**本轮没有任何真实请求**；仍只有 02:13 的二维码生成 + 轮询两次请求（见 ACCEPTANCE）。用户本轮未明确授权联网烟测，元数据/字幕、关键词搜索、扫码确认**均未执行**。

**未验证/未执行：真实 B站 元数据/字幕/登录态请求、真实关键词搜索、真实 App 扫码确认、真实分P批量、真实凭证过期/风控恢复、本地转写、多进程/多节点调度、并发预算、Docker 启动、375px/1280px 完整自动 UI 覆盖、真实模型、完整安全测试、生产规模性能、备份恢复、断电级恢复演练。不得把 170 项测试解释为 AC01–AC45 全部通过，也不得称为真实 B站 采集、真实关键词发现或完整扫码登录通过。**

## 6.2 更早轮次实际验证（2026-10-20，M2 Phase 2）

- 基线复现（改动前）：`init-local.py` 复用本地 PostgreSQL；`python -m pytest -q` **106 passed**；`npm.cmd --prefix apps/web run build` 通过；`infra/launch.ps1 -NoBrowser` 输出“已就绪”（复用现有进程）；`infra/stop.ps1` 退出码 0、三个端口释放。同时确认 `/capabilities` 的 `live_collection_verified=false`，没有把测试替身当真实采集。
- 迁移：新增 `0004_discovery`（三张表 + `jobs.checkpoint` + `sources.discovery_source_id`），`alembic current` 为 `0004 (head)`；未改 0001–0003、未删库、未覆盖 `.env`；正式库计数不变（1 用户 / 6 来源 / 6 任务 / 0 关键词来源）。
- `python -m pytest -q`：**165 passed**（真实 PostgreSQL 17 隔离 schema）。新增 40 项关键词规则/适配器单元测试（`tests/test_discovery_rules.py`，不触网）与 19 项发现流程集成测试（`tests/test_discovery_flow.py`），覆盖配置边界、授权门、发现入队、增量复制/刷新、排除词与每轮上限、显式分P、语言不匹配、fence 丢失零写入、跨阶段 checkpoint 恢复、尝试耗尽/取消清理、调度只处理已授权来源。全部使用测试替身。
- `npm.cmd --prefix apps/web run build`：TypeScript 与 Vite 构建通过；无新增前端/Python 依赖。
- 启动/停止（改动后复测）：`infra/launch.ps1 -NoBrowser` 脚本输出“FishingNet 已就绪”，`/api/v1/health/ready` 返回 `{"status":"ok","database":"ok"}`，5173 返回 200，`/api/v1/discovery-sources` 未登录返回 401，`/openapi.json` 版本 0.4.0、48 路径，worker 错误日志为空；`infra/stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- OpenAPI：`docs/openapi.json` 由 `.runtime/export-openapi.py` 重新导出（48 路径 / 0.4.0）；新增 `/discovery-sources`、`/discovery-sources/{id}`、`/discovery-sources/{id}/run`、`/discovery-sources/{id}/runs`、`/discovery-runs/{id}`、`/sources/{id}/parts/{page_no}/import`。
- 真实网络（用户触发）：仍只有 02:13 的二维码生成 + 轮询两次请求（见 ACCEPTANCE）；**本轮没有任何真实请求**。

**未验证/未执行：真实关键词搜索、真实 B站 元数据/字幕/登录态请求、真实 App 扫码确认、真实账号连接、分P批量、本地转写、多进程/多节点调度、并发预算、Docker 启动、375px/1280px 完整自动 UI 覆盖、真实模型、完整安全测试、生产规模性能、备份恢复、断电级恢复演练。不得将 164 项测试解释为 AC01–AC45 全部通过，也不得称为真实 B站 采集、真实关键词发现或完整扫码登录通过。**

> 历史记录（M1.1，2026-10-20 早先轮次）：曾发现既有 PostgreSQL 由旧沙箱令牌启动，新连接报共享内存 `Permission denied`，`pg_ctl stop` 无法发送信号；用基本用户信任级别重启后恢复，未删除 `pgdata`。该环境问题仍可能复现，按第 5、7 节处理。

## 7. 接手时必须注意的实现缺口

1. **Git尚无已提交基线。** 当前源文件可能仍全部未跟踪；不要执行 `git clean`、`reset --hard` 或重建目录。先检查用户新增修改；如建立初始提交，先检查暂存区，确保不包含 `.env`、`.runtime`、数据库和缓存。
2. **迁移只能向前追加。** 0001–0006 已用于现有库，不能回头改 SQL 文件然后要求删库重来。后续约束、清理和数据修复通过 0007 及以后实施。启动脚本会自动执行 `alembic upgrade head`。
3. **PID文件不等于进程归属。** 本轮已用 JSON 记录 PID、启动时间、命令标记和项目根目录，并在停止前校验；仍需覆盖更多场景（服务崩溃、端口被占、连续双击、用户手动启动同名进程）。不要退回只按 PID 存在判断。
4. **API契约已覆盖主要列表，但仍有缺口。** `topic_id/entity_id/since/until/update_type` 已支持，`sort=changed`、relations 审查、feedback、digests 范围等仍未实现；新增接口必须继续使用统一错误 envelope、request_id、If-Match 和游标规则，并同步 OpenAPI。
5. **前端仍使用hash页面导航。** 详情是抽屉，不是 `/updates/:id` 深链接；主题管理已可在抽屉内完成，关键词来源面板本轮只有生产构建与接口回归，**没有浏览器实测**。修改列表渲染时要保留“切换页面先清空数据”的约束。
6. **worker可靠性只验证了单进程与有界网络调用。** `collect_source` 与 `discover_keywords` 都已把网络调用移出事务、用心跳续租并以 fence 守卫写入，且用 `jobs.checkpoint` 做跨阶段恢复；M3 网关也把预算预留、HTTP 调用与结算拆成三段短事务。但仍没有多进程/多节点调度、没有跨进程并发配额，scheduler 运行在 worker 进程内。运行中取消可能需要等阶段边界；不要为了“长任务”把网络调用放回持锁事务。
7. **删除是逻辑失效，不是物理清除。** 不可变版本触发器当前禁止修改/删除。关键词来源删除是 `deleted_at`；`discovery_runs` 永久保留，`discovery_run_items` 默认只保留 30 天并由 worker 有界清理。**不要**为此加会破坏运行账本的级联删除，也不要让清理触及 `queued/running` 运行或来源 watermark。
8. **发布策略仍不完整，但 M4 Phase 1 已把事实材料与人工复核接进门槛。** 当前策略按传入的 claim + `current_verification_id` 记录评估；`visible_update` 同时约束标题与正文，并用 `mode_is_within` 防止已存储句子被升级。仍缺来源画像、纠错传播、日报/导出/搜索的统一重算与 AC32–45 专项签收；任何新的“接受/确认”入口都必须先补验证，不能把编辑 assessment 或 checker_type 当作确认证据。
9. **任意真实材料不进入Mock自动分析。** 不要把“待复核”误判为导入失败，也不要把“发现入队”误当成“已经采集/分析完成”。
10. **M0文档及数据集仍不完整。** 6份样例不足以替代30来源、100主张及事实核查专项集。锁文件与依赖许可证库存存在，但生产复核尚未做完。
11. **PostgreSQL 权限与编码是环境级故障点。** 不要把管理员令牌启动失败误判为数据库损坏；不要删除 pgdata。PowerShell 中文脚本必须保持 UTF-8 BOM。
12. **OpenAPI 由应用内 `_openapi_document()` 生成。** 修改接口后重跑导出脚本；不要手改 `docs/openapi.json` 与代码脱节。当前为 **70 路径 / 0.11.0**（M4 Phase 2 补充未新增端点，更新了实体消歧能力声明）。
13. **B站 真实烟测的边界。** 2026-10-20 已通过 `BV1MLJe6fEmH` 元数据/字幕（6 片段）与 WBI 关键词搜索（`"AI"` 两页各 20 条），但**真实扫码确认、分P批量、凭证过期/风控恢复、定时调度运行仍未验证**。`collectors/fake.py` 的输出带“合成示例”字样，只能用于测试；不得把替身结果、接口契约或本机静态核对写成“已接入 B站”或“真实采集/真实发现通过”。真实调用前必须读 `docs/bilibili-api-policy.md`，并在用户本机授权后低频执行、记录日期/结果/错误码。**扫码登录接口在 `passport.bilibili.com`**，误用 `api.bilibili.com` 会得到 HTTP 404（2026-10-20 实际发生过，已修并加回归测试）；元数据/字幕/搜索仍在 `api.bilibili.com`。
14. **平台凭证只存 SESSDATA，依赖 `MASTER_KEY`。** 更换主密钥会让既有密文不可解密（API 返回 `CREDENTIAL_UNREADABLE`，需要用户重新提供）；不要静默清空凭证或绕过加密。凭证不得进入日志、错误信息、聊天或模型请求。
15. **扫码登录会话只在 API 进程内存。** `app/domain/qr_sessions.py` 保存 `qrcode_key`（180 秒、最多 60 次轮询、生成冷却 10 秒、同工作区单会话）；API 重启或多进程部署时待处理二维码会失效。这是有意的取舍：`qrcode_key` 可换取登录 Cookie，**不得**为了“持久化”把它写进数据库、日志或前端。若将来需要多进程，必须单独设计加密的短期存储并补验收。
16. **二维码必须本机渲染。** 前端使用 `qrcode.react`（ISC）生成 SVG；禁止改成第三方二维码图片服务，否则会把登录 URL 发给外部。
17. **关键词搜索使用标准 WBI 签名（OE 表 + `_enc_wbi`），与字幕适配器相同；不使用 WBI2、浏览器伪装或设备激活。** 2026-10-20 用户授权扩展。buvid 设备指纹通过 `/x/frontend/finger/spi` 获取并缓存（搜索必需，缺失返回 HTTP 412）。风控/限流如实停止，不绕过。
18. **`enabled` 与 `network_authorized` 必须保持分离且默认 false。** 任何批量启用、自动授权或把两者合并的改动都会让真实请求在用户不知情时发生，属于破坏第 2 节第 11 条。
19. **发现任务的去重权威是 `sources` 唯一键，不是 watermark。** 修改 `_ensure_discovered_source`/`plan_candidates` 时保持“pending 或最近检查过=duplicate，超窗口=refreshed”，否则会产生重复来源或重复采集任务。
20. **checkpoint 写入也必须带 fence。** 任何新增阶段都必须通过 `_checkpoint()` 持久化，并在该阶段已完成时跳过网络调用；不要在 checkpoint 中存凭证、原始响应或未脱敏 URL。
21. **分P批量必须有用户确认。** `POST /sources/{id}/parts/import` 要求 `confirmed: true`，一次 ≤20 个分P；不要在客户端默认勾选、自动确认或绕过服务端校验。每个分P会真实排队独立采集任务，扩大上限前先评估请求预算与政策边界；任一所选分P不存在必须整批拒绝。
22. **凭证/风控恢复不是自动重试。** `POST /platforms/{platform}/recover` 只重新排队被阻塞的采集任务；过期凭证必须先替换，风控冷却由服务端强制。不要让它修改 `enabled`/`network_authorized`、换账号、换 IP 或跳过冷却。
23. **日报中的发现结果不是采集完成或事实确认。** 日报同时列出“入队候选”和“本日完成采集的来源”，并保留失败错误码；不得把入队数量写成已分析数量，也不得因日报出现条目而降低发布门槛。
24. **明细保留只删 `discovery_run_items`。** 清理必须限定已结束运行、按保留天数、有界批量执行；`discovery_runs` 计数与来源 watermark 永久保留。不要级联删除运行账本，也不要在 `queued/running` 运行上清理。
25. **模型调用默认关闭是硬约束。** 供应商与模型默认 `enabled=false`、任务路由未配置、日/月预算 0、工作区数据策略 `local_only`；云端路由保存还必须 `confirm_cloud: true`。不要为了“跑通”把默认值改成 true、跳过数据策略或绕过 `confirm_cloud`。`/ai/test-call` 必须 `confirm_spend: true`。
26. **预算必须在调用前预留、调用后结算，HTTP 不得放进事务。** 顺序是：短事务锁工作区行并写 `ai_budget_ledger`（最坏费用）→ 无事务 HTTP → 短事务结算/释放并更新调用行。供应商未返回用量时按预留结算并标记 `unknown`，只能高估不能低估；不要让回退绕过预算或数据策略。已知边界：结算与写库之间崩溃会重试并重复计费（账本如实记录，当前不缓存模型输出）；`needs_review` 任务沿用 M1 不可重试语义，配置路由后需重新导入材料才会走模型抽取。
27. **模型密钥与平台凭证分开。** `model_secrets.py` 使用独立派生标签与 schema，只保存密文和 `****`+末 4 位；密钥不得进入日志、错误信息、调用快照、聊天或测试断言。删除供应商只删除本地配置，不声称撤销平台授权。
28. **模型抽取只能由服务端判定证据。** `app/ai/extraction.py` 只接受请求中出现过的 `segment_id`；`quote` 必须逐字出现在片段可见文本（空白折行归一化后映射回原文），`char_start/char_end/quote_hash` 由服务端按 Unicode code point 计算；模型自报偏移不一致即拒绝。未通过锚点的主张只能保存为 `candidate`，不得写 `evidenced_by`、不得生成动态、不得标为 `validated`。`fact_check_status` 只能由核查流程设置，抽取模型与任务结果都不能改成 `corroborated`。`/ai/test-call` 与 `/model-providers/{id}/test` 仍不写知识库；合成示例（`metadata.demo=true`）不得进入模型路径。
29. **事实证明材料必须先 proposed、再由人工接受。** `POST /claims/{id}/evidence-links` 只创建 `proposed` 关系；服务端重算锚点，`applicability` 缺省必须是 `unknown`（关键条件未知不能算匹配）。不要在接口或 worker 里直接写 `accepted`，也不要让模型创建事实材料；接受只能通过 `POST /verification-records/{id}/review` 并在同一事务里写 `assessment_record_id`。
30. **核查记录只追加，人工结论是新版本。** `verification_records` 的 UPDATE/DELETE 被触发器拒绝；接受/拒绝必须追加带 `supersedes_id` 的版本。复核前必须校验 `expected_input_fingerprint`、claim `revision` 与 `evidence_snapshot.snapshot_hash`，不一致返回 409；同一候选不能复核两次。`fact_check` 任务只写 `pending` 记录，不得修改 `claims.fact_check_status` 或指针。
31. **接受不等于事实确认，门槛不能放宽。** `accept_gate` 必须继续拒绝 E1 corroborated、重要事实 E2、E1 refuted 与“有强支持却判 refuted”；`auto_accept_allowed` 保持 false。`kb_only` 空结果不是反证，`insufficient` 不是虚假；`corroborated` 也不表示外部已查证。不要把“人工点击接受”写成“已核实为真”。
32. **已发布句子不会被自动升级。** `visible_update` 用 `mode_is_within` 只允许更弱或相等表达；接受新记录后旧句子仍是旧模式，需要更强表达必须重新生成并重新过门槛。新增出口（日报、导出、搜索）必须复用同一策略，不能只检查页面正文。

## 8. 已完成：M4 Phase 1 / M3 Phase 2 / M3 Phase 1 / M2 收尾 / M2 Phase 2

### 本轮（M4 Phase 1）

| 顺序 | 工作 | 结果 |
|---|---|---|
| 1 | 基线复现 | 275 passed、前端构建、迁移 0007、启动/停止全部通过；确认正式库 6 条既有核查记录、0 条事实关系 |
| 2 | 迁移 0008 | 事实证据 `fact_evidence.v1` 载荷/锚点约束、accepted 关系必须带评估记录、核查记录 review/checker/trigger 约束与索引 |
| 3 | 材料模块 | `fact_evidence.py`：逐字引文、服务端偏移/哈希、受控枚举、条件未知不匹配、同源角色说明 |
| 4 | 分档与门槛 | `verification.py`：E0–E3、supports/refutes 分档、状态推导、`accept_gate`、复查期限、材料快照哈希 |
| 5 | 核查任务 | `worker.fact_check()`：只读库内材料、`kb_only` 清单、pending 记录、fence 事务、claim 状态不变 |
| 6 | API/前端 | 7 条新路径；主张详情核查面板、工作台发起核查、接受/拒绝理由；0.9.0（70 路径） |
| 7 | 测试/文档 | 299 passed（新增 24 项）；README/PROGRESS/ACCEPTANCE/交接/数据字典/ADR 0005/OpenAPI 更新 |

### 上一轮（M3 Phase 2）

| 顺序 | 工作 | 结果 |
|---|---|---|
| 1 | 基线复现 | 234 passed、前端构建、启动/停止全部通过；确认 `live_model_protocol_verified=false` |
| 2 | 抽取模块 | `app/ai/extraction.py`：有界提示、片段可见范围、受控词表、服务端偏移/哈希、拒绝原因、候选不发布 |
| 3 | 网关 | `invoke_messages` 复用预算预留/结算/账本，`invoke` 保持原签名；阻断错误带 `invocation_id` |
| 4 | worker | `process()` 分派；`_process_with_model` 心跳内调用、校验后单事务 fenced 写入；合成示例仍本地 Mock |
| 5 | 失败与重试 | 零预算 `blocked_budget`（可重试）；策略/网络 `failed`；非法输出/全部无锚点 `needs_review`；fence 丢失零写入 |
| 6 | API/前端 | `model_extraction` 能力声明；0.7.0（62 路径）；动态卡片与任务页标注模型抽取/未事实确认 |
| 7 | 测试/文档 | 255 passed（新增 21 项）；README/PROGRESS/ACCEPTANCE/交接/ADR 0004/OpenAPI 更新 |

### 上一轮（M3 Phase 1）

| 顺序 | 工作 | 结果 |
|---|---|---|
| 1 | 基线复现 | 181 passed、前端构建、启动/停止全部通过；确认 `live_model_protocol_verified=false`（当时尚未实现） |
| 2 | 迁移 0006 | `provider_connections`/`model_profiles`/`task_routes`/`ai_invocations`/`ai_budget_ledger` + `budget_currency`/`routing_revision`；head=0006 |
| 3 | 密钥与协议 | 独立 Fernet 密封+掩码；openai/openai_compatible/local/mock；端点校验、不跟随重定向、错误映射、响应上限 |
| 4 | 路由与回退 | 8 任务、模式/启用/主模型/≤3 备用/上限/超时/尝试次数/数据策略；整组原子保存与 If-Match；云端 `confirm_cloud` |
| 5 | 预算与账本 | 调用前最坏费用预留（行锁）、调用后结算、未知用量保守计费；UTC 日/月、单币种；`/costs/summary` |
| 6 | API/前端 | 12 新路径，62 路径/0.6.0；模型管理四页签、预算设置、任务页费用 |
| 7 | 测试/文档 | 234 passed（新增 53 项）；README/PROGRESS/ACCEPTANCE/交接/数据字典/ADR 0004/OpenAPI |

### 上一轮（M2 收尾）

| 顺序 | 工作 | 结果 |
|---|---|---|
| 1 | 基线复现 | 165 项测试、前端构建、启动/停止全部通过；确认 `live_*_verified=false` |
| 2 | 迁移 0005 | `platform_connections` 增加 `risk_control`/`cooldown_until`/`last_success_at`；`discovery_runs.finished_at` 索引；head=0005 |
| 3 | 分P批量 | `POST /sources/{id}/parts/import`，必须 `confirmed: true`，≤20，整批原子校验，每P独立任务；前端勾选+确认 |
| 4 | 凭证/风控恢复 | 采集错误写入连接状态与冷却；`POST /platforms/{platform}/recover` 只重排被阻塞任务，过期/冷却由服务端强制 |
| 5 | 日报联动 | `/digests/today` 增加发现汇总、入队候选、当日采集来源；Markdown 同步 |
| 6 | 明细保留 | `FISHINGNET_DISCOVERY_ITEM_RETENTION_DAYS`（默认30）；worker 约6小时有界清理；运行账本与 watermark 保留 |
| 7 | API/测试/文档 | 50 路径 / 0.5.0；170 passed；README/PROGRESS/ACCEPTANCE/交接/数据字典/政策/ADR 更新 |

### 上一轮（M2 Phase 2）

| 顺序 | 工作 | 结果 |
|---|---|---|
| 1 | 基线复现 | 106 项测试、前端构建、启动/停止全部通过；确认能力端点仍声明真实采集未验证 |
| 2 | 迁移 0004 | `discovery_sources` / `discovery_runs` / `discovery_run_items` / `jobs.checkpoint` / `sources.discovery_source_id`；head=0004 |
| 3 | 契约与适配器 | `discover()` 契约、checkpoint 序列化、字幕语言偏好与 `LANGUAGE_MISMATCH`、B站搜索结果解析与拒绝映射 |
| 4 | 规则 | 配置边界、关键词匹配、排除词、每轮上限、duplicate/refreshed/deleted、watermark、退避 |
| 5 | 调度 | worker 内短事务调度，`FOR UPDATE SKIP LOCKED`，只处理 `enabled AND network_authorized`，可整体关闭 |
| 6 | 发现任务与恢复 | 分页 checkpoint、单事务计划/入队/账本/watermark、fence 守卫、`claim()` 清理取消与尝试耗尽 |
| 7 | 分P定位 | 元数据 `parts` + `POST /sources/{id}/parts/{p}/import`（单P，不做批量） |
| 8 | API/前端 | 6 条新路径、48 路径 / 0.4.0；“采集与调度”页可操作面板 |
| 9 | 文档 | README、PROGRESS、ACCEPTANCE、交接文档、数据字典、B站政策、ADR 0003、OpenAPI |

**M2 Phase 1 新增（保留，2026-10-20）：** 接口/政策核验、采集器契约、B站适配器（BV 链接、元数据、字幕、登录态、预算/限速/错误映射）、测试替身、迁移 0003、SESSDATA 加密、`collect()` 三阶段短事务 + `LeaseKeeper` + fence、扫码登录（生成/轮询/取消、内存会话、本机渲染）、9 条 API、前端导入页签与平台账号页。

**M1 推迟项（用户已同意，不作为进入 M2 收尾的前置）：**

- 批量导入：20 文件限制、逐文件结果、部分成功、重复提交保护。用户判断当前没有大量本地文档需求，**无限期推后**；手动单文件导入继续作为兜底。
- 个人语言偏好与部署端密码恢复。密码恢复**暂时完全推迟**；它不是当前使用阻塞，但属于生产发布前必须补的安全/运维项，当前没有网页找回。
- 完整前端自动 E2E（375px/1280px、键盘/焦点、错误恢复、深链接）。
- API 12.2/12.4 剩余端点与参数（`sort=changed`、relations 审查、feedback、digests 范围）。

**M2 边界（2026-10-20）：**

- `dsh-bili-scroll` 只作为行为参考：关键词发现 → 读取字幕 → 可选本地转写 → 定时执行 → Markdown 日报。**独立实现，不依赖 DSH，不照搬代码**；未复用其代码，因此无新增许可证声明需求；不复制其价格/版本假设，不做自动评论、@、回复。
- 采集结果必须进入 `sources → source_versions → jobs → claims/relations/updates`，不是视频推荐流；不得用播放量/UP主排名替代证据；缺字幕/认证失败/风控要如实显示，不能伪造。
- 默认不转写、不调用付费模型、不外发材料；本地转写作为后续可选能力，需单独评估依赖和版权。
- **真实 B站 网络**：元数据/字幕（`BV1MLJe6fEmH`，6 片段）与 WBI 关键词搜索（`"AI"` 两页各 20 条）已完成短时烟测；真实扫码确认、分P批量、凭证恢复、定时调度仍未执行。任何新的真实请求都必须由用户在本机明确授权后低频执行，并记录时间、结果与错误码；未授权时只做契约与测试替身。

## 9. 下一阶段顺序

1. **真实模型连接测试（已完成 1 次）**：用户已在本机应用内配置 MIMO 2.6（`openai_compatible`）并成功执行一次固定合成提示的“测试连接”（37/26 token，0.0000445 USD）。下一步若要验证抽取，必须由用户再次明确授权：显式启用 `claim_extraction` 路由、预算充足，并用**固定合成材料**（不是用户导入材料）跑一次，检查真实模型输出能否通过服务端锚点校验；不要把一次连接成功写成“真实抽取已通过”。
2. **M3 收尾（已完成）**：`evidence_review`（引文是否支持表达）与 `entity_disambiguation`（仅已有实体的候选）已接入流水线，实体候选需人工接受/拒绝；自动测试全为替身。真实语义复核/实体消歧仍需用户授权后单独烟测。`claim_comparison`/`fact_check` 模型路由、跨进程配额、多节点预算、Anthropic/Gemini 原生协议仍是后续。
3. **M4 Phase 1（已完成）**：库内事实材料、E0–E3 规则分档、追加型核查记录、人工接受/拒绝、重要事实 E3 硬门槛、标题/正文读取门槛与发布预览。
4. **M4 Phase 2（第一部分已完成）**：AC32–45 专项阻断数据集（48 案例 + 12 对照，`tests/fixtures/ac32-45.json` 与 `tests/test_m4_ac_dataset.py`）、`fact_check` **仅建议**模型路由（只读材料元数据、预算预留/结算、拒绝外部读取自述、不改变分档/状态/门槛）、来源版本过期封顶 E1、断章取义确定性拦截、`scoped_fact` 必须人工接受。
5. **M4 剩余**：`claim_comparison` 模型路由接入流水线；来源画像评估与 `source_reliability` 记录；纠错表达式生成与统一重算（当前靠读取时重新评估）；批量复核与浏览器点击回归；在用户明确授权下用固定合成材料真实执行一次 `fact_check`/`claim_comparison` 调用并记录端点、token 与费用。真实外部检索必须单独授权，不得发送真实导入材料给未授权端点。
6. **M2 后续**：UP 主订阅/合集/番剧展开、多进程/节点调度；真实扫码/登录态与搜索字段核对。分P批量、凭证恢复、日报联动、明细保留已完成但未真实烟测。
7. **M5**：安全、负载、恢复、容器与发布验收；在发布前重新评估 M1 推迟项，尤其是密码恢复、批量导入和完整 E2E。没有真实凭证的测试如实标为未执行。

## 10. 数据与权限边界

- 不读取DSH或用户其他项目配置；本项目 `.env` 只在必要服务运行时读取。
- 所有跨表引用限定工作区；不能为了方便测试去掉复合外键/约束触发器。
- 引用通过只说明来源表达过该主张；来源数量、身份、模型分数不能推导事实确认。
- `local_only`必须约束主模型与全部备用模型；未实现网关时保持不外发。
- 报告下载失败时提供准确URL、错误及已尝试次数；不要继续大范围重试。
- 正式工作区账号由用户管理；测试账号与真实账号分离，不擅自重置密码或删除数据。
- B站采集只使用平台允许的公开能力或用户授权；不得绕过登录、付费、验证码或风控；缺少字幕/权限/接口时显示真实状态，不用 Mock 冒充实采。
- 关键词来源只保存用户配置、运行账本与候选决定，不保存搜索页正文、凭证或事实结论；联网授权可按来源单独撤销。
- `.runtime/pgdata`、`.env`、用户导入材料均不是可清理的测试缓存。

## 11. 本交接文档的维护规则

每次完成一轮开发后更新：日期、当前里程碑、实际验证命令与结果、迁移编号、剩余阻断项、下一工作包。不要把计划写进“已完成”。若用户改变优先级，以用户最新指示为准，并保留原需求的未完成清单。

维护脚本时额外注意：PowerShell 5.1 的 UTF-8 BOM；PostgreSQL 非管理员令牌；停止脚本必须先校验归属再结束进程；测试必须使用真实 PostgreSQL 隔离 schema；跑测试前确认数据库已启动。

用户已多次调整优先级，必须在第二、八、九节保留记录。当前结论：M1 核心收口；批量导入、密码恢复、语言偏好、完整 E2E 推迟；M2 采集器优先且 Phase 1/2/收尾已完成；M3 Phase 1 模型供应商/路由/预算/账本已完成、M3 Phase 2 抽取接入与服务端锚点校验已完成、M3 收尾表达语义复核与实体候选人工确认已完成；M4 Phase 1 事实证明材料/核查记录/人工复核/发布门槛已完成，M4 Phase 2 第一部分（建议路由、过期封顶、断章取义拦截、AC32–45 数据集）已完成，但真实模型请求、真实抽取/复核/实体消歧/事实核查均未执行；手动导入仅兜底。

## 12. 可直接发给下一位 CODEX 的任务提示

```text
请继续开发 C:\Users\Administrator\Desktop\fishingNet 项目。

先阅读根目录 CODEX交接文档.md、AI多来源信息聚合平台_开发需求文档.md、docs/ACCEPTANCE.md、docs/bilibili-api-policy.md、docs/adr/0003-discovery-pipeline.md、docs/adr/0004-model-gateway-budget.md 与 docs/adr/0005-fact-verification.md；再检查代码、数据库迁移与Git状态。当前是M0/M4过渡开发预览：M1核心（M1.1加固、主题管理、范围/时间筛选）、M2 Phase 1（B站单链接导入、账号能力检查、元数据/字幕状态、采集器契约与测试替身、网页扫码登录）、M2 Phase 2（关键词来源配置、排除词/语言/每轮上限、限速与退避、调度、增量watermark、分P定位、跨阶段持久恢复）、M2收尾（分P批量需确认、凭证过期/风控恢复、发现结果与日报联动、discovery_run_items保留策略）、M3 Phase 1（模型供应商与密钥、OpenAI原生/兼容/本地/Mock协议、8任务路由与回退、预算预留/结算、调用与费用账本、模型管理界面）、M3 Phase 2（路由化主张抽取接入 process_source、有界提示、服务端引用/锚点校验、无锚点主张保持待复核）、M3收尾（evidence_review表达语义复核、entity_disambiguation仅已有实体候选、人工接受/拒绝）、M4 Phase 1（库内事实材料、E0–E3规则分档、fact_check任务、追加型核查记录、人工接受/拒绝、重要事实E3硬门槛、标题/正文发布门槛与发布预览）与M4 Phase 2第一部分（fact_check仅建议模型路由、来源版本过期封顶E1、断章取义拦截、scoped_fact必须人工接受、AC32–45阻断数据集48案例+12对照）已完成。真实B站元数据/字幕与WBI关键词搜索已烟测通过；真实模型连接测试已由用户执行1次（MIMO 2.6，固定合成提示，成功），**真实模型抽取、真实语义复核、真实实体消歧、真实事实核查（含外部检索与模型核查）**、真实扫码确认、分P批量、凭证恢复与定时调度尚未验证。批量导入、密码恢复、语言偏好、完整E2E已按用户决定推迟，不是完整MVP。不要重建项目、删除数据库或覆盖.env；保留用户现有账号和数据。项目可能尚无Git提交，未跟踪文件也是已有成果，不能清理掉。不要删除或改写已完成的M1核心、M2 Phase 1/2/收尾、M3 Phase 1/2/收尾、M4与迁移0001–0008。

真实 B站 烟测已通过（2026-10-20）：BV1MLJe6fEmH 元数据/字幕（6 片段）+ WBI 签名关键词搜索（两页各 20 条）+ B站平台连接全部验证通过。M3 Phase 2/收尾、M4 Phase 1 与 M4 Phase 2 第一部分已完成，真实模型连接测试也已成功一次；建议下一项为**接入 claim_comparison 模型路由**，或用固定合成材料在用户明确授权下真实执行一次 **fact_check 路由调用**并记录端点、token 与费用（不得发送真实导入材料）。真实网络调用继续保持短事务+心跳租约+fence，模型调用必须“预留预算→无事务HTTP→结算”；不要为通过烟测而放宽分P批量确认、恢复冷却、明细保留策略、云端 confirm_cloud、复核/实体人工确认、事实材料人工接受或默认零预算。

保持六表语义、不可变证据、工作区隔离、事实发布门槛和默认零预算。Mock/测试替身只用于合成例子，真实导入、采集、发现或模型测试不得伪装成AI分析完成。未实现的模型、平台及事实确认能力如实标注，不隐式收费或外发材料。B站凭证与模型密钥由用户在本机应用内提供，不得索取到聊天或日志，不得绕过登录、付费、验证码或风控。enabled与network_authorized必须保持分离且默认false；供应商/模型默认停用、任务路由默认未配置、预算默认0、数据策略默认local_only。事实核查目前只做库内（kb_only）规则评估，fact_check 模型路由只给建议且不改变分档：空结果不是反证，insufficient 不是虚假，corroborated 也不等于外部已查证；核查记录只追加，接受/拒绝必须新增版本，不得直接改状态或放宽 accept_gate。

先复现：.venv\Scripts\python.exe infra\init-local.py、.venv\Scripts\python.exe -m pytest -q、npm.cmd --prefix apps/web run build、infra\launch.ps1 -NoBrowser 与停止入口。注意PostgreSQL不能用管理员令牌直接启动；PowerShell中文脚本保持UTF-8 BOM；先停止服务会让测试连接失败。

需要新依赖时，若下载失败或卡住，先停止，把准确下载链接和错误交给我看，不要无限重试、切换不明镜像或改我的代理。已有授权范围内的常规开发请直接推进。

完成后更新README、docs/PROGRESS.md、docs/ACCEPTANCE.md、CODEX交接文档.md；API变化同步docs/openapi.json。报告实际测试结果、可运行入口、本轮完成项与仍未完成项。不要把M1核心、M2、M3 Phase 1/2/收尾、测试替身或Mock说成真实模型已接入、真实B站采集全流程或整个MVP已完成；没有真实授权烟测就明确写未执行。
```
