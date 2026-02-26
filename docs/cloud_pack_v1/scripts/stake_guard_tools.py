"""Stake guard utility for BTC futures minimum position-size validation.

This module provides ``apply_stake_guard`` which enforces a minimum notional
value constraint on the stake_amount override and returns exchange meta
information used for audit / manifest tracking.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Exchange constants (fallback / static values for BTC/USDT:USDT on Binance)
# ---------------------------------------------------------------------------
_BTC_MIN_AMOUNT = 0.001          # BTC, exchange minimum order size
_BTC_MIN_NOTIONAL_USDT = 100.0  # USDT, exchange minimum notional
_BTC_PRICE_FALLBACK = 64904.9   # USD, used when live price unavailable


def apply_stake_guard(
    overrides: dict[str, Any],
    min_stake_amount: float = 100.0,
    pair: str = "BTC/USDT:USDT",
) -> dict[str, Any]:
    """Validate and, if necessary, raise the stake_amount to meet exchange minimums.

    Parameters
    ----------
    overrides:
        Mutable dict of strategy / config overrides.  May contain a
        ``stake_amount`` key (numeric string or float).  Modified in-place only
        when guard is triggered.
    min_stake_amount:
        The caller-supplied floor for stake_amount in quote currency (USDT).
    pair:
        Trading pair string used for logging / metadata only.

    Returns
    -------
    dict with keys:
        overrides              – updated overrides dict (same object)
        stake_amount_numeric   – resolved numeric stake_amount (float)
        stake_guard_applied    – True if stake was raised to meet floor
        stake_guard_min_stake_amount – the floor used
        estimated_min_notional – exchange-minimum notional (USDT)
        estimated_min_amount   – exchange-minimum BTC amount
        stake_to_notional_ratio – stake_amount / estimated_min_notional
        exchange_meta_source   – "fallback" (static constants used)
    """
    estimated_min_notional: float = _BTC_MIN_NOTIONAL_USDT
    estimated_min_amount: float = _BTC_MIN_AMOUNT
    exchange_meta_source: str = "fallback"

    # Resolve current stake_amount from overrides
    raw_stake = overrides.get("stake_amount", min_stake_amount)
    try:
        current_stake = float(raw_stake)
    except (TypeError, ValueError):
        current_stake = min_stake_amount

    # Determine required floor: max of caller floor and exchange notional
    required_floor = max(min_stake_amount, estimated_min_notional)

    guard_applied = False
    if current_stake < required_floor:
        overrides["stake_amount"] = required_floor
        current_stake = required_floor
        guard_applied = True

    stake_to_notional = (
        current_stake / estimated_min_notional
        if estimated_min_notional > 0
        else 0.0
    )

    return {
        "overrides": overrides,
        "stake_amount_numeric": current_stake,
        "stake_guard_applied": guard_applied,
        "stake_guard_min_stake_amount": min_stake_amount,
        "estimated_min_notional": estimated_min_notional,
        "estimated_min_amount": estimated_min_amount,
        "stake_to_notional_ratio": stake_to_notional,
        "exchange_meta_source": exchange_meta_source,
    }
