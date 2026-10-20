"""Build the AC32-45 blocking dataset used by ``tests/test_m4_ac_dataset.py``.

The dataset is synthetic (no real Bilibili or model material) and is checked in so the
expected behaviour can be reviewed without running the generator. Each case states what
the server must decide: the deterministic evidence tier, the fact status, the human
acceptance gate and the publication mode allowed on every surface.

Run from the project root::

    .venv\\Scripts\\python.exe tools\\build_ac_dataset.py
"""
import json
from pathlib import Path

OUT = Path('tests/fixtures/ac32-45.json')
CONDITIONS = ('product_version', 'date', 'environment', 'region', 'method')


def mat(role='support', *, direct='direct', integrity='verified', independence='unknown',
        origin='G1', conditions='matched', superseded=False, retention='ok',
        limitations=None):
    role = {'fact_supported_by': 'support', 'fact_refuted_by': 'refute',
            'fact_context_by': 'context'}.get(role, role)
    if conditions == 'matched':
        applicability = {field: 'matched' for field in CONDITIONS}
    elif conditions == 'unknown':
        applicability = {field: 'unknown' for field in CONDITIONS}
    else:
        applicability = dict(conditions)
    return {'role': role, 'directness': direct, 'artifact_integrity': integrity,
            'independence_status': independence, 'origin_group_id': origin,
            'applicability': applicability, 'superseded': superseded,
            'retention': retention, 'limitations': limitations or []}


def pol(feed, overview, digest=None, export=None):
    return {'feed': list(feed), 'detail': list(feed), 'title': list(feed),
            'overview': list(overview),
            'digest': list(digest or feed), 'search': list(feed),
            'export': list(export or feed)}


UNVERIFIED = pol(('allow', 'attributed_unverified'), ('hold', None))
HOLD = pol(('hold', None), ('hold', None))
FACT = pol(('allow', 'scoped_fact'), ('allow', 'scoped_fact'))
DISPUTE = pol(('hold', None), ('hold', None))
CORRECTION = pol(('hold', None), ('hold', None))

CASES = []


def case(cid, category, ac, title, statement, materials, *, importance='medium',
         kind='factual_assertion', verifiability='verifiable', status,
         support='E0', refutation='E0', gate=(), policy=None, blocked=(), pair=None,
         qualifiers=None, expression=None):
    CASES.append({
        'id': cid, 'category': category, 'ac': list(ac), 'title': title,
        'claim': {'statement': statement, 'claim_kind': kind, 'aspect': 'compatibility',
                  'importance': importance, 'verifiability': verifiability,
                  'qualifiers': qualifiers or {'product_version': '2.0'}},
        'materials': materials, 'expression': expression,
        'expect': {'status': status, 'support_strength': support,
                   'refutation_strength': refutation,
                   'gate': list(gate), 'policy': policy or UNVERIFIED,
                   'must_not_show': list(blocked)},
        'pair': pair,
    })


# ------------------------------------------------ AC32 false feature advertising
case('AC32-01', 'false_feature_advertising', ['AC32', 'AC43'],
     '宣传字幕声称支持离线，但没有任何事实材料', 'Plugin X 2.0 支持完全离线使用。',
     [], importance='high', status='insufficient', support='E0',
     gate=(), policy=HOLD, blocked=['scoped_fact', 'attributed_unverified'])
case('AC32-02', 'false_feature_advertising', ['AC32'],
     '只有一条间接的安装说明', 'Plugin X 2.0 支持完全离线使用。',
     [mat(direct='indirect')], importance='high', status='insufficient',
     support='E1', policy=HOLD, blocked=['scoped_fact'])
case('AC32-03', 'false_feature_advertising', ['AC32', 'AC43'],
     '一条直接材料，但重要主张仍需 E3', 'Plugin X 2.0 支持完全离线使用。',
     [mat(origin='G1')], importance='high', status='corroborated', support='E2',
     gate=['IMPORTANT_FACT_REQUIRES_E3'], policy=HOLD, blocked=['scoped_fact'])
case('AC32-04', 'false_feature_advertising', ['AC32', 'AC35'],
     '三条相同上游的转载不能升级 E3', 'Plugin X 2.0 支持完全离线使用。',
     [mat(origin='G1', independence='shared_origin'),
      mat(origin='G1', independence='shared_origin'),
      mat(origin='G1', independence='shared_origin')],
     importance='high', status='corroborated', support='E2',
     gate=['IMPORTANT_FACT_REQUIRES_E3'], policy=HOLD, blocked=['scoped_fact'])
case('AC32-05', 'false_feature_advertising', ['AC32', 'AC35'],
     '两个上游组但独立性未知，不计作确认独立', 'Plugin X 2.0 支持完全离线使用。',
     [mat(origin='G1', independence='unknown'),
      mat(origin='G2', independence='unknown')],
     importance='high', status='corroborated', support='E2',
     gate=['IMPORTANT_FACT_REQUIRES_E3'], policy=HOLD, blocked=['scoped_fact'])
case('AC32-06', 'false_feature_advertising', ['AC32'],
     '两个独立上游组且确认独立，重要功能宣传可发布为事实', 'Plugin X 2.0 支持完全离线使用。',
     [mat(origin='G1', independence='confirmed'),
      mat(origin='G2', independence='confirmed')],
     importance='high', status='corroborated', support='E3',
     gate=(), policy=FACT)
case('AC32-07', 'false_feature_advertising', ['AC32', 'AC36'],
     '疑似拼接的截图不能作为支持材料', 'Plugin X 2.0 支持完全离线使用。',
     [mat(integrity='suspected_manipulation')], importance='high',
     status='insufficient', support='E0', policy=HOLD, blocked=['scoped_fact'])
case('AC32-08', 'false_feature_advertising', ['AC32', 'AC34'],
     '保留期到期的材料不可用', 'Plugin X 2.0 支持完全离线使用。',
     [mat(retention='expired')], importance='medium',
     status='insufficient', support='E0', policy=UNVERIFIED, blocked=['scoped_fact'])

# ------------------------------------------------ AC33 truncated quotation
EXPR_ACCEPT = {'schema': 'claim_extract.v1', 'result': 'accepted'}
EXPR_CANDIDATE = {'schema': 'claim_extract.v1', 'result': 'candidate',
                  'reason': 'CONTEXT_TRUNCATED_LIMITATION'}

case('AC33-01', 'truncated_quote', ['AC33'],
     '只引用“测试版可用”，省略“正式版不支持”',
     'Plugin X 2.0 已支持离线使用。', [], status='insufficient', support='E0',
     policy=UNVERIFIED, blocked=['scoped_fact'],
     expression={'text': '该功能只在测试版可用，正式版不支持离线。',
                 'quote': '该功能只在测试版可用', 'expect': EXPR_CANDIDATE})
case('AC33-02', 'truncated_quote', ['AC33'],
     '引用 beta 通道说明，省略“稳定版尚未开放”',
     'Plugin X 2.0 已支持离线使用。', [], status='insufficient', support='E0',
     policy=UNVERIFIED, blocked=['scoped_fact'],
     expression={'text': '离线模式仅在 beta 通道提供，稳定版尚未开放。',
                 'quote': '离线模式仅在 beta 通道提供', 'expect': EXPR_CANDIDATE})
case('AC33-03', 'truncated_quote', ['AC33'],
     '引用 Windows 支持，省略“macOS 尚未发布”',
     'Plugin X 2.0 支持所有桌面系统。', [], status='insufficient', support='E0',
     policy=UNVERIFIED, blocked=['scoped_fact'],
     expression={'text': '官方文档写明支持 Windows，但 macOS 版本尚未发布。',
                 'quote': '官方文档写明支持 Windows', 'expect': EXPR_CANDIDATE})
case('AC33-04', 'truncated_quote', ['AC33'],
     '完整句子可以正常锚定', 'Plugin X 2.0 支持 Windows 与 macOS。',
     [], status='insufficient', support='E0',
     expression={'text': '该版本支持 Windows 与 macOS。',
                 'quote': '支持 Windows 与 macOS', 'expect': EXPR_ACCEPT})
case('AC33-05', 'truncated_quote', ['AC33'],
     '并列补充句不触发截断规则', '导出功能已上线。',
     [], status='insufficient', support='E0',
     expression={'text': '导出功能已上线，用户反馈良好。',
                 'quote': '导出功能已上线', 'expect': EXPR_ACCEPT})
case('AC33-06', 'truncated_quote', ['AC33'],
     '“但是仅限于 20 个文件”被省略',
     'Plugin X 2.0 支持无限批量导入。', [], status='insufficient', support='E0',
     policy=UNVERIFIED, blocked=['scoped_fact'],
     expression={'text': '新版本增加了批量导入，但是仅限于 20 个文件。',
                 'quote': '新版本增加了批量导入', 'expect': EXPR_CANDIDATE})
case('AC33-07', 'truncated_quote', ['AC33'],
     '“不支持云端同步”被省略',
     'Plugin X 2.0 支持离线与云端同步。', [], status='insufficient', support='E0',
     policy=UNVERIFIED, blocked=['scoped_fact'],
     expression={'text': '该插件可在离线环境运行，不支持云端同步。',
                 'quote': '该插件可在离线环境运行', 'expect': EXPR_CANDIDATE})
case('AC33-08', 'truncated_quote', ['AC33'],
     '“并且支持云端同步”是并列而非限定', 'Plugin X 2.0 支持离线与云端同步。',
     [], status='insufficient', support='E0',
     expression={'text': '该插件可在离线环境运行，并且支持云端同步。',
                 'quote': '该插件可在离线环境运行', 'expect': EXPR_ACCEPT})

# ------------------------------------------------ AC34 superseded version
case('AC34-01', 'expired_version', ['AC34'],
     '唯一的支持材料来自已被新版本取代的来源', 'Plugin X 2.0 当前仍支持离线索引。',
     [mat(superseded=True)], importance='medium', status='insufficient',
     support='E1', policy=UNVERIFIED, blocked=['scoped_fact'])
case('AC34-02', 'expired_version', ['AC34', 'AC35'],
     '两个过期版本不能拼成 E3', 'Plugin X 2.0 当前仍支持离线索引。',
     [mat(origin='G1', independence='confirmed', superseded=True),
      mat(origin='G2', independence='confirmed', superseded=True)],
     importance='high', status='insufficient', support='E1', policy=HOLD,
     blocked=['scoped_fact', 'attributed_unverified'])
case('AC34-03', 'expired_version', ['AC34'],
     '当前版本材料与过期材料混合，只能按当前版本一个上游组计',
     'Plugin X 2.0 当前仍支持离线索引。',
     [mat(origin='G1', independence='confirmed'),
      mat(origin='G2', independence='confirmed', superseded=True)],
     importance='high', status='corroborated', support='E2',
     gate=['IMPORTANT_FACT_REQUIRES_E3'], policy=HOLD, blocked=['scoped_fact'])
case('AC34-04', 'expired_version', ['AC34'],
     '两个当前版本独立组可确认重要事实', 'Plugin X 2.0 当前仍支持离线索引。',
     [mat(origin='G1', independence='confirmed'),
      mat(origin='G2', independence='confirmed')],
     importance='high', status='corroborated', support='E3', policy=FACT)
case('AC34-05', 'expired_version', ['AC34', 'AC42'],
     '当前版本的强反证可以推翻旧结论', 'Plugin X 2.0 当前仍支持离线索引。',
     [mat('fact_refuted_by', origin='G1', independence='confirmed'),
      mat('fact_refuted_by', origin='G2', independence='confirmed')],
     importance='medium', status='refuted', refutation='E3',
     policy=CORRECTION, blocked=['scoped_fact', 'attributed_unverified'])
case('AC34-06', 'expired_version', ['AC34'],
     '保留期到期的支持材料不可用', 'Plugin X 2.0 当前仍支持离线索引。',
     [mat(retention='expired', origin='G1')], importance='medium',
     status='insufficient', support='E0', policy=UNVERIFIED, blocked=['scoped_fact'])
case('AC34-07', 'expired_version', ['AC34'],
     '过期反向材料仍作为未解决反证，不能直接确认', 'Plugin X 2.0 当前仍支持离线索引。',
     [mat(origin='G1'), mat('fact_refuted_by', superseded=True)],
     importance='medium', status='conflicted', support='E2', refutation='E1',
     policy=DISPUTE, blocked=['scoped_fact'])
case('AC34-08', 'expired_version', ['AC34'],
     '全部材料过期时状态必须为未核实', 'Plugin X 2.0 当前仍支持离线索引。',
     [mat(superseded=True), mat(superseded=True, origin='G2')],
     importance='high', status='insufficient', support='E1', policy=HOLD,
     blocked=['scoped_fact'])

# ------------------------------------------------ AC35 circular repost
case('AC35-01', 'circular_repost', ['AC35'],
     '十个来源互相转述同一公告', 'Plugin X 2.0 已发布。',
     [mat(origin='G1', independence='shared_origin') for _ in range(10)],
     importance='medium', status='corroborated', support='E2', gate=[],
     policy=FACT)
case('AC35-02', 'circular_repost', ['AC35'],
     '同一上游组即使标注确认独立也不能升级', 'Plugin X 2.0 已发布。',
     [mat(origin='G1', independence='confirmed'),
      mat(origin='G1', independence='confirmed')],
     importance='medium', status='corroborated', support='E2', gate=[],
     policy=FACT)
case('AC35-03', 'circular_repost', ['AC35'],
     '三个上游组但独立性全部未知', 'Plugin X 2.0 已发布。',
     [mat(origin='G1'), mat(origin='G2'), mat(origin='G3')],
     importance='high', status='corroborated', support='E2',
     gate=['IMPORTANT_FACT_REQUIRES_E3'], policy=HOLD, blocked=['scoped_fact'])
case('AC35-04', 'circular_repost', ['AC35'],
     '两个上游组，仅一组确认独立，达到 E3 的最低条件', 'Plugin X 2.0 已发布。',
     [mat(origin='G1', independence='confirmed'),
      mat(origin='G2', independence='unknown')],
     importance='medium', status='corroborated', support='E3', gate=[], policy=FACT)
case('AC35-05', 'circular_repost', ['AC35'],
     '十个来源、两个独立组才可升级', 'Plugin X 2.0 已发布。',
     [mat(origin='G1', independence='confirmed'),
      mat(origin='G2', independence='confirmed')] + [mat(origin='G1', independence='shared_origin') for _ in range(8)],
     importance='high', status='corroborated', support='E3', gate=[], policy=FACT)
case('AC35-06', 'circular_repost', ['AC35'],
     '同源转载数量不改变间接材料的档位', 'Plugin X 2.0 已发布。',
     [mat(direct='indirect', origin='G1') for _ in range(10)],
     importance='medium', status='insufficient', support='E1', policy=UNVERIFIED,
     blocked=['scoped_fact'])
case('AC35-07', 'circular_repost', ['AC35'],
     '同一来源版本重复登记仍是同一上游', 'Plugin X 2.0 已发布。',
     [mat(origin='G1', independence='unknown') for _ in range(3)],
     importance='high', status='corroborated', support='E2',
     gate=['IMPORTANT_FACT_REQUIRES_E3'], policy=HOLD)
case('AC35-08', 'circular_repost', ['AC35', 'AC40'],
     '多来源一致说法不替代事实证据', 'Plugin X 2.0 已发布。',
     [mat(direct='indirect', origin='G%d' % index) for index in range(1, 9)],
     importance='medium', status='insufficient', support='E1', policy=UNVERIFIED,
     blocked=['scoped_fact'])

# ------------------------------------------------ AC36 forged / unverifiable review
case('AC36-01', 'forged_review', ['AC36'],
     '仅截图且疑似拼接', 'Plugin X 2.0 性能提升三倍。',
     [mat(integrity='suspected_manipulation')], importance='high',
     status='insufficient', support='E0', policy=HOLD, blocked=['scoped_fact'])
case('AC36-02', 'forged_review', ['AC36'],
     '无原始数据的高分表无法核验完整性', 'Plugin X 2.0 性能提升三倍。',
     [mat(integrity='unverified')], importance='high', status='insufficient',
     support='E1', policy=HOLD, blocked=['scoped_fact'])
case('AC36-03', 'forged_review', ['AC36'],
     '截图转述属于间接材料', 'Plugin X 2.0 性能提升三倍。',
     [mat(direct='indirect', integrity='unverified')], importance='medium',
     status='insufficient', support='E1', policy=UNVERIFIED, blocked=['scoped_fact'])
case('AC36-04', 'forged_review', ['AC36'],
     '两条未核验材料仍不能确认', 'Plugin X 2.0 性能提升三倍。',
     [mat(integrity='unverified', origin='G1'),
      mat(integrity='unverified', origin='G2')],
     importance='high', status='insufficient', support='E1', policy=HOLD,
     blocked=['scoped_fact'])
case('AC36-05', 'forged_review', ['AC36'],
     '一条疑似材料不否定另一条可核验材料', 'Plugin X 2.0 性能提升三倍。',
     [mat(origin='G1'), mat(integrity='suspected_manipulation', origin='G2')],
     importance='medium', status='corroborated', support='E2', gate=[], policy=FACT)
case('AC36-06', 'forged_review', ['AC36', 'AC41'],
     '可核验支持与未核验反证形成争议', 'Plugin X 2.0 性能提升三倍。',
     [mat(origin='G1'), mat('fact_refuted_by', integrity='unverified')],
     importance='medium', status='conflicted', support='E2', refutation='E1',
     policy=DISPUTE, blocked=['scoped_fact'])
case('AC36-07', 'forged_review', ['AC36'],
     '矛盾日志不构成强反证', 'Plugin X 2.0 性能提升三倍。',
     [mat('fact_refuted_by', direct='indirect', integrity='unverified')],
     importance='medium', status='insufficient', support='E0', refutation='E1',
     policy=UNVERIFIED, blocked=['scoped_fact', 'refuted'])
case('AC36-08', 'forged_review', ['AC36'],
     '无法确定伪造时只能标未核实，不能指控造假', 'Plugin X 2.0 性能提升三倍。',
     [mat(integrity='unverified', direct='indirect',
          limitations=['无法判断截图来源'])],
     importance='high', status='insufficient', support='E1', policy=HOLD,
     blocked=['scoped_fact', 'refuted'])

# ------------------------------------------------ AC37 true and false criticism
case('AC37-01', 'mixed_criticism', ['AC37'],
     '真实限制：批量导入上限 20 个文件', 'Plugin X 2.0 批量导入每次最多 20 个文件。',
     [mat(origin='G1', limitations=['仅限 20 个文件'])], importance='medium',
     status='corroborated', support='E2', gate=[], policy=FACT, pair='CRIT-1')
case('AC37-02', 'mixed_criticism', ['AC37'],
     '真实限制：离线模式仍需首次登录', 'Plugin X 2.0 离线模式需要首次登录。',
     [mat(origin='G1')], importance='medium', status='corroborated', support='E2',
     gate=[], policy=FACT, pair='CRIT-2')
case('AC37-03', 'mixed_criticism', ['AC37'],
     '真实限制：不支持 Linux', 'Plugin X 2.0 不支持 Linux。',
     [mat(origin='G1')], importance='medium', status='corroborated', support='E2',
     gate=[], policy=FACT, pair='CRIT-3')
case('AC37-04', 'mixed_criticism', ['AC37'],
     '真实限制：同步功能尚未发布', 'Plugin X 2.0 的同步功能尚未发布。',
     [mat(origin='G1')], importance='medium', status='corroborated', support='E2',
     gate=[], policy=FACT, pair='CRIT-4')
case('AC37-05', 'mixed_criticism', ['AC37', 'AC42'],
     '错误指控：声称完全无法导入', 'Plugin X 2.0 完全无法导入文件。',
     [mat('fact_refuted_by', origin='G1', independence='confirmed'),
      mat('fact_refuted_by', origin='G2', independence='confirmed')],
     importance='high', status='refuted', refutation='E3', policy=CORRECTION,
     blocked=['scoped_fact', 'attributed_unverified'], pair='CRIT-1')
case('AC37-06', 'mixed_criticism', ['AC37'],
     '错误指控：声称离线模式需要付费', 'Plugin X 2.0 离线模式需要付费。',
     [mat('fact_refuted_by', origin='G1', independence='confirmed'),
      mat('fact_refuted_by', origin='G2', independence='confirmed')],
     importance='medium', status='refuted', refutation='E3', policy=CORRECTION,
     blocked=['scoped_fact'], pair='CRIT-2')
case('AC37-07', 'mixed_criticism', ['AC37'],
     '错误指控：声称所有平台均不可用', 'Plugin X 2.0 在所有平台均不可用。',
     [mat('fact_refuted_by', origin='G1', independence='confirmed'),
      mat('fact_refuted_by', origin='G2', independence='confirmed')],
     importance='medium', status='refuted', refutation='E3', policy=CORRECTION,
     blocked=['scoped_fact'], pair='CRIT-3')
case('AC37-08', 'mixed_criticism', ['AC37'],
     '错误指控：声称已经停止维护', 'Plugin X 2.0 已经停止维护。',
     [mat('fact_refuted_by', origin='G1', independence='confirmed'),
      mat('fact_refuted_by', origin='G2', independence='confirmed')],
     importance='high', status='refuted', refutation='E3', policy=CORRECTION,
     blocked=['scoped_fact', 'attributed_unverified'], pair='CRIT-4')

# ------------------------------------------------ controls: true feature
for index, (importance, origins) in enumerate(
        [('medium', [('G1', 'unknown')]), ('high', [('G1', 'confirmed'), ('G2', 'confirmed')]),
         ('medium', [('G1', 'confirmed'), ('G2', 'unknown')])], start=1):
    materials = [mat(origin=origin, independence=independence)
                 for origin, independence in origins]
    support = 'E3' if len(origins) == 2 else 'E2'
    gate = [] if importance == 'medium' or support == 'E3' else ['IMPORTANT_FACT_REQUIRES_E3']
    case('CTRL-0%d' % index, 'true_feature', ['AC32', 'AC40'],
         '对照：真实功能且证据适用（强度 %s）' % support, 'Plugin X 2.0 支持离线索引。',
         materials, importance=importance, status='corroborated', support=support,
         gate=gate, policy=FACT if not gate else HOLD)

# ------------------------------------------------ controls: applicable refutation
for index in range(4, 7):
    case('CTRL-0%d' % index, 'applicable_refutation', ['AC34', 'AC42'],
         '对照：适用的 E3 强反证使旧结论失效', 'Plugin X 2.0 支持离线索引。',
         [mat('fact_refuted_by', origin='G1', independence='confirmed'),
          mat('fact_refuted_by', origin='G2', independence='confirmed')],
         importance='medium', status='refuted', refutation='E3', policy=CORRECTION,
         blocked=['scoped_fact', 'attributed_unverified'])

# ------------------------------------------------ controls: low-reliability source
for index in range(7, 10):
    case('CTRL-0%d' % index, 'low_reliability_strong_evidence', ['AC38'],
         '对照：来源可靠度未知，但当前命题有可检查的直接证据',
         'Plugin X 2.0 支持离线索引。',
         [mat(origin='G1', independence='confirmed')], importance='medium',
         status='corroborated', support='E2', gate=[], policy=FACT)

# ------------------------------------------------ controls: asymmetric dispute
for index in range(10, 13):
    case('CTRL-%d' % index, 'asymmetric_dispute', ['AC41'],
         '对照：一侧直接证据、另一侧只有转述',
         'Plugin X 2.0 支持离线索引。',
         [mat(origin='G1'),
          mat('fact_refuted_by', direct='indirect', origin='G2')],
         importance='medium', status='conflicted', support='E2', refutation='E1',
         policy=DISPUTE, blocked=['scoped_fact'])

DATASET = {
    'schema_version': 'ac32_45_dataset.v1',
    'description': ('AC32–45 专项阻断数据集：48 个合成案例（6 类各 8 例）'
                    '与 12 个对照。全部为合成材料，不含真实 B站 内容、真实模型输出或外部检索结果。'),
    'synthetic': True,
    'policy_version': 'publication.v1.3.0',
    'categories': {
        'false_feature_advertising': {'ac': 'AC32', 'cases': 8},
        'truncated_quote': {'ac': 'AC33', 'cases': 8},
        'expired_version': {'ac': 'AC34', 'cases': 8},
        'circular_repost': {'ac': 'AC35', 'cases': 8},
        'forged_review': {'ac': 'AC36', 'cases': 8},
        'mixed_criticism': {'ac': 'AC37', 'cases': 8},
        'true_feature': {'ac': 'control', 'cases': 3},
        'applicable_refutation': {'ac': 'control', 'cases': 3},
        'low_reliability_strong_evidence': {'ac': 'control', 'cases': 3},
        'asymmetric_dispute': {'ac': 'control', 'cases': 3},
    },
    # Which artefact verifies each acceptance criterion. Cases carry their own AC tags;
    # AC39/44/45 need a route/DB fixture and are proven by the integration tests.
    'ac_coverage': {
        'AC32': 'dataset', 'AC33': 'dataset', 'AC34': 'dataset', 'AC35': 'dataset',
        'AC36': 'dataset', 'AC37': 'dataset', 'AC38': 'dataset controls',
        'AC39': 'unit + integration（模型声称外部读取被拒绝，检索仍为 kb_only）',
        'AC40': 'dataset + unit（抽取置信度与来源数量不改变档位）',
        'AC41': 'dataset controls（不对称争议的强弱与缺口）',
        'AC42': 'integration（接受争议记录后旧确定性在读取面同时消失，历史保留）',
        'AC43': 'dataset + integration（标题/正文/日报/导出统一门槛）',
        'AC44': 'integration（fact_check 路由预算、回退、费用与不降低门槛）',
        'AC45': 'integration（过期输入 409）+ policy unit（规则记录不能提升表达）',
    },
    'cases': CASES,
}

if __name__ == '__main__':
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(DATASET, ensure_ascii=False, indent=1) + '\n',
                   encoding='utf8')
    print('wrote %d cases to %s' % (len(CASES), OUT))
