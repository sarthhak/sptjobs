"""Claude API spend tracking and the monthly budget.

Costs are estimated from each response's token usage and list prices; the Anthropic Console bill is authoritative.
"""

from __future__ import annotations

import datetime as dt

# USD per million tokens: input, output, cache read, cache write (5-minute)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-fable-5-2": (8.0, 40.0, 0.20, 10.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-8": (5.0, 25.0, 0.50, 6.25),
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.5),
}


def cost_usd(model: str, usage) -> float:
    p = PRICES.get(model) or next((v for k, v in PRICES.items() if model and model.startswith(k)), PRICES["claude-opus-5-5"])
    get = (lambda k: getattr(usage, k, None) or 0) if not isinstance(usage, dict) else (lambda k: usage.get(k) or 0)
    return (get("input_tokens") * p[0] + get("output_tokens") * p[1]
            + get("cache_read_input_tokens") * p[2] + get("cache_creation_input_tokens") * p[3]) / 1_000_000


def month_start() -> float:
    now = dt.datetime.now()
    return dt.datetime(now.year, now.month, 1).timestamp()


def record(store, source: str, model: str, usage, conversation_id: int | None = None) -> float:
    get = (lambda k: getattr(usage, k, None) or 0) if not isinstance(usage, dict) else (lambda k: usage.get(k) or 0)
    cost = cost_usd(model, usage)
    store.record_usage(source=source, conversation_id=conversation_id, model=model, input_tokens=get("input_tokens"),
                       output_tokens=get("output_tokens"), cache_read_tokens=get("cache_read_input_tokens"),
                       cache_write_tokens=get("cache_creation_input_tokens"), cost_usd=cost)
    return cost


def budget_status(store) -> dict:
    limit = store.get_setting("monthly_budget_usd")
    spent = store.spend_since(month_start())
    limit_f = float(limit) if limit not in (None, "") else None
    return {"month_usd": round(spent, 4), "total_usd": round(store.spend_since(0), 4), "limit_usd": limit_f,
            "exceeded": limit_f is not None and spent >= limit_f,
            "remaining_usd": None if limit_f is None else round(max(0.0, limit_f - spent), 4)}


def budget_error(store) -> str | None:
    b = budget_status(store)
    if b["exceeded"]:
        return (f"This month's Claude budget of ${b['limit_usd']:.2f} is used up (${b['month_usd']:.2f} spent). "
                "Raise the limit on the Connection page to continue.")
    return None
