import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stake_guard_tools import apply_stake_guard

BASE_CONFIG = Path("user_data/config_btc_futures_calm.json")
MICROV2_MANIFEST = Path("user_data/phase5_fc_anchor_microv2_manifest.json")
RECONCILE = Path("user_data/backtest_results/phase5_fc_anchor_tail_reconcile_v1.json")

MANIFEST_OUT = Path("user_data/phase5_fc_anchor_tail2x3_v6_manifest.json")
TARGET_WINDOWS = ["W002", "W012"]
BEST_SOURCE_VARIANT = "EXIT_DELAY_2_RSI_NEAR_ONLY"

SHADOW_ML_OVERRIDES = {
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


def main() -> None:
    reconcile = json.loads(RECONCILE.read_text(encoding="utf-8"))
    if str(reconcile.get("effective_state", "")) != "tail2x3_v3":
        raise RuntimeError("effective_state is not tail2x3_v3 - reconcile required before V6")

    microv2_manifest = json.loads(MICROV2_MANIFEST.read_text(encoding="utf-8"))
    base_cfg = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))

    ctrl_overrides = deep_copy(microv2_manifest.get("variant_contract", {}).get("CTRL", {}))
    best_overrides = deep_copy(microv2_manifest.get("variant_contract", {}).get(BEST_SOURCE_VARIANT, {}))
    if not best_overrides:
        raise RuntimeError(f"Missing source variant contract: {BEST_SOURCE_VARIANT}")

    window_meta: dict[str, dict[str, Any]] = {}
    for exp in microv2_manifest.get("experiments", []):
        if str(exp.get("variant", "")) != "CTRL":
            continue
        win = str(exp.get("window_id", ""))
        window_meta[win] = {
            "window_id": win,
            "anchor_id": exp.get("anchor_id"),
            "anchor_timestamp_utc": exp.get("anchor_timestamp_utc"),
            "timerange": exp.get("timerange"),
            "expected_side": exp.get("expected_side"),
            "miss_reason": exp.get("miss_reason"),
        }

    missing = [win for win in TARGET_WINDOWS if win not in window_meta]
    if missing:
        raise RuntimeError(f"Target windows missing in source manifest: {missing}")

    experiments: list[dict[str, Any]] = []
    for win in TARGET_WINDOWS:
        meta = window_meta[win]
        variants_for_run: list[tuple[str, dict[str, Any]]] = [
            ("CTRL", ctrl_overrides),
            ("VOL_SHOCK_FORCE_ON", best_overrides),
            ("VOL_SHOCK_SOFT", best_overrides),
        ]

        for variant_label, variant_overrides in variants_for_run:
            run = f"FC-ANCHOR-TAIL2x3V6-{win}-{variant_label}"
            overrides = deep_copy(variant_overrides)

            if variant_label == "VOL_SHOCK_FORCE_ON":
                overrides["vol_shock_kill_enable"] = True
                overrides["vol_shock_atrp_mult"] = 0.1
                overrides["vol_shock_adx_min"] = 0
                overrides["vol_shock_lookback_bars"] = 1
                overrides["vol_shock_loss_floor_pct"] = 0.0
                overrides["negative_exit_cut_enable"] = False

            if variant_label == "VOL_SHOCK_SOFT":
                overrides["vol_shock_kill_enable"] = True
                overrides["vol_shock_atrp_mult"] = 1.0
                overrides["vol_shock_adx_min"] = 10
                overrides["vol_shock_lookback_bars"] = 5
                overrides["vol_shock_loss_floor_pct"] = -0.005
                overrides["negative_exit_cut_enable"] = False

            for key, value in SHADOW_ML_OVERRIDES.items():
                overrides[key] = value

            guard = apply_stake_guard(overrides, min_stake_amount=100.0, pair="BTC/USDT:USDT")
            overrides = guard["overrides"]

            cfg = deep_copy(base_cfg)
            cfg["strategy"] = "BTCFuturesCalmLS"
            for key, value in overrides.items():
                apply_override(cfg, key, value)

            cfg_name = f"config_{run}.json"
            Path("user_data", cfg_name).write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

            experiments.append(
                {
                    "run": run,
                    "strategy": "BTCFuturesCalmLS",
                    "type": "PH5-FC-ANCHOR-TAIL2x3V6",
                    "timerange": meta["timerange"],
                    "window_id": win,
                    "anchor_id": meta["anchor_id"],
                    "anchor_timestamp_utc": meta["anchor_timestamp_utc"],
                    "expected_side": meta["expected_side"],
                    "miss_reason": meta["miss_reason"],
                    "is_control": variant_label == "CTRL",
                    "variant": variant_label,
                    "best_variant_source": BEST_SOURCE_VARIANT,
                    "targeted_tail_window": True,
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
        "purpose": "V6 minimal tail-reduction test with unchanged activation contract on W002/W012",
        "execution_mode": "lean",
        "stage": "ANCHOR_TAIL2x3_V6",
        "planning_gate": {
            "objective": "Keep activation contract as in V5 and optimize SOFT variant only for tail reduction.",
            "acceptance": "vol_shock_kill_activations_total>0 AND FORCE_ON activations>0 AND SOFT delta_tail_vs_ctrl<=0 in W002/W012 AND SOFT delta_profit_vs_ctrl>=-0.10 in W002/W012",
            "shadow_ml_mode": "telemetry-only; coverage denominator uses emitted entries",
            "required_artifacts": {
                "manifest": "user_data/phase5_fc_anchor_tail2x3_v6_manifest.json",
                "execution": "user_data/backtest_results/phase5_fc_anchor_tail2x3_v6_execution_results.json",
                "summary": "user_data/backtest_results/phase5_fc_anchor_tail2x3_v6_summary.json",
                "table": "user_data/backtest_results/phase5_fc_anchor_tail2x3_v6_table.csv",
                "failure_modes_top3": "user_data/backtest_results/phase5_fc_anchor_tail2x3_v6_failure_modes_top3.json",
                "shadow_summary": "user_data/backtest_results/phase5_fc_anchor_tail2x3_v6_shadow_ml_summary.json"
            },
            "failure_states": {
                "wiring_or_context": "FORCE_ON activations == 0",
                "tail_impact_fail": "SOFT delta_tail_vs_ctrl > 0 in any target window",
                "profit_floor_fail": "SOFT delta_profit_vs_ctrl < -0.10 in any target window"
            }
        },
        "matrix": {
            "windows": len(TARGET_WINDOWS),
            "variants_per_window": 3,
            "total_runs": len(experiments),
            "broad_sweep": False,
            "targeted_tail_windows": TARGET_WINDOWS
        },
        "best_variant_source": BEST_SOURCE_VARIANT,
        "total": len(experiments),
        "experiments": experiments
    }

    MANIFEST_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated windows: {len(TARGET_WINDOWS)}")
    print(f"Generated runs: {len(experiments)}")
    print(f"Best source variant: {BEST_SOURCE_VARIANT}")
    print(f"Manifest: {MANIFEST_OUT}")


if __name__ == "__main__":
    main()
