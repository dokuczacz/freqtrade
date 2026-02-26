"""
stake_guard_tools – stake amount guard utilities for cloud_pack_v1 runs.

Ensures the effective stake_amount in overrides satisfies the exchange
minimum notional requirement for BTC/USDT:USDT perpetual futures.

Public API
----------
apply_stake_guard(overrides, min_stake_amount, pair) -> dict
"""

from __future__ import annotations

from typing import Any

# Exchange minimum parameters (fallback / static, no live call required).
# Source: Binance BTCUSDT perpetual contract spec (approximate).
_EXCHANGE_META: dict[str, Any] = {
    "pair": "BTC/USDT:USDT",
    "min_notional_usdt": 100.0,
    "min_amount_btc": 0.001,
    "reference_price_usdt": 65000.0,  # conservative mid-market fallback
    "source": "fallback",
}


def apply_stake_guard(
    overrides: dict[str, Any],
    min_stake_amount: float = 100.0,
    pair: str = "BTC/USDT:USDT",
) -> dict[str, Any]:
    """
    Validate and enforce minimum stake amount in *overrides*.

    Parameters
    ----------
    overrides:
        Mutable dict of strategy/config override keys.  The function adds or
        replaces the ``stake_amount`` key.
    min_stake_amount:
        Floor value for ``stake_amount`` in USDT.
    pair:
        Trading pair (informational; used to select exchange meta).

    Returns
    -------
    dict with keys:
        overrides                   – updated overrides dict (same object)
        stake_amount_numeric        – final numeric stake amount
        stake_guard_applied         – True if stake was raised to the floor
        stake_guard_min_stake_amount – the floor that was applied
        estimated_min_notional      – exchange min notional
        estimated_min_amount        – exchange min base amount
        stake_to_notional_ratio     – stake / min_notional
        exchange_meta_source        – "fallback" or "live"
    """
    meta = _EXCHANGE_META

    current = overrides.get("stake_amount")
    try:
        current_numeric = float(current) if current is not None else 0.0
    except (TypeError, ValueError):
        current_numeric = 0.0

    guard_applied = current_numeric < min_stake_amount
    final_stake = max(current_numeric, min_stake_amount)

    overrides["stake_amount"] = final_stake

    min_notional = float(meta["min_notional_usdt"])
    min_amount = float(meta["min_amount_btc"])
    ratio = final_stake / min_notional if min_notional > 0 else 0.0

    return {
        "overrides": overrides,
        "stake_amount_numeric": final_stake,
        "stake_guard_applied": guard_applied,
        "stake_guard_min_stake_amount": min_stake_amount,
        "estimated_min_notional": min_notional,
        "estimated_min_amount": min_amount,
        "stake_to_notional_ratio": round(ratio, 10),
        "exchange_meta_source": str(meta["source"]),
    }
