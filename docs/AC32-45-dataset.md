# AC32–45 专项阻断数据集

- 日期：2026-10-21（M4 Phase 2）
- 状态：数据集与运行器已建立并通过自动测试；**全部为合成材料**，不含真实 B站 内容、真实模型输出或外部检索结果
- 文件：`tests/fixtures/ac32-45.json`（数据）、`tools/build_ac_dataset.py`（生成器）、`tests/test_m4_ac_dataset.py`（运行器）

## 1. 目的

需求文档第 17 节要求在原数据集之外新增至少 48 个合成/授权案例（六类各 8 例）和至少 12 个对照，并逐例标注表达锚点、事实正反证据、独立来源组、条件、核查状态、允许的 `publication_mode` 与应阻断的表述。本数据集把这一要求落成可重复运行的阻断用例：每个案例都声明服务端**必须**得出的档位、状态、人工接受门槛与各读取面允许的表达，而不是事后记录现状。

## 2. 结构

```json
{
  "schema_version": "ac32_45_dataset.v1",
  "synthetic": true,
  "policy_version": "publication.v1.3.0",
  "cases": [
    {"id": "AC34-01", "category": "expired_version", "ac": ["AC34"],
     "claim": {"statement": "...", "importance": "medium", "...": "..."},
     "materials": [{"role": "support", "directness": "direct", "artifact_integrity": "verified",
                    "independence_status": "unknown", "origin_group_id": "G1",
                    "applicability": {"product_version": "matched", "...": "..."},
                    "superseded": true, "retention": "ok", "limitations": []}],
     "expression": null,
     "expect": {"status": "insufficient", "support_strength": "E1",
                "refutation_strength": "E0", "gate": [],
                "policy": {"feed": ["allow", "attributed_unverified"], "overview": ["hold", null],
                           "...": ["...", "..."]},
                "must_not_show": ["scoped_fact"]}}
  ]
}
```

- `materials` 只描述材料元数据（作用、直接性、完整性、独立性、上游组、五项条件、是否被新版本取代、保留期），不包含任何真实来源文本。
- `expression` 仅用于 AC33：给出片段原文、模型引用和期望结果（`accepted` 或带 `CONTEXT_TRUNCATED_LIMITATION` 的 `candidate`）。
- `expect.policy` 覆盖 `feed / detail / title / overview / digest / search / export` 七个面；`overview` 只有已接受 E2/E3 才允许。
- `must_not_show` 列出该案例在任何面上都不允许出现的表达（例如没有事实材料的宣传不得出现 `scoped_fact`）。

## 3. 案例构成

| 类别 | 数量 | 覆盖 |
|---|---|---|
| 虚假功能宣传 `false_feature_advertising` | 8 | 无材料、间接材料、单条 E2、同源转载、两个未知独立组、E3、疑似篡改、保留期到期 |
| 断章取义 `truncated_quote` | 8 | 测试版/正式版、beta/稳定版、但/macOS 未发布、仅限 20 个文件、不支持云端；含 3 个完整引用对照 |
| 过期版本 `expired_version` | 8 | 旧版本 E2 封顶、两个旧版本、新旧混合、两个当前独立组、新版本强反证、保留期到期、过期反证 |
| 循环转载 `circular_repost` | 8 | 10 个同源转载、同组确认独立、3 组未知、2 组一组确认、10 来源 2 组、10 条间接、同版本重复 |
| 伪造/不可核验测评 `forged_review` | 8 | 疑似篡改、未核验完整性、截图转述、两条未核验、疑似与可核验并存、可核验支持 + 未核验反证、矛盾日志、无法判断来源 |
| 真假批评混合 `mixed_criticism` | 8 | 4 组「真实限制 + 错误指控」成对案例，逐条独立评估 |
| 对照：真实功能 `true_feature` | 3 | E2 / E3 / 一组确认一组未知（均允许 `scoped_fact`） |
| 对照：适用强反证 `applicable_refutation` | 3 | E3 反证 → `refuted`，所有面 hold |
| 对照：低可靠来源强证据 `low_reliability_strong_evidence` | 3 | 来源可靠度 `unknown` 不构成一票否决，当前命题有可检查直接证据时允许发布 |
| 对照：证据不对称争议 `asymmetric_dispute` | 3 | 一侧 E2、一侧 E1 → `conflicted`，强弱与缺口必须可解释 |

合计 **48 + 12 = 60** 例。

## 4. 运行器验证什么

`tests/test_m4_ac_dataset.py` 对每个案例调用真实的服务端纯函数：

1. `app.domain.verification.assess` — 档位、状态、逐档计数、`stale_*` 与 `dispute_balance`；
2. `verification.accept_gate` — 人工接受门槛（重要事实 E2、E1 corroborated、E1 refuted、强支持却 refuted 等）；
3. `app.domain.policy.PublicationPolicy.evaluate` — 七个读取面的 action/mode，记录使用“已接受 + 当前有效 + 人工 checker”的合成记录；
4. `ai_extraction.validate_claim_output` — AC33 的截断限定检测。

另有集成测试覆盖需要数据库/路由的部分：

| AC | 验证方式 |
|---|---|
| AC32/35/36/37/40 | 数据集案例 + 单元（数量、置信度不改变档位） |
| AC33 | 数据集表达案例（8 例）+ 截断检测保守性单元 |
| AC34 | 数据集 + 集成：新增来源版本后旧材料封顶 E1；待复核记录因材料快照变化返回 409 |
| AC38 | 数据集对照（来源可靠度 unknown 不阻断单条证据） |
| AC39 | 单元 + 集成：模型声称外部读取/工具调用 → `MODEL_CLAIMED_EXTERNAL_READS`，检索清单仍 `kb_only` |
| AC41 | 数据集对照 + `dispute_balance` 单元 |
| AC42 | 集成：接受争议记录后旧确定性在 feed/detail/日报/导出同时消失，历史记录仍可查 |
| AC43 | 数据集 + 集成：重要事实待复核时标题/正文/日报/导出/概览全部 hold |
| AC44 | 集成：`fact_check` 路由预算耗尽→不请求 provider、记录仍待复核；成功调用计入账本；模型结论不改变分档 |
| AC45 | 集成：revision 变化 → 409；policy 单元：规则记录的 accepted 不能提升为 `scoped_fact` |

## 5. 明确未证明

- 没有真实外部检索连接器；`kb_only` 空结果不是反证。
- 没有用真实模型执行事实核查；AC44 的路由测试全部是 `httpx.MockTransport`。
- 数据集是**合成**的，不代表真实 B站 视频、官方文档或第三方测评的分布。
- 通过本数据集不等于 AC01–AC45 全部通过，也不等于 M4 整体签收；`claim_comparison`、来源画像、纠错表达式生成仍未实现。
- `insufficient` 不是“虚假”，`corroborated` 也不是外部已查证。

## 6. 重新生成

```powershell
.venv\Scripts\python.exe tools\build_ac_dataset.py
.venv\Scripts\python.exe -m pytest -q tests/test_m4_ac_dataset.py
```

生成器只写 `tests/fixtures/ac32-45.json`，不访问网络、不读写数据库。
