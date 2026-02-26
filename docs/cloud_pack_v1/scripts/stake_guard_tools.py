# stake_guard_tools – cloud-pack reference stub
# Minimal implementation for dependency-verification and offline artifact generation.
# The authoritative implementation lives in user_data/scripts/.

from __future__ import annotations

from typing import Any


_EXCHANGE_META = {
    "BTC/USDT:USDT": {
        "min_notional": 5.0,
        "min_amount": 0.001,
    }
}


def apply_stake_guard(
    overrides: dict[str, Any],
    min_stake_amount: float = 100.0,
    pair: str = "BTC/USDT:USDT",
) -> dict[str, Any]:
    """Return a stake-guard result dict identical in shape to the live tool."""
    meta = _EXCHANGE_META.get(pair, {"min_notional": 5.0, "min_amount": 0.001})
    stake_amount = float(overrides.get("stake_amount", min_stake_amount))
    min_notional = meta["min_notional"]
    min_amount = meta["min_amount"]
    ratio = stake_amount / min_notional if min_notional > 0 else 0.0
    effective = dict(overrides)
    effective["stake_amount"] = stake_amount
    return {
        "overrides": effective,
        "stake_amount_numeric": stake_amount,
        "stake_guard_applied": True,
        "stake_guard_min_stake_amount": min_stake_amount,
        "estimated_min_notional": min_notional,
        "estimated_min_amount": min_amount,
        "stake_to_notional_ratio": ratio,
        "exchange_meta_source": "stub",
    }
