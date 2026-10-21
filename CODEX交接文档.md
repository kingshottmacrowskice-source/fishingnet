# FishingNet · CODEX 开发交接文档

## 当前状态（2026-10-22 · V1 精简版，迁移 0010 head）

用户已把第一版收敛为「定时按关键词选 B站 视频取字幕 → 每个视频一条总结 → 每轮汇总 + 实体更新 + **按天日报** → 定期检查 @ → 视频不重复总结」，高级入口（主题、事实核查、证据分档、表达复核、实体消歧等）主界面隐藏但**代码与数据全部保留**（目标见 `docs/V1-SCOPE.md`）。

- 迁移 `0009_simple_v1` + `0010_daily_digest`（`daily_digests` 表）；新模块 `app/domain/summaries.py`、`app/jobs/simple.py`、`app/simple_api.py`（`/api/v1/simple/*`）、`apps/web/src/simple.tsx`；`discovery_sources` 增加每天固定钟点 + 时区调度；`bilibili.py` 增加 `fetch_mentions`/`resolve_mention_video`/`fetch_pinned_comments`。
- 总结上下文：除字幕外，同时参考**视频简介（`desc`）+ 置顶评论**；实体抽取带 `type`，并做**确定性模糊实体过滤**（丢弃“作者自用预设/官方桌面端/在校大学生（主角）”这类描述/角色/标题），每轮合并近似重名。
- 日报：`rebuild_daily_digest` 把当天所有视频合成**一份统一日报**（今日总结 + 今日视频 + 今日实体），不再按轮次分段。
- 验证：`pytest -q` **415 passed / 99 warnings（66.81s）**；`npm run build` 通过；`alembic current` = **0010 (head)**；OpenAPI **76 路径 / 0.11.0**。
- 未验证：真实 B站 关键词发现/字幕/@ 通知、真实模型总结/汇总/实体更新/按天日报、「每天固定钟点 → 发现 → 总结 → 汇总 → 日报」无人值守端到端。

历史 M0–M4 与 V1 的详细开发/验收记录已归档到 `docs/HISTORY.md`。

交接日期：2026-10-21  
最后更新：2026-10-21（V1 精简版：视频总结 + 每轮汇总 + 自动日报 + @ 入口；历史 M1–M4 能力保留但主界面隐藏）  
项目目录：`C:\Users\Administrator\Desktop\fishingNet`  
需求基线：`AI多来源信息聚合平台_开发需求文档.md`，v1.2  
当前阶段：**M1 核心已按用户优先级收口（M1.1 加固 + 主题管理/阅读筛选）；M2 Phase 1、Phase 2 与收尾已完成；M3 Phase 1（模型供应商/路由/预算/账本）、M3 Phase 2（路由化抽取与服务端锚点校验）、M3 收尾（表达语义复核、实体候选人工确认）已完成；M4 Phase 1（库内事实材料、E0–E3 规则分档、追加型核查记录、人工接受/拒绝、重要事实 E3 硬门槛、标题/正文发布门槛与发布预览）、M4 Phase 2（`fact_check` 仅建议路由、来源版本过期封顶、断章取义拦截、`scoped_fact` 必须人工接受、AC32–45 阻断数据集）与 M4 Phase 2 补充（模型自动建实体 + 按实体分组的动态）已完成**；**V1 精简版（每个视频一条总结、每轮汇总与实体更新、当日日报、@ 入口、每天固定钟点调度、三入口视频去重）已实现，迁移 0010 (head)**。批量导入、语言偏好、部署端密码恢复、完整 E2E 已获用户同意推迟。B站 元数据/字幕与 WBI 关键词搜索已通过真实烟测；**真实模型连接测试已由用户在本机执行 1 次（MIMO 2.6，固定合成提示，成功），但真实模型抽取/复核/实体消歧/事实核查、以及 V1 的“每天固定钟点 → 发现 → 总结 → 汇总 → 日报”无人值守端到端均未用真实服务验证**，不是完整 MVP。**“每天固定钟点”调度已在 V1 实现（字段+逻辑），但真实无人值守链路仍未烟测，见 `docs/ROADMAP.md`。**

优先级更新：2026-10-20 用户决定把密码恢复暂时完全推迟、批量导入无限期推后，主线转向 B站 自动采集。M2 Phase 1 完成单链接导入与契约；Phase 2 完成发现流水线与调度；M2 收尾完成批量确认、恢复、日报与保留；M3 Phase 1 完成模型网关与费用控制；M3 Phase 2 完成抽取接入与服务端锚点校验；M3 收尾完成表达语义复核与实体候选人工确认；M4 Phase 1 完成事实证明材料、版本化核查记录、人工复核与发布门槛；M4 Phase 2 完成建议路由、过期版本封顶、断章取义拦截与 AC32–45 数据集；M4 Phase 2 补充完成模型自动建实体与按实体分组。**2026-10-21 用户进一步收敛范围，要求“第一版只干好：定时按关键词选视频取字幕并每个视频生成一条总结、每轮汇总并更新实体与日报、定期检查 @ 并处理被 @ 视频、视频不重复总结，其他功能暂时隐藏”。该 V1 精简版已实现并迁移 0010 (head)，高级入口仅隐藏不删除。** 真实上游请求仍应低频、可中止、记录结果。

## 1. 如何把下一项任务交给 CODEX

在 Codex 中打开同一个项目目录，新建一个开发对话，把本文第 12 节的任务提示完整发给它即可。继续当前对话也可以，不必复制整个项目。

下一位 CODEX 应先读取本文件和需求原文，再检查当前代码、数据库迁移与 Git 状态；不要仅依赖历史对话总结。用户可能在交接后登录、导入或修改数据，必须保留这些变化。

**真实 B站 烟测已通过（2026-10-20）：** BV1MLJe6fEmH 元数据/字幕（6 片段，ai-zh）+ WBI 签名关键词搜索（`"AI"` 两页各 20 条）+ B站平台连接（connected）全部验证通过。**V1 精简版已实现（迁移 0010 head），M3 Phase 2/收尾、M4 Phase 1 与 M4 Phase 2 第一部分也已完成；用户完成 1 次真实模型连接测试（MIMO 2.6，固定合成提示，成功），但真实模型抽取/复核/实体消歧/事实核查、以及真实 V1 发现/总结/汇总均未执行。建议下一项任务：在用户明确授权下，用固定合成材料真实执行一次“视频总结 + 每轮汇总与实体更新”，再低频真实验证关键词发现/字幕/@，最后跑通“每天固定钟点 → 发现 → 总结 → 汇总 → 日报”无人值守端到端；随后再考虑接入 `claim_comparison` 模型路由。** 分P批量（需确认）、账号过期/风控恢复、日报联动与 `discovery_run_items` 保留策略已在 M2 收尾完成。不要重新搭建项目，不要为了“干净”删除数据库、`.runtime/pgdata` 或覆盖 `.env`，也不要把 V1、M1 核心、M2、Mock、测试替身、M3、M4 写成完整 MVP 或“真实模型/真实采集已接入”。

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

> **V1 精简版（2026-10-21）是当前主界面。** 下表 M1–M4 的高级能力（主题、事实核查、证据分档、表达复核、实体消歧等）代码与数据全部保留，但在 V1 主界面暂时隐藏；它们不是已删除，只是不暴露在精简入口。

| 范围 | 当前实现 | 限制 |
|---|---|---|
| V1 精简主流程 | 关键词来源（可配每天固定钟点 + 时区或间隔）→ 自动发现 B站 视频 → 取字幕 → **每个视频一条总结（信息动态）**；每轮结束汇总本轮动态、更新实体简介、更新当日日报；定期检查登录账号收到的 @ 并把被 @ 视频送入同一流程；关键词/手动/@ 三入口共用视频身份去重 | 真实关键词发现/字幕/@ 通知、真实模型总结/汇总/实体更新均未烟测；“每天固定钟点 → 发现 → 总结 → 汇总 → 日报”无人值守端到端未用真实服务跑通 |
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
| API | 统一错误 envelope、`X-Request-Id`、428/409 If-Match、不透明游标、导入幂等键、主题/节点/关系端点、平台账号/B站导入/扫码登录端点、关键词来源与运行记录端点、分P单P/批量端点、凭证恢复端点、模型供应商/模型/任务路由/测试调用/调用记录/费用汇总/预算端点、模型实体候选复核端点、事实材料/核查任务/核查记录/人工复核/核查能力/来源画像/GW发布预览端点、**V1 精简只读面 `/api/v1/simple/*`（feed/rounds/entities/digest/mentions + 每轮重试）**；OpenAPI **76 路径（0.11.0）** | 仍缺部分需求 12.2/12.4 的端点（feedback、digests 范围、来源画像评估、reliability-assessments、claim_comparison、批量复核等） |
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
| `migrations/versions/0001_baseline.*`、`0002_integrity.*`、`0003_platform_connections.*`、`0004_discovery.*`、`0005_recovery_retention.*`、`0006_ai_gateway.*`、`0007_m3_wrapup.*`、`0008_verification_loop.*`、`0009_simple_v1.*`、`0010_daily_digest.*` | 六表、M1支撑表、关系/证据约束、平台连接、关键词发现配置/运行/决定与 checkpoint、恢复状态与发现明细保留索引、模型供应商/模型/任务路由/调用/预算账本、表达语义复核与 proposed 关系索引、事实证据载荷/锚点与核查记录约束、V1 精简版（simple_mode、每天钟点字段、视频总结/每轮/实体历史/@ 监控表）、按天日报（`daily_digests`）；当前head=**0010** |
| `app/domain/summaries.py` | V1 纯函数：视频身份、长字幕全量分段、总结输出校验、有界提示、@ 通知视频解析 |
| `app/jobs/simple.py` | V1 流水线：`summarize`（分段+归并+去重锁）、`tick`/`finish_round`（每轮汇总/实体/日报）、`poll_mentions`（只读 @） |
| `app/simple_api.py` | V1 只读面：`/api/v1/simple/feed|rounds|entities|digest|mentions` + 每轮重试 |
| `apps/web/src/simple.tsx` | V1 精简主界面（信息动态/自动任务/实体/日报/设置），`main.tsx` 渲染 `SimpleHome` |
| `tests/test_simple_v1.py` | V1 流程集成/单元（隔离 schema、`httpx.MockTransport`、合成字幕/模型输出，不触网不调用真实模型） |
| `docs/V1-SCOPE.md` | V1 精简目标范围（目标，非已实现声明） |
| `tests/test_policy.py` | 发布门槛和片段校验测试 |
| `tests/test_integration.py` | 独立PostgreSQL schema的集成与队列回归 |
| `tests/fixtures/plugin-x.json` | 6份合成材料；不是完整评测集 |
| `docs/openapi.json` | 当前API契约（76路径，版本0.11.0，含 V1 `/api/v1/simple/*`） |
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

## 6. 本轮实际验证（2026-10-21，V1 精简版）

- 迁移：新增 `0009_simple_v1`（`simple_mode`、`daily_time/schedule_timezone`、`video_summaries`/`summary_rounds`/`summary_round_items`/`summary_entity_history`/`mention_monitors`/`mention_receipts`）；`alembic current` = **0009 (head)**；未改 0001–0008、未删库、未覆盖 `.env`。正式库 `simple_mode=true`。
- 后端测试：`pytest -q` **407 passed / 95 warnings（57.83s）**（真实 PostgreSQL 17 隔离 schema）。新增 `tests/test_simple_v1.py` 17 项（完整一轮：一个视频一条总结 + 实体更新 + 日报、重复来源版本复用、长字幕不截尾、关键词轮等待汇总、失败可见可重试、畸形输出不发布、@ 开关与去重、BV 跨分P身份、chunks 覆盖长段落、@ 解析与凭证游标、每天钟点调度、无字幕 partial、搜索失败不谎报无新内容、动态链接解析、汇总失败不动实体），全部合成 + `httpx.MockTransport`，**无真实模型、无真实 B站 请求**。
- 前端：`npm.cmd --prefix apps/web run build`（`tsc -b && vite build`）通过；**修复页面切换时旧列表数据串场导致的 `run.items`/`entity.sources` 未定义崩溃**（`simple.tsx` 在页面切换时先清空数据）。
- 真实网络：本轮**没有新增任何真实模型或 B站 请求**；真实 B站 烟测与 1 次模型连接测试沿用 2026-10-20 的历史记录。

**未验证/未执行：真实 B站 关键词发现/字幕/@ 通知，真实模型视频总结/每轮汇总/实体更新；V1 的“每天固定钟点 → 发现 → 总结 → 汇总 → 日报”无人值守端到端尚未用真实服务跑通。历史 M1–M4 能力（主题、事实核查、证据分档、表达复核、实体消歧等）仅主界面隐藏，代码与数据保留。不得把 407 项测试解释为 V1 真实采集/真实总结已通过，也不得称为完整 MVP。**

历史验证轮次（M4 Phase 1 / M3 收尾 / M3 Phase 2 / M3 Phase 1 / M2 收尾 / M2 Phase 2）已归档到 `docs/HISTORY.md`。

## 7. 接手时必须注意的实现缺口

1. **Git 已有基线提交（`ac94d03`、`2d416f1`），但工作区改动未提交。** 不要执行 `git clean`、`reset --hard` 或重建目录；先检查用户新增修改，确保不包含 `.env`、`.runtime`、数据库和缓存。`新需求.md` 为用户提供的对话记录，属未跟踪文件，不要删除。
2. **迁移只能向前追加。** 0001–0010 已用于现有库，不能回头改 SQL 文件然后要求删库重来。后续约束、清理和数据修复通过 0011 及以后实施。启动脚本会自动执行 `alembic upgrade head`。
3. **PID文件不等于进程归属。** 本轮已用 JSON 记录 PID、启动时间、命令标记和项目根目录，并在停止前校验；仍需覆盖更多场景（服务崩溃、端口被占、连续双击、用户手动启动同名进程）。不要退回只按 PID 存在判断。
4. **API契约已覆盖主要列表，但仍有缺口。** `topic_id/entity_id/since/until/update_type` 已支持，`sort=changed`、relations 审查、feedback、digests 范围等仍未实现；新增接口必须继续使用统一错误 envelope、request_id、If-Match 和游标规则，并同步 OpenAPI。
5. **前端仍使用hash页面导航。** 详情是抽屉，不是 `/updates/:id` 深链接；主题管理已可在抽屉内完成，关键词来源面板本轮只有生产构建与接口回归，**没有浏览器实测**。修改列表渲染时要保留“切换页面先清空数据”的约束。
6. **worker可靠性只验证了单进程与有界网络调用。** `collect_source` 与 `discover_keywords` 都已把网络调用移出事务、用心跳续租并以 fence 守卫写入，且用 `jobs.checkpoint` 做跨阶段恢复；M3 网关也把预算预留、HTTP 调用与结算拆成三段短事务。但仍没有多进程/多节点调度、没有跨进程并发配额，scheduler 运行在 worker 进程内。运行中取消可能需要等阶段边界；不要为了“长任务”把网络调用放回持锁事务。
7. **删除是逻辑失效，不是物理清除。** 不可变版本触发器当前禁止修改/删除。关键词来源删除是 `deleted_at`；`discovery_runs` 永久保留，`discovery_run_items` 默认只保留 30 天并由 worker 有界清理。**不要**为此加会破坏运行账本的级联删除，也不要让清理触及 `queued/running` 运行或来源 watermark。
8. **发布策略仍不完整，但 M4 Phase 1 已把事实材料与人工复核接进门槛。** 当前策略按传入的 claim + `current_verification_id` 记录评估；`visible_update` 同时约束标题与正文，并用 `mode_is_within` 防止已存储句子被升级。仍缺来源画像、纠错传播、日报/导出/搜索的统一重算与 AC32–45 专项签收；任何新的“接受/确认”入口都必须先补验证，不能把编辑 assessment 或 checker_type 当作确认证据。
9. **任意真实材料不进入Mock自动分析。** 不要把“待复核”误判为导入失败，也不要把“发现入队”误当成“已经采集/分析完成”。
10. **M0文档及数据集仍不完整。** 6份样例不足以替代30来源、100主张及事实核查专项集。锁文件与依赖许可证库存存在，但生产复核尚未做完。
11. **PostgreSQL 权限与编码是环境级故障点。** 不要把管理员令牌启动失败误判为数据库损坏；不要删除 pgdata。PowerShell 中文脚本必须保持 UTF-8 BOM。
12. **OpenAPI 由应用内 `_openapi_document()` 生成。** 修改接口后重跑导出脚本；不要手改 `docs/openapi.json` 与代码脱节。当前为 **76 路径 / 0.11.0**（V1 精简版新增 `/api/v1/simple/*` 6 条路径）。
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

## 8. 已完成：V1 精简版 / M4 Phase 1 / M3 Phase 2 / M3 Phase 1 / M2 收尾 / M2 Phase 2

### 本轮（V1 精简版）

| 顺序 | 工作 | 结果 |
|---|---|---|
| 1 | 范围收口 | 用户确认第一版只做：关键词定时选视频取字幕并每视频一条总结、每轮汇总+实体+日报、@ 入口、视频去重；其余入口隐藏不删除（`docs/V1-SCOPE.md`） |
| 2 | 迁移 0009 | `simple_mode`、`daily_time/schedule_timezone`、`video_summaries`/`summary_rounds`/`summary_round_items`/`summary_entity_history`/`mention_monitors`/`mention_receipts`；head=0009 |
| 3 | 总结与去重 | `summaries.py`（视频身份/长字幕分段/输出校验/提示）、`simple.summarize`（分段+层级归并+会话级 advisory lock 防并发重复计费） |
| 4 | 每轮收尾 | `simple.tick`/`finish_round`（汇总简报、实体简介更新、当日日报、失败可见可重试） |
| 5 | @ 入口 | `bilibili.fetch_mentions`/`resolve_mention_video` + `simple.poll_mentions`（只读通知、识别视频并入队，不发评论/私信） |
| 6 | 每天钟点调度 | `discovery.next_configured_run`（daily_time + 时区），scheduler 已接；间隔模式兜底 |
| 7 | API/前端 | `/api/v1/simple/*` 只读面 + 每轮重试；`simple.tsx` 精简主界面；模型配置收敛到两项任务；修复页面切换数据串场崩溃 |
| 8 | 测试/文档 | 407 passed（新增 17 项）；前端构建通过；README/PROGRESS/ACCEPTANCE/ROADMAP/交接文档更新 |

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

0. **V1 精简版真实验证（当前主线）**：在用户明确授权下，用固定合成材料（非用户导入材料）真实执行一次“视频总结 + 每轮汇总与实体更新”（需显式启用 `update_generation`/`digest_polish` 路由、预算充足，检查真实模型 JSON 能否通过服务端校验）；再用低频方式真实验证一次关键词发现/字幕/@ 通知；最后跑通“每天固定钟点 → 发现 → 总结 → 汇总 → 日报”的无人值守端到端并记录错误码与费用。任何真实测试都不能降低发布门槛、默认预算或云端确认要求，也不得发送真实导入材料给未授权端点。
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

用户已多次调整优先级，必须在第二、八、九节保留记录。当前结论：**2026-10-21 用户进一步收敛范围，要求第一版只干好“定时按关键词选视频取字幕并每视频生成一条总结、每轮汇总并更新实体与日报、定期检查 @ 并处理被 @ 视频、视频不重复总结、其他功能暂时隐藏”；该 V1 精简版已实现（迁移 0010 head），高级入口仅隐藏不删除**。M1 核心收口；批量导入、密码恢复、语言偏好、完整 E2E 推迟；M2 采集器优先且 Phase 1/2/收尾已完成；M3 Phase 1 模型供应商/路由/预算/账本已完成、M3 Phase 2 抽取接入与服务端锚点校验已完成、M3 收尾表达语义复核与实体候选人工确认已完成；M4 Phase 1 事实证明材料/核查记录/人工复核/发布门槛已完成，M4 Phase 2 第一部分（建议路由、过期封顶、断章取义拦截、AC32–45 数据集）已完成，但真实模型请求、真实抽取/复核/实体消歧/事实核查、真实 V1 发现/总结/汇总均未执行；手动导入仅兜底。

## 12. 可直接发给下一位 CODEX 的任务提示

```text
请继续开发 C:\Users\Administrator\Desktop\fishingNet 项目。

先阅读根目录 CODEX交接文档.md、AI多来源信息聚合平台_开发需求文档.md、docs/V1-SCOPE.md、docs/ACCEPTANCE.md、docs/bilibili-api-policy.md、docs/adr/0003-discovery-pipeline.md、docs/adr/0004-model-gateway-budget.md 与 docs/adr/0005-fact-verification.md；再检查代码、数据库迁移与Git状态。当前是V1精简版 + M0/M4过渡开发预览：**V1精简版（2026-10-21，迁移0010，head）已把产品收敛为“关键词定时选视频取字幕→每个视频一条总结→每轮汇总+实体+日报→定期检查@→三入口视频去重”，主界面只保留信息动态/自动任务/实体/日报/设置，高级入口隐藏但代码与数据保留**；M1核心（M1.1加固、主题管理、范围/时间筛选）、M2 Phase 1（B站单链接导入、账号能力检查、元数据/字幕状态、采集器契约与测试替身、网页扫码登录）、M2 Phase 2（关键词来源配置、排除词/语言/每轮上限、限速与退避、调度、增量watermark、分P定位、跨阶段持久恢复）、M2收尾（分P批量需确认、凭证过期/风控恢复、发现结果与日报联动、discovery_run_items保留策略）、M3 Phase 1（模型供应商与密钥、OpenAI原生/兼容/本地/Mock协议、8任务路由与回退、预算预留/结算、调用与费用账本、模型管理界面）、M3 Phase 2（路由化主张抽取接入 process_source、有界提示、服务端引用/锚点校验、无锚点主张保持待复核）、M3收尾（evidence_review表达语义复核、entity_disambiguation仅已有实体候选、人工接受/拒绝）、M4 Phase 1（库内事实材料、E0–E3规则分档、fact_check任务、追加型核查记录、人工接受/拒绝、重要事实E3硬门槛、标题/正文发布门槛与发布预览）与M4 Phase 2第一部分（fact_check仅建议模型路由、来源版本过期封顶E1、断章取义拦截、scoped_fact必须人工接受、AC32–45阻断数据集48案例+12对照）已完成。真实B站元数据/字幕与WBI关键词搜索已烟测通过；真实模型连接测试已由用户执行1次（MIMO 2.6，固定合成提示，成功），**真实模型抽取、真实语义复核、真实实体消歧、真实事实核查（含外部检索与模型核查）、真实V1发现/总结/汇总**、真实扫码确认、分P批量、凭证恢复与定时调度尚未验证。批量导入、密码恢复、语言偏好、完整E2E已按用户决定推迟，不是完整MVP。不要重建项目、删除数据库或覆盖.env；保留用户现有账号和数据。Git 已有基线提交，未跟踪文件（如 `新需求.md`）也是已有成果，不能清理掉。不要删除或改写已完成的V1、M1核心、M2 Phase 1/2/收尾、M3 Phase 1/2/收尾、M4与迁移0001–0009。

真实 B站 烟测已通过（2026-10-20）：BV1MLJe6fEmH 元数据/字幕（6 片段）+ WBI 签名关键词搜索（两页各 20 条）+ B站平台连接全部验证通过。**V1 精简版已实现并迁移 0010 (head)，M3 Phase 2/收尾、M4 Phase 1 与 M4 Phase 2 第一部分也已完成，真实模型连接测试成功一次；建议下一项优先为 V1 真实验证**：在用户明确授权下用固定合成材料真实执行一次“视频总结 + 每轮汇总与实体更新”（显式启用 update_generation/digest_polish 路由、预算充足，检查真实模型 JSON 能否通过服务端校验），再低频真实验证一次关键词发现/字幕/@ 通知，最后跑通“每天固定钟点 → 发现 → 总结 → 汇总 → 日报”的无人值守端到端并记录错误码与费用；随后再考虑接入 claim_comparison 模型路由。任何真实测试都不能降低发布门槛、默认预算或云端确认要求，不得发送真实导入材料给未授权端点。真实网络调用继续保持短事务+心跳租约+fence，模型调用必须“预留预算→无事务HTTP→结算”；不要为通过烟测而放宽分P批量确认、恢复冷却、明细保留策略、云端 confirm_cloud、复核/实体人工确认、事实材料人工接受或默认零预算。

保持六表语义、不可变证据、工作区隔离、事实发布门槛和默认零预算。Mock/测试替身只用于合成例子，真实导入、采集、发现或模型测试不得伪装成AI分析完成。未实现的模型、平台及事实确认能力如实标注，不隐式收费或外发材料。B站凭证与模型密钥由用户在本机应用内提供，不得索取到聊天或日志，不得绕过登录、付费、验证码或风控。enabled与network_authorized必须保持分离且默认false；供应商/模型默认停用、任务路由默认未配置、预算默认0、数据策略默认local_only。事实核查目前只做库内（kb_only）规则评估，fact_check 模型路由只给建议且不改变分档：空结果不是反证，insufficient 不是虚假，corroborated 也不等于外部已查证；核查记录只追加，接受/拒绝必须新增版本，不得直接改状态或放宽 accept_gate。

先复现：.venv\Scripts\python.exe infra\init-local.py、.venv\Scripts\python.exe -m pytest -q、npm.cmd --prefix apps/web run build、infra\launch.ps1 -NoBrowser 与停止入口。注意PostgreSQL不能用管理员令牌直接启动；PowerShell中文脚本保持UTF-8 BOM；先停止服务会让测试连接失败。

需要新依赖时，若下载失败或卡住，先停止，把准确下载链接和错误交给我看，不要无限重试、切换不明镜像或改我的代理。已有授权范围内的常规开发请直接推进。

完成后更新README、docs/PROGRESS.md、docs/ACCEPTANCE.md、docs/HISTORY.md、CODEX交接文档.md；API变化同步docs/openapi.json。报告实际测试结果、可运行入口、本轮完成项与仍未完成项。不要把M1核心、M2、M3 Phase 1/2/收尾、测试替身或Mock说成真实模型已接入、真实B站采集全流程或整个MVP已完成；没有真实授权烟测就明确写未执行。
```
