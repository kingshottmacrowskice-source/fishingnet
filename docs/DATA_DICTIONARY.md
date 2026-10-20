# 当前实现的数据字典

权威结构为 `migrations/versions/*.sql`。金额使用 PostgreSQL numeric，ID使用uuid，时间使用timestamptz；API将费用序列化为十进制字符串。

| 核心表 | 本次语义与约束 |
|---|---|
| sources | 独立来源身份；平台+外部ID唯一；当前版本复合外键验证归属；手动新导入生成UUID，指定source_id才更新已有文档 |
| source_versions | 只追加；段落/字幕片段JSONB、原始/规范化哈希、版本号、30天保留截止；禁止原地修改 |
| claims | 来源表达的主张；按版本+指纹幂等；validated必须有origin版本的accepted表达引用；事实状态与抽取分离 |
| nodes | entity/topic；名字不全局唯一；合成Plugin X使用明确fixture标识识别，不推广为按名称合并 |
| relations | 多态关系；PostgreSQL约束触发器验证端点、同工作区、类型矩阵、节点子类型、无环及表达锚点；删除端点时阻止悬空引用 |
| updates | event_key幂等；新增表达引用增加revision；首次发布时间保留；正文包含主张/证据/核查ID；追加修订快照 |

支撑表：workspace_settings、users、auth_sessions、verification_records、jobs、job_attempts、request_idempotency、subscriptions、user_item_states、feedback、audit_events、platform_connections、discovery_sources、discovery_runs、discovery_run_items、provider_connections、model_profiles、task_routes、ai_invocations、ai_budget_ledger、claim_expression_reviews。

`platform_connections`（0003，0005 扩展）：每个工作区每个平台最多一行；`credential_sealed` 保存由部署主密钥派生的 Fernet 密文（只含 SESSDATA），`status` 为 unconfigured/unverified/connected/expired/invalid/unavailable/risk_control，`capabilities` 是采集能力快照，`last_checked_at` 只由用户手动检查写入，`last_success_at` 记录最近一次成功采集阶段（不代表平台授权），`cooldown_until` 是本地风控冷却截止（不是平台解封承诺）。过期凭证与风控只能由用户重新提供凭证或等待冷却后显式“恢复”，不会自动重试。`sources.last_checked_at` 记录最近一次元数据/字幕检查时间；`sources.content_status` 区分 pending/ready/missing/permission_required/failed，缺字幕与需要登录不得互相冒充。

关键词发现支撑表（0004）：

| 表 | 语义与约束 |
|---|---|
| discovery_sources | 关键词来源配置：`keywords` 1–5 个、`exclude_terms` ≤20、`languages` 为字幕语言偏好、`match_mode` any/all、`page_policy` first/explicit + `target_pages` ≤3、`max_results_per_run` 1–20、`max_pages_per_run` 1–3、`max_requests_per_run` 1–4、`rescan_after_hours`、`schedule_interval_minutes` 60–10080；`enabled` 与 `network_authorized` 是两项独立开关，只有两者同时为真才会被调度；`watermark` 保存 `newest_published_at` 与最多 500 条 `seen` 映射；删除为 `deleted_at` 逻辑删除，历史运行保留 |
| discovery_runs | 每次运行一行：trigger（manual/schedule/retry）、state（queued/running/succeeded/failed/cancelled/blocked_credentials/blocked_upstream）、请求预算与实际页数/请求数、discovered/matched/excluded/duplicates/enqueued 计数、`job_id` 指向实际执行任务、错误码与安全说明 |
| discovery_run_items | 每个候选的处理决定：enqueued/refreshed/excluded_term/keyword_miss/duplicate/over_limit/skipped_deleted，带原因与可选的 `source_id`；同一次运行同一外部ID只允许一行，保证重试幂等。明细保留 `FISHINGNET_DISCOVERY_ITEM_RETENTION_DAYS`（默认 30，1–3650）天；worker 约每 6 小时有界清理已结束运行的旧明细，运行账本计数与来源 watermark 不删除 |
| jobs.checkpoint | 已完成阶段的结构化快照（元数据、字幕结果、已读搜索页、当前阶段），用于跨阶段持久恢复；写入同样受 `fence AND state='running'` 守卫 |
| sources.discovery_source_id | 该来源由哪个关键词来源发现（可空）；不改变 `sources` 的身份唯一键 `(workspace_id, platform, external_id)`，因此同一视频不会因多个关键词重复建档 |

模型与费用支撑表（0006）：

| 表 | 语义与约束 |
|---|---|
| provider_connections | 模型供应商：`protocol` 为 openai/openai_compatible/local/mock；`base_url` 必须是 http(s)、无账号信息/查询串/片段；`is_local` 只允许 local/mock；`credential_sealed` 为独立派生标签的 Fernet 密文（只含 api_key），`credential_hint` 只存 `****` + 末 4 位；`status` 为 unverified/ok/auth_failed/network_failed/capability_mismatch/disabled，`last_tested_at` 只由显式测试写入；默认 `enabled=false`；有模型引用时不能删除 |
| model_profiles | 真实模型配置：`(workspace_id,provider_id,model_id)` 唯一；`tier` 经济/标准/高质量；上下文 512–2000000、输出 1–200000；`supports_structured_output` 决定是否发送 `response_format`；单价为 numeric(20,8)/百万 token，`price_currency` 必须与工作区 `budget_currency` 一致，不做汇率换算；默认 `enabled=false`；被任务路由引用时不能删除（数据库触发器） |
| task_routes | 8 类任务的路由：`mode` rule/mock/model、`enabled`、`primary_model_id`（模型模式必填）、`fallback_model_ids` ≤3、输入/输出 token 上限、`timeout_seconds` 1–300、`max_attempts` 1–3、`data_policy` local_only/approved_cloud；整组保存使用 `workspace_settings.routing_revision` 做 If-Match；云端模型 + 允许云端策略要求在保存时显式 `confirm_cloud` |
| ai_invocations | 调用账本：task（含 connection_test）、status running/succeeded/failed/blocked、provider/model 快照、`request_fingerprint`、`input_summary`（只含字符数/消息数/跳过原因，不含正文）、`config_snapshot`（路由、限制、价格；绝不含密钥）、尝试日志、token、`cost_estimate`/`cost_settled`、`cost_status` none/estimated/unknown/confirmed、错误码与安全说明；不保存提示词、材料正文或模型完整输出 |
| ai_budget_ledger | 预算流水：每次付费调用前一行 `reserved`（按 token 上限的最坏费用），成功后改为 `settled` 并写实际估算费用，失败则 `released`；`day`/`month_start` 为 UTC 日与月初，`currency` 与工作区一致；调用被预算阻断时不写预留行，但会写一条 blocked 调用记录 |

`workspace_settings`（0006 扩展）：`budget_currency`（USD/CNY，默认 USD，已有其他币种账本时禁止更换）、`routing_revision`（任务路由整组保存的乐观锁版本）。预算窗口是 UTC 日与自然月；`ai_budget_ledger` 的 `reserved` 与 `settled` 都计入已用额度，`released` 不计入。

表达复核支撑表（0007）：

| 表 | 语义与约束 |
|---|---|
| claim_expression_reviews | 追加型表达语义复核：`claim_id`、`job_id`、`invocation_id`、受控 `verdict`（supports/partially_supports/unsupported/unclear）、有界 `rationale`、`limitations` 数组、`quote_count`、`model_label`、`schema_version`、`policy_version`；只追加（触发器禁止 UPDATE/DELETE）。它只回答“引文是否支持表达”，不是事实核查；不能写 `claims.fact_check_status`。`relations(status='proposed', asserted_by='model')` 是模型实体候选，人工接受后才用于实体视图；接受会补 `update→node` 的 accepted 关系，拒绝/接受都不改变事实状态。 |

M4 Phase 1 事实证明材料（0008）：

| 对象 | 语义与约束 |
|---|---|
| relations（`fact_supported_by` / `fact_refuted_by` / `fact_context_by`） | 主张 → 来源版本的**事实**证据关系，和 `evidenced_by`（永远只表示表达溯源）分开。创建时 `status='proposed'`；载荷必须是 `fact_evidence.v1`：`anchor{segment_id,char_start,char_end,quote,quote_hash}`、`proposition_scope`、`directness`、`origin_group_id`、`independence_status`、`applicability`（version/date/environment/region/method 各为 matched/mismatched/unknown）、`artifact_integrity`、`method_transparency`、`limitations`，可选 `note`。0008 的约束触发器重算引文与哈希，要求 accepted 关系带 `assessment_record_id`；只有人工接受核查记录时才把材料从 proposed 改为 accepted。状态只允许 proposed/accepted/rejected；每个主张最多 20 条非拒绝材料。 |
| verification_records（`record_kind='claim_fact_check'`） | 追加型核查版本。`review_status` 只允许 pending/accepted/rejected，`checker_type` 只允许 rule/model/human，`trigger` 只允许 new_claim/manual/evidence_added/evidence_retired/recheck_due/policy_upgrade。规则任务写 pending；人工复核追加一条带 `supersedes_id`、`reviewer_id`、`review_reason`、`reviewed_at` 的版本。`retrieval_manifest` 记录只读库内（`kb_only`）与 `external_reads=[]`；`evidence_snapshot` 保存材料 id、分档、条件、锚点、`superseded`/`retention_expired` 与 `snapshot_hash`；`assessment` 保存状态、E0–E3 正反强度、`stale_*`、`dispute_balance`、缺口与 `auto_accept_allowed=false`。`model_runs` 保存可选的 `fact_check` 建议路由结论/错误与调用信息；它**不进入** `assessment`，不改变分档、状态或发布门槛，也不构成外部检索。 |

模型调用不在数据库事务内执行：先短事务预留预算并创建调用行，HTTP 调用无事务/行锁，完成后另一短事务结算并更新账本。供应商未返回 token 用量时按预留额结算并标记 `unknown`，因此预算只可能被高估。当前没有供应商账单对账，`cost_status` 不会写成“已确认”。

`discovery_sources`、`discovery_runs`、`discovery_run_items` 都带 `workspace_id` 且复合外键限定同工作区；它们只记录配置、运行账本与候选决定，不保存正文、凭证或事实结论。

会话令牌仅存SHA-256哈希；密码使用Argon2id；写请求要求双提交CSRF令牌（服务端绑定其哈希）。初始化使用部署随机口令与数据库事务锁。

证据字符偏移采用Python Unicode code point，PostgreSQL substring按字符复核，不采用JS UTF-16长度。SRT/VTT时间保存毫秒。数据库再次校验accepted表达引用，应用校验不能代替数据库约束。

verification_records只追加；current指针只能指向同主张、已接受、输入指纹及revision匹配的记录。M4 Phase 1 起，规则任务会追加“库内事实材料评估”的 pending 记录，人工接受/拒绝再追加一条带 `supersedes_id` 的版本；只有人工接受且满足硬门槛（corroborated 至少 E2、重要事实需 E3、refuted 需 E3 反证）才会移动指针并改变 `claims.fact_check_status`。接受一条 insufficient 记录只记录缺口，不赋予事实发布资格；接受记录不等于外部查证。M4 Phase 2 起，`scoped_fact` 只允许来自人工接受（`checker_type='human'` 且有 reviewer）的记录；来源出现新版本后旧材料封顶 E1、保留期到期降 E0，材料快照哈希包含这两个标记，因此新版本出现后旧记录接受会返回 409。

模型抽取只产生候选：`claims.review_status='validated'` 仅表示服务端已在原文片段中逐字定位引用并计算出偏移与哈希，且（若启用了表达复核）复核判定为 supports/partially_supports；不表示事实为真。没有可校验引用的模型主张必须保存为 `candidate`；复核不支持、未完成或未覆盖的主张同样保持 `candidate`，不得生成动态。`fact_check_status` 由核查流程设置，抽取/复核模型不能写入。

jobs使用FOR UPDATE SKIP LOCKED、租约和fence；尝试上限3，过期租约重领时fence+1，支持取消/重试及尝试耗尽失败。`process_source` 的合成示例路径仍是单事务；M3 Phase 2 的模型抽取路径把网络调用放到事务外，用后台心跳续租，预留/结算各自短事务，最终 claims/updates 与任务完成写在同一 `fence AND state='running'` 事务中；M3 收尾在同一 `process_source` 中按路由增加一次表达复核和一次实体候选调用，仍逐次预留/结算，复核结果与候选关系在同一 fenced 事务写入。`collect_source`（B站）与 `discover_keywords` 已把网络阶段拆到事务外，用后台心跳续租，所有阶段写入以 `fence AND state='running'` 守卫，fence 丢失只回滚且不发布。两个长任务的已完成阶段写入 `jobs.checkpoint`，重试时从第一个未完成阶段继续；被尝试耗尽或取消的发现任务会在 `claim()` 的清理阶段关闭对应 `discovery_runs` 并给来源记录退避，不会留下永远 queued 的运行。0005 迁移只为恢复/保留补充状态与索引，不改六表语义。0006 已实现调用前预算预留、调用后结算与未知费用保守计费；0007 增加表达复核记录与可复核实体候选，不改变六表语义。真实模型抽取/复核/实体消歧、跨进程并发配额和供应商账单对账仍未实现。

物理内容清除、发布状态数据库硬门槛仍待后续迁移；事实证据载荷完整性约束与事实证据状态约束已在 0008 落地。模型/路由/预算配置表已在 0006 落地，表达复核在 0007，事实证明材料与核查记录约束在 0008。现有删除为立即逻辑失效，读取路径检查来源状态及保留期。
