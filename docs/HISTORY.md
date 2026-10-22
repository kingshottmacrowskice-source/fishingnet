# 开发历史归档（分级存放）

> 本文件集中存放各里程碑的**详细开发与验收记录**（时间倒序）。主文档只保留当前状态与使用说明，详细历史在此归档，避免主文档膨胀。所有历史结论以“当时的验证为准”，不代表当前状态；当前状态见 `README.md` 与 `CODEX交接文档.md`。

## 2026-10-22（UTC+8）· PostgreSQL 崩溃恢复的启动等待修复

- **现象**：10:50–10:52 连续停止/启动后，PostgreSQL 进入崩溃恢复（同步整个数据目录约 60s + WAL redo 约 71s），直到 **10:54:54** 才输出 `database system is ready to accept connections`；`infra/init-local.py` 固定等待 **30s** 就退出，日志里大量 `FATAL: the database system is starting up` 被误读为权限/端口故障。**数据库没有损坏，未删除 pgdata。**
- **`pg_isready.exe` 无法启动（0xc0000142）**：本机/受限令牌下该探针二进制加载失败，导致旧的就绪检测既不能确认“已就绪”也不能区分“仍在恢复”。已改为用 **psycopg 连接 `postgres` 库执行 SQL 级探测**（与 API 使用同一客户端库），不再依赖 `pg_isready.exe`。
- **修复**：`ready_timeout` 30→**300s**；等待期间每 10s 打印进度并提示“崩溃恢复可能需要几分钟，不要反复停止/启动”；PostgreSQL 进程存在但未监听时先等待而不是立即报错；超时信息附带 `postgres.log` 末尾。
- **验证**：恢复完成后 psycopg 连接成功：`fishingnet` 库 **15 nodes / 20 summary_entity_history links**，`pg_is_in_recovery()=false`；`tcp_ready()`、`db_ready()`、`wait_ready()` 均返回 True。
- **恢复方式**：数据库已就绪时只需再运行一次 **`启动FishingNet.cmd`**（不要先停止、不要重复点击）；等控制台出现“FishingNet 已就绪”再打开 http://127.0.0.1:5173 。若再次失败，把 `.runtime/postgres.log` 末尾和启动窗口输出一起发回。

## 2026-10-22（UTC+8）· V1 实体隐藏 / 删除与信息动态链接清理

- **保守自动隐藏**：`app/domain/summaries.py` 新增 `should_auto_hide_entity`。只对 `组织/机构/平台/人物` 生效；实体名（或 `entity_key` 去后缀后的规范名）没有出现在**关联视频（含历史轮次）的标题或简介**中时，节点 `metadata.hidden_by='auto'`。产品/工具/技术/其他永不自动隐藏。手动恢复会写 `no_auto_hide=true`，后续轮次不再自动隐藏；手动隐藏优先于自动规则。
- **实体 API（`app/simple_api.py`）**：`GET /simple/entities?include_hidden=` 返回 `hidden`/`hidden_by`/`hidden_count`；`PATCH /simple/entities/{id}` 手动隐藏/恢复；`DELETE /simple/entities/{id}` 删除 `summary_entity_history` 里该实体的全部链接（信息动态本身保留），并清理 `relations`/`subscriptions`，节点以 `status='deleted'` 墓碑保留，后续轮次按 `summary_entity_key` 跳过、不会重建。
- **日报同步**：`simple.refresh_daily_digest_entities` 在隐藏/删除后用**已存摘要 + 当天视频 + 可见实体**重算 markdown，不额外调用模型；`rebuild_daily_digest` 与实体列表都排除隐藏实体。
- **前端**：实体页增加“显示已隐藏（N）”；卡片对隐藏实体使用虚线样式并标注“自动隐藏/已隐藏”；实体详情增加隐藏/恢复与删除（二次确认，提示会清链接）。
- **验证**：`pytest -q` **418 passed / 99 warnings（65.79s）**（新增 4 项：纯函数规则、自动隐藏/手动隐藏/恢复/删除链接、历史标题保护、日报实体段不含隐藏实体）；`tsc` 通过；OpenAPI **78 路径 / 0.12.0**（`docs/openapi.json` 重导出）。顺带修正 `test_full_round_one_summary_entity_and_digest` 中把来源 ID 与总结 ID 比较的错误断言（改为 `source_id`）。
- **未验证**：真实模型抽取的实体类型/标题简介是否与自动隐藏规则吻合仍需烟测；删除墓碑只对同一 `summary_entity_key` 生效，改名后的同一对象可能重新出现。

## 2026-10-22（UTC+8）· V1 实体收口 + 总结上下文（视频简介 + 置顶评论）+ 日报按天合并

- **实体收口（不具体的实体不再出现）**：模型抽取的实体新增 `type`（产品/工具/人物/组织/机构/平台/技术/其他）；服务端在 `summary_output` 里增加**确定性模糊实体过滤**（`_is_vague_entity`），按名称丢弃：长度 >20、含括号角色描述（“在校大学生（主角）”）、书名号/方括号标题（“《莫愁乡》”）、以“模块/插件/预设/方案/桌面端/作者/用户/学生/主角”等后缀结尾、以及“软件/工具/系统/概念”等泛化词本身。提示词同步给出正反例。每轮汇总时增加一次 `digest_polish` 合并近似重名（“墨记” vs “墨记软件”），并把 `entity_type` 写入节点 metadata。
- **总结参考视频简介 + 置顶评论**：采集器 `fetch_source` 增加 `desc`（视频简介，去 HTML）与 `aid`；新增 `fetch_pinned_comments`（`/x/v2/reply`，只读 `data.top`/`data.upper`/`replies[].is_top`，失败不影响采集）；`simple.summarize` 把简介与置顶评论作为 `context` 拼入首个分段的总结提示。请求预算 4→5（`MAX_REQUESTS=5`）。
- **日报按天合并，不再按轮次**：迁移 **0010**（`daily_digests` 表）；`finish_round` 结束后 `rebuild_daily_digest` 把当天所有视频总结用 `digest_polish` 合成**一份统一日报**（今日总结 + 今日视频 + 今日实体），`/simple/digest` 直接读 `daily_digests`，不再输出“第 N 轮”分段。
- **验证**：`pytest -q` **415 passed / 99 warnings（66.81s）**；`npm run build` 通过；`alembic current` = **0010 (head)**（`desc`/`pinned_comments` 存 `sources.metadata`，`type` 存实体 jsonb/节点 metadata）。
- **未验证**：真实模型对“简介+置顶评论”上下文、实体收口与按天日报的表现仍需真实服务烟测；既有库里的模糊实体未清理（可按 `_is_vague_entity` 逻辑软删除）。

## 2026-10-21 17:40（UTC+8）· V1 精简版：视频总结 + 每轮汇总 + 自动日报 + @ 入口

用户最新指示（`docs/V1-SCOPE.md`）：第一版只做“定时按关键词选 B站 视频取字幕 → 每个视频一条总结 → 每轮汇总 + 实体更新 + 日报 → 定期检查 @ 并处理被 @ 视频 → 视频不重复总结”，其他功能主界面暂时隐藏。新增迁移 **0009**，未改 0001–0008、未删库、未覆盖 `.env`。

- 迁移 `0009_simple_v1`：`workspace_settings.simple_mode`（默认 true）、`discovery_sources.daily_time/schedule_timezone`、`video_summaries`（`UNIQUE(workspace_id,video_key)`）、`summary_rounds/summary_round_items/summary_entity_history`、`mention_monitors/mention_receipts`；head=0009。
- `app/domain/summaries.py`（新增）：`video_key`（BV 跨分P归一）、`chunks`（长字幕不截尾）、`summary_output`（受控校验）、`messages`（有界提示，材料内指令不执行）、`mention_video`。
- `app/jobs/simple.py`（新增）：`summarize`（分段+层级归并 + 会话级 advisory lock 防并发重复计费）、`tick`/`finish_round`（每轮汇总简报、实体简介、日报）、`poll_mentions`（只读 @，不发评论/私信）。
- `app/simple_api.py`（新增）：`/api/v1/simple/feed|rounds|entities|digest|mentions` + 每轮重试。
- 调度/采集：`discovery.next_configured_run`（每天固定钟点 + 时区）；`worker.py` 在 `simple_mode` 下分派 `process_source → simple.summarize`，`tick()` 接入 `simple.tick()` 与 `summary_round`/`poll_mentions`；`bilibili.py` 增加 `fetch_mentions`/`resolve_mention_video`。
- 前端：`apps/web/src/simple.tsx` 精简主界面；`main.tsx` 渲染 `SimpleHome`，模型配置收敛到“视频总结 + 每轮汇总与实体更新”；修复页面切换旧数据串场导致 `run.items`/`entity.sources` 未定义崩溃。

**验证**：`pytest -q` **407 passed / 95 warnings（57.83s）**（新增 `tests/test_simple_v1.py` 17 项，全部合成 + `httpx.MockTransport`，无真实模型/网络）；`npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0009 (head)**；`docs/openapi.json` 重导出 **76 路径 / 0.11.0**；本地 PostgreSQL 已迁移，正式库 `simple_mode=true`。

**未验证**：真实 B站 关键词发现/字幕/@ 通知、真实模型总结/汇总/实体更新；“每天固定钟点 → 发现 → 总结 → 汇总 → 日报”无人值守端到端未用真实服务跑通。历史 M1–M4 能力代码与数据保留，仅主界面隐藏。

## 2026-10-21 01:55（UTC+8）· M4 Phase 2 补充：模型自动建实体 + 按实体分组的动态

按用户指示完成“模型可自建实体 + 一个实体一条动态”；并把“每日定时检索与整合（自动化为主）”作为未实现需求记录（见 `docs/ROADMAP.md`）。未新增迁移、未改 0001–0008、未删库、未覆盖 `.env`。

- `app/ai/entities.py`：`entity_disambiguation` 允许空实体库调用；模型返回 `links`（仅已有 node_id）与 `new_entities`（name/entity_type/aliases/description/claim_refs）；服务端校验、同名去重、上限。
- `app/jobs/worker.py`：`_suggest_entities` 不再因无节点跳过；`_store_model_claims` 按 `canonical_name` 去重后自动建 `provisional` 实体（`asserted_by='model'`，不自动合并），并建立 `accepted` claim→node `about`；动态改为“按（来源版本 + 实体）分组，一个实体一条动态”，未归类仍逐条；既有实体链接仍 `proposed` 需人工接受。
- `app/main.py`：`/capabilities` 更新；API **0.11.0**（端点仍 70）。
- 前端：feed 卡片“关于 {实体}”标签 + 多句预览；任务页“新建实体 N 个”。
- 验证：`pytest -q` **390 passed / 93 warnings（47.89s）**；新增 `tests/test_m4_entities.py` 4 项；`npm build` 通过；`alembic current` = **0008 (head)**；OpenAPI **70 路径 / 0.11.0**。

**未实现（仅记录）**：每日固定时间段定时检索与自动整合；主题（topic）自动创建；真实模型实体消歧/建实体烟测。

## 2026-10-21 01:10（UTC+8）· M4 Phase 2：事实核查建议路由、版本过期封顶与 AC32–45 阻断数据集

完成 M4 Phase 2 第一部分（没有新增迁移、没有真实模型调用、没有外部检索、没有真实事实核查烟测）。未改 0001–0008、未删库、未覆盖 `.env`。

- `app/ai/factcheck.py`（新增）：`build_fact_check_messages` 只发送主张与库内材料元数据；`validate_advisory` 只接受受控 `supports/refutes/conflicts/insufficient/unclear`、已知 `material_refs`、理由与限制，拒绝编造引用、非法结论与任何外部读取自述（`MODEL_CLAIMED_EXTERNAL_READS`）；忽略模型置信度；`agreement` 只说明与规则分档是否一致。
- `app/jobs/worker.py`：`_fact_materials` 增加 `superseded`/`retention_expired`/`version_no`；新增 `_fact_check_advisory`（未配置路由直接跳过；否则 `LeaseKeeper` 内 `invoke_messages('fact_check')`）；`fact_check` 把建议写 `model_runs`，评估、记录状态与 `claims.fact_check_status` 不变。
- `app/domain/verification.py`：来源出现新版本后旧材料封顶 E1（`SOURCE_VERSION_SUPERSEDED`），保留期到期 E0；`assess` 增加 `stale_*`、`dispute_balance` 与不对称缺口；快照哈希纳入 `superseded`/`retention_expired`，旧记录接受时 409。
- `app/ai/extraction.py`：AC33 截断限定——引文在子句分隔符处截断且后半句含但/仅/不支持/尚未/正式版/beta 等标记时，主张进入 `unverified`（`CONTEXT_TRUNCATED_LIMITATION`），不发布。
- `app/domain/policy.py`：`scoped_fact` 必须来自人工接受（`checker_type='human'` + reviewer）的当前记录。
- 数据集：`tests/fixtures/ac32-45.json`（48 案例 + 12 对照，全部合成）、`tests/test_m4_ac_dataset.py`（86 项）、`tools/build_ac_dataset.py`、`docs/AC32-45-dataset.md`。
- 验证：`pytest -q` **385 passed / 93 warnings（45.72s）**；`npm build` 通过；`alembic current` = **0008 (head)**；OpenAPI **70 路径 / 0.10.0**；正式库 1 用户 / 7 来源 / 10 任务 / 1 供应商 / 1 模型 / 0 任务路由 / 1 调用 / 1 预算流水 / 6 条核查记录 / 0 条事实关系。

**未执行/未验证**：真实模型事实核查与真实外部检索；`claim_comparison` 模型路由；来源画像评估与记录；纠错表达式生成与统一重算；批量复核与浏览器点击实测。AC32–45 数据集通过不等于 AC01–AC45 全部签收。

## 2026-10-20 23:45（UTC+8）· M4 Phase 1：库内事实材料、版本化核查记录与人工复核

迁移新增 **0008**，未改 0001–0007、未删库、未覆盖 `.env`。没有外部检索、没有模型调用、没有真实事实核查烟测。

- `app/domain/fact_evidence.py`（新增）：`normalize_link` 生成 `fact_evidence.v1` 载荷；引文必须逐字出现在所选片段且唯一，偏移/哈希由服务端按 Unicode code point 计算；受控校验，条件缺省 `unknown`；材料作用映射为 `fact_supported_by/fact_refuted_by/fact_context_by`。
- `app/domain/verification.py`（新增）：确定性分档 E0–E3；`assess` 产出 `insufficient/corroborated/conflicted/refuted`、缺口、逐档计数与 `human_review_required/auto_accept_allowed=false`；`accept_gate` 拦截越权接受；`snapshot_hash` 检测材料变化。
- `app/jobs/worker.py`：新增 `kind='fact_check'` 任务（初始阶段 `retrieve`），只读库内材料，写 `kb_only` 检索清单、证据快照与评估；在 fence 事务内追加 `pending` 记录；**不改变** `claims.fact_check_status`。
- `app/main.py`：新增 7 条路径；`GET /claims/{id}` 返回核查材料/记录；复核要求输入指纹、claim revision 与材料快照哈希一致（409 过期/重复）；API **0.9.0**，端点 **70**。
- `app/domain/policy.py`：`publication.v1.3.0` + `mode_is_within`。
- 文档：新增 `docs/adr/0005-fact-verification.md`；同步 README、PROGRESS、ACCEPTANCE、数据字典、OpenAPI。
- 验证：`pytest -q` **299 passed / 93 warnings（43.84s）**，新增 `tests/test_m4_verification.py` 24 项；`alembic current` = **0008 (head)**；OpenAPI **70 路径 / 0.9.0**。

## 2026-10-20 21:25（UTC+8）· M3 收尾：表达语义复核与实体候选人工确认

迁移新增 **0007**（`claim_expression_reviews`、proposed 关系索引），未改 0001–0006、未删库、未覆盖 `.env`。自动测试未用真实模型。

- `app/ai/review.py`（新增）：`build_review_messages` 只发送带服务端锚点的主张、逐字引文和限定上下文；`validate_review_output` 只接受请求中出现过的 `claim_ref` 与受控 verdict（supports/partially_supports/unsupported/unclear）。
- `app/ai/entities.py`（新增）：`build_entity_messages` 只发送主张与库内已有实体；`validate_entity_output` 只接受请求中出现过的 `node_id`，拒绝编造节点，不创建或合并节点。
- `app/jobs/worker.py`：`_process_with_model` 抽取与锚点校验后按 `evidence_review` 执行一次语义复核、按 `entity_disambiguation` 执行一次实体候选；两次调用复用“短事务预留 → 无事务 HTTP → 结算/释放 + 调用账本”和 `LeaseKeeper`。`_store_model_claims`：只有锚点通过且复核为 supports/partially_supports 的主张写 `validated` 并发布；其余写 `candidate`。实体候选写 `relations(status='proposed', asserted_by='model')`。
- `app/main.py`：新增 `POST /relations/{relation_id}/review`；`GET /claims/{id}` 增加 `expression_reviews`/`about_nodes`；API **0.8.0**，端点 **63**。
- 验证：`pytest -q` **275 passed / 93 warnings（61.43s）**（新增 `tests/test_m3_wrapup.py` 20 项，全部 `httpx.MockTransport`）；`alembic current` = **0007 (head)**；OpenAPI **63 路径 / 0.8.0**。

## 2026-10-20 20:20（UTC+8）· M3 Phase 2：路由化抽取接入 process_source 与服务端锚点校验

没有新增迁移（head 仍 `0006`），未改 0001–0006、未删库、未覆盖 `.env`。

- `app/ai/extraction.py`（新增）：`build_claim_messages` 有界抽取提示；`validate_claim_output` 只接受请求中出现过的 `segment_id`，`quote` 必须逐字出现在片段可见文本，`char_start/char_end/quote_hash` 服务端按 Unicode code point 计算；类别/极性/重要性/可验证性受控词表；无锚点主张返回 `unverified` 候选。
- `app/ai/gateway.py`：`invoke` 拆出 `invoke_messages`，复用“短事务预留 → 无事务 HTTP → 结算 + 调用账本”。
- `app/jobs/worker.py`：`process()` 改分派器；原单事务逻辑保留为 `_process_local`（合成示例）。新增 `_process_with_model`：读路由 → 有界提示 → `LeaseKeeper` 内调用 → 校验 → fence 事务写 anchored claims；零预算 → `blocked_budget`；策略/鉴权/网络 → `failed`；输出非法或全部无锚点 → `needs_review`。
- `app/main.py`：`/capabilities` 增加 `model_extraction`；API **0.7.0**（端点 62）。
- 验证：`pytest -q` **255 passed / 93 warnings（30.71s）**（新增 `tests/test_m3_extraction.py` 21 项）；`alembic current` = **0006 (head)**；OpenAPI **62 路径 / 0.7.0**。
- 真实连接测试（用户本机，20:35）：MIMO 2.6 `openai_compatible`，`succeeded`，37/26 token，延迟 1625 ms，结算 0.0000445 USD；无 claims/updates、无导入材料外发。

## 2026-10-20 19:55（UTC+8）· M3 Phase 1：模型供应商、网关、预算与费用账本

迁移新增 `0006_ai_gateway.sql`：`provider_connections`、`model_profiles`、`task_routes`、`ai_invocations`、`ai_budget_ledger`，`workspace_settings` 增加 `budget_currency`/`routing_revision`；未改 0001–0005、未删库、未覆盖 `.env`。

- `app/domain/model_secrets.py`：模型密钥独立派生标签的 Fernet 密封，只显示 `****`+末 4 位。
- `app/ai/budget.py`：Decimal 计价；调用前按 token 上限预留最坏费用，调用后结算；未知用量保留全额预留并标记 unknown；UTC 日/月，单一币种无汇率。
- `app/ai/providers.py`：openai / openai_compatible / local / mock 四协议；固定 UA、无自动重试、不跟随重定向、限制响应大小、错误映射。
- `app/ai/gateway.py`：8 任务路由、资料策略门、主→备回退、超时/尝试次数、调用账本与连接测试。
- API 新增 12 路径，OpenAPI **62 路径 / 0.6.0**。
- 验证：**234 passed / 93 warnings（26.02s）**；`alembic current` = 0006；本轮没有任何真实模型请求。

## 2026-10-20 16:10（UTC+8）· WBI 签名关键词搜索真实烟测通过

用户授权扩展：关键词搜索可使用 bilibili-api-python 内置标准 WBI 签名（OE 表 + `_enc_wbi`）。`bilibili.py` 新增 `_get_wbi_key()`、`_sign_wbi()`、`_get_buvid()`；搜索前获取 buvid 并签名。真实烟测：WBI key 获取 OK，关键词 `"AI"` 两页各 20 条；搜索端点改为 `x/web-interface/wbi/search/type`。全量 181 passed，前端构建通过。M3 未开始。

## 2026-10-20 12:33（UTC+8）· 最终运行验证与数据库就绪修复

最终业务回归 179 passed / 33 warnings（21.05s）。启动脚本原先仅检查 TCP 端口，误把恢复中的数据库当作就绪，已修正为 `pg_isready` 协议就绪检查。新增 2 项独立回归。未删除数据库或重写 .env。

## 2026-10-20 12:32（UTC+8）· 字幕库接入完成、真实字幕取得；关键词被 HTTP 412 阻断

bilibili-api-python 17.4.1 及全部运行依赖已安装，pip check 通过。受限适配器 `apps/api/app/collectors/bilibili_subtitles.py` 继承库的 `Video.get_subtitle`，只保留获准的标准 WBI。真实样本 `BV1MLJe6fEmH`：6 片段，原文导出 `.runtime/exports/BV1MLJe6fEmH-subtitles.txt`。真实关键词 `AI DEEPSEEK`：首个请求 HTTP 412，任务 failed / RISK_CONTROL，立即停止未绕过。全量 179 passed / 33 warnings（22.42s）。

## 2026-10-20 12:26（UTC+8）· PyYAML 校验成功，后续下载及元数据查询被代理阻塞

`pyyaml-6.0.3-cp312-cp312-win_amd64.whl` 与官方 PyPI SHA-256 比对通过；Brotli 官方 wheel 下载遇 ProxyError，按约定立即停止。未安装新包，未更改业务代码。

## 2026-10-20 12:23（UTC+8）· 兼容性核对、依赖校验及字幕凭证隔离

本机 Python 3.12.14 / AMD64。lxml 6.1.3 cp312 win_amd64 wheel 已下载并核对 SHA-256。修复字幕正文请求附带平台凭证的问题（正文请求不传 Credential，清空持久 Cookie）；新增两项合成回归，`pytest -q tests/test_collectors.py tests/test_discovery_rules.py` 81 passed。

## 2026-10-20 12:14（UTC+8）· 使用用户提供的 wheel 接续，代理握手超时

BeautifulSoup 4.15.0 wheel 已核验。pip download 收集离线依赖时 `ProxyError: Cannot connect to proxy`，底层 `_ssl.c:993: handshake timed out`。已停止，未切镜像、未改代理。

## 2026-10-20 12:10（UTC+8）· 接续检查：字幕依赖下载失败，已停止

pip download（官方 PyPI、timeout=15、retries=0）报 `No matching distribution found for beautifulsoup4`，只能说明本次索引解析未取得可用分发，不能据此断言包不存在。未执行安装，requirements.lock 未变。

## 2026-10-20 07:35（UTC+8）· 字幕库接入准备，依赖安装中止

用户明确授权：仅字幕查询允许使用 bilibili-api-python 内置标准 WBI 签名。修复 `ai-zh/ai-en` 与语言偏好匹配；字幕空清单提示改为“本次接口未返回可用字幕轨”。全量 173 passed。核对发行包 bilibili-api-python 17.4.1：依赖许可证 GPL-3.0-or-later。发行 wheel 已下载，SHA-256 校验一致；随后 pip 下载 beautifulsoup4 无进展，人工中止。

## 2026-10-20 07:13（UTC+8）· 端口已释放，本地复现通过

Clash 出站连接已释放；`infra/init-local.py` 成功，`alembic current` = **0005 (head)**。正式库 1 用户 / 6 来源 / 6 任务 / 0 关键词来源 / 0 发现运行。全量 **170 passed，14 warnings，19.29s**。`infra/launch.ps1 -NoBrowser` 成功；健康检查 ok/ok，前端 200；`infra/stop.ps1` 退出码 0。本轮真实 B站 烟测尚未执行。

## 2026-10-20 07:06（UTC+8）· 真实烟测准备，因本地端口占用暂停

用户已授权本轮真实低频烟测（本人扫码、1 条自有 BV 链接元数据/字幕、1 个关键词来源手动 1 轮且 ≤2 页）。本轮初始化被本机 55432 端口占用阻塞：`verge-mihomo.exe` PID 25048 出站连接导致 PostgreSQL 绑定失败（Windows 10013）。已请用户释放，未改代理、未覆盖 `.env` 或删库。前端构建和 104 项无数据库单元测试通过；全量测试与真实请求未执行。

## 2026-10-20 · M2 收尾：分P批量确认、凭证/风控恢复、日报联动与明细保留

- 分P批量（需确认）：`POST /sources/{id}/parts/import`，必须 `confirmed: true`，一次 ≤20；任一所选分P不存在则整批 404 且不排队。
- 凭证过期/风控恢复：`-101`→`expired`，412/-352/429→`risk_control` 并写 15 分钟冷却；`POST /platforms/{platform}/recover` 只重排被阻塞任务，不改授权。
- 发现结果与日报联动：`GET /digests/today` 增加 `discovery` 结构；入队不等于已采集/已分析。
- 明细保留：`discovery_run_items` 默认保留 30 天，worker 有界清理；`discovery_runs` 与 watermark 不删。
- 迁移 0005：`platform_connections` 增加 `risk_control`/`cooldown_until`/`last_success_at`，`discovery_runs.finished_at` 索引。

## 2026-10-20 · M2 Phase 2：关键词来源、调度、增量 watermark、分P定位与跨阶段恢复

- 迁移 0004：`discovery_sources`/`discovery_runs`/`discovery_run_items`/`jobs.checkpoint`/`sources.discovery_source_id`。
- 关键词来源配置：1–5 关键词、≤20 排除词、优先语言、any/all、每轮 ≤20 条 / ≤3 页 / ≤4 请求、重扫窗口、定时间隔；`enabled` 与 `network_authorized` 分离且默认 false。
- 有界发现：每页一读、最多 3 页；本地再过滤；候选决定写 `discovery_run_items`。
- 限速与重试：1.5 秒最小间隔；超时/5xx 重试一次，412/429/-352/-403 立即停止；失败指数退避。
- 调度：worker 进程内短事务 `FOR UPDATE SKIP LOCKED`，只处理 `enabled AND network_authorized`。
- 增量 watermark：去重权威是 `sources (workspace_id, platform, external_id)`。
- 分P定位：元数据保存 ≤100 个分P；`POST /sources/{id}/parts/{p}/import` 单P。
- 跨阶段持久恢复：`jobs.checkpoint` 阶段快照，崩溃后从第一个未完成阶段继续。
- 全量 **165 passed**；OpenAPI 48 路径 / 0.4.0。真实关键词搜索未执行。

## 2026-10-20 · M2 Phase 1.5：B站 网页扫码登录

平台账号页生成 B站 登录二维码（`qrcode.react` 本机渲染，ISC），轮询等待扫码/已扫码/过期/风控；成功后只提取 `SESSDATA` 并加密。`qrcode_key` 只在 API 进程内存，不落库、不写日志。真实扫码尚未验收；真实网络已验证到“生成 + 轮询（返回 waiting_scan）”。

## 2026-10-20 · M2 Phase 1：B站 单链接导入与采集器契约

- 迁移 0003（平台连接）。`app/collectors/base.py` 定义契约；`fake.py` 是测试替身；`bilibili.py` 只接受含 BV 号的单条链接，单任务上游请求 ≤4 次，最小间隔 1.5 秒。
- 凭证安全：只保存 `SESSDATA`，`MASTER_KEY` 派生 Fernet 加密，不回显、不写日志。
- 三阶段短事务 + `LeaseKeeper` + fence；状态诚实（缺字幕/需登录/风控分别标记）。

## 2026-10-20 · 优先级调整：M2 B站采集器优先

用户决定把密码恢复暂时完全推迟、批量导入无限期推后，主线转向 B站 自动采集。参考 `dsh-bili-scroll` 的处理链但独立实现，不依赖 DSH、不照搬代码。

## 2026-10-20 · M1 核心：主题管理与阅读范围

主题/实体创建与 If-Match 编辑、父子主题、实体归属、环冲突 409、主题/实体详情与相关动态、删除关系；动态支持 `topic_id`/`entity_id`/`since`/`until`/`update_type` 筛选。仍未完成：合并/拆分、传递闭包展示、批量维护。

## 2026-10-20 · M1.1 现有闭环加固与接口一致性

- 启动/停止：PID 文件记录进程启动时间与命令归属，重复启动校验复用；端口占用明确报错。
- API 契约：统一 `{error,request_id}` envelope、`X-Request-Id`、If-Match 428/409、不透明游标、导入幂等键、`GET /jobs/{id}`、`POST /auth/sessions/revoke-others`、`GET /health/ready`。
- 队列回归：并发 worker `SKIP LOCKED` 只领取一次；过期租约重领并递增 fence；尝试耗尽 `ATTEMPTS_EXHAUSTED`。
- 界面流程：页面切换清理上一页数据；列表“加载更多”保留筛选。

## 2026-10-19 · M0 架构与基线

M0：架构、数据字典、六表迁移（0001 基线 + 0002 完整性约束）、契约与合成样例。六张核心表：sources、source_versions、claims、nodes、relations、updates。
