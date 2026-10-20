# B站接口与使用政策核验记录（M2 Phase 1 + Phase 2）

## 2026-10-20 16:10（UTC+8）· 关键词搜索 WBI 签名真实烟测通过

**用户已授权关键词搜索使用 WBI 签名**（扩展自此前"仅字幕查询允许标准 WBI"的授权）。搜索端点从 `x/web-interface/search/type`（无签名，HTTP 412）改为 `x/web-interface/wbi/search/type`（WBI 签名）。

实现：`bilibili.py` 的 `discover()` 在搜索前获取 buvid 设备指纹（`/x/frontend/finger/spi`，缓存）并调用 `_sign_wbi()`（OE 表 + `_enc_wbi`，与字幕适配器相同）。buvid 缺失时搜索返回 HTTP 412，已通过真实烟测确认。未使用 WBI2、浏览器伪装、设备激活或额外重试。

真实烟测（2026-10-20 16:10 UTC+8）：关键词 `"AI"` 两页均返回 20 条结果，has_more=True。WBI key 获取、buvid 获取、签名搜索全链路通过。全量 181 passed，前端构建通过。

## 2026-10-20 12:32（UTC+8）· 字幕库接入完成、真实字幕取得；关键词被HTTP 412阻断

**覆盖旧的依赖未安装/字幕未取得状态：** bilibili-api-python 17.4.1及全部运行依赖已安装，pip check通过。官方wheel均与PyPI摘要核对，qrcode-terminal 0.8官方源码包经摘要和setup.py检查后本地构建安装。requirements.lock与docs/python-dependencies.json已更新；离线缓存保留。许可证边界与私有API固定说明见docs/subtitle-library-notice.md（GPL-3.0-or-later，非DSH MIT）。

受限适配器 `apps/api/app/collectors/bilibili_subtitles.py` 继承库的Video.get_subtitle，覆盖get_player_info，使用库内置标准WBI签名和OE参数表；所有实际HTTP仍走现有采集器。没有使用库的默认传输、WBI2、设备激活、ticket、浏览器UA或额外重试，没有修改库全局配置。完整采集正常4请求（元数据/nav/清单/正文），重试消耗同一总预算。字幕CDN无凭证，持久Cookie亦不继承。worker短事务、心跳、fence保持原流程。

真实样本 `BV1MLJe6fEmH`：任务 `585521f4-14e8-4921-9c3c-fc040510b626` succeeded、error_code=null、subtitle_status=available；新版本 `f54d2445-c6ca-418d-8d70-e999ef52afb2`，6片段。原文TXT已导出到 `.runtime/exports/BV1MLJe6fEmH-subtitles.txt`（418字符）。未改写旧任务和不可变证据；未调用模型，未完成AI分析或事实确认。

验证：全量181 passed / 前端构建通过；迁移仍0005(head)，OpenAPI 50路径/0.5.0。新增回归覆盖标准签名请求、无库默认传输/浏览器UA/设备Cookie/WBI2，以及-403/-352/-101不重试、无效签名参数停止。既有CDN隔离重试回归仍通过。

## 2026-10-20 07:35（UTC+8）· 字幕库接入准备，依赖安装中止

**用户已明确授权：仅字幕查询允许使用 bilibili-api-python 内置标准 WBI 签名。** 搜索路径不变；不伪装浏览器、不绕过验证码/付费/风控。此授权覆盖旧的全面禁止 WBI 限制中的字幕查询部分，无需再次索取同一授权；不代表字幕已接入或真实复测通过。

已完成：修复 `ai-zh/ai-en` 与语言偏好的匹配，默认中文优先且严格中文筛选不会排除 `ai-zh`；字幕空清单提示改为“本次接口未返回可用字幕轨”，来源标签改为“未取得字幕”，不再断言视频没有字幕。保留原状态字段和不可变历史，不改写此前任务结果。新增 3 项合成回归，**全量 173 passed / 20 warnings（18.75s）**；前端生产构建通过。没有 API 结构变化，OpenAPI 无需变更。

核对发行包 `bilibili-api-python 17.4.1`：`get_subtitle` 调用 `/x/player/wbi/v2`，默认 WBI/WBI2 开启，默认浏览器 UA、自动 buvid 与 WBI 错误重试不能直接带入本项目。拟接入方式必须只保留获准的标准 WBI，关闭 WBI2 模拟行为、自动设备激活、ticket 与额外重试；所有实际请求统一经过现有限速、4 次请求预算、错误映射与短事务/心跳/fence。字幕 CDN 请求不应附带平台凭证。依赖许可证为 GPL-3.0-or-later，不能把 DSH 包装层的 MIT 误作其依赖许可证。尚未实现此适配层、尚未安装该库。

发行 wheel 已成功下载到本地 `.runtime/bilibili_api_python-17.4.1-py3-none-any.whl`，SHA-256 `3054b824f0f7ac81917329f19909f39d24fb3b7d9de1672c6dd4cb9fa6e5757d` 校验一致。随后 pip（官方 PyPI、timeout=20、retries=0）输出 `Downloading beautifulsoup4-4.15.0-py3-none-any.whl.metadata (3.8 kB)` 后持续无进展，按用户要求人工中止，退出码 1；没有收到具体 HTTP/网络异常，不能推断为某个域名故障。未重试、未切镜像、未改代理。核对后该库及其新增依赖均未安装，requirements.lock 未变。

下载信息：
- [bilibili-api-python 17.4.1 wheel（已取得）](https://files.pythonhosted.org/packages/07/50/520f98ef607687d686c9170b3ff2db944152bb0dc43bef0df7b4face8c5b/bilibili_api_python-17.4.1-py3-none-any.whl)
- [BeautifulSoup 4.15.0 wheel 元数据（最后可见下载项）](https://files.pythonhosted.org/packages/88/c6/92fcd42f1ba33e1184263f25bfabf3d27c383410470f169e4b8163bf9c17/beautifulsoup4-4.15.0-py3-none-any.whl.metadata)
- [BeautifulSoup 4.15.0 wheel](https://files.pythonhosted.org/packages/88/c6/92fcd42f1ba33e1184263f25bfabf3d27c383410470f169e4b8163bf9c17/beautifulsoup4-4.15.0-py3-none-any.whl)

下一步先处理依赖下载，再完成受限适配层和对应测试，最后对已授权 `BV1MLJe6fEmH` 复测。不能以语言选择修复冒充库接入，也不能宣称字幕已取得。本轮未新增真实 B站请求；关键词 `AI DEEPSEEK` 一轮发现仍未执行，M3 未开始。数据库、凭证、迁移 0001–0005 保留。


- 核验日期：2026-10-20（Phase 2 补充：关键词来源与搜索结果读取；M2 收尾补充：分P批量确认、凭证/风控恢复、日报与明细保留）
- 核验人：FishingNet 开发（本机开发会话）
- 适用范围：**B站单链接导入 + 元数据/字幕状态**（Phase 1）、**用户自配置关键词来源的有界发现**（Phase 2）与**分P批量确认/失败恢复**（M2 收尾）。定时调度、增量 watermark、分P定位在本项目内实现；视频下载、本地转写、UP 主遍历、合集抓取均不在范围内。
- 结论摘要：**未取得平台官方 API 授权，也未与平台签署任何协议。** 实现只使用“用户主动提供链接或用户自配置关键词 + 用户本机扫码/提供登录 Cookie + 低频、有界、可中止”的网页接口调用方式，并把接口不可用、需要登录、缺字幕、风控等情况如实呈现；**不绕过登录、付费、验证码或风控**，不伪造 WBI 签名、不伪装浏览器 UA。2026-10-20 02:13 已完成一次真实的二维码生成 + 一次轮询（见第 6 节），但**未完成真实扫码确认**；元数据/字幕（`BV1MLJe6fEmH`，6 片段）、WBI 关键词搜索（`"AI"` 两页各 20 条）与平台连接已通过短时烟测；分P批量、凭证恢复、定时调度仍未执行。

## 1. 核验方式与证据等级

本机 `web_fetch` 对 `github.com`、`raw.githubusercontent.com`、`www.bilibili.com`、`open.bilibili.com` 的直连均返回 “resolves to a non-public IP address”（本机 hosts/DNS 把外部域名解析到非公网地址），**未能取得页面全文**。因此本轮采用搜索引擎摘要核对页面标题与条款要点，逐条标注证据等级：

| 等级 | 含义 |
|---|---|
| A | 官方页面（条款/开放平台协议），本轮只核对了标题、URL 与检索摘要 |
| B | 社区维护的非官方接口文档，仅作行为参考，未复用其代码或文本 |
| C | 未核验/待用户本机授权后复核 |

**待办**：真实采集上线前，需由用户在本机直连上述页面复核条款全文，并把复核日期写入本节。不得把 B 级社区文档当作平台授权。

## 2. 关键来源

| # | 来源 | URL | 等级 | 本轮用途 |
|---|---|---|---|---|
| 1 | 哔哩哔哩用户使用协议（中文） | <https://www.bilibili.com/protocal/licence.html> | A | 平台对自动化获取内容/数据的限制 |
| 2 | Bilibili Terms of Service（英文版，同一协议） | <https://www.bilibili.com/blackboard/era/44ZJAVYNG9lkl24M.html> | A | 第 (3) 条：禁止以任何方式（含自动化程序）获取平台服务、内容或数据 |
| 3 | 哔哩哔哩开放平台 | <https://open.bilibili.com/> | A | 官方接口需开发者/应用申请与审核 |
| 4 | 哔哩哔哩开放平台开发者服务协议 | <https://open.bilibili.com/agreement/developer-service> | A | 官方数据使用范围由协议与授权范围决定 |
| 5 | 哔哩哔哩开放平台文档入口 | <https://open.bilibili.com/doc> | A/C | 本轮未获授权，未取得具体接口文档 |
| 6 | bilibili-API-collect（社区文档） | <https://github.com/SocialSisterYi/bilibili-API-collect> | B | 仅参考网页接口形态：`x/web-interface/view`、`x/web-interface/nav`、`x/player/v2` |
| 7 | 社区问答：匿名状态取不到字幕 | <https://ask.csdn.net/questions/9424827> | B | 佐证字幕接口需登录态 |
| 8 | 社区问答：接口频繁 412 | <https://ask.csdn.net/questions/9405843> | B | 佐证存在频率风控（HTTP 412 / `code=-412`） |

> 引用说明：以上仅为事实核验来源，不构成平台授权。社区文档可能随时过时，且其许可证需在**实际复用其文本或代码前逐项核对**；本项目没有复制其代码。

## 3. 条款与接口限制（实现必须遵守）

1. **不得绕过访问控制**：不使用验证码识别、不伪造登录、不破解付费/大会员内容、不使用多账号轮换规避风控、不伪造浏览器客户端。WBI 签名仅使用 `bilibili-api-python` 库内置的标准 OE 表与 `_enc_wbi`（与字幕适配器相同），不使用 WBI2、设备激活或浏览器伪装。
2. **不得批量抓取**：单链接导入只处理用户逐条提交的视频；关键词发现只读取用户自配置关键词的**搜索结果页**，每轮最多 3 页、每轮最多 20 条候选、每轮最多 4 次上游请求；不做 UP 主全量遍历、不做全站抓取、不做合集/播放列表展开、不按播放量或排名生成推荐流。
3. **不得自动互动**：不评论、不 @、不回复、不点赞、不投币、不收藏、不关注。参考项目 `dsh-bili-scroll` 的自动评论等能力明确不采用。
4. **不得转存视频**：只读取平台返回的元数据与字幕文本；不下载、不缓存、不代理播放视频流。
5. **频率**：单条采集任务正常最多 3 次上游请求（元数据、字幕清单、字幕正文），实例上限 4 次（仅供超时/5xx 的单次重试）；关键词发现每轮请求数由来源配置限制在 1–4 次、页数 1–3 页；账号检查是独立的 1 次请求。同一进程内两次请求最小间隔 1.5 秒（`FISHINGNET_BILIBILI_MIN_INTERVAL` 可调高，不建议调低）。
6. **不自动重试**：仅对网络超时/5xx 做 1 次延迟重试；遇到 `412`、`-352`、`-101`、`-403` 等风控或鉴权错误立即停止，交由用户处理，不换 IP、不换账号、不加重试。失败会记入 `discovery_sources.failure_streak` 并以 15 分钟起指数退避，但不会静默停用来源。
7. **凭证**：`SESSDATA` 等 Cookie 只能由用户在本机应用界面提交，使用 `MASTER_KEY` 派生的 Fernet 密钥加密后落库；不写日志、不进错误信息、不回显、不发送给模型供应商。断开连接时清除本地密文。
8. **默认零预算**：不调用任何模型/付费接口；字幕原文已保存时，处理任务停在“待复核”，不伪装成 AI 分析完成。
9. **可撤回**：断开平台连接后，已采集内容仍按保留期可读；不声称“平台授权已撤销”（平台未提供撤权接口时，只显示“已移除本地连接”）。

## 4. 本轮使用的接口（行为契约）

以下为**网页端内部接口**，不是对外承诺稳定的公开 API；实现把它们视为“可能随时变更、可能风控”的上游，并对每一类失败给出明确状态，不重试、不隐瞒。

| 用途 | 方法与路径 | 鉴权 | 关键字段 | 失败映射 |
|---|---|---|---|---|
| 视频元数据 | `GET https://api.bilibili.com/x/web-interface/view?bvid=...` | 匿名 | `code`、`data.title`、`data.owner.name/mid`、`data.pubdate`、`data.duration`、`data.pages[]`（`cid`、`page`、`part`、`duration`）、`data.bvid` | `code=-404/-400`→`SOURCE_NOT_FOUND`；`-403`→`PERMISSION_REQUIRED`；`-412/-352`→`RISK_CONTROL`；HTTP 429→`RATE_LIMITED` |
| 登录态/能力 | `GET https://api.bilibili.com/x/web-interface/nav` | 需 `SESSDATA` | `data.isLogin`、`data.uname`、`data.mid`、`data.vipStatus` | `code=-101`→`CREDENTIAL_EXPIRED`；未配置凭证→`NOT_CONFIGURED` |
| 字幕清单 | `GET https://api.bilibili.com/x/player/v2?bvid=...&cid=...` | 需 `SESSDATA` | `data.subtitle.subtitles[]`（`lan`、`lan_doc`、`subtitle_url`、`ai_type`） | 空数组→`missing`（缺字幕）；未登录→`login_required` |
| 字幕正文 | `GET https://...subtitle_url` | 随清单 | `body[]`（`from`、`to`、`content`） | 结构不符→`SUBTITLE_UNREADABLE` |

实现约定：

- 请求头使用**如实标识**的 User-Agent（`FishingNet/0.1 local-single-user`），不伪装成浏览器；带 `Referer: https://www.bilibili.com/`（多数网页接口要求）。
- 元数据接口匿名可用；字幕接口匿名通常返回空字幕列表，**因此“取不到字幕”不能等同于“视频没有字幕”**，必须区分 `login_required` 与 `missing`。
- 分P身份使用 `(BV号, p 序号)`：`external_id = "{bvid}:p{n}"`，`cid` 存入 `metadata.video_cid`；主视频信息放来源元数据。
- 单次超时 8 秒，总请求预算 4 次；超时或上游异常如实写入任务 `error_code`，不把失败显示成“没有新信息”。

## 4.1 扫码登录（网页登录流程，非开放平台 OAuth）

用户要求提供与参考插件类似的扫码登录。实现只使用 B站 网页登录的二维码接口，仍属**非官方、可能变更的内部接口**：

| 用途 | 方法与路径 | 鉴权 | 关键字段 | 失败映射 |
|---|---|---|---|---|
| 生成二维码 | `GET https://passport.bilibili.com/x/passport-login/web/qrcode/generate` | 匿名 | `data.url`、`data.qrcode_key` | 结构不符→`UPSTREAM_INVALID_JSON`；域名错误会得到 HTTP 404（本项目 2026-10-20 曾误用 `api.bilibili.com`） |
| 轮询状态 | `GET .../x/passport-login/web/qrcode/poll?qrcode_key=…` | 匿名 | `data.code`（`86101` 未扫码、`86090` 已扫码待确认、`86038` 过期、`0` 成功）；成功响应的 `Set-Cookie` 含 `SESSDATA` | `412/-352`→`RISK_CONTROL`；成功但无 `SESSDATA`→`QR_NO_CREDENTIAL` |

实现约束（本轮已落实）：

1. **只在用户点击“生成登录二维码”时发起**，不后台预取、不自动轮询；关闭/取消后停止。
2. **二维码在本机渲染**：使用 `qrcode.react`（ISC 许可证）生成 SVG，**不使用任何第三方二维码图片服务**，避免把登录 URL 发给外部。
3. **`qrcode_key` 只保存在 API 进程内存**（`app/domain/qr_sessions.py`），不返回浏览器、不落库、不写日志；API 重启后待处理会话失效，需重新扫码。
4. **限流**：生成冷却 10 秒；同一工作区同时最多 1 个会话；会话有效期 180 秒；最多 60 次轮询；浏览器每 2 秒轮询一次，服务端最小间隔 1.5 秒（更快的请求返回缓存状态，不打上游）。
5. **凭证最小化**：成功后只从 `Set-Cookie` 提取 `SESSDATA`，立即 Fernet 加密写入 `platform_connections`；`bili_jct`、`DedeUserID` 等一律丢弃。API 响应、审计、日志都不含 Cookie 明文。
6. **状态如实**：等待扫码、已扫码待确认、已过期、风控、未返回凭证分别返回，不合并成“失败”或“成功”。
7. **不绕过风险控制**：出现 `412/-352` 立即停止该会话，不换 IP、不换账号、不自动重试。
8. **不是平台授权**：这是网页登录会话，不是开放平台 OAuth；界面与文档都不得写成“官方授权登录”。断开连接只删本地密文，并明确说明未调用平台撤权接口。

## 4.2 关键词来源与搜索结果读取（M2 Phase 2 + WBI 签名，2026-10-20 真实烟测通过）

关键词来源是**用户显式配置并由用户在本机单独授权联网**的能力，默认关闭。搜索使用 WBI 签名（2026-10-20 用户授权扩展）。实现遵守以下约束：

| 用途 | 方法与路径 | 鉴权 | 关键字段 | 失败映射 |
|---|---|---|---|---|
| 关键词搜索结果 | `GET https://api.bilibili.com/x/web-interface/wbi/search/type?search_type=video&keyword=…&page=N&order=pubdate&w_rid=…&wts=…` | WBI 签名（OE 表 + `_enc_wbi`）+ `SESSDATA` Cookie + `buvid3`/`buvid4` 设备指纹 | `data.result[]`（`type`、`bvid`、`title`、`author`、`mid`、`pubdate`、`duration`）、`has_more` | `-403`→`DISCOVERY_DENIED`；`-400`→`INVALID_QUERY`；`-412/-352`→`RISK_CONTROL`；HTTP 412/429→`RISK_CONTROL`/`RATE_LIMITED` |
| WBI 密钥获取 | `GET https://api.bilibili.com/x/web-interface/nav` | 需 `SESSDATA` | `data.wbi_img.img_url`、`data.wbi_img.sub_url` → 提取 32+32 hex → OE 表映射 → 32 字符 mixin key | 缓存 30 分钟；失败→`UPSTREAM_INVALID_JSON` |
| 设备指纹 | `GET https://api.bilibili.com/x/frontend/finger/spi` | 匿名 | `data.b_3`（buvid3）、`data.b_4`（buvid4） | 缓存；失败时搜索可能返回 HTTP 412 |

实现约束（本轮已落实并有单元/集成测试）：

1. **联网授权是独立开关**：`discovery_sources.network_authorized` 默认 false；未授权时手动运行返回 422 `NETWORK_NOT_AUTHORIZED`，调度器也不会选取该来源，**不发起任何请求**。
2. **每轮有界**：页数 ≤3、候选 ≤20、请求 ≤4；只读搜索结果页的公开元数据，不跟进 UP 主、合集、相关视频或播放列表。
3. **本地再过滤**：关键词命中（any/all，Unicode NFKC+casefold）、排除词（标题或作者）、每轮上限、语言偏好（字幕轨选择）都在本地二次执行，不信任上游排序。
4. **决定可解释**：每个候选写入 `discovery_run_items`，包含 enqueued/refreshed/excluded_term/keyword_miss/duplicate/over_limit/skipped_deleted 与原因；被排除的候选不会被静默丢弃。
5. **增量与去重**：来源身份仍是 `(workspace_id, platform, external_id)`，同一视频不会因多个关键词重复建档；`watermark.seen` 记录最近入队候选与最新发布时间，整页都早于 watermark 时停止翻页。已存在且未到重新检查时间的来源记为 duplicate，超过 `rescan_after_hours` 记为 refreshed。
6. **分P定位与批量**：元数据阶段保存 `parts`（≤100 个分P的序号/cid/标题/时长），用户可对单个分P调用 `POST /sources/{id}/parts/{p}/import`；M2 收尾新增 `POST /sources/{id}/parts/import` 批量排队，**必须由用户在界面明确确认（`confirmed: true`）**，一次最多 20 个分P，每个分P仍是独立采集任务，不抓取未选分P。批量从未真实烟测。
7. **网络阶段不持锁**：发现任务按页把结果写入 `jobs.checkpoint`，网络调用之间提交事务并由心跳续租；所有写入受 fence 守卫，崩溃或取消不会留下半成品来源。
8. **失败可恢复但不绕过**：凭证过期（`-101`）把本地连接标为 `expired`，风控/限流（412、-352、429）标为 `risk_control` 并设 15 分钟本地冷却；用户替换凭证或冷却结束后可调用恢复端点重新排队被阻塞任务。恢复不自动重试、不换账号/IP、不修改启用或联网授权。
9. **明细有界保留**：`discovery_run_items` 默认只保留 30 天，运行账本与 watermark 不删除；这不是平台数据留存承诺，只是本地账本治理。

## 5. 本轮明确不做的能力

- UP 主订阅、UP 主全量遍历、全站抓取、推荐流：**未实现**。
- 分P批量采集：**已实现但未真实烟测**（需用户逐次确认、≤20 个分P）；合集/番剧/播放列表展开：**未实现**。
- 本地转写（ASR）：**未实现**，不下载音频、不引入转写依赖。
- 官方开放平台 OAuth：**未申请、未实现**。应用内提供两种本机方式：B站 网页扫码登录（非开放平台授权）与手动粘贴 Cookie；两者都不是官方授权，界面与文档均如实标注。
- 视频下载、弹幕抓取、自动评论/@/回复：**不做**。
- WBI2、浏览器伪装、设备激活或任何反风控绕过：**不做**。搜索使用标准 WBI 签名（OE 表 + `_enc_wbi`），与字幕查询相同；风控/限流如实停止。
- `b23.tv` 短链与 `av` 号：本阶段返回 `UNSUPPORTED_LOCATOR`，提示用户改用含 BV 号的完整链接（避免在请求处理线程里做跳转解析与身份合并）。

## 6. 真实网络执行状态

| 项目 | 状态 |
|---|---|
| 真实 B站 HTTP 请求（元数据/字幕/登录态） | **通过（2026-10-20 12:30 前后）**：使用本机 SESSDATA 对 `BV1MLJe6fEmH` 完成元数据、账号/导航检查与字幕清单/正文（6 片段，ai-zh）；结果写入 `source_versions`，不代表事实确认 |
| 真实关键词搜索请求 | **通过（2026-10-20 16:10）**：WBI 签名 + buvid 设备指纹 + SESSDATA；关键词 `"AI"` 两页各 20 条结果；端点 `x/web-interface/wbi/search/type` |
| 真实分P批量 / 凭证恢复 / 定时调度 | **未执行**（M2 收尾）：`POST /sources/{id}/parts/import`、`POST /platforms/{platform}/recover` 与调度 tick 只用测试替身验证；没有真实请求、没有真实过期凭证或风控响应 |
| 扫码登录的真实网络流程 | **部分执行（2026-10-20 02:13）**：1 次 `qrcode/generate` + 1 次 `qrcode/poll`；生成成功，轮询返回 `waiting_scan`；**未扫码、未保存任何凭证**。首次尝试因实现把域名写成 `api.bilibili.com` 返回 HTTP 404，已修正为 `passport.bilibili.com` 并复测通过 |
| 真实 B站账号连接 | **部分通过**：已用用户本机提供的 SESSDATA 完成导航/账号检查与平台连接（`connected`）；**没有完成真实 App 扫码确认**，也未验证凭证到期/重授权/断开的真实流程 |
| 上游接口契约验证 | 二维码生成/轮询、元数据、字幕清单/正文、WBI 搜索已按真实响应核对；分P批量、恢复、合集等仍为契约/测试替身或社区文档（B 级）静态核对 |
| 测试替身 | 已实现，仅用于合成数据测试，不代表真实采集、真实发现、真实分P批量或完整扫码登录通过 |

已执行的短时烟测方式（低频、可中止）：用户在本机应用内提供 SESSDATA → 检查平台连接 → 导入 1 条自有链接读取元数据/字幕 → 手动运行 1 个关键词来源两页；结果已写入 `docs/ACCEPTANCE.md`。仍未执行：真实 App 扫码确认、分P批量、凭证过期/风控恢复、定时调度。任何一步失败都不得改写为成功。

## 7. 许可证与参考项目边界

- 参考 `dsh-bili-scroll` 的**流程思路**（发现→字幕→调度→日报），本项目独立实现：不依赖 DSH、不读取其配置、不复制其代码、不采用其模型价格/优惠时段/版本假设。
- 本轮未复制 `bilibili-API-collect` 或任何第三方仓库的代码与文本；接口路径与字段名属于事实性信息。若后续复用任何代码，须逐文件核对许可证并在 `docs/python-dependencies.json` 与本文记录声明。
