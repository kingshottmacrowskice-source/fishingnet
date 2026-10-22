# 开发与维护

## 仓库结构

- apps/web：React + TypeScript + Vite界面。
- apps/api/app：FastAPI、认证、数据库访问、Mock网关、worker、调度 tick、证据与发布策略。
- apps/api/app/collectors：平台采集契约（含 `discover()`）、B站适配器与仅用于测试的确定性替身。
- apps/api/app/domain/discovery.py：关键词配置校验、匹配/排除、每轮上限、watermark 与退避规则。
- apps/api/app/jobs/scheduler.py：到期关键词来源的短事务调度（只写行，不联网）。
- migrations：显式Alembic迁移，禁止运行时create_all；当前 head=0005。
- tests：策略单元测试与真实PostgreSQL集成测试。
- infra：本机启动/停止、隔离QA实例和未验收的容器配置。

## 数据安全

本地实例默认只监听127.0.0.1。当前HTTP Cookie适用于本机开发，不应直接公网部署；外网部署需要TLS和COOKIE_SECURE=true并完成安全验收。

`.env`、`.runtime`、`.venv`、node_modules均不提交。数据库主密码和初始化口令随机生成。测试输出不得包含密钥或完整真实材料。

禁止把来源输入拼为SQL、shell或系统指令。Mock只识别内置合成语法。未来真实模型只能输出待验证数据，所有生成都经过统一网关和发布策略。

## 本地故障排查

- 页面无法连接：先运行 `infra\launch.ps1 -NoBrowser` 查看阶段错误；检查 `.runtime/api.error.log`、`.runtime/web.error.log`；确认8000/5173未被其他应用占用。启动脚本不会停止占用端口的其他程序。
- 任务未运行：确认 worker PID 记录与 `.runtime/worker.log`；队列页面只显示脱敏错误类名。过期租约与尝试次数耗尽会记录为可解释状态。
- 数据库连接失败：检查 `.runtime/postgres.log`，确认55432可用；不要删除pgdata来“修复”。PostgreSQL不能以管理员令牌运行，`init-local.py` 会在提升shell下用基本用户信任级别启动；若日志出现共享内存 `Permission denied`，先停止再按此方式重启，而不是改pgdata。
- 启动时报 `FATAL: the database system is starting up`：这是上次未正常关闭后的崩溃恢复，日志会先 `syncing data directory (fsync)` 再 `redo in progress`，本机可能持续 2–5 分钟；`init-local.py` 现在等待 300 秒并每 10 秒打印进度。**不要在恢复期间反复停止/启动**，等出现 `database system is ready to accept connections` 后只启动一次。若 `pg_isready.exe` 报 `0xc0000142`，不影响判断：就绪探测已改用 psycopg 连接 `postgres` 库。
- 双击启动：重复打开会复用归属匹配的进程；停止请用 `停止FishingNet.cmd` 或 `infra\stop.ps1`，它们只结束命令、项目路径和启动时间匹配的进程树。
- 初始化口令：在本地 `.env` 中读取SETUP_TOKEN。已有管理员时初始化接口始终返回409；不要为了“重新设置”删除数据库或重置正式账号。
- 忘记管理员密码：当前尚未交付安全恢复工具，不通过前端或仅用户名重置；后续补一次性部署端恢复流程。
- 脚本乱码/解析失败：Windows PowerShell 5.1 需要 UTF-8 BOM；`infra` 下的中文 `.ps1` 改写后必须保留 BOM。
- 测试：先启动本地PostgreSQL，再执行 `python -m pytest -q`；测试用独立schema并自动删除，连接超时5秒。当前170项通过（含40项关键词规则/适配器单元测试、19项发现流程集成测试、5项M2收尾集成测试、36项不触网契约测试、14项采集流程测试、8项扫码登录测试），1条TestClient弃用警告。先执行停止入口会让测试连接失败，这不是测试失败。不要把测试放到后台作业启动：本轮一次后台启动时 PostgreSQL 服务进程收到控制台中断并快速关闭；重启数据库、前台执行后全部通过，未删除 pgdata。
- 关键词发现：`discover_keywords` 任务按页读取搜索结果并逐页写入 `jobs.checkpoint`，网络调用之间不持锁；`app/jobs/scheduler.py` 在 worker 进程内的 tick 中只做短事务入队，`FISHINGNET_SCHEDULER=0` 可关闭。调度器只处理 `enabled AND network_authorized` 的来源，任何把两者合并或默认开启的改动都会让真实请求在用户不知情时发生。
- 跨阶段恢复：`jobs.checkpoint` 保存已完成阶段（元数据、字幕、已读搜索页）；重试时 `claim()` 保留阶段并从第一个未完成阶段继续，手动 retry 与凭证恢复会清空 checkpoint。新增阶段必须通过 `_checkpoint()` 写入并带 fence 守卫，不得在 checkpoint 中存凭证或原始响应。
- 分P批量：`POST /sources/{id}/parts/import` 必须带 `confirmed: true`，一次 ≤20 个分P；任一分P不存在整批拒绝，每个分P仍建独立采集任务。不要在客户端自动确认或绕过服务端上限；前端来源抽屉提供勾选与确认框。
- 凭证/风控恢复：`collect()` 遇到 `CREDENTIAL_EXPIRED` 写连接 `expired`，遇到 `RISK_CONTROL`/`RATE_LIMITED` 写 `risk_control` 与 15 分钟本地冷却；`POST /platforms/{platform}/recover` 只重排被阻塞任务，不自动重试、不换账号/IP、不修改启用或联网授权。
- 发现明细保留：worker 约每 6 小时调用 `cleanup_discovery_items()`，只删除已结束运行且超过 `FISHINGNET_DISCOVERY_ITEM_RETENTION_DAYS` 的 `discovery_run_items`；不要在 `queued/running` 运行上清理，也不要删除运行账本或 watermark。
- B站 采集：`collect_source` 任务在 `fetch_metadata → fetch_subtitles → store_version` 之间不持有行锁，靠 `LeaseKeeper` 心跳与 `fence AND state='running'` 守卫保证不发布过期结果。不要为了"方便"把网络调用塞回事务或去掉守卫。请求上限与最小间隔见 `docs/bilibili-api-policy.md`（`FISHINGNET_BILIBILI_MIN_INTERVAL` 可调高）。
- 扫码登录：会话只存在 API 进程内存（`app/domain/qr_sessions.py`），180 秒有效、生成冷却 10 秒、最多 60 次轮询、同工作区单会话。`qrcode_key` 不返回前端、不落库、不得写日志；二维码由前端 `qrcode.react`（ISC）本机渲染，禁止改用第三方二维码图片服务。API 重启会丢失待处理会话，这是已知限制，不要改成把 key 写入数据库或日志来“修复”。

## 前端 QA

`infra/qa-preview.py` 创建隔离schema、合成账号和6份示例，默认在8001提供API；构建后的 `apps/web/dist` 存在时也提供静态前端。它不会初始化正式工作区，但当前没有自动清理schema。QA 账号只用于合成界面验证，不能作为正式登录方式。

## 备份状态

当前未交付或验证自动备份、加密恢复、RPO/RTO及删除清单重放。重要材料暂不应仅存于本开发预览。正式发布前须按需求M5补齐，不能把复制运行目录视为已通过恢复验收。

## 版本与许可证

Python锁文件为requirements.lock，前端锁文件为apps/web/package-lock.json。第三方清单由已安装包元数据生成，只是库存，不替代发布前的逐项许可证复核。没有复制dsh-bili-scroll代码，也不读取或依赖DSH。
