# ADR 0003：关键词来源、调度、增量 watermark 与跨阶段恢复

- 日期：2026-10-20（M2 Phase 2；M2 收尾补充分P批量、恢复、日报与保留）
- 状态：已实现，真实上游请求未验证

## 背景

M2 Phase 1 只支持用户逐条提交 B站 链接。用户要求进入第二阶段：关键词来源配置、排除词/语言/每轮上限、限速与重试、调度、增量 watermark、分P定位，并补齐跨阶段持久恢复。约束是不把网络调用放进持锁事务、不绕过平台风控、默认零预算、未授权不联网。

## 决策

1. **采集器契约扩展，而不是在 worker 里写平台逻辑。**
   `Collector` 增加 `discover(DiscoveryQuery) -> DiscoveryResult`；适配器只返回候选元数据，不写库、不读配置、不发多页请求。B站 适配器每次调用只读一页搜索结果，最多 3 页/任务。

2. **关键词来源是独立配置表，不改六表语义。**
   `discovery_sources` 保存关键词、排除词、语言偏好、每轮上限、页数/请求上限、重扫窗口、定时间隔、两个独立开关（enabled / network_authorized）与 watermark。发现产生的视频仍然进入 `sources → source_versions → jobs → claims/updates`，不是推荐流。

3. **联网授权与启用分离。**
   `enabled` 表示“参与调度”，`network_authorized` 表示“允许本机发起真实请求”。调度器只选取两者同时为真的来源；手动运行未授权来源返回 422 `NETWORK_NOT_AUTHORIZED`。这样用户可以在不联网的前提下先编辑和检查配置。

4. **调度器只写行，不做网络 I/O。**
   `app/jobs/scheduler.py` 在一个短事务里用 `FOR UPDATE SKIP LOCKED` 选取到期来源，推进 `next_run_at` 并插入 `discovery_runs` + `discover_keywords` 任务。它运行在 worker 进程内的独立 tick 中，保持现有三进程启动脚本不变；`FISHINGNET_SCHEDULER=0` 可整体关闭。

5. **失败退避而不是静默停用。**
   失败/风控运行写入 `last_error_code` 并递增 `failure_streak`，`next_run_at` 从 15 分钟起指数退避、不超过配置间隔；成功则清零。尝试耗尽或被取消的任务在 `claim()` 的清理阶段关闭对应运行记录并给来源退避，避免运行永远停在 queued。

6. **增量以来源唯一键为准，watermark 只做辅助。**
   去重权威是 `sources (workspace_id, platform, external_id)`：`pending` 或最近检查过的来源记为 duplicate，超过 `rescan_after_hours` 的记为 refreshed。`watermark.seen`（≤500 条）记录最近入队候选，`newest_published_at` 用于“整页都早于水位就停止翻页”。上游排序不被信任，本地仍按 published_at 排序并执行每轮上限。

7. **跨阶段持久恢复写入 `jobs.checkpoint`。**
   采集任务在元数据、字幕两个阶段完成后写入可序列化快照；发现任务每读完一页写入该页候选与已用请求数。重试（租约过期或手动重试）时 `claim()` 保留 `checkpoint->>'stage'`，任务从第一个未完成阶段继续；手动 retry 与凭证恢复会清空 checkpoint，以便重新验证。所有 checkpoint 写入与最终状态一样受 `fence AND state='running'` 守卫。

8. **每个候选都有决定与原因。**
   `discovery_run_items` 记录 enqueued/refreshed/excluded_term/keyword_miss/duplicate/over_limit/skipped_deleted，使“为什么没有结果”可解释，也避免把过滤说成上游没有内容。

9. **分P批量是显式、有限、原子的用户操作（M2 收尾）。**
   单P导入保留；批量端点要求 `confirmed: true`，一次 ≤20 个分P，重复序号去重；任一所选分P不在已保存的 `parts` 中则整批拒绝，不静默跳过。每个分P仍生成独立来源与独立采集任务，因此失败、取消、限速与 fence 语义不变；不下载视频、不抓取未选分P。

10. **凭证过期/风控是状态，不是重试。**
   采集错误码写入连接状态：`-101` → `expired`，412/-352/429 → `risk_control` 加 15 分钟本地冷却。恢复端点只重新排队被阻塞的采集任务，且服务端强制“必须已有替换后的凭证 + 冷却已结束”；不自动重试、不换账号/IP、不改来源或两个开关。

11. **日报读取运行账本，不复制事实。**
   `/digests/today` 汇总当日发现运行、入队候选与当日完成采集的来源，并保留错误码；明确“入队 ≠ 已采集/已分析/事实确认”。日报只读，不改变运行状态或发布门槛。

12. **明细有界保留，账本永久保留。**
   worker 约每 6 小时按 `FISHINGNET_DISCOVERY_ITEM_RETENTION_DAYS`（默认 30 天）有界删除已结束运行的 `discovery_run_items`；`discovery_runs` 的计数与来源 watermark 不删除，因此“运行过几次、入队几条、为何失败”仍可解释。

## 后果

- 优点：长调用与事务分离、崩溃可恢复、限额与政策边界可测试、发现结果进入既有证据链而非新数据模型；批量/恢复/日报都复用既有任务与账本，没有新增“推荐流”或事实捷径。
- 代价：多一张运行账本与一张候选决定表；`discovery_run_items` 会随运行增长，因此引入有界保留清理（只删明细，不删账本）；分P批量会排队多个真实任务，必须由用户逐次确认。
- 未覆盖：真实关键词搜索、真实分P批量、真实凭证过期/风控响应均未烟测；上游可能因签名/登录态拒绝（映射为 `DISCOVERY_DENIED`，不做绕过）；多进程调度与并发预算未实现。
