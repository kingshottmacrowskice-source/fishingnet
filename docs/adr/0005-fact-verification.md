# ADR 0005：事实证明材料、E0–E3 分档、追加型核查记录与人工复核

- 日期：2026-10-20（M4 Phase 1；2026-10-21 M4 Phase 2 补充版本过期、断章取义、事实核查路由建议与 AC32–45 数据集）
- 状态：Phase 1 已实现；Phase 2 的过期版本封顶、截断限定拦截、`fact_check` 建议路由与 AC32–45 数据集已实现。只有库内（`kb_only`）规则评估；外部检索、来源画像与纠错表达式生成未实现，也未做真实烟测。

## 背景

M1–M3 已经能保存不可变来源版本、用服务端锚点校验模型抽取的主张，并把表达语义复核（引文是否支持表达）与实体候选接入流水线。缺口是需求文档 6.5–6.12：主张的**事实证据**（支持/反驳/背景材料）、带版本的核查记录、人工复核、发布门槛与纠错。此前 `verification_records` 只有抽取时写入的一条 `insufficient` 规则记录，`fact_check_status` 无法被任何真实证据改变。

约束：不降低事实发布门槛；模型不能自授事实资格；未读取的材料不能算作已查证；默认不联网、不付费、不外发；六张核心表语义不变。

## 决策

1. **事实材料是 `claim → source_version` 关系，不新增证据表。**
   使用既有 `relations` 的 `fact_supported_by / fact_refuted_by / fact_context_by`（数据库矩阵早已允许），创建时为 `proposed`。材料载荷遵循 `fact_evidence.v1`，由迁移 0008 的约束触发器校验：必含锚点、命题范围、直接性、上游来源组、独立性、五项条件、材料完整性、透明度和限制；锚点必须在存储片段中逐字存在，偏移与哈希由服务端按 Unicode code point 计算；accepted 关系必须带 `assessment_record_id`。

2. **评估是确定性的规则，不是模型判断。**
   `app/domain/verification.py` 只读取已保存材料。分档：E0 不可用（疑似篡改/条件不匹配）；E1 有限间接（间接、条件未知、完整性未核验）；E2 实质支持（直接 + 五项条件全部 `matched` + 完整性 `verified`）；E3 还需至少两条 E2、不同上游来源组、无同源转载且至少一组确认独立。数量、抽取置信度、作者身份、平台热度都不是输入。

3. **核查任务独立、只读写库内材料。**
   `fact_check` 是一个独立 job（kind=`fact_check`），在短事务里读取主张、材料、同实体候选主张，生成 `retrieval_manifest`（`kb_only`、`external_reads=[]`、`model_route=not_wired_in_this_phase`）与 `evidence_snapshot`，再在 `fence AND state='running'` 的同一事务里追加一条 `pending` 记录。任务不改变 `claims.fact_check_status`、不移动指针、不联网、不调用模型。

4. **记录只追加，人工复核是第二条版本。**
   接受/拒绝不修改原记录（数据库触发器禁止 UPDATE/DELETE），而是追加一条 `checker_type='human'`、带 `supersedes_id` 的版本。复核要求 `expected_input_fingerprint`、claim `revision` 和材料快照哈希一致，否则 409；同一候选不能复核两次。接受时原子接受该记录列出的 proposed 材料（写 `assessment_record_id`），并按输入 Fingerprint 移动 `current_verification_id`；拒绝不改变主张状态。

5. **硬门槛防止“点一下就成了事实”。**
   `accept_gate` 拒绝：E1 的 `corroborated`；重要事实的 E2 `corroborated`（需 E3）；E1 的 `refuted`（需 E3 反向材料）；同时存在 E2/E3 支持却判 `refuted`；无材料的争议。`insufficient` 可以接受，但它只记录缺口，不提升表达。`auto_accept_allowed` 恒为 false。

6. **发布门槛继续由服务端决定，且覆盖标题与正文。**
   `PublicationPolicy` 升到 `publication.v1.3.0`；`visible_update` 同时评估 feed/detail 与 title，并只在“已存储的表达不强于策略允许”时展示。这样接受一条新记录不会把旧句子悄悄升级为 `scoped_fact`，也不会把更弱的表达隐藏；需要更强表达时必须重新生成并重新过门槛。`publication-preview` 只读预览逐句决定。

7. **能力如实降级。**
   `/verification-capabilities` 明确 `kb_only`、无连接器、模型路由未接入、人工复核必需；`/sources/{id}/reliability` 返回 `unknown`，不制造来源画像；`GET /claims/{id}` 把“表达定位通过”“语义复核结论”“事实证据分档”“核查状态”分列，避免把它们混成一个可信分数。

## 补充（2026-10-21，M4 Phase 2）

8. **来源版本被取代时封顶为 E1。**
   `sources.current_version_id` 指向新版本后，旧版本材料仍可解释历史，但不再能确认“当前版本”的命题：`material_tier` 返回 E1（`SOURCE_VERSION_SUPERSEDED`），不能参与 E2/E3。快照哈希把 `superseded`/`retention_expired` 计入，因此新版本出现后旧的待复核记录在人工接受时返回 409，而不是沿用旧结论。保留期到期的材料降为 E0。

9. **断章取义由服务端确定性拦截。**
   锚点校验之外增加保守规则：引文在同一句的逗号/分号/冒号处截断，且后半句含明确的限定/转折标记（但、仅、不支持、尚未、正式版、beta 等）时，主张保存为 `candidate` 并记录 `CONTEXT_TRUNCATED_LIMITATION`，不写证据关系、不发布。完整句子或并列补充句不受影响。

10. **`fact_check` 模型路由只给建议。**
    `app/ai/factcheck.py` 只发送库内材料的**元数据**（作用、范围、直接性、上游组、独立性、完整性、条件、限制），不发送原文；输出受控 verdict、理由、限制与材料引用。服务端拒绝编造材料引用、非法 verdict，以及任何“读过外部文档/调用工具”的自述（`MODEL_CLAIMED_EXTERNAL_READS`）；模型置信度不存储、不参与分档。建议写入 `verification_records.model_runs`，确定性评估仍是唯一分档来源，记录保持 `pending`、`auto_accept_allowed=false`。路由未配置、预算耗尽、策略阻断或调用失败只记录“建议不可用”，不降低门槛。

11. **`scoped_fact` 只认人工接受的记录。**
   `PublicationPolicy` 不再允许非人工的 accepted 记录（例如历史 `checker_type='rule'`）把句子提升为 `scoped_fact`，无论主张重要度如何；这避免旧 `corroborated` 未经验证继承确认资格（AC45）。

12. **AC32–45 阻断数据集是验收工件。**
    `tests/fixtures/ac32-45.json` 记录 48 个案例（六类各 8 例）与 12 个对照；`tests/test_m4_ac_dataset.py` 用真实服务端纯函数与隔离 schema 集成测试逐例断言档位、状态、接受门槛、七个读取面的表达与应阻断表述。数据集全部合成，不含真实材料或模型输出；通过它不等于 M4 整体签收。

## 后果

- 优点：事实核查有了可解释、可复核、可追溯的最小闭环；模型不能创建或接受材料；未接受的材料不会影响发布；旧输入不能覆盖新结论；空结果明确是“未查证”而不是“虚假”；六表语义与不可变证据边界保持不变。
- 代价：需要用户手工登记材料与接受记录；E2/E3 的严格条件会让多数真实材料停在 E1/insufficient；每次复核追加一行记录；`fact_check_status` 只在人工接受后才变化，自动流程不会“自己确认”。
- 未覆盖：`claim_comparison` 模型路由；外部检索连接器与来源画像；纠错传播与统一重算（当前只靠读取时按 `current_verification_id` 重新评估，历史保留）；批量复核与界面点击回归；真实事实核查烟测。
- 已知边界：材料状态变化会使旧记录在复核时报 409，需要重新发起核查；接受 `scoped_fact` 不会自动改写已发布动态的句子，需要重新处理来源；同一主张的多条 pending 记录可能分别被接受（各自输入相同时），界面应按最新版本引导；`fact_check` 模型建议只是文本，不会进入 `assessment` 或发布门槛。
