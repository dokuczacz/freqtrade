"""
generate_phase5_fc_cloud_profit_target_v1_4.py

Generate run matrix for phase5 FC cloud profit target v1.4.
Goal: Find BTCFuturesCalmLS variants with monthly_profit_norm_pct in [2.0, 2.5]
using REAL Freqtrade backtests (zip-primary sources only).

Hard constraints:
  - PF >= 1.10
  - max_drawdown_pct <= 3.0
  - tail_worsening_count_guard == 0
  - economic_metrics_valid_ratio == 1.0
  - pass on BOTH windows: LW6M and LW12M
  - >=2 non-control variants pass all constraints

Windows:
  - LW6M: last 6 months rolling window
  - LW12M: last 12 months rolling window

Run matrix:
  - CTRL_A (baseline, untouched)
  - VARIANT_A: EXIT_DELAY adjusted (decouple_exit_cooldown_bars=1)
  - VARIANT_B: RSI threshold tightened (rsi_entry_long_max=58, rsi_entry_short_min=43)
  - VARIANT_C: EXIT_DELAY + RSI combined
  - 2 windows x 4 variants = 8 runs total
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from stake_guard_tools import apply_stake_guard

MANIFEST_OUT = Path("user_data/phase5_fc_cloud_profit_target_v1_4_manifest.json")
TARGET_WINDOWS = ["LW6M", "LW12M"]

# Window time ranges (6M and 12M lookback from 2026-02-26)
WINDOW_TIMERANGES = {
    "LW6M": "20250826-20260226",
    "LW12M": "20250226-20260226",
}

CTRL_OVERRIDES: dict[str, Any] = {
    "rsi_entry_long_max": 60,
    "rsi_entry_short_min": 41,
    "rsi_near_case_only_enable": False,
    "rsi_near_case_max_distance": 2,
    "decouple_exit_cooldown_bars": 0,
    "decouple_asymmetry_long": False,
    "adx_min": 8,
    "trend_gate_adx_min": 8,
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

VARIANT_OVERRIDES: dict[str, dict[str, Any]] = {
    "VARIANT_A": {
        **CTRL_OVERRIDES,
        "decouple_exit_cooldown_bars": 1,
        "decouple_asymmetry_long": True,
    },
    "VARIANT_B": {
        **CTRL_OVERRIDES,
        "rsi_entry_long_max": 58,
        "rsi_entry_short_min": 43,
        "rsi_near_case_only_enable": True,
        "rsi_near_case_max_distance": 2,
    },
    "VARIANT_C": {
        **CTRL_OVERRIDES,
        "decouple_exit_cooldown_bars": 1,
        "decouple_asymmetry_long": True,
        "rsi_entry_long_max": 58,
        "rsi_entry_short_min": 43,
        "rsi_near_case_only_enable": True,
        "rsi_near_case_max_distance": 2,
    },
}


def deep_copy(obj: Any) -> Any:
    return json.loads(json.dumps(obj))


def main() -> None:
    experiments: list[dict[str, Any]] = []

    for win in TARGET_WINDOWS:
        timerange = WINDOW_TIMERANGES[win]
        variants_for_run: list[tuple[str, dict[str, Any]]] = [
            ("CTRL_A", CTRL_OVERRIDES),
            ("VARIANT_A", VARIANT_OVERRIDES["VARIANT_A"]),
            ("VARIANT_B", VARIANT_OVERRIDES["VARIANT_B"]),
            ("VARIANT_C", VARIANT_OVERRIDES["VARIANT_C"]),
        ]

        for variant_label, variant_overrides in variants_for_run:
            run = f"FC-CLOUD-PROFIT-V1_4-{win}-{variant_label}"
            overrides = deep_copy(variant_overrides)
            guard = apply_stake_guard(overrides, min_stake_amount=100.0, pair="BTC/USDT:USDT")
            overrides = guard["overrides"]

            cfg_name = f"config_{run}.json"

            experiments.append(
                {
                    "run": run,
                    "strategy": "BTCFuturesCalmLS",
                    "type": "PH5-FC-CLOUD-PROFIT-V1_4",
                    "timerange": timerange,
                    "window_id": win,
                    "is_control": variant_label == "CTRL_A",
                    "variant": variant_label,
                    "config": cfg_name,
                    "overrides": overrides,
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
        "purpose": "V1.4 cloud profit target: find BTCFuturesCalmLS variants with monthly_profit_norm_pct in [2.0, 2.5]",
        "execution_mode": "real_backtest_only",
        "stage": "CLOUD_PROFIT_TARGET_V1_4",
        "profit_target_range_pct": [2.0, 2.5],
        "hard_constraints": {
            "pf_min": 1.10,
            "max_drawdown_pct_max": 3.0,
            "tail_worsening_count_guard": 0,
            "economic_metrics_valid_ratio": 1.0,
            "required_windows": TARGET_WINDOWS,
            "min_passing_non_control_variants": 2,
        },
        "planning_gate": {
            "objective": "Monthly profit normalization in [2.0, 2.5] pct with PF>=1.10 and drawdown<=3.0",
            "acceptance": "PASS only if >=2 non-control variants meet all hard constraints on BOTH LW6M and LW12M",
            "shadow_ml_mode": "telemetry-only; NO entry/exit decision impact",
            "metrics_source_policy": "zip_primary_only",
            "required_artifacts": {
                "manifest": "user_data/phase5_fc_cloud_profit_target_v1_4_manifest.json",
                "execution": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_4_execution_results.json",
                "summary": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_4_summary.json",
                "decision_summary": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_4_decision_summary.json",
                "table": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_4_table.csv",
                "failure_modes_top3": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_4_failure_modes_top3.json",
                "shadow_ml_summary": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_4_shadow_ml_summary.json",
                "validation_report": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_4_validation_report.json",
                "sot_update_patch": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_4_sot_update_patch.json",
            },
            "failure_states": {
                "blocked_no_runtime": "BLOCKED_NO_RUNTIME_EXECUTION",
                "non_auditable_metrics": "NON_AUDITABLE_METRICS_SOURCE",
                "insufficient_economics": "INSUFFICIENT_VALID_ECONOMICS",
                "profit_target_miss": "PROFIT_TARGET_NOT_MET",
                "constraint_fail": "HARD_CONSTRAINT_VIOLATION",
            },
        },
        "matrix": {
            "windows": len(TARGET_WINDOWS),
            "variants_per_window": 4,
            "total_runs": len(experiments),
            "broad_sweep": False,
            "targeted_profit_windows": TARGET_WINDOWS,
        },
        "ctrl_variant": "CTRL_A",
        "compare_variants": ["VARIANT_A", "VARIANT_B", "VARIANT_C"],
        "total": len(experiments),
        "experiments": experiments,
    }

    MANIFEST_OUT.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated windows: {len(TARGET_WINDOWS)}")
    print(f"Generated runs: {len(experiments)}")
    print(f"Manifest: {MANIFEST_OUT}")


if __name__ == "__main__":
    main()
