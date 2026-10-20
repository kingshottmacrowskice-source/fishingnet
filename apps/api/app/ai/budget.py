"""Worst-case budget reservation and cost accounting for model calls.

Every paid call reserves the worst-case cost (route input/output token ceilings at
the model's price) in a short transaction before any network I/O, then settles the
actual usage afterwards. A crash between the two leaves the reservation in place,
so the ledger can only over-estimate spend, never under-estimate it. Budget windows
are UTC days and calendar months; a workspace has exactly one currency and a model
profile must use it (there is no implicit FX conversion).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy.exc import OperationalError

from app.storage.db import one, execute

MONEY_QUANT = Decimal('0.00000001')
TOKEN_SCALE = Decimal('1000000')
AMOUNT_FIELDS = ('daily_budget', 'monthly_budget')


class BudgetError(Exception):
    def __init__(self, code: str, message: str, details=None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def to_decimal(value, field: str = 'amount') -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BudgetError('INVALID_AMOUNT', f'{field} 必须是十进制金额')
    if not amount.is_finite():
        raise BudgetError('INVALID_AMOUNT', f'{field} 必须是有限金额')
    if amount < 0:
        raise BudgetError('INVALID_AMOUNT', f'{field} 不能为负数')
    if amount > Decimal('1000000'):
        raise BudgetError('INVALID_AMOUNT', f'{field} 超出允许范围（≤ 1000000）')
    return amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def estimate_cost(input_tokens, output_tokens, model: dict) -> Decimal:
    """Cost of known token counts at the model's per-million-token prices."""
    prompt = Decimal(max(0, int(input_tokens or 0)))
    completion = Decimal(max(0, int(output_tokens or 0)))
    price_in = Decimal(str(model.get('input_price_per_mtok') or 0))
    price_out = Decimal(str(model.get('output_price_per_mtok') or 0))
    total = (prompt * price_in + completion * price_out) / TOKEN_SCALE
    return total.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def reservation_amount(route: dict, model: dict) -> Decimal:
    """Worst case for one attempt: both token ceilings at the model's prices."""
    return estimate_cost(route.get('max_input_tokens') or 0,
                         route.get('max_output_tokens') or 0, model)


def normalize_usage(usage) -> dict:
    """Map an OpenAI-compatible ``usage`` object to plain ints (0 when absent)."""
    if not isinstance(usage, dict):
        return {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0}
    cached = usage.get('prompt_tokens_details') or usage.get('prompt_tokens_detail') or {}
    try:
        cached_tokens = int(cached.get('cached_tokens') or 0) if isinstance(cached, dict) else 0
    except (TypeError, ValueError):
        cached_tokens = 0
    result = {}
    for target, source in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens')):
        try:
            result[target] = max(0, int(usage.get(source) or 0))
        except (TypeError, ValueError):
            result[target] = 0
    result['cached_tokens'] = max(0, cached_tokens)
    return result


def _sum(conn, workspace_id, day: date, column_filter: str) -> Decimal:
    row = one(conn, f"""SELECT COALESCE(SUM(COALESCE(settled,reserved)),0) AS spent
        FROM ai_budget_ledger WHERE workspace_id=:ws AND {column_filter}
        AND status IN ('reserved','settled')""", ws=workspace_id, day=day)
    return Decimal(str(row['spent'] if row else 0))


def spend_totals(conn, workspace_id, day: date | None = None) -> dict:
    """Spend held against today's and this month's budget (reserved + settled)."""
    today = day or utc_today()
    month_start = today.replace(day=1)
    return {'day': today, 'month_start': month_start,
            'day_spent': _sum(conn, workspace_id, today, 'day=:day'),
            'month_spent': _sum(conn, workspace_id, month_start, 'month_start=:day')}


def budget_state(conn, workspace_id) -> dict:
    settings_row = one(conn, 'SELECT daily_budget,monthly_budget,budget_currency FROM workspace_settings WHERE id=:ws',
                       ws=workspace_id)
    if not settings_row:
        raise BudgetError('WORKSPACE_NOT_FOUND', '工作区不存在')
    daily = Decimal(str(settings_row['daily_budget']))
    monthly = Decimal(str(settings_row['monthly_budget']))
    totals = spend_totals(conn, workspace_id)
    return {'currency': settings_row['budget_currency'],
            'daily_budget': daily, 'monthly_budget': monthly,
            'day_spent': totals['day_spent'], 'month_spent': totals['month_spent'],
            'day_remaining': max(Decimal('0'), daily - totals['day_spent']),
            'month_remaining': max(Decimal('0'), monthly - totals['month_spent']),
            'day': totals['day'], 'month_start': totals['month_start']}


def reserve(conn, *, workspace_id, invocation_id, task: str, amount: Decimal,
            currency: str, reason: str = '', day: date | None = None) -> dict:
    """Reserve ``amount`` inside the caller's transaction (short, no network I/O).

    The workspace row is locked so concurrent reservations are serialized; the
    check and the insert use the same lock, so N parallel workers cannot all pass
    a budget that only fits one call.
    """
    amount = Decimal(str(amount)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    if amount < 0:
        raise BudgetError('INVALID_AMOUNT', '预留金额不能为负数')
    today = day or utc_today()
    month_start = today.replace(day=1)
    try:
        settings_row = one(conn, """SELECT daily_budget,monthly_budget,budget_currency
            FROM workspace_settings WHERE id=:ws FOR UPDATE""", ws=workspace_id)
    except OperationalError:
        raise BudgetError('BUDGET_BUSY', '预算账本正忙，请稍后重试')
    if not settings_row:
        raise BudgetError('WORKSPACE_NOT_FOUND', '工作区不存在')
    if settings_row['budget_currency'] != currency:
        raise BudgetError('CURRENCY_MISMATCH',
                          f'模型计价货币 {currency} 与工作区预算货币 {settings_row["budget_currency"]} 不一致')
    if amount > 0:
        daily = Decimal(str(settings_row['daily_budget']))
        monthly = Decimal(str(settings_row['monthly_budget']))
        day_spent = _sum(conn, workspace_id, today, 'day=:day')
        month_spent = _sum(conn, workspace_id, month_start, 'month_start=:day')
        details = {'currency': currency, 'required': str(amount),
                   'daily_budget': str(daily), 'daily_spent': str(day_spent),
                   'monthly_budget': str(monthly), 'month_spent': str(month_spent)}
        if daily <= 0:
            raise BudgetError('BUDGET_EXHAUSTED', '本工作区云调用预算为零；请先在系统设置中设置预算', details)
        if monthly <= 0:
            raise BudgetError('BUDGET_EXHAUSTED', '本工作区月度预算为零；请先在系统设置中设置预算', details)
        if day_spent + amount > daily:
            raise BudgetError('BUDGET_EXHAUSTED', '本日预算不足以覆盖本次调用的最坏费用', details)
        if month_spent + amount > monthly:
            raise BudgetError('BUDGET_EXHAUSTED', '本月预算不足以覆盖本次调用的最坏费用', details)
    row = one(conn, """INSERT INTO ai_budget_ledger(workspace_id,invocation_id,task,day,month_start,
          reserved,currency,reason,status) VALUES(:ws,:inv,:task,:day,:month,:amount,:currency,:reason,'reserved')
          RETURNING *""",
              ws=workspace_id, inv=invocation_id, task=task, day=today, month=month_start,
              amount=amount, currency=currency, reason=reason)
    return row


def settle(conn, ledger_id, actual: Decimal) -> None:
    actual = Decimal(str(actual)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    execute(conn, """UPDATE ai_budget_ledger SET status='settled',settled=:amount,settled_at=now()
        WHERE id=:id AND status='reserved'""", id=ledger_id, amount=actual)


def release(conn, ledger_id) -> None:
    execute(conn, """UPDATE ai_budget_ledger SET status='released',settled=0,settled_at=now()
        WHERE id=:id AND status='reserved'""", id=ledger_id)
