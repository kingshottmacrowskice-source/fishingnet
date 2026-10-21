# 验收记录（摘要）

> 各里程碑的**详细**验收记录已归档到 [docs/HISTORY.md](HISTORY.md)。本文件只保留当前验收结论、真实网络执行记录与未交付清单。

## 当前验收状态（2026-10-21 · V1 精简版，迁移 0009 head）

**已验证（自动化）**

- `pytest -q` **407 passed / 95 warnings（57.83s）**（真实 PostgreSQL 隔离 schema；新增 `tests/test_simple_v1.py` 17 项，全部合成材料 + `httpx.MockTransport`，无真实模型/网络）。
- `npm.cmd --prefix apps/web run build` 通过；`alembic current` = **0009 (head)**；OpenAPI **76 路径 / 0.11.0**。
- V1 流程用合成数据验证：一个视频一条总结、视频去重（BV 跨分P同一身份）、每轮汇总 + 实体更新 + 日报、长字幕不截尾、失败可见可重试、畸形输出不发布、@ 开关与通知去重、每天固定钟点调度、搜索失败不谎报无新内容。

**未验证（真实服务）**

- 真实 B站 关键词发现/字幕/@ 通知；真实模型视频总结/每轮汇总/实体更新。
- 「每天固定钟点 → 发现 → 总结 → 汇总 → 日报」无人值守端到端。
- 历史 M1–M4 高级能力（主题、事实核查、证据分档、表达复核、实体消歧等）代码与数据保留，仅 V1 主界面隐藏。

## 真实网络执行记录（历史，保留）

- **B站 元数据/字幕**（2026-10-20）：`BV1MLJe6fEmH` 6 片段，导出 `.runtime/exports/BV1MLJe6fEmH-subtitles.txt`。
- **WBI 关键词搜索**（2026-10-20）：`"AI"` 两页各 20 条；搜索端点 `x/web-interface/wbi/search/type`。
- **B站 平台连接**：connected。
- **模型连接测试**（用户本机，2026-10-20）：MIMO 2.6 `openai_compatible`，`succeeded`，37/26 token，0.0000445 USD；固定合成提示，未处理材料。
- **未通过**：真实 App 扫码确认（只到「生成 + 轮询」）；关键词 `AI DEEPSEEK` 首次请求被 HTTP 412 阻断（RISK_CONTROL，已停止未绕过）。

## 当前明确未交付

- V1 真实服务端到端（见上「未验证」）。
- 真实模型抽取/语义复核/实体消歧/事实核查（连接测试成功 ≠ 真实抽取/核查通过）。
- 真实扫码确认、分P批量、凭证过期/风控恢复、定时调度运行。
- M1 推迟项：批量导入、部署端密码恢复（发布前需补）、语言偏好、完整 E2E、`sort=changed`。
- M2 剩余：UP 主订阅/合集/番剧展开、本地转写、多进程/多节点调度。
- M3 剩余：`claim_comparison` 与事实核查模型路由、跨进程配额、Anthropic/Gemini 原生协议、供应商账单对账、模型面板浏览器点击实测。
- M4 剩余：外部检索连接器、来源画像、纠错传播与统一重算、批量复核。
- M5：安全、性能、备份恢复、容器部署、许可证复核（Docker Compose 未实测）。

## 可复现命令

```powershell
.venv\Scripts\python.exe infra\init-local.py
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m pytest -q
npm --prefix apps/web run build
powershell.exe -NoProfile -ExecutionPolicy Bypass -File infra\launch.ps1 -NoBrowser
powershell.exe -NoProfile -ExecutionPolicy Bypass -File infra\stop.ps1
```

## 证据

- 政策与取舍：`docs/bilibili-api-policy.md`、`docs/adr/0003-discovery-pipeline.md`、`docs/adr/0004-model-gateway-budget.md`、`docs/adr/0005-fact-verification.md`。
- 测试：`tests/test_simple_v1.py`（V1）、`test_m4_*`（M4）、`test_m3_*`（M3）、`test_m2_*`/`test_discovery_*`/`test_bilibili_*`（M2）、`test_collectors.py`/`test_qr_login.py`（M2 契约与扫码）。
- 迁移：`migrations/versions/0001`–`0009`，head=0009。
- 截图：`docs/m1.1-ui-preview.png`、`docs/m1.2-topics-preview.png`（隔离 QA schema 合成数据，非正式账号/真实平台）。

**407 项测试通过不等于真实采集/真实总结/真实核查通过，也不等于完整 MVP。** 详细逐轮验收见 [docs/HISTORY.md](HISTORY.md)。
