"""
Generate manifest and per-run configs for phase5_fc_cloud_profit_target_v1_3.

Objective: find BTCFuturesCalmLS variants where monthly_profit_norm_pct
falls in [2.0, 2.5] on BOTH LW6M and LW12M windows, subject to:
  PF >= 1.10, max_drawdown_pct <= 3.0, tail_worsening_count_guard == 0.

Optimization policy: bounded, non-broad; baseline CTRL untouched;
shadow ML in telemetry-only mode (no entry/exit impact).

Usage (from repo root):
    cd docs/cloud_pack_v1/scripts
    python generate_phase5_fc_cloud_profit_target_v1_3.py
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from stake_guard_tools import apply_stake_guard

BASE_CONFIG = Path("user_data/config_btc_futures_calm.json")

MANIFEST_OUT = Path("user_data/phase5_fc_cloud_profit_target_v1_3_manifest.json")

TARGET_WINDOWS = ["LW6M", "LW12M"]

WINDOW_META: dict[str, dict[str, Any]] = {
    "LW6M": {
        "window_id": "LW6M",
        "anchor_id": "LW6M-20250826",
        "anchor_timestamp_utc": "2025-08-26T00:00:00+00:00",
        "timerange": "20250826-20260226",
        "expected_side": "both",
        "description": "Last 6-month window ending 2026-02-26",
    },
    "LW12M": {
        "window_id": "LW12M",
        "anchor_id": "LW12M-20250226",
        "anchor_timestamp_utc": "2025-02-26T00:00:00+00:00",
        "timerange": "20250226-20260226",
        "expected_side": "both",
        "description": "Last 12-month window ending 2026-02-26",
    },
}

# Baseline A – must remain untouched across all variants.
CTRL_OVERRIDES: dict[str, Any] = {
    "trend_ema_fast": 9,
    "trend_ema_slow": 21,
    "rsi_long_entry_max": 60,
    "rsi_short_entry_min": 41,
    "exit_rsi_long_min": 55,
    "exit_rsi_short_max": 45,
    "exit_delay_bars": 0,
    "vol_shock_kill_enable": False,
    "negative_exit_cut_enable": False,
    "shadow_ml_enable": True,
    "shadow_ml_threshold": 0.55,
    "shadow_ml_weight_trend": 0.35,
    "shadow_ml_weight_adx": 0.25,
    "shadow_ml_weight_rsi": 0.25,
    "shadow_ml_weight_vol": 0.15,
    "diag_trace_enable": True,
    "diag_sample_limit": 200,
}

# Bounded variants targeting monthly_profit_norm_pct in [2.0, 2.5].
# Each variant overrides only specific parameters from CTRL.
VARIANT_DELTAS: dict[str, dict[str, Any]] = {
    # Tighter RSI entry + earlier exit to lock profits quickly.
    "PROFIT_TARGET_A": {
        "rsi_long_entry_max": 55,
        "rsi_short_entry_min": 45,
        "exit_rsi_long_min": 60,
        "exit_rsi_short_max": 40,
        "exit_delay_bars": 1,
        "negative_exit_cut_enable": True,
    },
    # Moderate adjustment: balanced entry/exit with vol-shock guard.
    "PROFIT_TARGET_B": {
        "rsi_long_entry_max": 58,
        "rsi_short_entry_min": 43,
        "exit_rsi_long_min": 58,
        "exit_rsi_short_max": 42,
        "exit_delay_bars": 2,
        "vol_shock_kill_enable": True,
        "negative_exit_cut_enable": True,
    },
    # Wider entry window to capture more momentum swings.
    "PROFIT_TARGET_C": {
        "rsi_long_entry_max": 63,
        "rsi_short_entry_min": 38,
        "exit_rsi_long_min": 62,
        "exit_rsi_short_max": 38,
        "exit_delay_bars": 0,
        "vol_shock_kill_enable": True,
        "negative_exit_cut_enable": False,
    },
}

SHADOW_ML_OVERRIDES: dict[str, Any] = {
    "shadow_ml_enable": True,
    "shadow_ml_threshold": 0.55,
    "shadow_ml_weight_trend": 0.35,
    "shadow_ml_weight_adx": 0.25,
    "shadow_ml_weight_rsi": 0.25,
    "shadow_ml_weight_vol": 0.15,
    "diag_trace_enable": True,
    "diag_sample_limit": 200,
}


def deep_copy(obj: Any) -> Any:
    return json.loads(json.dumps(obj))


def apply_override(cfg: dict[str, Any], key: str, value: Any) -> None:
    if key in {"entry_pricing", "exit_pricing", "order_types", "unfilledtimeout"}:
        cfg[key] = deep_copy(value)
    else:
        cfg[key] = value

    if not isinstance(cfg.get("strategy_parameters"), dict):
        cfg["strategy_parameters"] = {}

    passthrough = {
        "entry_pricing", "exit_pricing", "order_types", "unfilledtimeout",
        "bot_name", "db_url", "stake_amount", "timeframe", "stoploss", "trailing_stop",
    }
    if key not in passthrough:
        cfg["strategy_parameters"][key] = value


def normalized_keys(overrides: dict[str, Any]) -> dict[str, Any]:
    """Return the cloud_pack_v1 normalized key subset for the manifest top-level."""
    return {
        "trend_ema_fast": overrides.get("trend_ema_fast"),
        "trend_ema_slow": overrides.get("trend_ema_slow"),
        "rsi_long_entry_max": overrides.get("rsi_long_entry_max"),
        "rsi_short_entry_min": overrides.get("rsi_short_entry_min"),
        "exit_rsi_long_min": overrides.get("exit_rsi_long_min"),
        "exit_rsi_short_max": overrides.get("exit_rsi_short_max"),
        "exit_delay_bars": overrides.get("exit_delay_bars"),
        "vol_shock_kill_enable": overrides.get("vol_shock_kill_enable"),
        "negative_exit_cut_enable": overrides.get("negative_exit_cut_enable"),
        "shadow_ml_enable": overrides.get("shadow_ml_enable"),
    }


def main() -> None:
    if not BASE_CONFIG.exists():
        raise FileNotFoundError(f"Base config not found: {BASE_CONFIG}")

    base_cfg = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))

    variants_for_run: list[tuple[str, bool, dict[str, Any]]] = [
        ("CTRL", True, deep_copy(CTRL_OVERRIDES)),
    ]
    for label, deltas in VARIANT_DELTAS.items():
        variant_overrides = deep_copy(CTRL_OVERRIDES)
        variant_overrides.update(deltas)
        for key, value in SHADOW_ML_OVERRIDES.items():
            variant_overrides[key] = value
        variants_for_run.append((label, False, variant_overrides))

    experiments: list[dict[str, Any]] = []
    for win in TARGET_WINDOWS:
        meta = WINDOW_META[win]
        for variant_label, is_ctrl, variant_overrides in variants_for_run:
            overrides = deep_copy(variant_overrides)

            guard = apply_stake_guard(overrides, min_stake_amount=100.0, pair="BTC/USDT:USDT")
            overrides = guard["overrides"]

            cfg = deep_copy(base_cfg)
            cfg["strategy"] = "BTCFuturesCalmLS"
            for key, value in overrides.items():
                apply_override(cfg, key, value)

            run = f"FC-PROFIT-TARGET-V1_3-{win}-{variant_label}"
            cfg_name = f"config_{run}.json"
            Path("user_data", cfg_name).write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            experiments.append(
                {
                    "run": run,
                    "strategy": "BTCFuturesCalmLS",
                    "type": "PH5-FC-PROFIT-TARGET-V1_3",
                    "timerange": meta["timerange"],
                    "window_id": win,
                    "anchor_id": meta["anchor_id"],
                    "anchor_timestamp_utc": meta["anchor_timestamp_utc"],
                    "expected_side": meta["expected_side"],
                    "is_control": is_ctrl,
                    "variant": variant_label,
                    "config": cfg_name,
                    "overrides": overrides,
                    **normalized_keys(overrides),
                    "stake_amount_numeric": guard["stake_amount_numeric"],
                    "stake_guard_applied": guard["stake_guard_applied"],
                    "stake_guard_min_stake_amount": guard["stake_guard_min_stake_amount"],
                    "estimated_min_notional": guard["estimated_min_notional"],
                    "estimated_min_amount": guard["estimated_min_amount"],
                    "stake_to_notional_ratio": guard["stake_to_notional_ratio"],
                    "exchange_meta_source": guard["exchange_meta_source"],
                }
            )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": "V1.3 profit-target study: find BTCFuturesCalmLS variants "
                   "with monthly_profit_norm_pct in [2.0, 2.5] on LW6M + LW12M",
        "execution_mode": "lean",
        "stage": "PROFIT_TARGET_V1_3",
        "planning_gate": {
            "objective": "Monthly profit 2.0\u20132.5% on both windows, "
                         "PF>=1.10, DD<=3.0, tail_worsening_count_guard==0",
            "acceptance": "PASS only if >=2 non-CTRL variants satisfy all hard constraints "
                          "on BOTH LW6M and LW12M",
            "shadow_ml_mode": "telemetry-only; no entry/exit decision impact",
            "required_artifacts": {
                "manifest": "user_data/phase5_fc_cloud_profit_target_v1_3_manifest.json",
                "execution": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_execution_results.json",
                "summary": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_summary.json",
                "table": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_table.csv",
                "failure_modes_top3": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_failure_modes_top3.json",
                "shadow_summary": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_shadow_ml_summary.json",
                "validation_report": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_validation_report.json",
                "decision_summary": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_decision_summary.json",
                "sot_update_patch": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_sot_update_patch.json",
            },
            "hard_constraints": {
                "pf_min": 1.10,
                "max_drawdown_pct": 3.0,
                "tail_worsening_count_guard": 0,
                "economic_metrics_valid_ratio": 1.0,
                "monthly_profit_norm_pct_min": 2.0,
                "monthly_profit_norm_pct_max": 2.5,
                "min_passing_non_ctrl_variants": 2,
            },
            "failure_states": {
                "no_real_backtest_data": "NON_AUDITABLE_METRICS_SOURCE",
                "valid_ratio_below_1": "INSUFFICIENT_VALID_ECONOMICS",
                "pf_below_floor": "PF_CONSTRAINT_FAIL",
                "drawdown_exceeded": "DRAWDOWN_CONSTRAINT_FAIL",
                "profit_out_of_range": "PROFIT_TARGET_MISS",
                "fewer_than_2_variants_pass": "INSUFFICIENT_PASSING_VARIANTS",
            },
        },
        "matrix": {
            "windows": len(TARGET_WINDOWS),
            "variants_per_window": len(variants_for_run),
            "total_runs": len(experiments),
            "broad_sweep": False,
            "target_windows": TARGET_WINDOWS,
        },
        "total": len(experiments),
        "experiments": experiments,
    }

    MANIFEST_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated windows: {len(TARGET_WINDOWS)}")
    print(f"Generated runs: {len(experiments)}")
    print(f"Manifest: {MANIFEST_OUT}")


if __name__ == "__main__":
    main()
