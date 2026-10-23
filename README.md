# FishingNet · AI 多来源信息聚合平台

把关注的信息（B站 视频/资料）自动收集、逐条总结、按轮汇总成日报，每条信息保留原文依据。当前为 **V1 精简版**（迁移 0011 head）。

## 当前状态（2026-10-21 · V1 精简版）

用户已把第一版收敛为「能干好」的范围（目标见 `docs/V1-SCOPE.md`）：

- 定时按关键词挑选若干 B站 视频 → 取字幕 → **每个视频只生成一条信息动态（整份视频/资料的总结）**；
- 每轮全部视频成功或明确失败后，汇总本轮动态、更新相关实体简介、更新当日日报；
- 实体只建被视频实质讨论的对象，一笔带过的对象不建实体；动态结构化保留项目地址、官网、下载链接；
- 定期检查已登录 B站 账号收到的 @，把被 @ 的视频送入同一流程；
- 关键词、手动链接、@ 三个入口共用视频身份去重，已成功总结的视频直接复用。

主界面只保留：信息动态、自动任务、实体、日报、设置。历史 M1–M4 高级能力（主题、事实核查、证据分档、表达复核、实体消歧等）**代码与数据保留，仅在主界面隐藏**。

实现要点：迁移 `0009_simple_v1`；新模块 `app/domain/summaries.py`、`app/jobs/simple.py`、`app/simple_api.py`（`/api/v1/simple/*`）、`apps/web/src/simple.tsx`；`discovery_sources` 增加每天固定钟点 + 时区调度。

**验证**：`pytest -q` **419 passed**（另 2 项 `test_local_readiness.py` 失败，属既有的 `wait_ready` 签名不匹配，与本次改动无关）；`npm run build` 通过；`alembic current` = **0011 (head)**；OpenAPI **76 路径 / 0.11.0**。

**未验证**：真实 B站 关键词发现/字幕/@ 通知、真实模型总结/汇总/实体更新、「每天固定钟点 → 发现 → 总结 → 汇总 → 日报」无人值守端到端尚未用真实服务跑通。

## 本机启动

双击根目录 `启动FishingNet.cmd`（自动启动数据库、后端、worker、前端并等全部就绪）；停止双击 `停止FishingNet.cmd`。打开 <http://127.0.0.1:5173>。

- PostgreSQL 只监听 `127.0.0.1:55432`；不允许以管理员权限运行（`infra/init-local.py` 在提升 shell 下自动走「基本用户」信任级别）。
- 已有账号直接登录；**不要为「重置」删除数据库、`.runtime/pgdata` 或覆盖 `.env`**。空实例首次设置口令在 `.env` 的 `SETUP_TOKEN` 中。
- 导入文本/Markdown/SRT/VTT 或 B站 链接；每个资料生成一条总结，使用模型设置里的「视频总结」任务。默认零预算、供应商/模型默认停用；未配置模型时任务停在「待复核」。

也可命令行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra/launch.ps1 -NoBrowser
```

## 在新环境安装

需要 Python 3.12+、Node.js 22.12+/24、PostgreSQL 17。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
npm --prefix apps/web ci
```

Windows 本地脚本预期 PostgreSQL 二进制位于 `.runtime/pgsql/bin`（EDB 官方 x64 zip 解压到 `.runtime`）。`infra/init-local.py` 创建本地数据库并生成随机密钥。容器方案 `docker compose up --build` **尚未实际验证**。

## 验证

```powershell
.venv\Scripts\python -m pytest -q      # 需要本地 PostgreSQL 已启动
npm --prefix apps/web run build
```

最新结果：**419 项后端测试通过**（真实 PostgreSQL 隔离 schema，无 SQLite 替代；另有 2 项 `test_local_readiness.py` 既存失败与本次无关）；`tsc -b && vite build` 通过。测试需要 PostgreSQL；自动建立并删除独立 schema，不改正式工作区。API 契约见 `docs/openapi.json`。

## 技术栈

React + TypeScript + Vite；Python 3.12 + FastAPI + Pydantic；PostgreSQL；SQLAlchemy + Alembic。独立 worker 共享 PostgreSQL 持久队列（scheduler 在 worker 进程内短事务），不依赖 DSH、Redis 或向量数据库。

## 里程碑一览

- M0：架构、数据字典、六表迁移、契约与合成样例。
- M1：初始化/会话、手动导入、不可变片段、Mock 处理、动态与证据闭环、主题管理。
- M2：B站 单链接导入/扫码登录、关键词来源/调度/增量/恢复、分P批量/凭证恢复/日报联动。
- M3：模型供应商/路由/预算/账本、路由化抽取与锚点校验、表达语义复核与实体候选。
- M4：库内事实材料与 E0–E3 分档、人工复核、建议路由/过期封顶/断章取义/AC32–45 数据集。
- **V1（当前主线）**：视频总结 + 每轮汇总 + 自动日报 + @ 入口，高级入口隐藏。
- M5（未开始）：安全、性能、备份恢复与完整验收。

真实模型与平台烟测必须单独记录，Mock 不作为真实验收；默认预算为零，禁止隐式付费或自动采集。

## 开发约束

- 六张核心表：sources、source_versions、claims、nodes、relations、updates；运行支撑表独立。
- 所有引用按工作区隔离；Unicode code point 偏移；证据验证与事实判断分开。
- 重要且未经核实的事实默认暂缓发布；所有展示及导出执行同一服务端策略。
- `enabled` 与 `network_authorized` 分离且默认 false；供应商/模型默认停用、预算默认 0、数据策略默认 `local_only`。
- 下载受阻即暂停并向用户提供原始下载链接，不自行切换不明镜像。

## 完整历史

各里程碑的详细开发与验收记录见 [docs/HISTORY.md](docs/HISTORY.md)。当前环境与进度见 [docs/PROGRESS.md](docs/PROGRESS.md)，验收结论见 [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)，数据约束见 [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md)，产品方向见 [docs/ROADMAP.md](docs/ROADMAP.md)。
