# 开发记录

> 各里程碑的**详细**开发记录已归档到 [docs/HISTORY.md](HISTORY.md)。本文件只保留当前进度与里程碑一览。

## 当前进度（2026-10-22 · V1 精简版，迁移 0011 head）

用户把第一版收敛为「定时按关键词选 B站 视频取字幕 → 每个视频一条总结 → 每轮汇总 + 实体更新 + 日报 → 定期检查 @ → 视频不重复总结」，其他功能主界面暂时隐藏（目标见 [V1-SCOPE.md](V1-SCOPE.md)）。

- 新增迁移 `0009_simple_v1`、`0010_daily_digest`、`0011_summary_links`；新模块 `summaries.py` / `jobs/simple.py` / `simple_api.py` / `web/src/simple.tsx`。
- 调度支持每天固定钟点 + 时区（`discovery_sources.daily_time/schedule_timezone`）。
- 实体只建被视频实质讨论的对象：提取提示词排除「顺带一提/一笔带过」，每轮再用一次 `digest_polish` 把候选交回模型合并近重复并剔除一笔带过项（失败安全回退为不丢弃）。
- 信息动态以 `video_summaries.links` 结构化保留项目地址、官网、下载链接，并在动态卡片中展示。
- 验证：`pytest -q` **419 passed / 2 pre-existing failures（test_local_readiness.py 的 `wait_ready` 签名不匹配，与本次无关）**；`npm run build` 通过；`alembic current` = **0011 (head)**。
- 未验证：真实 B站 发现/字幕/@、真实模型总结/汇总/实体更新、无人值守端到端。

## 里程碑一览

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 架构、六表迁移、契约与合成样例 | 完成 |
| M1 | 账号/会话、手动导入、Mock、动态/证据、主题管理 | 完成（批量导入/密码恢复/E2E 推迟） |
| M2 | B站 单链接/扫码、关键词来源/调度/增量、分P批量/恢复/日报 | 完成（真实扫码/分P批量/恢复未烟测） |
| M3 | 供应商/路由/预算/账本、抽取锚点、语义复核、实体候选 | 完成（真实模型抽取/复核/消歧未执行） |
| M4 | 库内事实材料 E0–E3、人工复核、建议路由/过期封顶/AC32–45 | 完成（真实外部检索/事实核查未执行） |
| **V1（主线）** | 视频总结 + 每轮汇总 + 自动日报 + @ 入口 | 代码完成，真实验证未做 |
| M5 | 安全、性能、备份恢复、完整验收 | 未开始 |

## 验收状态（摘要）

- 自动化测试：407 项通过（真实 PostgreSQL 隔离 schema；网络全部 `httpx.MockTransport`，无真实模型/网络）。
- 真实烟测：B站 元数据/字幕（`BV1MLJe6fEmH` 6 片段）+ WBI 关键词搜索（`"AI"` 两页各 20 条）已通过；1 次真实模型连接测试（MIMO 2.6）成功。**其余真实服务验证均未执行**。

## 完整历史

详细逐轮开发记录见 [docs/HISTORY.md](HISTORY.md)；验收细节见 [docs/ACCEPTANCE.md](ACCEPTANCE.md)。
