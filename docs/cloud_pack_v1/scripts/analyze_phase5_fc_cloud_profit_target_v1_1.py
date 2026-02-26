"""Analyse phase5_fc_cloud_profit_target_v1_1 execution results.

PRIMARY KPI: delta_profit_vs_ctrl (reported first in all outputs)
HARD CONSTRAINTS:
  - PF >= 1.10
  - max_drawdown_pct <= 3.0  (tail <= 0.03 account fraction)
  - tail_worsening_count_guard == 0
  - economic_metrics_valid_ratio == 1.0
  - pass on both LW6M and LW12M
  - at least 2 non-control runs pass economics

GOVERNANCE: no broad sweep; shadow ML is telemetry-only.
"""

import csv
import json
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST = Path("user_data/phase5_fc_cloud_profit_target_v1_1_manifest.json")
EXECUTION = Path(
    "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_execution_results.json"
)
OUT_SUMMARY = Path(
    "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_summary.json"
)
OUT_DECISION = Path(
    "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_decision_summary.json"
)
OUT_TABLE = Path(
    "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_table.csv"
)
OUT_FAILURE_MODES = Path(
    "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_failure_modes_top3.json"
)
OUT_SOT_PATCH = Path(
    "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_sot_update_patch.json"
)
RESULTS_ROOT = Path(
    "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1"
)

STAGE = "CLOUD_PROFIT_TARGET_V1_1"
TARGET_WINDOWS = ["LW6M", "LW12M"]
COMPARE_VARIANTS = ["PROFIT_TUNE_A", "PROFIT_TUNE_B", "PROFIT_TUNE_C"]

# Hard constraint thresholds
REQUIRED_ECONOMIC_VALID_RATIO = 1.0
HARD_PF_MIN = 1.10
HARD_DRAWDOWN_MAX = 0.03       # as account fraction (max_drawdown_account)
MONTHLY_PROFIT_LOW = 2.0       # percent
MONTHLY_PROFIT_HIGH = 2.5      # percent
NON_CTRL_PASSES_MIN = 2

# Window durations for monthly normalisation
WINDOW_MONTHS: dict[str, float] = {
    "LW6M": 6.0,
    "LW12M": 12.0,
}


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


def extract_metrics_from_zip(run_row: dict[str, Any]) -> dict[str, Any]:
    run = str(run_row.get("run", ""))
    zip_name = str(run_row.get("backtest_file", ""))
    if not run or not zip_name:
        return {
            "economic_metrics_valid": False,
            "profit_total_pct": 0.0,
            "pf": 0.0,
            "tail": 0.0,
        }

    zip_path = RESULTS_ROOT / run / zip_name
    if not zip_path.exists():
        return {
            "economic_metrics_valid": False,
            "profit_total_pct": 0.0,
            "pf": 0.0,
            "tail": 0.0,
        }

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            json_members = [
                n for n in zf.namelist() if n.endswith(".json") and "_config" not in n
            ]
            if not json_members:
                return {
                    "economic_metrics_valid": False,
                    "profit_total_pct": 0.0,
                    "pf": 0.0,
                    "tail": 0.0,
                }
            payload = json.loads(zf.read(json_members[0]))
            comparison = payload.get("strategy_comparison")
            if not isinstance(comparison, list) or not comparison:
                return {
                    "economic_metrics_valid": False,
                    "profit_total_pct": 0.0,
                    "pf": 0.0,
                    "tail": 0.0,
                }
            strategy_name = str(run_row.get("strategy", ""))
            metric_row = None
            for item in comparison:
                if str(item.get("key", "")) == strategy_name:
                    metric_row = item
                    break
            if metric_row is None:
                metric_row = comparison[0]
            return {
                "economic_metrics_valid": True,
                "profit_total_pct": to_float(metric_row.get("profit_total_pct"), 0.0),
                "pf": to_float(metric_row.get("profit_factor"), 0.0),
                "tail": to_float(metric_row.get("max_drawdown_account"), 0.0),
            }
    except Exception:
        return {
            "economic_metrics_valid": False,
            "profit_total_pct": 0.0,
            "pf": 0.0,
            "tail": 0.0,
        }


def monthly_profit_norm(profit_total_pct: float, window_id: str) -> float:
    months = WINDOW_MONTHS.get(window_id, 6.0)
    return profit_total_pct / months if months > 0 else 0.0


def variant_passes_hard_constraints(
    row: dict[str, Any],
    window_id: str,
) -> tuple[bool, list[str]]:
    """Return (passes, list_of_failure_reasons)."""
    reasons: list[str] = []

    if not row.get("economic_metrics_valid", False):
        reasons.append("economic_metrics_invalid")
        return False, reasons

    pf = to_float(row.get("pf"), 0.0)
    tail = to_float(row.get("tail"), 0.0)
    monthly = monthly_profit_norm(to_float(row.get("profit_total_pct"), 0.0), window_id)

    if pf < HARD_PF_MIN:
        reasons.append(f"pf_below_min ({pf:.4f} < {HARD_PF_MIN})")
    if tail > HARD_DRAWDOWN_MAX:
        reasons.append(f"drawdown_exceeds_max ({tail:.4f} > {HARD_DRAWDOWN_MAX})")
    if not (MONTHLY_PROFIT_LOW <= monthly <= MONTHLY_PROFIT_HIGH):
        reasons.append(
            f"monthly_profit_out_of_target "
            f"({monthly:.4f} not in [{MONTHLY_PROFIT_LOW}, {MONTHLY_PROFIT_HIGH}])"
        )

    return len(reasons) == 0, reasons


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
        win = str(meta.get("window_id", ""))
        monthly = monthly_profit_norm(
            to_float(zip_metrics.get("profit_total_pct"), 0.0), win
        )
        eval_rows.append(
            {
                "run": run,
                "window_id": win,
                "variant": str(meta.get("variant", "")),
                "economic_metrics_valid": bool(zip_metrics.get("economic_metrics_valid", False)),
                "profit_total_pct": to_float(zip_metrics.get("profit_total_pct"), 0.0),
                "monthly_profit_norm_pct": round(monthly, 6),
                "pf": to_float(zip_metrics.get("pf"), 0.0),
                "tail": to_float(zip_metrics.get("tail"), 0.0),
            }
        )

    per_window: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in eval_rows:
        per_window[row["window_id"]][row["variant"]] = row

    # Accumulate stats per variant
    acc: dict[str, dict[str, Any]] = {
        v: {
            "n": 0, "n_valid": 0,
            "passes_hard": 0,
            "tail_worsening": 0,
            "sum_delta_profit": 0.0,
            "sum_delta_pf": 0.0,
            "sum_delta_tail": 0.0,
            "sum_monthly_profit": 0.0,
        }
        for v in COMPARE_VARIANTS
    }

    table_rows: list[dict[str, Any]] = []
    per_window_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for window_id in TARGET_WINDOWS:
        bucket = per_window.get(window_id, {})
        ctrl = bucket.get("CTRL")
        if not ctrl:
            continue

        for variant in COMPARE_VARIANTS:
            cand = bucket.get(variant)
            if not cand:
                continue

            econ_valid = bool(ctrl["economic_metrics_valid"]) and bool(
                cand["economic_metrics_valid"]
            )
            delta_profit = cand["profit_total_pct"] - ctrl["profit_total_pct"]
            delta_pf = cand["pf"] - ctrl["pf"]
            delta_tail = cand["tail"] - ctrl["tail"]
            monthly = cand["monthly_profit_norm_pct"]

            passes, fail_reasons = variant_passes_hard_constraints(cand, window_id)
            tail_worsened = delta_tail > 0.0 if econ_valid else True

            a = acc[variant]
            a["n"] += 1
            if econ_valid:
                a["n_valid"] += 1
                a["sum_delta_profit"] += delta_profit
                a["sum_delta_pf"] += delta_pf
                a["sum_delta_tail"] += delta_tail
                a["sum_monthly_profit"] += monthly
                if passes:
                    a["passes_hard"] += 1
                if tail_worsened:
                    a["tail_worsening"] += 1

            out_row: dict[str, Any] = {
                "window_id": window_id,
                "variant": variant,
                "economic_metrics_valid": econ_valid,
                "profit_total_pct": round(cand["profit_total_pct"], 6),
                "ctrl_profit_total_pct": round(ctrl["profit_total_pct"], 6),
                # PRIMARY KPI first
                "delta_profit_vs_ctrl": round(delta_profit, 6),
                "monthly_profit_norm_pct": round(monthly, 6),
                "ctrl_monthly_profit_norm_pct": round(ctrl["monthly_profit_norm_pct"], 6),
                "delta_tail_vs_ctrl": round(delta_tail, 6),
                "pf": round(cand["pf"], 6),
                "ctrl_pf": round(ctrl["pf"], 6),
                "delta_pf_vs_ctrl": round(delta_pf, 6),
                "tail": round(cand["tail"], 6),
                "ctrl_tail": round(ctrl["tail"], 6),
                "passes_hard_constraints": passes,
                "fail_reasons": "; ".join(fail_reasons),
            }
            table_rows.append(out_row)
            per_window_rows[window_id].append(out_row)

    # Gate evaluation
    expected_comparisons = len(TARGET_WINDOWS) * len(COMPARE_VARIANTS)
    valid_comparisons = sum(1 for r in table_rows if r["economic_metrics_valid"])
    valid_ratio = (
        valid_comparisons / expected_comparisons if expected_comparisons > 0 else 0.0
    )

    # Count variant-level passes across both windows
    variant_pass_count: dict[str, int] = {}
    for variant in COMPARE_VARIANTS:
        wins_passing = sum(
            1 for win in TARGET_WINDOWS
            if any(
                r["variant"] == variant
                and r["window_id"] == win
                and bool(r.get("passes_hard_constraints", False))
                for r in table_rows
            )
        )
        variant_pass_count[variant] = wins_passing

    # A variant "passes both windows" if it passes on BOTH LW6M and LW12M
    variants_passing_both = [
        v for v, count in variant_pass_count.items() if count == len(TARGET_WINDOWS)
    ]
    non_ctrl_passes_both = len(variants_passing_both)

    total_tail_worsening = sum(a["tail_worsening"] for a in acc.values())

    # Hard constraint evaluation
    econ_ratio_ok = valid_ratio >= REQUIRED_ECONOMIC_VALID_RATIO
    tail_guard_ok = total_tail_worsening == 0
    non_ctrl_passes_ok = non_ctrl_passes_both >= NON_CTRL_PASSES_MIN

    all_hard_pass = econ_ratio_ok and tail_guard_ok and non_ctrl_passes_ok

    # Determine fail_state
    fail_state = None
    if not econ_ratio_ok:
        fail_state = "CONTRACT_FIX_REQUIRED_ECONOMIC_VALID_RATIO_BELOW_1"
    elif not tail_guard_ok:
        fail_state = "TAIL_WORSENING_GUARD_VIOLATED"
    elif not non_ctrl_passes_ok:
        fail_state = f"INSUFFICIENT_NON_CTRL_PASSES (need {NON_CTRL_PASSES_MIN}, got {non_ctrl_passes_both})"

    decision = (
        "PROFIT_TARGET_REACHED_READY_FOR_DEPLOYMENT_GATE"
        if all_hard_pass
        else "PROFIT_TARGET_NOT_MET_CONTINUE_BOUNDED_SEARCH"
    )

    # Failure modes per window
    failure_modes_top3: dict[str, list[dict[str, Any]]] = {}
    for window_id in TARGET_WINDOWS:
        rows = per_window_rows.get(window_id, [])
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            reasons_str = str(row.get("fail_reasons", ""))
            reasons = [r.strip() for r in reasons_str.split(";") if r.strip()] if reasons_str else []
            delta_profit = to_float(row.get("delta_profit_vs_ctrl"), 0.0)
            delta_tail = to_float(row.get("delta_tail_vs_ctrl"), 0.0)
            monthly = to_float(row.get("monthly_profit_norm_pct"), 0.0)
            pf = to_float(row.get("pf"), 0.0)

            severity = 0.0
            if monthly < MONTHLY_PROFIT_LOW:
                severity += (MONTHLY_PROFIT_LOW - monthly) * 100.0
            elif monthly > MONTHLY_PROFIT_HIGH:
                severity += (monthly - MONTHLY_PROFIT_HIGH) * 50.0
            if pf < HARD_PF_MIN:
                severity += (HARD_PF_MIN - pf) * 20.0
            if delta_tail > 0.0:
                severity += delta_tail * 500.0

            scored.append(
                (
                    severity,
                    {
                        "variant": str(row.get("variant", "")),
                        "severity_score": round(severity, 6),
                        # PRIMARY KPI first
                        "delta_profit_vs_ctrl": delta_profit,
                        "delta_tail_vs_ctrl": delta_tail,
                        "delta_pf_vs_ctrl": to_float(row.get("delta_pf_vs_ctrl"), 0.0),
                        "monthly_profit_norm_pct": monthly,
                        "pf": pf,
                        "economic_metrics_valid": bool(row.get("economic_metrics_valid", False)),
                        "passes_hard_constraints": bool(row.get("passes_hard_constraints", False)),
                        "reasons": reasons,
                    },
                )
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        failure_modes_top3[window_id] = [item[1] for item in scored[:3]]

    # Variants summary
    variants_summary = []
    for variant in COMPARE_VARIANTS:
        a = acc[variant]
        n_valid = a["n_valid"]
        variants_summary.append(
            {
                "variant": variant,
                "windows_evaluated": a["n"],
                "windows_with_economic_valid": n_valid,
                "windows_passing_hard_constraints": variant_pass_count.get(variant, 0),
                "passes_both_windows": variant in variants_passing_both,
                # PRIMARY KPI first
                "delta_profit_vs_ctrl": round(
                    a["sum_delta_profit"] / n_valid if n_valid > 0 else 0.0, 6
                ),
                "delta_tail_vs_ctrl": round(
                    a["sum_delta_tail"] / n_valid if n_valid > 0 else 0.0, 6
                ),
                "delta_pf_vs_ctrl": round(
                    a["sum_delta_pf"] / n_valid if n_valid > 0 else 0.0, 6
                ),
                "avg_monthly_profit_norm_pct": round(
                    a["sum_monthly_profit"] / n_valid if n_valid > 0 else 0.0, 6
                ),
                "tail_worsening_count": a["tail_worsening"],
            }
        )

    summary: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "stage": STAGE,
        "status": "PASS_CLEAR" if all_hard_pass else "FAIL_CLEAR",
        "decision": decision,
        "fail_state": fail_state,
        "gate": {
            "status": "PASS" if all_hard_pass else "FAIL",
            "economic_valid_ratio": round(valid_ratio, 6),
            "required_economic_valid_ratio": REQUIRED_ECONOMIC_VALID_RATIO,
            "tail_worsening_count_guard": total_tail_worsening,
            "tail_worsening_count_guard_ok": tail_guard_ok,
            "non_ctrl_passes_both_windows": non_ctrl_passes_both,
            "non_ctrl_passes_min_required": NON_CTRL_PASSES_MIN,
            "non_ctrl_passes_ok": non_ctrl_passes_ok,
            "variants_passing_both_windows": variants_passing_both,
        },
        "primary_kpi": "delta_profit_vs_ctrl",
        "kpi_priority": [
            "delta_profit_vs_ctrl",
            "delta_tail_vs_ctrl",
            "delta_pf_vs_ctrl",
            "activation_counts",
        ],
        "hard_constraints": {
            "pf_min": HARD_PF_MIN,
            "max_drawdown_pct": HARD_DRAWDOWN_MAX * 100,
            "monthly_profit_norm_target": [MONTHLY_PROFIT_LOW, MONTHLY_PROFIT_HIGH],
        },
        "expected_comparisons": expected_comparisons,
        "comparisons_total": len(table_rows),
        "comparisons_with_economic_valid": valid_comparisons,
        "variants": variants_summary,
        "failure_modes_top3": failure_modes_top3,
    }

    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # Decision summary (minimal, decision-grade)
    decision_summary: dict[str, Any] = {
        "generated_at": summary["generated_at"],
        "stage": STAGE,
        "status": summary["status"],
        "decision": decision,
        "fail_state": fail_state,
        "primary_kpi_diff": {
            "metric": "delta_profit_vs_ctrl",
            "per_variant": {
                v_sum["variant"]: v_sum["delta_profit_vs_ctrl"]
                for v_sum in variants_summary
            },
        },
        "gate_summary": summary["gate"],
        "recommended_next_step": (
            "DEPLOY_GATE"
            if all_hard_pass
            else (
                "Adjust RSI entry bands (single axis): increase rsi_long_entry_max by 3 pts "
                "to capture additional trend entries; re-run on LW6M+LW12M."
                if not non_ctrl_passes_ok
                else "Fix economic validity before any further search."
            )
        ),
    }
    OUT_DECISION.write_text(
        json.dumps(decision_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Failure modes
    with OUT_FAILURE_MODES.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "generated_at": summary["generated_at"],
                "stage": STAGE,
                "target_windows": TARGET_WINDOWS,
                "failure_modes_top3": failure_modes_top3,
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )

    # SOT update patch (delta to checkpoint)
    sot_patch: dict[str, Any] = {
        "generated_at": summary["generated_at"],
        "patch_type": "delta_to_checkpoint",
        "stage": STAGE,
        "status": summary["status"],
        "decision": decision,
        "variants_passing_both_windows": variants_passing_both,
        "non_ctrl_passes_both": non_ctrl_passes_both,
        "tail_worsening_total": total_tail_worsening,
        "economic_valid_ratio": round(valid_ratio, 6),
        "artifacts": {
            "summary": str(OUT_SUMMARY),
            "decision_summary": str(OUT_DECISION),
            "table": str(OUT_TABLE),
            "failure_modes_top3": str(OUT_FAILURE_MODES),
        },
    }
    OUT_SOT_PATCH.write_text(
        json.dumps(sot_patch, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Table CSV
    fieldnames = [
        "window_id",
        "variant",
        "economic_metrics_valid",
        # PRIMARY KPI first
        "delta_profit_vs_ctrl",
        "profit_total_pct",
        "ctrl_profit_total_pct",
        "monthly_profit_norm_pct",
        "ctrl_monthly_profit_norm_pct",
        "delta_tail_vs_ctrl",
        "pf",
        "ctrl_pf",
        "delta_pf_vs_ctrl",
        "tail",
        "ctrl_tail",
        "passes_hard_constraints",
        "fail_reasons",
    ]
    with OUT_TABLE.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in table_rows:
            writer.writerow(row)

    print(f"Status: {summary['status']}")
    print(f"Decision: {summary['decision']}")
    print(f"Gate: {summary['gate']['status']}")
    if fail_state:
        print(f"Fail state: {fail_state}")


if __name__ == "__main__":
    main()
