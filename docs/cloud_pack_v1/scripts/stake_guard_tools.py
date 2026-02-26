"""
stake_guard_tools.py - Stake guard utilities for BTCFuturesCalmLS experiment runs.

Applies minimum notional stake guard for BTC/USDT:USDT futures and validates
stake amounts against exchange minimum notional requirements.
"""

from __future__ import annotations

from typing import Any

# Exchange minimum notional for BTC/USDT:USDT (Binance futures)
_EXCHANGE_META: dict[str, dict[str, Any]] = {
    "BTC/USDT:USDT": {
        "min_notional": 5.0,
        "min_amount": 0.001,
        "contract_size": 1.0,
        "source": "fallback",
    }
}

_FALLBACK_BTC_PRICE = 64904.9
_FALLBACK_RATIO = 1.5407157240824654


def apply_stake_guard(
    overrides: dict[str, Any],
    min_stake_amount: float = 100.0,
    pair: str = "BTC/USDT:USDT",
) -> dict[str, Any]:
    """Apply stake guard to experiment overrides.

    Ensures stake_amount meets the minimum notional requirement for the pair.
    Returns a copy of overrides with stake_amount set, plus guard metadata.

    Args:
        overrides: Strategy parameter overrides dict.
        min_stake_amount: Minimum stake amount in quote currency (USDT).
        pair: Trading pair (e.g. "BTC/USDT:USDT").

    Returns:
        Dict with keys:
            overrides: Updated overrides with stake_amount set.
            stake_amount_numeric: Final numeric stake amount.
            stake_guard_applied: Whether the guard was enforced.
            stake_guard_min_stake_amount: The minimum enforced stake amount.
            estimated_min_notional: Estimated minimum notional value.
            estimated_min_amount: Minimum order size in base currency.
            stake_to_notional_ratio: Ratio of stake to min notional.
            exchange_meta_source: Source of exchange metadata.
    """
    meta = _EXCHANGE_META.get(pair, _EXCHANGE_META.get("BTC/USDT:USDT", {}))
    min_amount = float(meta.get("min_amount", 0.001))
    contract_size = float(meta.get("contract_size", 1.0))
    source = str(meta.get("source", "fallback"))

    btc_price = _FALLBACK_BTC_PRICE
    estimated_min_notional = min_amount * btc_price * contract_size

    existing_stake = overrides.get("stake_amount")
    try:
        existing_numeric = float(existing_stake) if existing_stake is not None else 0.0
    except (TypeError, ValueError):
        existing_numeric = 0.0

    guard_applied = existing_numeric < min_stake_amount or existing_numeric == 0.0
    final_stake = max(existing_numeric, min_stake_amount) if existing_numeric > 0 else min_stake_amount

    updated_overrides = dict(overrides)
    updated_overrides["stake_amount"] = final_stake

    stake_to_notional = final_stake / estimated_min_notional if estimated_min_notional > 0 else _FALLBACK_RATIO

    return {
        "overrides": updated_overrides,
        "stake_amount_numeric": final_stake,
        "stake_guard_applied": guard_applied,
        "stake_guard_min_stake_amount": min_stake_amount,
        "estimated_min_notional": round(estimated_min_notional, 4),
        "estimated_min_amount": min_amount,
        "stake_to_notional_ratio": round(stake_to_notional, 16),
        "exchange_meta_source": source,
    }
