# ADR 0004：模型供应商、统一网关、任务路由与预算/费用账本

- 日期：2026-10-20（M3 Phase 1；Phase 2 补充路由化抽取与服务端锚点校验；收尾补充表达语义复核与实体候选人工确认）
- 状态：Phase 1 已实现；Phase 2 抽取已实现；收尾复核/实体候选已实现；真实模型协议、抽取、复核与实体消歧均未验证

## 背景

M1/M2 只保存原文与引用，`process_source` 使用内置 Mock 语法，从未调用模型。M3 需要落实需求文档第 3.11、3.8、5.9 节：供应商连接与密钥管理、真实模型协议、逐任务模型分配与回退、默认零预算、并发预算和费用账本。约束是：默认不启用付费模型；未经用户显式动作不得外发材料或产生费用；不得把 Mock 结果写成 AI 分析完成；金额使用 Decimal；网络调用不得放回持锁事务。

## 决策

1. **新增四张运行支撑表，六张核心表语义不变。**
   `provider_connections` 保存协议、端点、是否本地、密封密钥与掩码提示、测试状态；`model_profiles` 保存真实模型 ID、限制、结构化输出能力与单价；`task_routes` 保存逐任务路由；`ai_invocations` 与 `ai_budget_ledger` 保存调用与预算流水。模型/供应商/路由都是运行配置，不进入证据链。

2. **协议只实现 MVP 列出的三类加 Mock。**
   `openai`（Chat Completions 原生）、`openai_compatible`、`local`、`mock`。Anthropic/Gemini 原生协议明确不实现。使用非流式 JSON 请求；模型声明支持结构化输出时附加 `response_format={"type":"json_object"}`。

3. **密钥与平台凭证分开派生、分开访问。**
   同一 `MASTER_KEY`，但 `fishingnet.model-credential.v1` 派生标签与独立 schema；平台 SESSDATA 无法被当成模型密钥解封，反之亦然。只保存密文和 `****` + 末 4 位提示；密钥不进入日志、错误信息、快照或响应。端点校验拒绝非 http(s)、内嵌账号、查询串和片段；请求不跟随重定向，避免把 `Authorization` 带到其他主机。

4. **默认全部关闭，三重门才可能产生费用。**
   供应商默认停用、模型默认停用、任务路由默认不存在（等价 `mock` 且未启用）、日/月预算默认 0、工作区数据策略默认 `local_only`。云端调用要求：路由启用且模式为 `model`、主/备用模型与供应商启用、工作区与路由数据策略均为 `approved_cloud`、保存路由时 `confirm_cloud: true`、最坏费用可通过预算预留。Mock 提供器不触网，费用恒为 0，不占预算。

5. **先预留，再调用，最后结算；网络调用不在事务内。**
   `budget.reserve` 在短事务里 `SELECT ... FOR UPDATE` 锁定工作区行，按“输入上限×输入单价 + 输出上限×输出单价”冻结最坏费用并写账本，然后提交。HTTP 调用没有任何数据库事务或行锁。成功后按供应商返回的 token 用量计算估算费用并结算，差额释放；失败/回退耗尽则释放预留。供应商未返回用量时按最坏情况结算并标记 `unknown`，宁可高估不可低估。

6. **路由是显式配置，不是自动发现。**
   8 类任务（初筛、主张抽取、实体消歧、主张比较、表达证据复核、事实核查、动态生成、日报润色）分别配置模式、主模型、≤3 个备用模型、token 上限、超时与尝试次数。整组保存是单个事务，使用 `workspace_settings.routing_revision` 做 If-Match；任一路由校验失败则整组不落库。被引用的模型不能删除（数据库触发器 + API 预检）；被引用的供应商有模型时不能删除。

7. **回退不等于重试绕过。**
   同一候选模型内只对超时/网络错误/5xx/429 按 `max_attempts` 重试；鉴权失败、端点不存在、非法响应立即放弃该候选。候选失败后按配置顺序尝试下一个模型。数据策略与预算不足是全局停止条件，不会通过回退“绕开”。

8. **账本记录被拒绝的尝试，但不伪造成功。**
   预算或策略阻断时写 `status='blocked'` 的调用行并带错误码；没有网络请求、没有 ledger 预留。成功调用记录 token、估算/结算费用、费用状态与尝试日志；配置快照只含路由/价格/限制，不含密钥与材料正文。

9. **费用状态区分“估算”和“未知”，不制造“已确认”。**
   MVP 没有供应商账单 API，因此 `cost_status` 只用 `estimated`（按实际用量×本地单价）、`unknown`（无用量，按预留计）和 `none`（Mock/免费）。界面明确说明未经供应商账单确认；不把估算写成“已确认费用”。

10. **显式测试调用是测试入口；真实抽取走 `process_source`（M3 Phase 2）。**
    `POST /ai/test-call` 要求 `confirm_spend: true`，使用固定合成提示，只返回结果与费用，不写 claims/updates/relations。`POST /model-providers/{id}/test` 只发送固定连接测试提示。真实材料只有在 `claim_extraction` 路由显式启用为 `model` 时才由 `process_source` 调用模型；合成示例（`metadata.demo=true`）始终走本地 Mock，未配置路由的真实材料停在 `needs_review`。

11. **模型只提出候选，服务端决定证据（M3 Phase 2）。**
    `app/ai/extraction.py` 只发送路由输入上限内的片段；模型只能引用请求中出现过的 `segment_id`，`quote` 必须在片段可见文本中逐字存在（空白折行归一化后映射回原文位置），偏移与哈希由服务端按 Unicode code point 计算；模型自报偏移不一致即拒绝。通过锚点的主张才写 `evidenced_by` 与 `insufficient` 核查记录并进入既有发布门槛；未通过的主张保存为 `candidate`，不建关系、不发布。抽取模型不能设置 `fact_check_status`，任务结果固定 `fact_confirmation=false`、`semantic_review=not_performed`。一次 `process_source` 至多一次模型调用，HTTP 期间由心跳续租，最终写入与任务完成在同一 `fence AND state='running'` 事务中回滚或提交。

12. **语义复核是第二个任务，不是事实核查（M3 收尾）。**
    `app/ai/review.py` 在抽取与锚点校验之后，按 `evidence_review` 路由发送有界 JSON：主张、服务端已校验的引文和限定上下文。模型只能返回 `supports`/`partially_supports`/`unsupported`/`unclear` 与理由/限制；服务端拒绝未知 `claim_ref`、非法 verdict、重复条目，未覆盖条目记为 missing。只有 supports/partially_supports 可进入发布门槛；其余以及复核未完成、失败、预算阻断的主张一律保存为 `candidate`，不建核查记录、不发布。复核记录写入追加型 `claim_expression_reviews`，`fact_check_status` 永不由它改变。复核路由未配置时保持 Phase 2 行为（锚点通过即可进入发布门槛），不会隐式调用第二模型。

13. **实体消歧：已有实体只给候选，缺失实体可自动新建（M3 收尾 → M4 Phase 2 扩展）。**
    `app/ai/entities.py` 发送主张与库内已有实体（名称、类型、有限别名）；模型返回 `links`（只能引用请求中的 `node_id`，服务端拒绝编造节点、未知主张、重复/超限/非法角色）与 `new_entities`（`name/entity_type/aliases/description/claim_refs`）。链接到**已有**实体的写为 `relations(status='proposed', asserted_by='model')`，仍需用户接受后才用于实体视图；接受会补 `update→node` 关系并写审计，拒绝保持不可见。对**库内不存在的实体**，服务端按 `canonical_name` 去重后自动创建 `provisional` 节点（`asserted_by='model'`，不自动合并两个已有实体）并立即建立 `accepted` 的 `claim→node about` 关系，使同一来源的主张按实体分组为一条动态。实体归属不改变 `fact_check_status`，也不构成事实确认。

## 后果

- 优点：默认零费用、预算在并发下不可穿透、崩溃只能高估费用、密钥有独立边界、路由/回退/费用可解释；运行账本与六表证据链解耦；模型输出不能自证引用，未锚定或未被语义复核支持的主张不会进入发布；实体候选不能由模型直接创建或合并节点。
- 代价：多出四张运行表、一张复核表与一套账单；未知用量会长期占用预算直到结算；单价需要用户手工维护且没有自动更新；每个真实调用都要先通过预算预留，低预算下可能被保守拒绝；一次抽取只发送有限片段，长文会被截断且截断部分不可引用；启用复核与实体路由后，一次来源处理最多产生三次模型调用（抽取、复核、实体候选），费用与延迟相应增加；复核/实体候选需要人工处理。
- 未覆盖：真实模型协议、抽取、复核与实体消歧烟测；事实核查与主张比较接入；跨进程并发配额（当前只保证单库预算预留原子性）；多节点调度；Anthropic/Gemini 原生协议；供应商账单对账；浏览器端模型面板与实体候选按钮的完整交互回归。另有三个已知边界：结算与写库之间崩溃时，重试会重新调用模型并再次计费（账本如实记录，当前不缓存模型输出）；`needs_review` 任务沿用 M1 不可重试语义，配置路由后要重新导入材料才会走模型抽取；复核阶段预算不足会把锚点主张保留为 candidate 并结束为 `needs_review`，不会自动重试（抽取阶段零预算仍可重试 `blocked_budget`）。
