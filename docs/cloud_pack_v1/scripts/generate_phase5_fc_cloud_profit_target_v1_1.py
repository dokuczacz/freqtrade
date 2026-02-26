"""Generate phase5_fc_cloud_profit_target_v1_1 experiment manifest.

ROOT INPUT: docs/cloud_pack_v1/index.json (GitHub-pack mode)
TARGET:     Find BTCFuturesCalmLS variants with monthly_profit_norm_pct
            in [2.0, 2.5] on LW6M and LW12M.

OPTIMIZATION POLICY: bounded only, start from v7 CTRL baseline.
GOVERNANCE: keep baseline A untouched; shadow ML is telemetry-only.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stake_guard_tools import apply_stake_guard

BASE_CONFIG = Path("user_data/config_btc_futures_calm.json")
PACK_INDEX = Path("docs/cloud_pack_v1/index.json")
V7_SUMMARY = Path("docs/cloud_pack_v1/artifacts/phase5_fc_anchor_tail2x3_v7_summary.json")

MANIFEST_OUT = Path("user_data/phase5_fc_cloud_profit_target_v1_1_manifest.json")
TARGET_WINDOWS = ["LW6M", "LW12M"]
STAGE = "CLOUD_PROFIT_TARGET_V1_1"

# Hard constraints (from task spec)
HARD_PROFIT_TARGET_LOW = 2.0
HARD_PROFIT_TARGET_HIGH = 2.5
HARD_PF_MIN = 1.10
HARD_DRAWDOWN_MAX_PCT = 3.0

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

# Baseline A (CTRL) – taken from v7 CTRL, kept untouched
CTRL_OVERRIDES: dict[str, Any] = {
    "vol_shock_kill_enable": False,
    "negative_exit_cut_enable": False,
    "trend_ema_fast": 12,
    "trend_ema_slow": 26,
    "rsi_period": 14,
    "rsi_long_entry_max": 55.0,
    "rsi_short_entry_min": 45.0,
    "exit_rsi_long_min": 70.0,
    "exit_rsi_short_max": 30.0,
    "exit_delay_bars": 2,
}

# Bounded variants – single-axis perturbations from CTRL baseline
# Targeting monthly_profit_norm_pct in [2.0, 2.5]
PROFIT_VARIANTS: list[tuple[str, dict[str, Any]]] = [
    (
        "PROFIT_TUNE_A",
        {
            # Widen RSI entry bands slightly to capture more trend momentum
            "rsi_long_entry_max": 60.0,
            "rsi_short_entry_min": 40.0,
            # Tighten exit RSI to let winners run longer
            "exit_rsi_long_min": 75.0,
            "exit_rsi_short_max": 25.0,
            "vol_shock_kill_enable": False,
            "negative_exit_cut_enable": False,
        },
    ),
    (
        "PROFIT_TUNE_B",
        {
            # Slightly faster EMA crossover + tighter entry RSI for precision
            "trend_ema_fast": 8,
            "trend_ema_slow": 21,
            "rsi_long_entry_max": 52.0,
            "rsi_short_entry_min": 48.0,
            "exit_rsi_long_min": 72.0,
            "exit_rsi_short_max": 28.0,
            "vol_shock_kill_enable": False,
            "negative_exit_cut_enable": False,
        },
    ),
    (
        "PROFIT_TUNE_C",
        {
            # Balanced RSI + moderate EMA + soft vol guard disabled
            "trend_ema_fast": 10,
            "trend_ema_slow": 24,
            "rsi_long_entry_max": 58.0,
            "rsi_short_entry_min": 42.0,
            "exit_rsi_long_min": 73.0,
            "exit_rsi_short_max": 27.0,
            "vol_shock_kill_enable": False,
            "negative_exit_cut_enable": True,
        },
    ),
]


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
        "entry_pricing",
        "exit_pricing",
        "order_types",
        "unfilledtimeout",
        "bot_name",
        "db_url",
        "stake_amount",
        "timeframe",
        "stoploss",
        "trailing_stop",
    }
    if key not in passthrough:
        cfg["strategy_parameters"][key] = value


def load_window_meta() -> dict[str, dict[str, Any]]:
    """Return minimal window metadata for LW6M and LW12M."""
    return {
        "LW6M": {
            "window_id": "LW6M",
            "anchor_id": "ANCH-LW6M",
            "anchor_timestamp_utc": "2026-02-26T00:00:00+00:00",
            "timerange": "20250901-20260226",
            "expected_side": "both",
            "miss_reason": None,
        },
        "LW12M": {
            "window_id": "LW12M",
            "anchor_id": "ANCH-LW12M",
            "anchor_timestamp_utc": "2026-02-26T00:00:00+00:00",
            "timerange": "20250301-20260226",
            "expected_side": "both",
            "miss_reason": None,
        },
    }


def main() -> None:
    base_cfg: dict[str, Any]
    if BASE_CONFIG.exists():
        base_cfg = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    else:
        base_cfg = {
            "strategy": "BTCFuturesCalmLS",
            "timeframe": "15m",
            "stake_currency": "USDT",
            "stake_amount": 100.0,
            "stoploss": -0.05,
            "trailing_stop": False,
        }

    window_meta = load_window_meta()

    experiments: list[dict[str, Any]] = []

    for win in TARGET_WINDOWS:
        meta = window_meta[win]

        variants_for_run: list[tuple[str, dict[str, Any]]] = [
            ("CTRL", deep_copy(CTRL_OVERRIDES)),
        ] + [(label, deep_copy(ov)) for label, ov in PROFIT_VARIANTS]

        for variant_label, overrides in variants_for_run:
            run = f"FC-PROFIT-V1_1-{win}-{variant_label}"

            # Shadow ML is telemetry-only
            for key, value in SHADOW_ML_OVERRIDES.items():
                overrides[key] = value

            guard = apply_stake_guard(overrides, min_stake_amount=100.0, pair="BTC/USDT:USDT")
            overrides = guard["overrides"]

            cfg = deep_copy(base_cfg)
            cfg["strategy"] = "BTCFuturesCalmLS"
            for key, value in overrides.items():
                apply_override(cfg, key, value)

            cfg_name = f"config_{run}.json"
            Path("user_data", cfg_name).write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            experiments.append(
                {
                    "run": run,
                    "strategy": "BTCFuturesCalmLS",
                    "type": f"PH5-FC-PROFIT-V1_1",
                    "timerange": meta["timerange"],
                    "window_id": win,
                    "anchor_id": meta["anchor_id"],
                    "anchor_timestamp_utc": meta["anchor_timestamp_utc"],
                    "expected_side": meta["expected_side"],
                    "miss_reason": meta["miss_reason"],
                    "is_control": variant_label == "CTRL",
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

    variants_per_window = 1 + len(PROFIT_VARIANTS)  # CTRL + variants
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": (
            "V1.1 bounded profit-target search: find BTCFuturesCalmLS variants "
            f"with monthly_profit_norm_pct in [{HARD_PROFIT_TARGET_LOW}, {HARD_PROFIT_TARGET_HIGH}] "
            "on LW6M and LW12M."
        ),
        "execution_mode": "lean",
        "stage": STAGE,
        "optimization_policy": "bounded_only",
        "baseline_a_untouched": True,
        "shadow_ml_mode": "telemetry-only",
        "planning_gate": {
            "objective": (
                f"Find at least 2 non-control variants with monthly_profit_norm_pct in "
                f"[{HARD_PROFIT_TARGET_LOW}, {HARD_PROFIT_TARGET_HIGH}] on LW6M+LW12M."
            ),
            "hard_constraints": {
                "pf_min": HARD_PF_MIN,
                "max_drawdown_pct": HARD_DRAWDOWN_MAX_PCT,
                "tail_worsening_count_guard": 0,
                "economic_metrics_valid_ratio": 1.0,
                "window_coverage": "LW6M and LW12M both required",
                "non_control_passes_min": 2,
            },
            "acceptance": (
                f"PASS only if ALL hard constraints pass: PF>={HARD_PF_MIN}, "
                f"max_drawdown_pct<={HARD_DRAWDOWN_MAX_PCT}, tail_worsening==0, "
                f"economic_valid_ratio==1.0, both LW6M+LW12M pass, >=2 non-ctrl variants pass."
            ),
            "shadow_ml_mode": "telemetry-only; no entry/exit decision impact",
            "required_artifacts": {
                "manifest": "user_data/phase5_fc_cloud_profit_target_v1_1_manifest.json",
                "execution": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_execution_results.json",
                "summary": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_summary.json",
                "decision_summary": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_decision_summary.json",
                "table": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_table.csv",
                "failure_modes_top3": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_failure_modes_top3.json",
                "shadow_summary": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_shadow_ml_summary.json",
                "sot_update_patch": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_sot_update_patch.json",
                "validation_report": "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_validation_report.json",
            },
        },
        "matrix": {
            "windows": len(TARGET_WINDOWS),
            "variants_per_window": variants_per_window,
            "total_runs": len(experiments),
            "broad_sweep": False,
            "target_windows": TARGET_WINDOWS,
        },
        "primary_kpi": "delta_profit_vs_ctrl",
        "kpi_priority": [
            "delta_profit_vs_ctrl",
            "delta_tail_vs_ctrl",
            "delta_pf_vs_ctrl",
            "activation_counts",
        ],
        "total": len(experiments),
        "experiments": experiments,
    }

    MANIFEST_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Stage: {STAGE}")
    print(f"Generated windows: {len(TARGET_WINDOWS)}")
    print(f"Generated runs: {len(experiments)}")
    print(f"Manifest: {MANIFEST_OUT}")


if __name__ == "__main__":
    main()
