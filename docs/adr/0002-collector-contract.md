# ADR 0002：采集器契约与网络阶段隔离

状态：采纳。日期：2026-10-20。

## 背景

M2 需要接入 B站 单链接导入。直接的做法是在请求处理或 worker 事务里同步调用平台接口；这会带来三个问题：

1. 网络延迟不可控，长事务会持有 `jobs` 行锁与数据库连接，拖住其他任务和取消操作。
2. 上游失败（风控、登录失效、缺字幕）容易被写成笼统的“失败”或“没有新信息”，违背证据诚实要求。
3. 平台凭证一旦进入日志或错误信息，等价于泄露账号。

## 决策

1. **契约先行。** `apps/api/app/collectors/base.py` 定义 `Capabilities / Locator / RemoteSource / SubtitleResult / AccountCapability / CollectorError`。适配器不访问数据库、不写日志，只返回值或抛出带稳定错误码的异常。
2. **导入与采集分离。** `POST /sources/import-url` 只做离线解析、去重、写 `pending` 来源与任务，不联网。网络行为只发生在 worker 的 `collect_source` 任务中。
3. **阶段化短事务。** `collect()` 依次执行 `fetch_metadata → fetch_subtitles → store_version`；每次网络调用之前与之后都是独立短事务，不跨网络持有行锁。
4. **心跳 + fence。** `LeaseKeeper` 在独立线程按租约三分之一周期续租，条件为 `state='running' AND fence=:fence`；任何阶段写入都带同样守卫。fence 丢失时只回滚并让出任务，绝不发布过期结果。
5. **状态必须可区分。** 字幕可用、缺字幕、需要登录、权限不足、风控/网络失败分别落到 `source_versions`、`sources.content_status`、`jobs.state/error_code`。真实材料在 Mock 网关下只保存原文并停在 `needs_review`。
6. **凭证最小化。** 只保存 `SESSDATA`，使用 `MASTER_KEY` 派生的 Fernet 密钥加密；API 不回显、日志不记录；断开只清除本地密文。
7. **上限优先于重试。** 单任务上游请求 ≤4 次，最小间隔 1.5 秒，只对超时/5xx 重试一次；`412/-352/-101` 立即停止，交由用户处理。

## 后果

- 采集失败不会污染业务表，也不会把“需要登录”伪装成“缺字幕”。
- worker 单进程模型仍可用，但尚未实现跨阶段持久恢复与 scheduler；崩溃后依赖租约过期重领，可能重复一次元数据请求（写操作有唯一键与 fence 保护）。
- 测试全部使用 `FakeBilibiliCollector` 与 `httpx.MockTransport`，不产生真实网络流量；真实烟测必须在用户授权后单独记录。

## 后续更新（2026-10-20 M2 Phase 2）

契约已扩展 `discover(DiscoveryQuery) -> DiscoveryResult`，新增关键词来源、调度、增量 watermark 与 `jobs.checkpoint` 跨阶段持久恢复，见 [ADR 0003](0003-discovery-pipeline.md)。上面“尚未实现跨阶段持久恢复与 scheduler”的描述保留为本 ADR 当时的决策背景，不再是当前状态；阶段化短事务、心跳 + fence、状态可区分与凭证最小化这四条约束继续有效。

