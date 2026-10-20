# 开发记录

## 2026-10-21 01:55（UTC+8）· M4 Phase 2 补充：模型自动建实体 + 按实体分组的动态

按用户最新指示，实体处理改为“模型可自建实体”，并把“一个实体一条动态”的粒度落地。未新增迁移、未改 0001–0008、未删库、未覆盖 `.env`。

- `app/ai/entities.py`：`entity_disambiguation` 现在允许空实体库调用；模型可返回 `links`（只关联已有 `node_id`）与 `new_entities`（name/entity_type/aliases/description/claim_refs）。服务端校验并去重：同名拒绝、`claim_ref` 必须已发送、新实体上限与字段长度受限。
- `app/jobs/worker.py`：`_suggest_entities` 不再因“无实体节点”跳过；`_store_model_claims` 在写主张前解析 `new_entities`——按 `canonical_name` 与库内去重后自动创建 `provisional` 节点（`metadata.asserted_by='model'`，**不自动合并两个已有实体**），并为相关主张建立 `accepted` 的 claim→node `about`；动态改为“按（来源版本 + 实体）分组，一个实体一条动态，正文列出该实体下的主张”，未归类主张仍一条一动态。既有实体的链接仍为 `proposed`、需人工接受（M3 收尾语义不变）。
- `app/main.py`：`/capabilities` 的 `entity_disambiguation` 如实声明“已有实体→proposed、新实体→auto-created、按实体分组”；API **0.11.0**（端点仍 70）。
- 前端：feed 卡片新增“关于 {实体}”标签与多句预览；任务页显示“新建实体 N 个”。

**验证**：`pytest -q` **390 passed / 93 warnings（47.89s）**（真实 PostgreSQL 17 隔离 schema）；新增 `tests/test_m4_entities.py` 4 项（双实体各一条动态、单实体多句合并、同名复用、无实体逐条），`tests/test_m3_wrapup.py` 更新空库/去重语义；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0008 (head)**；`docs/openapi.json` 重导出 **70 路径 / 0.11.0**。

**未验证/边界**：真实模型实体消歧与建实体仍只用 `httpx.MockTransport`；自动建的实体为 `provisional`，可在实体管理里改名/合并/删除；模型不自动合并两个已有实体；按实体分组只发生在“新实体自动建立”或“已接受关系”的场景，未归类内容仍逐条。

## 2026-10-21 01:10（UTC+8）· M4 Phase 2：事实核查建议路由、版本过期封顶与 AC32–45 阻断数据集

基线复现（改动前）：`infra/init-local.py` 复用本地 PostgreSQL；`pytest -q` **299 passed / 93 warnings（42.42s）**；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0008 (head)**；`docs/openapi.json` = 0.9.0 / 70 路径；正式库 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水 / 6 条既有核查记录**。

**未新增迁移**（head 仍 0008）；未改 0001–0008、未删库、未覆盖 `.env`、未改写既有来源/主张/核查记录。

**实现**：
- `app/ai/factcheck.py`（新增）：`build_fact_check_messages` 只发送主张与库内材料元数据（作用、范围、直接性、上游组、独立性、完整性、条件、限制），按路由 `max_input_tokens` 截断；`validate_advisory` 只接受受控 verdict（supports/refutes/conflicts/insufficient/unclear）、已知 `material_refs`、理由与限制，拒绝编造引用、非法结论和任何外部读取自述（`MODEL_CLAIMED_EXTERNAL_READS`）；`confidence_ignored` 明确丢弃置信度；`agreement` 只做与规则分档的一致性说明。
- `app/jobs/worker.py`：`_fact_materials` 增加 `superseded`、`retention_expired`、`version_no`、`current_version_id`；新增 `_fact_check_advisory`（路由未配置直接跳过；否则 `LeaseKeeper` 内调用 `invoke_messages('fact_check')`，失败/预算/策略/非法输出只记录状态）；`fact_check` 把建议写入 `model_runs`、manifest 增加 `model_route`/`model_advisory_status`，评估、状态、`fact_check_status` 不变。
- `app/domain/verification.py`：来源版本被取代封顶 E1（`SOURCE_VERSION_SUPERSEDED`）、保留期到期 E0；`assess` 增加 `stale_support_materials`、`stale_refutation_materials`、`dispute_balance` 与不对称缺口；快照哈希纳入 `superseded`/`retention_expired`；`build_snapshot` 记录这两个标记。
- `app/ai/extraction.py`：AC33 截断限定检测——引文在子句分隔符处截断且后半句含限定/转折标记时，主张进入 `unverified`（`CONTEXT_TRUNCATED_LIMITATION`），锚点保留用于追溯但不发布。
- `app/domain/policy.py`：`scoped_fact` 必须来自人工接受（`checker_type='human'` 且有 reviewer）的当前记录，历史规则记录不能继承确认资格。
- `app/main.py`：`_claim_materials` 同步 `superseded`；`/verification-capabilities` 说明建议路由、`advisory_only`、不接受外部读取自述；`/capabilities` 更新阶段、AC32–45 数据集、过期封顶与截断拦截，deferred 改为 `claim_comparison` 路由/来源画像/纠错表达式；API **0.10.0**（端点仍 70）。
- 前端：主张详情的待复核记录显示模型建议、是否与规则一致、材料是否截断，并注明“不改变分档、状态或发布门槛”；核查版本列表显示模型建议；模型管理补充 `fact_check` 路由说明。
- 数据集：`tests/fixtures/ac32-45.json`（48 案例 + 12 对照，全部合成）、`tools/build_ac_dataset.py`（生成器）、`docs/AC32-45-dataset.md`（说明与 AC 覆盖表）。

**验证**：
- `pytest -q` **385 passed / 93 warnings（45.72s）**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_m4_ac_dataset.py` 86 个测试项：60 个数据集案例 + 8 个断章取义表达案例（数据集内 8 例单独参数化）+ 18 项单元/集成；另新增 `tests/m4_support.py` 测试辅助。路由测试全部 `httpx.MockTransport`，事实材料全部合成。
- `npm.cmd --prefix apps/web run build`（`tsc -b && vite build`）通过。
- `alembic current` = **0008 (head)**；`docs/openapi.json` 重新导出 **70 路径 / 0.10.0**。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”，`/api/v1/health/ready` ok/ok、5173 返回 200、OpenAPI 0.10.0 / 70 路径；`/verification-capabilities`、`/claims/{id}/fact-checks` 未登录 401；`stop.ps1` 退出码 0，8000/5173/55432 全部释放（详见 ACCEPTANCE）。
- 正式库计数未变：**1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水 / 6 条核查记录 / 0 条事实关系**；未新增调用、预算流水或核查记录。

**明确未做**：真实模型事实核查与真实外部检索；`claim_comparison` 模型路由；来源画像评估与记录；纠错表达式生成与统一重算；批量复核与浏览器点击实测；真实扫码确认、分P批量、凭证恢复、定时调度。AC32–45 数据集通过不等于 AC01–AC45 全部签收或 M4 完成。

**下一项建议**：把 `claim_comparison` 接入事实核查任务（同实体候选主张比较，仍只输出建议/候选且不改变分档），或在用户明确授权下用固定合成材料执行一次真实 `fact_check` 路由调用并记录端点、token 与费用（不得发送真实导入材料，不得降低门槛）。

## 2026-10-20 23:45（UTC+8）· M4 Phase 1：库内事实材料、版本化核查记录与人工复核

基线复现（改动前）：`infra/init-local.py` 复用本地 PostgreSQL；`pytest -q` **275 passed / 93 warnings（34.35s）**；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0007 (head)**；正式库 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水 / 6 条既有 insufficient 核查记录**；`/capabilities` 仍写 `fact_check`/`claim_comparison` 未接入。

**迁移 0008（head）**：`fact_evidence_integrity` 约束触发器（校验 `fact_supported_by/refuted_by/context_by` 的 `fact_evidence.v1` 载荷、逐字引文、偏移、哈希、上游组/命题范围，并要求 accepted 关系必须带 `assessment_record_id`）；`verification_records` 的 `review_status/checker_type/trigger` 约束与 claim/created 索引、待复核部分索引；事实关系部分索引；`claims.fact_check_status` 索引。未改 0001–0007、未删库、未覆盖 `.env`。

**实现**：
- `app/domain/fact_evidence.py`（新增）：`normalize_link` 校验材料作用/直接性/独立性/完整性/条件/透明度/限制；`build_anchor` 在存储片段中逐字定位（重复引文拒绝），偏移与哈希由服务端计算；`applicability` 缺省全部 `unknown`，未知字段与非法枚举拒绝；同一来源版本同时承担表达溯源与事实证据时要求 `note`。
- `app/domain/verification.py`（新增）：`material_tier`（E0 疑似篡改/条件不匹配；E1 间接或条件/完整性缺口；E2 直接 + 五项条件匹配 + 完整性已核验）；`_strongest` 仅在“≥2 条 E2、不同上游组、至少一组确认独立、无同源转载”时升 E3；`assess` 产出 `insufficient/corroborated/conflicted/refuted`、逐档计数、缺口与 `auto_accept_allowed=false`；`accept_gate` 拦截 E1 corroborated、重要事实 E2、E1 refuted 与“有强支持材料却判 refuted”；`build_snapshot`/`snapshot_hash` 记录材料状态以便发现过期输入。
- `app/jobs/worker.py`：新增 `fact_check(job)`（`kind='fact_check'`，初始阶段 `retrieve`）。只读主张、库内事实材料、同实体候选主张；写 `retrieval_manifest`（`kb_only`、`external_reads=[]`、`model_route=not_wired_in_this_phase`）、`evidence_snapshot`、条件与评估；在 `fence AND state='running'` 的同一事务内追加 `pending` 记录并结束任务；主张 `fact_check_status` 不变。claim 不可用时 `CLAIM_UNAVAILABLE` 且不写记录。
- `app/main.py`：新增 `POST /claims/{id}/evidence-links`（同工作区、可用/未过期版本、材料上限 20、去重、审计）、`POST|GET /claims/{id}/fact-checks`（202、活动任务去重、可选 Idempotency-Key）、`GET /verification-records/{id}`（历史链、gate）、`POST /verification-records/{id}/review`（输入指纹 + revision + 快照哈希校验；409 过期/重复；422 硬门槛；接受时原子接受材料并移动指针，拒绝不改状态）、`GET /verification-capabilities`、`GET /sources/{id}/reliability`（unknown）、`POST /updates/{id}/publication-preview`（只读逐句预览）。
- `app/main.py` 读取端：`visible_update` 同时评估标题与正文，改用 `mode_is_within` 允许更弱表达、拒绝更强表达；`GET /claims/{id}` 增加 `current_verification`、`fact_evidence`、`fact_evidence_groups`、`pending_verification_records`（含 gate）；`GET /verification-queue` 增加 `latest_record_*`、`pending_reviews`、`status`/`review` 筛选；`/capabilities` 增加 `fact_verification` 并把 `external_verification` 改为 `kb_only`；API **0.9.0**。
- `app/domain/policy.py`：`POLICY_VERSION` 升为 `publication.v1.3.0`，新增 `mode_is_within`（opinion/conditional/attributed < dispute/correction < scoped_fact）。
- 前端：主张详情新增“库内事实核查”面板（事实材料分档/状态/用途说明、待复核记录与 gate 提示、接受/拒绝需填理由、发起核查、选择来源与片段添加待确认材料）；核查工作台每行显示待复核数量并可发起核查；模式横幅与页脚更新为 M4 Phase 1。
- 文档：新增 `docs/adr/0005-fact-verification.md`；更新 README、PROGRESS、ACCEPTANCE、交接文档、数据字典、OpenAPI。

**验证**：
- `pytest -q` **299 passed / 93 warnings（43.84s）**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_m4_verification.py` 24 项：12 项无数据库单元测试（锚点/枚举/条件、E0–E3、状态矩阵、accept gate、快照哈希、表达强度）+ 12 项隔离 schema 集成测试（材料锚点与角色说明、去重与上限、无材料核查、接受后联动、拒绝保持 proposed、过期输入与指纹、重要事实 E3、伪造 E1 记录被 422、发布门槛隐藏/放行、无关主张不升级、队列筛选与任务去重、能力声明、缺失主张失败、记录只追加）。全部无网络、无模型调用。
- `npm.cmd --prefix apps/web run build`（`tsc -b && vite build`）通过。
- `alembic current` = **0008 (head)**；`docs/openapi.json` 重新导出 **70 路径 / 0.9.0**。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”，`/api/v1/health/ready` ok/ok、5173 返回 200、OpenAPI 0.9.0 / 70 路径；新端点未登录均 401；`stop.ps1` 退出码 0，8000/5173/55432 全部释放；api/worker/web 错误日志检查（见 ACCEPTANCE）。
- 正式库计数未变（1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水）；新表/新关系无新增行，未改写既有来源、主张与核查记录。

**明确未做**：`fact_check` 模型路由与 `claim_comparison` 接入、外部检索连接器、来源画像评估、纠错传播、AC32–45 专项数据集与整体门槛签收、批量复核；真实模型抽取/语义复核/实体消歧、真实扫码确认、分P批量、凭证恢复、定时调度仍未执行。事实核查目前只做库内规则评估；`insufficient` 不等于虚假，`corroborated` 也不等于外部已查证。

**下一项建议**：先补 AC32–45 专项阻断数据集（虚假宣传、断章取义、过期版本、循环转载、伪造测评、真假批评、无外部工具、多来源不等于真、不对称争议、纠错传播、全面发布门槛、预算路由、并发与迁移），再把 `claim_comparison`/`fact_check` 模型路由接入并用固定合成材料验证；任何真实外部检索都必须单独授权、记录来源与结果。

## 2026-10-20 21:25（UTC+8）· M3 收尾：表达语义复核与实体候选人工确认

基线复现（改动前）：`infra/init-local.py` 复用本地 PostgreSQL；`pytest -q` **255 passed / 93 warnings（34.85s）**；`npm.cmd --prefix apps/web run build` 通过；已有实例 `/api/v1/health/ready` 为 `{"status":"ok","database":"ok"}`、5173 返回 200、OpenAPI 0.7.0 / 62 路径；正式库 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**。

**迁移 0007（head）**：新增 `claim_expression_reviews`（追加型表达语义复核记录：claim、job、invocation、verdict、rationale、limitations、quote_count、model、policy）与 `relations(status='proposed', asserted_by='model')` 的部分索引。未改 0001–0006、未删库、未覆盖 `.env`。

**实现**：
- `app/ai/review.py`（新增）：`build_review_messages` 生成有界复核提示，只含带服务端锚点的主张、引文和限定上下文；`validate_review_output` 只接受请求中出现过的 `claim_ref` 与受控 verdict（supports/partially_supports/unsupported/unclear），未知/重复/非法条目拒绝，未覆盖条目记为 missing。复核只判断“引文是否支持表达”，不判断事实真假。
- `app/ai/entities.py`（新增）：`build_entity_messages` 只发送主张与库内已有实体（名称、类型、有限别名）；`validate_entity_output` 只接受请求中出现过的 node_id，拒绝编造节点、未知主张、重复/超限/非法角色，绝不创建或合并节点。
- `app/jobs/worker.py`：`_process_with_model` 在抽取与锚点校验后，按 `evidence_review` 路由执行一次语义复核，再按 `entity_disambiguation` 路由执行一次实体候选；两次调用都复用“短事务预留 → 无事务 HTTP → 结算/释放 + 调用账本”和 `LeaseKeeper`。`_store_model_claims` 现在：只有锚点通过且复核为 supports/partially_supports 的主张写 `validated` 并进入发布门槛；不支持、上下文不足、复核未完成/未覆盖的主张写 `candidate`，不建核查记录、不发布；复核结果写 `claim_expression_reviews`；实体候选写 `relations(status='proposed')`。`fact_check_status` 仍固定为 `insufficient`，任务结果固定 `fact_confirmation=false`。
- `app/main.py`：新增 `POST /relations/{id}/review`（人工接受/拒绝模型实体候选；接受后为已发布动态补 update→node 关系，拒绝保持不可见）；`GET /claims/{id}` 增加 `expression_reviews`、`expression_review`、`about_nodes`；`GET /verification-queue` 增加最新 `expression_verdict`；节点详情的来源主张只统计已接受的实体关系；`/capabilities` 增加 `expression_review` 与 `entity_disambiguation`，更新 B站/WBI 烟测边界与 deferred 列表；API 版本 **0.8.0**。
- 前端：核查工作台显示表达复核结论；任务页显示复核计数与实体候选；主张详情显示复核理由/限制与实体候选，可接受/拒绝；模型管理说明新路由用途；模式横幅更新为 M3 收尾。

**验证**：
- `pytest -q` **275 passed / 93 warnings（61.43s）**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_m3_wrapup.py` 20 项：10 项无数据库单元 + 10 项隔离 schema 集成，网络全部由 `httpx.MockTransport` 替身。
- 覆盖：复核提示边界/上下文/截断、未知 claim_ref/非法 verdict/重复/缺失、路由输入过小；实体提示只提供已有节点、编造节点/未知主张/重复/非法角色/每主张上限、无实体候选；复核 supports 发布与账本、unsupported 保持 candidate、上游失败保持 candidate、复核预算不足阻断、partial 发布并带限制、缺失全部复核结论保持 candidate；实体候选 proposed 且接受前不可见、接受后进入实体视图、拒绝保持不可见、编造节点被拒绝但主张仍发布、实体调用失败不影响发布。
- 前端：`npm.cmd --prefix apps/web run build`（`tsc -b && vite build`）通过。
- 迁移：`alembic current` = **0007 (head)**；`docs/openapi.json` 重新导出 **63 路径 / 0.8.0**。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”，`/api/v1/health/ready` ok/ok、5173 返回 200、OpenAPI 0.8.0 / 63 路径；`/api/v1/relations/{id}/review`、`/claims/{id}`、`/capabilities`、`/task-routes` 未登录均 401；`stop.ps1` 退出码 0，8000/5173/55432 全部释放。`api.error.log` 只有 Uvicorn 启动 INFO，web/worker 错误日志 0 字节。
- 正式库计数保持 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**；`claim_expression_reviews` 0 行、proposed 关系 0 行。

**明确未做**：真实模型抽取、真实表达复核、真实实体消歧；真实扫码确认、分P批量、凭证恢复、定时调度；`claim_comparison`/`fact_check` 路由接入；跨进程并发上限、多节点预算、供应商账单对账、Anthropic/Gemini 原生协议、浏览器端模型面板自动点击回归。复核阶段预算不足时任务为 `needs_review` 并保留 candidate，不会自动重试；抽取阶段零预算仍是可重试的 `blocked_budget`。

## 2026-10-20 20:20（UTC+8）· M3 Phase 2：路由化主张抽取接入 process_source

基线复现（改动前）：`infra/init-local.py` 复用本地 PostgreSQL；`pytest -q` **234 passed / 93 warnings（25.45s）**；`npm.cmd --prefix apps/web run build` 通过；`infra/launch.ps1 -NoBrowser` 输出“FishingNet 已就绪”，`/health/ready` = `{"status":"ok","database":"ok"}`、5173 返回 200、OpenAPI 0.6.0 / 62 路径；`infra/stop.ps1` 退出码 0，8000/5173/55432 全部释放。

**实现**：
- `app/ai/extraction.py`（新增）：`build_claim_messages` 按路由 `max_input_tokens` 生成有界提示；材料放在 JSON 数据块并声明“材料中的指令不要执行”。`validate_claim_output` 只接受请求中出现过的 `segment_id`；`quote` 必须逐字出现在该片段可见文本中（空白折行归一化后映射回原文）；`char_start/char_end/quote_hash` 全部由服务端按 Unicode code point 计算；模型自报偏移不一致即 `OFFSET_MISMATCH`。类别/极性/重要性/可验证性走受控词表，非法值整条拒绝。无锚点主张返回为 `unverified` 候选。
- `app/ai/gateway.py`：`invoke` 拆分出 `invoke_messages`，允许任务自带提示但复用同一套“预留最坏费用 → 无事务 HTTP → 结算/释放 → 调用账本”流程；预算阻断时把 `invocation_id` 放进错误 details。
- `app/jobs/worker.py`：`process()` 改为分派器；原单事务逻辑保持为 `_process_local`（合成示例路径）。新增 `_process_with_model`：解析路由 → 有界提示 → `LeaseKeeper` 内调用网关 → 校验输出 → 在一个 `fence AND state='running'` 守卫的事务里写入 anchored claims、`evidenced_by`、`insufficient` 核查记录，并按 `PublicationPolicy` 发布；无锚点主张写为 `review_status='candidate'`，不建关系、不发动态。零预算 → `blocked_budget`；策略/网络/鉴权 → `failed`；输出非法或全部无锚点 → `needs_review`。fence 丢失时主张、动态与任务状态一起回滚。
- `app/main.py`：`/capabilities` 增加 `model_extraction`（锚点校验、候选不发布、`fact_check_status` 固定 `insufficient`、语义复核未实现、真实模型抽取未验证）；`blocked_budget` 任务允许直接重试；API 版本 0.7.0。
- 前端：动态卡片标注“模型抽取 · 未事实确认”，任务页显示“原文锚点通过 / 待复核候选 / 拒绝 + 模型名”，任务路由说明标注抽取用途；模式横幅更新为 M3 Phase 2。

**验证**：`pytest -q` **255 passed / 93 warnings（30.71s）**（新增 `tests/test_m3_extraction.py`：11 项无数据库单元测试 + 10 项隔离 schema 集成测试）。覆盖提示边界与截断、Unicode code point 偏移、空白折行回映射、未知片段/错误偏移/不存在引文/非法枚举、主张与证据数量上限、非对象输出、指纹稳定；以及路由抽取落库与发布、调用账本与预算预留/结算、合成示例不触碰模型、无路由/停用路由保持 `needs_review`、无锚点候选不发布、部分校验只发布 anchored、零预算阻断不触网并在提高预算后重试成功、`local_only` 阻断云端、非法 JSON 不写库、空 claims 合法、fence 丢失零写入。自动测试网络全部由 `httpx.MockTransport` 替身。
- 前端：`npm.cmd --prefix apps/web run build` 通过（`tsc -b && vite build`）。
- API：`docs/openapi.json` 重新导出为 **62 路径 / 0.7.0**，无端点结构变化。
- 启动/停止（改动后复测）：`launch.ps1 -NoBrowser` 就绪（`/health/ready` ok/ok、5173 返回 200、OpenAPI 0.7.0 / 62 路径、`/model-providers`、`/task-routes`、`/ai/invocations`、`/capabilities` 未登录均 401、worker/api/web 错误日志均为 0 字节）；`stop.ps1` 退出码 0，三端口释放。
- **真实模型连接测试（用户在本机执行，20:35）**：用户在应用内配置 MIMO 2.6（`openai_compatible`，`https://token-plan-cn.xiaomimimo.com/v1`，模型 `mimo-v2.6-pro`），设置预算与“允许云端”后点击一次“测试连接”。结果：`succeeded`，输入 37 / 输出 26 token，延迟 1625 ms，估算并结算 0.0000445 USD，预留 0.000256 → 结算 0.0000445，账本 `settled`；无 claims/updates、无导入材料外发。正式库计数随之为 **1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水**。
- 正式库来源与任务数未变（7 来源 / 10 任务）；未执行真实模型抽取。

**明确未做**：真实模型抽取（没有用真实模型处理任何材料）；语义复核（判断引用是否支持主张）与事实核查；实体消歧/主张比较路由仍未接入流水线，模型抽取的动态暂无实体归属；跨进程并发上限与多节点预算；Anthropic/Gemini 原生协议；供应商账单级“已确认费用”；浏览器端模型面板点击实测（连接测试由用户在界面完成，但没有自动化点击回归）。默认仍为零预算、数据策略 `local_only`（用户为连接测试临时改为 `approved_cloud`，测试后未自动回退；下次可由用户按需改回）。

## 2026-10-20 19:55（UTC+8）· M3 Phase 1：模型供应商、网关、预算与费用账本

本轮在真实 B站 烟测通过的基础上进入 M3。基线复现：`infra/init-local.py` 复用本地 PostgreSQL，`pytest -q` **181 passed**，`npm --prefix apps/web run build` 通过，`infra/launch.ps1 -NoBrowser` 输出已就绪（健康检查 `ok/ok`、5173 返回 200），`infra/stop.ps1` 退出码 0 且 8000/5173/55432 全部释放。

**迁移 0006（head）**：`provider_connections`（协议、端点、是否本地、Fernet 密封密钥、掩码提示、状态/最近测试、启用开关）、`model_profiles`（真实模型 ID、等级、上下文/输出上限、结构化输出、输入/输出单价与计价货币、默认停用）、`task_routes`（8 类任务的模式/启用/主模型/≤3 备用模型/输入输出上限/超时/尝试次数/数据策略）、`ai_invocations`（调用账本：状态、失败阶段、token、估算/结算费用、费用状态、配置快照、尝试日志）、`ai_budget_ledger`（调用前最坏费用预留、调用后结算/释放）。`workspace_settings` 增加 `budget_currency` 与 `routing_revision`。只向前追加；未改 0001–0005、未删库、未覆盖 `.env`。

**实现**：
- `app/domain/model_secrets.py`：与平台凭证同一 `MASTER_KEY`、不同派生标签与 schema 的密钥密封；只保存密文与 `****` + 末 4 位掩码；校验长度、拒绝空白/换行。
- `app/ai/budget.py`：Decimal 精确计价；按路线 token 上限计算最坏费用；在短事务内锁定工作区行、检查日/月预算并写预留；结算/释放；预算窗口按 UTC 日与自然月；工作区单一币种，无隐式汇率。
- `app/ai/providers.py`：OpenAI Chat Completions 原生、通用兼容、本地兼容与 Mock 四种协议；固定合成请求头、`Referer` 无关、不跟随重定向、限制响应大小、按 401/403、429、5xx、3xx、非法 JSON 分别映射；Mock 不触网并标注合成。
- `app/ai/gateway.py`：保留 M1 的 `AIGateway.extract`（合成语法）不变；新增任务路由解析、云端数据策略门、主模型→备用模型回退、超时/尝试次数、预算预留、调用后结算、调用账本、未知用量保守计费；连接测试只用固定合成提示。
- API（新增 12 条路径，OpenAPI **62 路径 / 0.6.0**）：`/model-providers` CRUD、密钥写入/清除、连接测试；`/models` CRUD；`/task-routes` 整组原子保存（If-Match 使用 `routing_revision`，云端路由需 `confirm_cloud`）；`/ai/test-call`（必须 `confirm_spend: true`，结果不写入知识库）；`/ai/invocations` 与详情；`/costs/summary`；`PUT /settings/budget`。
- 前端：“模型管理”页四个页签（供应商连接 / 模型列表 / 任务分配 / 调用记录与费用），支持掩码密钥、连接测试、模型价格与限制、任务路由整组保存、云端确认、费用与预算摘要；“系统设置”页增加预算/币种/数据策略表单；任务页显示今日估算费用；模式横幅和侧栏不再写死 Mock。

**验证**：`pytest -q` **234 passed / 93 warnings（26.02s）**。新增 35 项 M3 单元测试（计价、预算金额、用量归一化、密钥密封与掩码、URL 校验、请求构造、结构化输出开关、不跟随重定向、错误映射、超时、非法载荷、Mock 不触网、探测提示不含用户材料）与 18 项隔离 schema 集成测试（供应商 CRUD/掩码/If-Match、密钥替换与清除、模型校验与重复、任务路由原子保存与版本冲突、云端确认、停用路由零副作用、Mock 调用账本、零预算阻断并记录 blocked、数据策略先于预算阻断、费用预留/结算、未知用量保守计费、主模型失败回退、并发预留只允许一笔、币种变更保护、供应商测试、被引用模型删除保护）。全部网络由 `httpx.MockTransport` 替身；**没有真实模型请求**。

**明确未做**（Phase 1 时点）：真实模型协议烟测（没有配置真实密钥、没有联网调用——M3 Phase 2 已由用户完成 1 次连接测试）、把模型抽取结果写入 claims/updates（M3 Phase 2 已完成）、跨进程并发上限与多节点预算、Anthropic/Gemini 原生协议、供应商账单级“已确认费用”、浏览器端模型面板点击实测。默认零预算、供应商与模型默认停用、任务路由未配置，Mock 只用于合成示例。

## 2026-10-20 16:10（UTC+8）· WBI 签名关键词搜索真实烟测通过

用户授权扩展：关键词搜索可使用 bilibili-api-python 库内置标准 WBI 签名（OE 表 + `_enc_wbi`），与字幕适配器相同。

实现变更：
- `bilibili.py`：新增 `_get_wbi_key()`（从 `/x/web-interface/nav` 获取 mixin key，30 分钟缓存）、`_sign_wbi()`、`_get_buvid()`（从 `/x/frontend/finger/spi` 获取 buvid3/buvid4 设备指纹，搜索必需）；`discover()` 在搜索前获取 buvid 并签名参数
- `worker.py`：`discover()` 从 `platform_connections` 加载凭证并传递给 `collector.discover(credential=...)`
- `fake.py`：`FakeBilibiliCollector.discover()` 签名同步更新为 `credential` keyword arg
- `test_discovery_rules.py`：测试 helper 预设 `_wbi_key`/`_buvid3`/`_buvid4`

真实烟测：关键词 `"AI"` 两页各 20 条结果，WBI key/buvid/签名全链路通过。搜索端点从 `x/web-interface/search/type`（无签名，HTTP 412）改为 `x/web-interface/wbi/search/type`（WBI 签名）。全量 181 passed，前端构建通过。

BV1MLJe6fEmH 状态确认：status=ready, availability=available, 6 片段 (ai-zh)。B站平台连接 connected。

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


## 2026-10-20 07:06（UTC+8）· 真实烟测准备，因本地端口占用暂停

- 已阅读交接、需求、验收、B站政策与 ADR 0003，并检查采集/发现的短事务、心跳、fence、调度授权门及迁移链；未修改业务代码和迁移 0001–0005。
- 用户已明确授权本轮扫码与低频真实烟测；自有 BV 链接、关键词和用户亲自扫码仍待提供/执行。授权只覆盖单链接及一个关键词来源一轮（≤2 页），不启用定时调度，不扩展到分P批量或模型调用。
- 初始化实际失败：07:04:57 PostgreSQL 日志报告 `could not bind IPv4 address "127.0.0.1": Permission denied`。独立绑定检查得到 `WinError 10013`；07:05:57 查询发现本地 55432 是 `verge-mihomo.exe`（PID 25048）的 Bound/Established 出站连接，并非监听端口或 PostgreSQL 残留。没有修改代理或终止该进程，已请用户自行释放对应连接。
- `npm.cmd --prefix apps/web run build` 通过；三个无需数据库的测试文件合计 **104 passed，14 warnings**（现有 TestClient/httpx 弃用提示）。未安装或下载依赖。
- 数据库不可用，因此本轮全量 pytest、数据库当前迁移版本/正式数据计数、启动/停止链路尚未完成复现；上一轮 170 passed 不能替代本轮结果。
- `docs/openapi.json` 与当前 `app.openapi()` 完全一致（50 路径 / 0.5.0），API 无变化；4 个 PowerShell 脚本仍为 UTF-8 BOM；Git 的 master 尚无提交，已有未跟踪成果全部保留。
- 本轮二维码生成/轮询/确认、登录态检查、元数据/字幕、关键词发现均 **未执行**，原因是本地启动阻塞及缺少样本，不是缺少本轮授权。M3 尚未开始。

恢复顺序：用户释放 55432 → 初始化 → 查询迁移/计数 → 全量测试 → 启动/停止复现 → 关闭调度的本机应用扫码 → 自有链接 → 单来源一轮发现。收到 -403 或风控即记录并停止，不签名、不伪装、不放宽冷却或保留策略。

## 2026-10-20 · M2 收尾：分P批量确认、凭证/风控恢复、日报联动与明细保留

本轮先复现基线（`init-local.py` 复用 PostgreSQL、**165 passed**、前端构建通过、启动/停止入口通过），再补 M2 收尾项。**没有执行任何真实 B站 请求、真实关键词搜索或扫码**：本轮用户未在本机明确授权联网烟测，所有新增流程只用确定性测试替身验证；真实烟测仍标为未执行。

- 迁移 `0005_recovery_retention`：`platform_connections.status` 增加 `risk_control`，新增 `cooldown_until`、`last_success_at` 与 `discovery_runs(workspace_id,finished_at)` 索引；只向前追加，未改 0001–0004、未删库、未覆盖 `.env`。正式库仍为 1 用户 / 6 来源 / 6 任务 / 0 关键词来源。
- 分P批量：新增 `POST /sources/{id}/parts/import`，请求必须带 `confirmed: true`，一次 ≤20 个分P；重复序号去重，任一所选分P不存在则整批 404 `SOURCE_PART_NOT_FOUND` 且不排队；超过 20 个返回 422 `PART_BATCH_TOO_LARGE`。每个分P仍是独立采集任务，不抓取未选分P、不下载视频；前端来源抽屉提供勾选、单独导入、全选前 20 与二次确认。
- 凭证过期/风控恢复：采集阶段遇到 `-101` 时把连接标记为 `expired`，遇到 412/-352/429 标记为 `risk_control` 并写入 15 分钟本地冷却；新增 `POST /platforms/{platform}/recover`，过期凭证必须先替换、冷却期内返回 429 `RISK_COOLDOWN`，恢复只重新排队被阻塞的采集任务（≤20），不修改来源、启用状态或联网授权。成功采集写 `last_success_at`，不宣称平台授权。
- 日报联动：`GET /digests/today` 增加 `discovery` 区块与 Markdown 的“关键词发现（本日）/关键词来源采集（本日）”小节，按来源汇总运行次数、候选、入队、失败与错误码，并列出本日入队候选与本日完成采集的来源；明确“入队 ≠ 已采集/已分析/事实确认”。下载入口同步。
- 明细保留：`discovery_run_items` 默认保留 30 天（`FISHINGNET_DISCOVERY_ITEM_RETENTION_DAYS`，1–3650），worker 约每 6 小时有界清理已结束运行的旧明细；`discovery_runs` 计数与来源 watermark 不删除。接口返回保留期说明。
- API/能力：OpenAPI 重新导出为 **50 路径 / 0.5.0**；`/capabilities` 增加 `part_batch`、`credential_recovery`、`discovery_digest`、`discovery_run_item_retention_days`，并把分P批量从 `deferred` 移除；`/platforms` 返回 `recovery`（阻塞/可恢复/冷却）。
- 测试：`python -m pytest -q` **170 passed**（新增 5 项集成测试：批量确认与整批失败、过期凭证恢复、风控冷却、日报联动、明细保留）；`npm --prefix apps/web run build` 通过（无新增依赖）。
- 真实网络：**未执行**（用户本轮未授权）。二维码只有 02:13 的生成 + 轮询历史记录；元数据/字幕、关键词搜索、扫码确认仍未验证，不能写成真实采集通过。
- 未完成：UP 主订阅/合集/番剧展开、本地转写、多进程/多节点调度与并发预算、完整 E2E、真实模型/事实确认、M3–M5。

## 2026-10-20 · M2 Phase 2：关键词来源、调度、增量 watermark、分P定位与跨阶段恢复

用户要求进入 M2 第二阶段。本轮先复现基线（106 项通过、前端构建通过、启动/停止通过），确认没有把测试替身当成真实采集或真实扫码，然后实现发现流水线。**没有执行任何真实 B站 搜索、元数据或字幕请求；也没有扫码。**

- 迁移 `0004_discovery`：新增 `discovery_sources`（关键词/排除词/语言/上限/页数与请求预算/重扫窗口/定时间隔/两个独立开关/watermark）、`discovery_runs`（运行账本与计数）、`discovery_run_items`（每个候选的决定与原因），以及 `jobs.checkpoint`、`sources.discovery_source_id`。只向前追加，未改 0001–0003、未删除数据库；正式库迁移前后仍为 1 用户 / 6 来源 / 6 任务。
- 契约：`Collector.discover(DiscoveryQuery) -> DiscoveryResult`，`DiscoveryItem.to_checkpoint()/from_checkpoint()`，`RemoteSource`/`SubtitleResult` 增加 checkpoint 序列化；`Capabilities` 增加 `max_discovery_pages`。字幕选择支持语言偏好与 `require` 语言不匹配状态（`LANGUAGE_MISMATCH`）。
- 适配器：`BilibiliCollector.discover()` 每次只读一页 `x/web-interface/search/type`，不伪造 WBI 签名、不伪装浏览器 UA；`-403` 映射为 `DISCOVERY_DENIED`，`-400` 映射为 `INVALID_QUERY`，412/-352/429 立即停止；标题 HTML 实体/高亮标签清洗、`mm:ss`/`h:mm:ss` 时长解析。
- 规则：`app/domain/discovery.py` 负责配置校验（关键词 1–5、排除词 ≤20、语言标签、any/all、页数/条数/请求/间隔边界）、Unicode NFKC+casefold 关键词匹配、排除词、每轮上限排序、duplicate/refreshed/skipped_deleted 判定、watermark 合并与 500 条上限、失败退避（15 分钟起指数，不超过间隔）。
- 调度：`app/jobs/scheduler.py` 在 worker 进程内以独立 tick 运行，只用短事务 + `FOR UPDATE SKIP LOCKED` 选取 `enabled AND network_authorized` 且到期的来源，插入 `discovery_runs` 与 `discover_keywords` 任务；`FISHINGNET_SCHEDULER=0` 可关闭；不新增进程，启动脚本保持三服务。
- 发现任务：`worker.discover()` 按页读取并逐页写入 `jobs.checkpoint`，整页早于 watermark 时停止翻页；随后在单个短事务里规划候选、创建/复用来源与采集任务、写运行账本、推进 watermark、给来源退避并受 fence 守卫完成。`_ensure_collect_job` 复用已排队的采集任务，避免重复采集。
- 跨阶段恢复：`collect()` 从 `jobs.checkpoint` 恢复元数据/字幕结果，重试时跳过已完成阶段；`claim()` 保留 checkpoint 阶段，并新增清理：被取消或 `ATTEMPTS_EXHAUSTED` 的发现任务会关闭对应 `discovery_runs` 并给来源记录退避，不再留下永远 queued 的运行。手动 retry 与凭证恢复清空 checkpoint。
- 分P定位：元数据保存 ≤100 个分P；新增 `POST /sources/{id}/parts/{p}/import` 单独排队一个分P；分P批量仍推迟。
- API：新增 6 条路径（discovery sources CRUD、手动 run、runs、run 详情、分P导入），OpenAPI **48 路径 / 0.4.0**；`/capabilities` 与 `/platforms` 明确 `live_keyword_discovery_verified=false`，`schedule` 页替换为可操作的关键词来源面板。
- 测试：`python -m pytest -q` **165 passed**（新增 40 项规则/适配器单元测试 + 19 项发现流程集成测试，含调度只处理已授权来源、增量复制/刷新、排除词与上限、显式分P、语言不匹配、fence 丢失零写入、跨阶段恢复、尝试耗尽与取消清理，全部替身、无真实网络）；`npm --prefix apps/web run build` 通过；`alembic current` = 0004 head。
- 启动/停止：`infra/launch.ps1 -NoBrowser` 输出“已就绪”，`/health/ready` 返回 `{"status":"ok","database":"ok"}`，5173 返回 200，worker 无错误日志；`infra/stop.ps1` 退出码 0，8000/5173/55432 全部释放。
- 真实网络：**未执行**关键词搜索、元数据、字幕与扫码确认；上游搜索接口可能需要签名或登录态，失败会如实显示 `DISCOVERY_DENIED`，不做绕过。
- 未完成：分P批量采集、UP 主订阅/合集展开、本地转写、真实烟测记录、多进程调度与并发预算、`discovery_run_items` 清理策略；M3–M5 未开始。

## 2026-10-20 · M2 Phase 1.5：B站 网页扫码登录

用户询问能否像参考插件一样扫码登录 B站，本轮实现网页登录二维码流程；**没有用真实 B站 App 扫码，也没有执行真实网络烟测**。

- 后端：`BilibiliCollector.start_qr_login()` / `poll_qr_login()` 对接网页登录二维码接口；状态映射为 `waiting_scan / scanned / confirmed / expired / error`，成功后只提取 `SESSDATA`。新增 `app/domain/qr_sessions.py` 管理短期会话（内存、180 秒、最多 60 次轮询、生成冷却 10 秒、同工作区单会话）。
- API：`POST /platforms/{platform}/qr`、`GET /platforms/{platform}/qr/{auth_id}`、`POST /platforms/{platform}/qr/{auth_id}/cancel`；成功后加密写入 `platform_connections` 并写审计；断开本地凭证会清除待处理会话。OpenAPI 42 路径 / 0.3.0。
- 安全：`qrcode_key` 不返回浏览器、不落库、不写日志；二维码由本机 `qrcode.react` 渲染（ISC 许可），不使用第三方二维码图片服务；会话失效时返回 410，不静默新建。
- 前端：平台账号页新增“扫码登录（实验）”面板，显示二维码、等待/已扫码/过期/风控状态，可取消与刷新；明确标注“会发起真实网络请求”“不是官方开放平台授权”。
- 依赖：新增 `qrcode.react@4.2.0`（ISC），下载正常（3 秒），已登记到 `docs/frontend-dependencies.json`；无新增 Python 依赖。
- 测试：`python -m pytest -q` **106 passed**（新增 7 项二维码单元测试 + 8 项扫码会话集成测试，全部替身）；`npm --prefix apps/web run build` 通过。
- 真实网络：2026-10-20 02:13 用户点击扫码后首次请求返回 HTTP 404。诊断为**本项目域名写错**（应为 `passport.bilibili.com`，误用 `api.bilibili.com`），不是 B站 风控。修正后复测：二维码生成成功、轮询返回 `waiting_scan`，共 2 次请求；**未扫码、未保存凭证**。新增 `test_qr_endpoints_use_passport_host` 防回归。
- 未执行：真实扫码确认、真实元数据/字幕采集、真实登录态检查；未申请官方开放平台 OAuth。

## 2026-10-20 · M2 Phase 1：B站 单链接导入与采集器契约

先核验接口与政策，再实现契约、适配器与流程；**没有执行任何真实 B站 请求**。

- 政策核验：新增 [docs/bilibili-api-policy.md](bilibili-api-policy.md)，记录官方用户协议/开放平台协议与社区接口文档的 URL、核对日期（2026-10-20）、证据等级和限制。本机 `web_fetch` 对外部域名解析到非公网地址，未能取得页面全文，文档中如实标注“待用户本机复核”。
- 采集器契约：`app/collectors/base.py`（Capabilities/Locator/RemoteSource/SubtitleResult/AccountCapability/CollectorError + 字幕分段器）、`app/collectors/bilibili.py`（URL 解析、元数据、字幕、登录态检查、请求预算/限速/错误映射）、`app/collectors/fake.py`（测试替身，输出标注合成）、`app/collectors/__init__.py`（注册表，每次返回新实例）。
- 迁移 `0003_platform_connections`：新增 `platform_connections`（凭证密文、状态、能力快照、最近检查）与 `sources.last_checked_at` 及索引；只向前追加，未改 0001/0002。
- API：`POST /sources/import-url`（202，幂等键，不联网）、`POST /sources/{id}/refresh`（If-Match）、`GET /platforms`、`PUT/DELETE /platforms/{platform}/credential`、`POST /platforms/{platform}/check`（30 秒冷却，未配置凭证时不发网络请求）；`/capabilities` 更新为 M2 Phase 1 并明确 `live_collection_verified=false`。OpenAPI 导出 39 路径 / 0.2.0。
- 长调用安全：worker 新增 `collect()` 分阶段处理与 `LeaseKeeper` 心跳；所有阶段写入带 `fence AND state='running'` 守卫；新增 `FenceLost` 处理，取消在阶段边界生效；`process()` 的短事务语义保持不变。
- 诚实状态：字幕可用→版本入库 + 另建 `process_source`（Mock 不分析真实材料，停在 `needs_review`）；缺字幕→`missing`；需登录→`blocked_credentials` 并可原地恢复；风控/网络失败→`failed` 且记录错误码，不生成主张/动态。
- 凭证：只保留 `SESSDATA`，Fernet(`MASTER_KEY` 派生) 加密；API 不回显、无日志；断开只删除本地密文。
- 前端：导入弹窗新增 B站 页签；平台账号页可保存/检查/移除凭证并显示真实状态；来源库显示“字幕已采集/缺字幕/需要登录/待采集/采集失败”。
- 测试：`python -m pytest -q` **91 passed**（48 既有 + 29 契约/凭证 + 14 采集流程，全部隔离 schema，无真实网络）；`npm --prefix apps/web run build` 通过；`alembic upgrade head` 到 0003，正式库仍为 1 用户 / 6 来源 / 2 动态 / 6 主张 / 6 任务（未删除或重建）。
- 启动/停止：`infra/launch.ps1 -NoBrowser` 脚本输出“已就绪”，`/health/ready` 返回数据库 `ok`，5173 返回 200；`infra/stop.ps1` 退出码 0，8000/5173/55432 全部释放。后台包装运行时因服务进程继承输出句柄，命令包装不会返回（工具/环境限制），验证改用文件重定向，不是启动脚本失败。
- 明确未做：真实网络烟测（未获用户授权）、关键词发现、调度、增量、分P批量、本地转写、官方开放平台 OAuth。不得把本轮称为真实 B站 采集通过。

## 2026-10-20 · 优先级调整：M2 B站采集器优先

- 用户判断本地文档批量导入价值不大，决定**无限期推后批量导入**；手动单文件导入保留为缺字幕/无授权/非视频来源的兜底。
- 用户决定**暂时完全推迟部署端密码恢复**。它不是当前使用阻塞，但仍是生产发布前的安全/运维待办；当前没有网页找回。
- 下一阶段主线转为 **M2 B站采集器**：参考 `dsh-bili-scroll` 的发现/字幕/调度/日报流程，但独立实现，不依赖 DSH、不照搬代码或价格假设；先做单链接导入、账号能力检查、采集器契约与测试替身，再考虑关键词发现和调度。
- 真实 B站采集需先核验接口与平台政策，并由用户在本机授权后低频烟测；不得绕过登录、付费、验证码或风控，未授权时不声称真实采集通过。
- 本轮只更新交接文档与优先级记录，没有改代码、迁移或数据库。

## 2026-10-20 · M1 核心：主题管理与阅读范围

按用户指示，将批量导入、语言偏好、密码恢复和完整自动 E2E 推迟，先完成信息聚合最核心的主题/实体组织与阅读筛选。

- 后端：新增 `POST /nodes`、`PATCH /nodes/{id}`（If-Match）、`POST /topic-links`、`DELETE /topic-links/{id}`；节点详情返回父/子主题、关联实体、所属主题、相关动态和来源主张；动态新增 `topic_id/entity_id/since/until/update_type` 筛选，默认最近 7 天。环由应用预检和数据库触发器双重拒绝（409 `TOPIC_CYCLE`）。
- 前端：主题页/实体页可新建；主题管理抽屉可加/删父主题、子主题、关联实体；主题/实体详情可“查看动态”并带入范围标签；动态页支持范围标签与 7/30/全部时间筛选，分页保持筛选。
- 测试：`python -m pytest -q` **48 passed**（真实 PostgreSQL）；`npm --prefix apps/web run build` 通过；隔离 QA + 无头 Edge 实测主题创建、管理抽屉、关联实体、子主题、主题动态筛选和清除范围。
- 未新增迁移；主题使用既有 `nodes` 与 `relations` 约束。仍未完成：批量 20 文件、语言偏好、部署端密码恢复、完整自动 E2E、`sort=changed` 等剩余端点/参数、长任务心跳与 scheduler；M1 完整签收仍未完成。
- 证据：`docs/m1.2-topics-preview.png`（合成 QA 数据）。

## 2026-10-20 · M1.1 现有闭环加固与接口一致性

基线与环境：

- 复现基线时发现既有 PostgreSQL 由旧沙箱令牌启动：新连接报共享内存 `Permission denied`，`pg_ctl` 也无法发送停止信号；随后用基本用户信任级别重启，未删除 `pgdata`、未覆盖 `.env`，正式库仍有 1 个既有用户。
- 当时修复后 `python -m pytest -q`：**47 passed**（真实 PostgreSQL 隔离 schema）；`npm --prefix apps/web run build`：通过。
- `infra/launch.ps1 -NoBrowser`、重复启动复用、`停止FishingNet.cmd`、端口冲突和伪 PID 归属均已实际执行。

本轮完成：

- 启动/停止入口：PID 记录启动时间与命令归属；端口被其他程序占用时拒绝启动且不杀进程；停止只结束匹配的进程树；服务未全部就绪不报告成功；新增停止双击入口；PostgreSQL 在提升 shell 下通过基本用户信任级别启动。
- API 契约：统一错误 envelope 与 `X-Request-Id`；If-Match 428/409/422；不透明游标与 400 非法游标；导入 Idempotency-Key；新增 `GET /jobs/{id}`、批量注销其他会话、`GET /health/ready`；OpenAPI 重新导出（32 路径）。
- 界面：修复页面切换时复用上一页数据导致的动态页未捕获渲染错误（白屏）；导入内容修改后换新幂等键；分页保留筛选；资料冲突刷新重试；会话页批量注销；错误提示可重试。
- 队列：并发领取、过期租约、fence、取消（排队/运行前检查点/忙锁）、重试、尝试耗尽和 worker 异常的回归测试；旧 fence 不能写入。
- 文档与证据：README、PROGRESS、ACCEPTANCE、CODEX 交接文档、OpenAPI 同步；`docs/m1.1-ui-preview.png` 为隔离 QA schema 的合成截图。

未完成（后续部分已按用户指示推迟）：批量 20 文件、语言偏好、部署端恢复、完整自动 E2E；长调用心跳/跨阶段恢复/scheduler；M2–M5。真实 B站、真实模型、事实核查、性能与恢复均未执行。

## 2026-10-19

- 已检查项目：初始目录仅有需求文档，无已有代码。
- 已读取产品、六表模型、证据与事实核查、采集与模型契约、任务队列及验收要求。
- 已确定沿用原技术栈和 M0→M5 顺序，无产品范围变更。
- 已创建项目独立 Python 环境（.venv）。
- PostgreSQL 官方二进制、Python 依赖和 npm 依赖均下载成功，没有下载阻塞。
- 已实现六张业务表与M1支撑表、两次迁移、证据及关系完整性约束。
- 已实现服务端账号/会话、手动导入、Mock样例、持久任务、动态修订、证据读取、关注/收藏/已读、当日日报、只读核查工作台。
- 前端中文页面、响应式样式、证据抽屉、导入表单及个人设置已落地。
- 当时通过33项测试和前端构建；已在浏览器检查隔离实例。
- 本机启动入口 http://127.0.0.1:5173；正式工作区尚待用户自行初始化。

## 验收状态

服务可运行，M1 核心与 M2 Phase 1/2/收尾的能力已交付；M0/M1 仍有边界项待补齐，M3–M5 未完成。真实 B站、真实模型、性能、安全、恢复演练均未执行。详见 ACCEPTANCE.md，不以 Mock、测试替身或合成数据代替真实验证。
