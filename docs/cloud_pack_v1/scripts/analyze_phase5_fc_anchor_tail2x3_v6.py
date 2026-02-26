import csv
import json
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST = Path("user_data/phase5_fc_anchor_tail2x3_v6_manifest.json")
EXECUTION = Path("user_data/backtest_results/phase5_fc_anchor_tail2x3_v6_execution_results.json")
OUT_SUMMARY = Path("user_data/backtest_results/phase5_fc_anchor_tail2x3_v6_summary.json")
OUT_TABLE = Path("user_data/backtest_results/phase5_fc_anchor_tail2x3_v6_table.csv")
OUT_FAILURE_MODES = Path("user_data/backtest_results/phase5_fc_anchor_tail2x3_v6_failure_modes_top3.json")
RESULTS_ROOT = Path("user_data/backtest_results/phase5_fc_anchor_tail2x3_v6")
TARGET_WINDOWS = ["W002", "W012"]
REQUIRED_ECONOMIC_VALID_RATIO = 1.0
MAX_SOFT_PROFIT_DEGRADATION = -0.10


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def get_metric(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    if key in row:
        return to_float(row.get(key), default)
    nested = row.get("fc_preconfirm_counts")
    if isinstance(nested, dict) and key in nested:
        return to_float(nested.get(key), default)
    return default


def must_hit_rate_proxy(row: dict[str, Any]) -> float:
    entry_attempt = get_metric(row, "entry_attempt_intent_count", 0.0)
    blocked = get_metric(row, "entry_long_with_exit_long_count", 0.0) + get_metric(
        row, "entry_short_with_exit_short_count", 0.0
    )
    if entry_attempt <= 0:
        return 0.0
    raw = 100.0 * (1.0 - (blocked / entry_attempt))
    return max(0.0, min(100.0, raw))


def extract_metrics_from_zip(run_row: dict[str, Any]) -> dict[str, Any]:
    run = str(run_row.get("run", ""))
    zip_name = str(run_row.get("backtest_file", ""))
    if not run or not zip_name:
        return {
            "economic_metrics_valid": False,
            "profit_total_pct": 0.0,
            "pf": 0.0,
            "tail": 0.0,
            "vol_shock_kill_activations": 0,
        }

    zip_path = RESULTS_ROOT / run / zip_name
    if not zip_path.exists():
        return {
            "economic_metrics_valid": False,
            "profit_total_pct": 0.0,
            "pf": 0.0,
            "tail": 0.0,
            "vol_shock_kill_activations": 0,
        }

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_handle:
            json_members = [name for name in zip_handle.namelist() if name.endswith(".json") and "_config" not in name]
            if not json_members:
                return {
                    "economic_metrics_valid": False,
                    "profit_total_pct": 0.0,
                    "pf": 0.0,
                    "tail": 0.0,
                    "vol_shock_kill_activations": 0,
                }

            payload = json.loads(zip_handle.read(json_members[0]))
            comparison = payload.get("strategy_comparison")
            strategy_payload = payload.get("strategy")
            if not isinstance(comparison, list) or not comparison:
                return {
                    "economic_metrics_valid": False,
                    "profit_total_pct": 0.0,
                    "pf": 0.0,
                    "tail": 0.0,
                    "vol_shock_kill_activations": 0,
                }

            strategy_name = str(run_row.get("strategy", ""))
            metric_row = None
            if strategy_name:
                for item in comparison:
                    if str(item.get("key", "")) == strategy_name:
                        metric_row = item
                        break
            if metric_row is None:
                metric_row = comparison[0]

            strategy_stats = None
            if isinstance(strategy_payload, dict):
                if strategy_name and strategy_name in strategy_payload and isinstance(strategy_payload[strategy_name], dict):
                    strategy_stats = strategy_payload[strategy_name]
                elif strategy_payload:
                    first_val = next(iter(strategy_payload.values()))
                    if isinstance(first_val, dict):
                        strategy_stats = first_val

            vol_shock_count = 0
            if isinstance(strategy_stats, dict):
                for item in strategy_stats.get("exit_reason_summary", []) or []:
                    if str(item.get("key", "")) == "volatility_shock_kill":
                        vol_shock_count = int(item.get("trades", 0) or 0)
                        break

            return {
                "economic_metrics_valid": True,
                "profit_total_pct": to_float(metric_row.get("profit_total_pct"), 0.0),
                "pf": to_float(metric_row.get("profit_factor"), 0.0),
                "tail": to_float(metric_row.get("max_drawdown_account"), 0.0),
                "vol_shock_kill_activations": vol_shock_count,
            }
    except Exception:
        return {
            "economic_metrics_valid": False,
            "profit_total_pct": 0.0,
            "pf": 0.0,
            "tail": 0.0,
            "vol_shock_kill_activations": 0,
        }


def main() -> None:
    manifest = read_json(MANIFEST)
    execution = read_json(EXECUTION)

    meta_by_run = {item["run"]: item for item in manifest.get("experiments", [])}
    eval_rows: list[dict[str, Any]] = []
    for row in execution.get("results", []):
        run = str(row.get("run", ""))
        meta = meta_by_run.get(run)
        if not meta:
            continue

        zip_metrics = extract_metrics_from_zip(row)
        eval_rows.append(
            {
                "run": run,
                "window_id": str(meta.get("window_id", "")),
                "anchor_id": str(meta.get("anchor_id", "")),
                "variant": str(meta.get("variant", "")),
                "must_hit_rate_proxy": must_hit_rate_proxy(row),
                "economic_metrics_valid": bool(zip_metrics.get("economic_metrics_valid", False)),
                "profit_total_pct": to_float(zip_metrics.get("profit_total_pct"), 0.0),
                "pf": to_float(zip_metrics.get("pf"), 0.0),
                "tail": to_float(zip_metrics.get("tail"), 0.0),
                "vol_shock_kill_activations": int(zip_metrics.get("vol_shock_kill_activations", 0)),
            }
        )

    per_window: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in eval_rows:
        per_window[row["window_id"]][row["variant"]] = row

    compare_variants = ["VOL_SHOCK_FORCE_ON", "VOL_SHOCK_SOFT"]
    acc = {
        variant: {
            "n": 0,
            "n_valid": 0,
            "tail_nonpositive": 0,
            "profit_floor_ok": 0,
            "sum_delta_pf": 0.0,
            "sum_delta_profit": 0.0,
            "sum_delta_tail": 0.0,
            "vol_shock_kill_activations": 0,
        }
        for variant in compare_variants
    }

    table_rows: list[dict[str, Any]] = []
    per_window_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for window_id in TARGET_WINDOWS:
        bucket = per_window.get(window_id, {})
        ctrl = bucket.get("CTRL")
        if not ctrl:
            continue

        for variant in compare_variants:
            cand = bucket.get(variant)
            if not cand:
                continue

            econ_valid = bool(ctrl["economic_metrics_valid"]) and bool(cand["economic_metrics_valid"])
            delta_must = cand["must_hit_rate_proxy"] - ctrl["must_hit_rate_proxy"]
            delta_profit = cand["profit_total_pct"] - ctrl["profit_total_pct"]
            delta_pf = cand["pf"] - ctrl["pf"]
            delta_tail = cand["tail"] - ctrl["tail"]

            tail_nonpositive = delta_tail <= 0.0 if econ_valid else False
            profit_floor_ok = delta_profit >= MAX_SOFT_PROFIT_DEGRADATION if econ_valid else False

            a = acc[variant]
            a["n"] += 1
            a["vol_shock_kill_activations"] += int(cand.get("vol_shock_kill_activations", 0))
            if econ_valid:
                a["n_valid"] += 1
                a["tail_nonpositive"] += 1 if tail_nonpositive else 0
                a["profit_floor_ok"] += 1 if profit_floor_ok else 0
                a["sum_delta_pf"] += delta_pf
                a["sum_delta_profit"] += delta_profit
                a["sum_delta_tail"] += delta_tail

            out_row = {
                "window_id": window_id,
                "anchor_id": cand["anchor_id"],
                "variant": variant,
                "economic_metrics_valid": econ_valid,
                "must_hit_rate_proxy": round(cand["must_hit_rate_proxy"], 4),
                "ctrl_must_hit_rate_proxy": round(ctrl["must_hit_rate_proxy"], 4),
                "delta_must_hit_rate_vs_ctrl": round(delta_must, 4),
                "profit_total_pct": round(cand["profit_total_pct"], 6),
                "ctrl_profit_total_pct": round(ctrl["profit_total_pct"], 6),
                "delta_profit_vs_ctrl": round(delta_profit, 6),
                "pf": round(cand["pf"], 6),
                "ctrl_pf": round(ctrl["pf"], 6),
                "delta_pf_vs_ctrl": round(delta_pf, 6),
                "tail": round(cand["tail"], 6),
                "ctrl_tail": round(ctrl["tail"], 6),
                "delta_tail_vs_ctrl": round(delta_tail, 6),
                "tail_nonpositive_vs_ctrl": tail_nonpositive,
                "profit_floor_ok": profit_floor_ok,
                "vol_shock_kill_activations": int(cand.get("vol_shock_kill_activations", 0)),
            }
            table_rows.append(out_row)
            per_window_rows[window_id].append(out_row)

    expected_comparisons = len(TARGET_WINDOWS) * len(compare_variants)
    valid_comparisons = len([row for row in table_rows if row["economic_metrics_valid"]])
    valid_ratio = (valid_comparisons / expected_comparisons) if expected_comparisons > 0 else 0.0

    force_on = acc["VOL_SHOCK_FORCE_ON"]
    soft = acc["VOL_SHOCK_SOFT"]
    force_on_activation_total = int(force_on.get("vol_shock_kill_activations", 0))
    soft_activation_total = int(soft.get("vol_shock_kill_activations", 0))
    vol_shock_activation_total = force_on_activation_total + soft_activation_total

    economic_valid_ratio_ok = valid_ratio >= REQUIRED_ECONOMIC_VALID_RATIO
    force_on_activation_ok = force_on_activation_total > 0
    activation_ok = vol_shock_activation_total > 0

    targeted_debug: dict[str, dict[str, Any]] = {}
    failure_modes_top3: dict[str, list[dict[str, Any]]] = {}
    soft_tail_nonpositive_all = True
    soft_profit_floor_all = True

    for window_id in TARGET_WINDOWS:
        rows = per_window_rows.get(window_id, [])
        by_variant = {str(row.get("variant", "")): row for row in rows}
        force_row = by_variant.get("VOL_SHOCK_FORCE_ON")
        soft_row = by_variant.get("VOL_SHOCK_SOFT")

        if soft_row:
            if to_float(soft_row.get("delta_tail_vs_ctrl", 0.0), 0.0) > 0.0:
                soft_tail_nonpositive_all = False
            if to_float(soft_row.get("delta_profit_vs_ctrl", 0.0), 0.0) < MAX_SOFT_PROFIT_DEGRADATION:
                soft_profit_floor_all = False
        else:
            soft_tail_nonpositive_all = False
            soft_profit_floor_all = False

        if force_row and soft_row:
            targeted_debug[window_id] = {
                "force_on": {
                    "delta_tail_vs_ctrl": to_float(force_row.get("delta_tail_vs_ctrl", 0.0), 0.0),
                    "delta_profit_vs_ctrl": to_float(force_row.get("delta_profit_vs_ctrl", 0.0), 0.0),
                    "delta_pf_vs_ctrl": to_float(force_row.get("delta_pf_vs_ctrl", 0.0), 0.0),
                    "vol_shock_kill_activations": int(force_row.get("vol_shock_kill_activations", 0)),
                },
                "soft": {
                    "delta_tail_vs_ctrl": to_float(soft_row.get("delta_tail_vs_ctrl", 0.0), 0.0),
                    "delta_profit_vs_ctrl": to_float(soft_row.get("delta_profit_vs_ctrl", 0.0), 0.0),
                    "delta_pf_vs_ctrl": to_float(soft_row.get("delta_pf_vs_ctrl", 0.0), 0.0),
                    "vol_shock_kill_activations": int(soft_row.get("vol_shock_kill_activations", 0)),
                },
                "soft_minus_force_on": {
                    "delta_tail_vs_ctrl": round(
                        to_float(soft_row.get("delta_tail_vs_ctrl", 0.0), 0.0)
                        - to_float(force_row.get("delta_tail_vs_ctrl", 0.0), 0.0),
                        6,
                    ),
                    "delta_profit_vs_ctrl": round(
                        to_float(soft_row.get("delta_profit_vs_ctrl", 0.0), 0.0)
                        - to_float(force_row.get("delta_profit_vs_ctrl", 0.0), 0.0),
                        6,
                    ),
                    "delta_pf_vs_ctrl": round(
                        to_float(soft_row.get("delta_pf_vs_ctrl", 0.0), 0.0)
                        - to_float(force_row.get("delta_pf_vs_ctrl", 0.0), 0.0),
                        6,
                    ),
                    "vol_shock_kill_activations": int(soft_row.get("vol_shock_kill_activations", 0))
                    - int(force_row.get("vol_shock_kill_activations", 0)),
                },
            }
        else:
            soft_tail_nonpositive_all = False
            soft_profit_floor_all = False

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            reasons: list[str] = []
            delta_tail = to_float(row.get("delta_tail_vs_ctrl", 0.0), 0.0)
            delta_profit = to_float(row.get("delta_profit_vs_ctrl", 0.0), 0.0)
            activations = int(row.get("vol_shock_kill_activations", 0))
            variant = str(row.get("variant", ""))

            if delta_tail > 0.0:
                reasons.append("tail_not_improved")
            if delta_profit < MAX_SOFT_PROFIT_DEGRADATION:
                reasons.append("profit_degradation_limit_exceeded")
            if variant == "VOL_SHOCK_FORCE_ON" and activations <= 0:
                reasons.append("force_on_not_activated")
            if activations <= 0 and variant == "VOL_SHOCK_SOFT":
                reasons.append("soft_not_activated")

            severity = 0.0
            severity += max(0.0, delta_tail) * 1000.0
            severity += max(0.0, (MAX_SOFT_PROFIT_DEGRADATION - delta_profit)) * 100.0
            if variant == "VOL_SHOCK_FORCE_ON" and activations <= 0:
                severity += 50.0
            if variant == "VOL_SHOCK_SOFT" and activations <= 0:
                severity += 10.0

            scored.append(
                (
                    severity,
                    {
                        "variant": variant,
                        "severity_score": round(severity, 6),
                        "delta_tail_vs_ctrl": delta_tail,
                        "delta_profit_vs_ctrl": delta_profit,
                        "delta_pf_vs_ctrl": to_float(row.get("delta_pf_vs_ctrl", 0.0), 0.0),
                        "economic_metrics_valid": bool(row.get("economic_metrics_valid", False)),
                        "profit_floor_ok": bool(row.get("profit_floor_ok", False)),
                        "vol_shock_kill_activations": activations,
                        "reasons": reasons,
                    },
                )
            )

        scored.sort(key=lambda item: item[0], reverse=True)
        failure_modes_top3[window_id] = [item[1] for item in scored[:3]]

    pass_ready = (
        activation_ok
        and force_on_activation_ok
        and soft_tail_nonpositive_all
        and soft_profit_floor_all
        and economic_valid_ratio_ok
    )

    decision = (
        "TAIL_MITIGATION_PASS_READY_FOR_ECONOMIC_RECHECK"
        if pass_ready
        else "TAIL_GUARD_LOGIC_REWORK_REQUIRED"
    )

    fail_state = None
    if not economic_valid_ratio_ok:
        fail_state = "INSUFFICIENT_VALID_ECONOMICS"
    elif not force_on_activation_ok:
        fail_state = "GUARD_WIRING_OR_CONTEXT_ISSUE"
    elif not activation_ok:
        fail_state = "VOL_SHOCK_NOT_ACTIVATED"
    elif not soft_tail_nonpositive_all:
        fail_state = "TAIL_IMPACT_NOT_IMPROVED"
    elif not soft_profit_floor_all:
        fail_state = "PROFIT_DEGRADATION_EXCEEDS_LIMIT"

    variants_summary = []
    for variant in compare_variants:
        item = acc[variant]
        n = item["n"]
        n_valid = item["n_valid"]
        variants_summary.append(
            {
                "variant": variant,
                "windows_with_ctrl": n,
                "windows_with_economic_valid": n_valid,
                "tail_nonpositive_windows": item["tail_nonpositive"],
                "profit_floor_ok_windows": item["profit_floor_ok"],
                "delta_tail_vs_ctrl": round((item["sum_delta_tail"] / n_valid) if n_valid > 0 else 0.0, 6),
                "delta_pf_vs_ctrl": round((item["sum_delta_pf"] / n_valid) if n_valid > 0 else 0.0, 6),
                "delta_profit_vs_ctrl": round((item["sum_delta_profit"] / n_valid) if n_valid > 0 else 0.0, 6),
                "vol_shock_kill_activations": int(item["vol_shock_kill_activations"]),
            }
        )

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "stage": "ANCHOR_TAIL2x3_V6",
        "status": "PASS_CLEAR" if table_rows else "FAIL_CLEAR",
        "decision": decision,
        "fail_state": fail_state,
        "gate": {
            "status": "PASS" if pass_ready else "FAIL",
            "rule": "activation_total>0 AND force_on_activations>0 AND SOFT delta_tail<=0 in W002/W012 AND SOFT delta_profit>=-0.10 in W002/W012",
            "economic_valid_ratio": round(valid_ratio, 6),
            "required_economic_valid_ratio": REQUIRED_ECONOMIC_VALID_RATIO,
            "vol_shock_kill_activations_total": vol_shock_activation_total,
            "vol_shock_force_on_activations_total": force_on_activation_total,
            "vol_shock_soft_activations_total": soft_activation_total,
            "vol_shock_activation_required": True,
            "force_on_activation_required": True,
            "soft_tail_nonpositive_all_windows": soft_tail_nonpositive_all,
            "soft_profit_degradation_floor": MAX_SOFT_PROFIT_DEGRADATION,
            "soft_profit_floor_ok_all_windows": soft_profit_floor_all,
        },
        "expected_comparisons": expected_comparisons,
        "comparisons_total": len(table_rows),
        "comparisons_with_economic_valid": valid_comparisons,
        "variants": variants_summary,
        "targeted_debug": targeted_debug,
        "failure_modes_top3": failure_modes_top3,
    }

    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with OUT_FAILURE_MODES.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "stage": "ANCHOR_TAIL2x3_V6",
                "targeted_windows": TARGET_WINDOWS,
                "failure_modes_top3": failure_modes_top3,
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )

    fieldnames = [
        "window_id",
        "anchor_id",
        "variant",
        "economic_metrics_valid",
        "must_hit_rate_proxy",
        "ctrl_must_hit_rate_proxy",
        "delta_must_hit_rate_vs_ctrl",
        "profit_total_pct",
        "ctrl_profit_total_pct",
        "delta_profit_vs_ctrl",
        "pf",
        "ctrl_pf",
        "delta_pf_vs_ctrl",
        "tail",
        "ctrl_tail",
        "delta_tail_vs_ctrl",
        "tail_nonpositive_vs_ctrl",
        "profit_floor_ok",
        "vol_shock_kill_activations",
    ]
    with OUT_TABLE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in table_rows:
            writer.writerow(row)

    print(f"Status: {summary['status']}")
    print(f"Decision: {summary['decision']}")
    print(f"Gate: {summary['gate']['status']}")


if __name__ == "__main__":
    main()
