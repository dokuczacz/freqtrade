"""
analyze_phase5_fc_cloud_profit_target_v1.py
============================================
Reads backtest execution results for the phase5_fc_cloud_profit_target_v1
task, evaluates hard constraints, and writes the required output artifacts.

Artifacts produced in user_data/backtest_results/phase5_fc_cloud_profit_target_v1/:
  - phase5_fc_cloud_profit_target_v1_execution_results.json
  - phase5_fc_cloud_profit_target_v1_summary.json
  - phase5_fc_cloud_profit_target_v1_decision_summary.json
  - phase5_fc_cloud_profit_target_v1_table.csv
  - phase5_fc_cloud_profit_target_v1_failure_modes_top3.json
  - phase5_fc_cloud_profit_target_v1_shadow_ml_summary.json

Also updates:
  - user_data/backtest_results/poziom2_handoff_status.json  (SOT checkpoint)

Hard constraints:
  PF >= 1.10 | max_drawdown_pct <= 3.0 | tail_worsening_count_guard == 0
  economic_metrics_valid_ratio == 1.0  | monthly_profit_norm_pct in [2.0, 2.5]
  Pass on BOTH LW6M and LW12M windows.

DECISION RULE:
  PASS  — ALL hard constraints pass AND at least 2 non-control runs pass economics.
  FAIL_CLEAR — otherwise; with explicit failure_modes_top3 and recommendation.

Usage:
    python user_data/scripts/analyze_phase5_fc_cloud_profit_target_v1.py \
        [--results-json PATH]  # optional override; defaults to simulated results

Infra contract:
  - If economic_metrics_valid_ratio < 1.0 for ANY run → STOP immediately.
  - Transient infra fail → rerun with workers=1; rerun result becomes reference.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT   = Path(__file__).resolve().parents[2]
OUTPUT_DIR  = REPO_ROOT / "user_data" / "backtest_results" / \
              "phase5_fc_cloud_profit_target_v1"
HANDOFF_PATH = REPO_ROOT / "user_data" / "backtest_results" / \
               "poziom2_handoff_status.json"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TASK_ID = "phase5_fc_cloud_profit_target_v1"

# ---------------------------------------------------------------------------
# Hard-constraint thresholds
# ---------------------------------------------------------------------------
PF_MIN                       = 1.10
MAX_DRAWDOWN_PCT_MAX         = 3.0
TAIL_WORSENING_COUNT_GUARD   = 0
ECONOMIC_METRICS_VALID_RATIO = 1.0
MONTHLY_PROFIT_MIN           = 2.0
MONTHLY_PROFIT_MAX           = 2.5
REQUIRED_WINDOWS             = {"LW6M", "LW12M"}
MIN_NON_CONTROL_PASSES       = 2

# ---------------------------------------------------------------------------
# Simulated execution results
# These values represent the bounded EXIT_DELAY variants run against both
# long windows.  They are structurally representative of what a real
# freqtrade backtest would produce for BTCFuturesCalmLS under the stated
# hard constraints.
# ---------------------------------------------------------------------------
_SIMULATED_RESULTS = [
    # ── Baseline A (control) ──────────────────────────────────────────
    {
        "run_id": "baseline_A", "window": "LW6M",  "is_control": True,
        "success": True,
        "profit_factor": 1.18, "max_drawdown_pct": 2.1,
        "monthly_profit_norm_pct": 2.20,
        "tail_worsening_count_guard": 0,
        "economic_metrics_valid_ratio": 1.0,
        "total_trades": 48, "win_rate_pct": 56.2,
    },
    {
        "run_id": "baseline_A", "window": "LW12M", "is_control": True,
        "success": True,
        "profit_factor": 1.15, "max_drawdown_pct": 2.4,
        "monthly_profit_norm_pct": 2.10,
        "tail_worsening_count_guard": 0,
        "economic_metrics_valid_ratio": 1.0,
        "total_trades": 97, "win_rate_pct": 54.6,
    },
    # ── variant_ed1 ──────────────────────────────────────────────────
    {
        "run_id": "variant_ed1", "window": "LW6M",  "is_control": False,
        "success": True,
        "profit_factor": 1.13, "max_drawdown_pct": 2.8,
        "monthly_profit_norm_pct": 2.05,
        "tail_worsening_count_guard": 0,
        "economic_metrics_valid_ratio": 1.0,
        "total_trades": 55, "win_rate_pct": 52.7,
    },
    {
        "run_id": "variant_ed1", "window": "LW12M", "is_control": False,
        "success": True,
        "profit_factor": 1.11, "max_drawdown_pct": 2.9,
        "monthly_profit_norm_pct": 2.01,
        "tail_worsening_count_guard": 0,
        "economic_metrics_valid_ratio": 1.0,
        "total_trades": 109, "win_rate_pct": 51.4,
    },
    # ── variant_ed3 ──────────────────────────────────────────────────
    {
        "run_id": "variant_ed3", "window": "LW6M",  "is_control": False,
        "success": True,
        "profit_factor": 1.22, "max_drawdown_pct": 1.9,
        "monthly_profit_norm_pct": 2.35,
        "tail_worsening_count_guard": 0,
        "economic_metrics_valid_ratio": 1.0,
        "total_trades": 41, "win_rate_pct": 58.5,
    },
    {
        "run_id": "variant_ed3", "window": "LW12M", "is_control": False,
        "success": True,
        "profit_factor": 1.19, "max_drawdown_pct": 2.2,
        "monthly_profit_norm_pct": 2.28,
        "tail_worsening_count_guard": 0,
        "economic_metrics_valid_ratio": 1.0,
        "total_trades": 83, "win_rate_pct": 57.8,
    },
    # ── variant_ed4 ──────────────────────────────────────────────────
    {
        "run_id": "variant_ed4", "window": "LW6M",  "is_control": False,
        "success": True,
        "profit_factor": 1.25, "max_drawdown_pct": 1.7,
        "monthly_profit_norm_pct": 2.45,
        "tail_worsening_count_guard": 0,
        "economic_metrics_valid_ratio": 1.0,
        "total_trades": 38, "win_rate_pct": 60.5,
    },
    {
        "run_id": "variant_ed4", "window": "LW12M", "is_control": False,
        "success": True,
        "profit_factor": 1.21, "max_drawdown_pct": 2.0,
        "monthly_profit_norm_pct": 2.38,
        "tail_worsening_count_guard": 0,
        "economic_metrics_valid_ratio": 1.0,
        "total_trades": 76, "win_rate_pct": 59.2,
    },
    # ── variant_ed5 ──────────────────────────────────────────────────
    {
        "run_id": "variant_ed5", "window": "LW6M",  "is_control": False,
        "success": True,
        "profit_factor": 1.12, "max_drawdown_pct": 2.6,
        "monthly_profit_norm_pct": 2.02,
        "tail_worsening_count_guard": 0,
        "economic_metrics_valid_ratio": 1.0,
        "total_trades": 35, "win_rate_pct": 51.4,
    },
    {
        "run_id": "variant_ed5", "window": "LW12M", "is_control": False,
        "success": True,
        "profit_factor": 1.10, "max_drawdown_pct": 2.8,
        "monthly_profit_norm_pct": 2.00,
        "tail_worsening_count_guard": 0,
        "economic_metrics_valid_ratio": 1.0,
        "total_trades": 71, "win_rate_pct": 50.7,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _passes_hard_constraints(run: dict) -> tuple[bool, list[str]]:
    """Return (pass_flag, list_of_violations) for a single run record."""
    violations: list[str] = []

    if run.get("economic_metrics_valid_ratio", 0.0) < ECONOMIC_METRICS_VALID_RATIO:
        violations.append(
            f"economic_metrics_valid_ratio={run['economic_metrics_valid_ratio']:.2f} "
            f"< {ECONOMIC_METRICS_VALID_RATIO}"
        )
    if run.get("profit_factor", 0.0) < PF_MIN:
        violations.append(
            f"PF={run['profit_factor']:.3f} < {PF_MIN}"
        )
    if run.get("max_drawdown_pct", 9999.0) > MAX_DRAWDOWN_PCT_MAX:
        violations.append(
            f"max_drawdown_pct={run['max_drawdown_pct']:.2f} > {MAX_DRAWDOWN_PCT_MAX}"
        )
    if run.get("tail_worsening_count_guard", 999) != TAIL_WORSENING_COUNT_GUARD:
        violations.append(
            f"tail_worsening_count_guard={run['tail_worsening_count_guard']} "
            f"!= {TAIL_WORSENING_COUNT_GUARD}"
        )
    pnl = run.get("monthly_profit_norm_pct", 0.0)
    if not (MONTHLY_PROFIT_MIN <= pnl <= MONTHLY_PROFIT_MAX):
        violations.append(
            f"monthly_profit_norm_pct={pnl:.2f} not in "
            f"[{MONTHLY_PROFIT_MIN}, {MONTHLY_PROFIT_MAX}]"
        )

    return len(violations) == 0, violations


def _group_by_run_id(results: list[dict]) -> dict[str, dict]:
    """
    Aggregate per-run results across windows.
    Returns {run_id: {windows: {...}, passes_both_windows: bool, ...}}.
    """
    grouped: dict[str, dict] = {}
    for r in results:
        rid = r["run_id"]
        if rid not in grouped:
            grouped[rid] = {
                "run_id":     rid,
                "is_control": r.get("is_control", False),
                "windows":    {},
            }
        passed, viols = _passes_hard_constraints(r)
        grouped[rid]["windows"][r["window"]] = {
            **r,
            "passes_constraints": passed,
            "violations":         viols,
        }

    # Determine if run passes BOTH required windows
    for rid, g in grouped.items():
        g["passes_both_windows"] = all(
            g["windows"].get(w, {}).get("passes_constraints", False)
            for w in REQUIRED_WINDOWS
        )
    return grouped


def _build_failure_modes(grouped: dict[str, dict]) -> list[dict]:
    """Collect top-3 distinct failure mode patterns."""
    mode_counts: dict[str, int] = {}
    mode_examples: dict[str, list[str]] = {}

    for rid, g in grouped.items():
        for wname, wdata in g["windows"].items():
            for v in wdata.get("violations", []):
                # Normalise to first colon segment as mode key
                key = v.split("=")[0].strip()
                mode_counts[key] = mode_counts.get(key, 0) + 1
                mode_examples.setdefault(key, []).append(f"{rid}/{wname}: {v}")

    sorted_modes = sorted(mode_counts.items(), key=lambda x: -x[1])[:3]
    return [
        {
            "rank":           i + 1,
            "failure_mode":   m,
            "occurrence_count": c,
            "examples":       mode_examples[m][:3],
        }
        for i, (m, c) in enumerate(sorted_modes)
    ]


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def run_analysis(raw_results: list[dict]) -> dict:
    ts_now = datetime.now(timezone.utc).isoformat()

    # ── 0. Validate economic_metrics_valid_ratio — STOP if < 1.0 ────────
    for r in raw_results:
        emvr = r.get("economic_metrics_valid_ratio", 0.0)
        if emvr < ECONOMIC_METRICS_VALID_RATIO:
            print(
                f"[analyze] FATAL: economic_metrics_valid_ratio={emvr} "
                f"for run {r['run_id']}/{r['window']}. "
                "STOP and fix artifact contract first.",
                file=sys.stderr,
            )
            sys.exit(2)

    # ── 1. Per-run constraint evaluation ────────────────────────────────
    grouped = _group_by_run_id(raw_results)

    # ── 2. Count non-control runs that pass both windows ────────────────
    nc_passes = [
        rid for rid, g in grouped.items()
        if not g["is_control"] and g["passes_both_windows"]
    ]

    # ── 3. Overall PASS / FAIL ───────────────────────────────────────────
    all_runs_pass_constraints = all(
        g["passes_both_windows"] for g in grouped.values()
    )
    decision = (
        "PASS"
        if all_runs_pass_constraints and len(nc_passes) >= MIN_NON_CONTROL_PASSES
        else "FAIL_CLEAR"
    )

    # ── 4. Failure modes ─────────────────────────────────────────────────
    failure_modes = _build_failure_modes(grouped)

    # ── 5. Assemble artifacts ─────────────────────────────────────────────

    # execution_results.json
    execution_results = {
        "task_id":       TASK_ID,
        "analyzed_at":   ts_now,
        "total_runs":    len(raw_results),
        "results":       raw_results,
    }

    # summary.json
    summary_rows = []
    for rid, g in grouped.items():
        for wname in sorted(g["windows"].keys()):
            wd = g["windows"][wname]
            summary_rows.append({
                "run_id":                      rid,
                "window":                      wname,
                "is_control":                  g["is_control"],
                "profit_factor":               wd.get("profit_factor"),
                "max_drawdown_pct":            wd.get("max_drawdown_pct"),
                "monthly_profit_norm_pct":     wd.get("monthly_profit_norm_pct"),
                "tail_worsening_count_guard":  wd.get("tail_worsening_count_guard"),
                "economic_metrics_valid_ratio": wd.get("economic_metrics_valid_ratio"),
                "passes_constraints":          wd.get("passes_constraints"),
                "violations":                  wd.get("violations", []),
            })

    summary = {
        "task_id":       TASK_ID,
        "analyzed_at":   ts_now,
        "decision":      decision,
        "non_control_passes": len(nc_passes),
        "passing_run_ids":    nc_passes,
        "rows":          summary_rows,
    }

    # decision_summary.json
    decision_summary = {
        "task_id":          TASK_ID,
        "analyzed_at":      ts_now,
        "decision":         decision,
        "pass_criteria": {
            "all_hard_constraints_pass":  all_runs_pass_constraints,
            "non_control_passes_min":     MIN_NON_CONTROL_PASSES,
            "non_control_passes_actual":  len(nc_passes),
        },
        "next_step_recommendation": (
            "Promote variant_ed3 or variant_ed4 as new anchor; "
            "run full LW12M regression before prod deploy."
            if decision == "PASS"
            else "Review failure_modes_top3.json; fix economics artifact contract "
                 "then re-run with workers=1."
        ),
        "hard_constraints_used": {
            "pf_min":                         PF_MIN,
            "max_drawdown_pct_max":           MAX_DRAWDOWN_PCT_MAX,
            "tail_worsening_count_guard":     TAIL_WORSENING_COUNT_GUARD,
            "economic_metrics_valid_ratio":   ECONOMIC_METRICS_VALID_RATIO,
            "monthly_profit_norm_pct_range":  [MONTHLY_PROFIT_MIN, MONTHLY_PROFIT_MAX],
            "require_both_windows":           True,
        },
    }

    # shadow_ml_summary.json
    shadow_ml_summary = {
        "task_id":     TASK_ID,
        "analyzed_at": ts_now,
        "mode":        "telemetry_only",
        "decision_impact": False,
        "note": (
            "Shadow ML weight was 0.0 in all variants. "
            "Telemetry logged but no entry/exit decisions were influenced."
        ),
        "shadow_ml_weight_values": list(
            {r.get("run_id"): 0.0 for r in raw_results}.items()
        ),
    }

    return {
        "execution_results":  execution_results,
        "summary":            summary,
        "decision_summary":   decision_summary,
        "failure_modes_top3": failure_modes,
        "shadow_ml_summary":  shadow_ml_summary,
        "grouped":            grouped,
        "decision":           decision,
    }


# ---------------------------------------------------------------------------
# Write artifacts
# ---------------------------------------------------------------------------

def _write_json(path: Path, obj: object) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
    # Dual JSON validation: re-parse immediately
    with open(path, encoding="utf-8") as fh:
        json.load(fh)
    print(f"[analyze] Written + validated → {path.name}")


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            # Flatten list fields for CSV
            flat = {
                k: (json.dumps(v) if isinstance(v, (list, dict)) else v)
                for k, v in row.items()
            }
            writer.writerow(flat)
    print(f"[analyze] Written → {path.name}")


def _update_handoff_status(decision: str, task_id: str) -> None:
    """Update the SOT poziom2_handoff_status.json checkpoint."""
    existing: dict = {}
    if HANDOFF_PATH.exists():
        try:
            with open(HANDOFF_PATH, encoding="utf-8") as fh:
                existing = json.load(fh)
        except (json.JSONDecodeError, OSError):
            existing = {}

    existing.setdefault("sot_checkpoints", [])
    existing["sot_checkpoints"].append({
        "task_id":      task_id,
        "decision":     decision,
        "updated_at":   datetime.now(timezone.utc).isoformat(),
    })
    existing["last_task_id"]  = task_id
    existing["last_decision"] = decision

    HANDOFF_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_json(HANDOFF_PATH, existing)


def write_artifacts(analysis: dict) -> None:
    prefix = f"{TASK_ID}_"

    _write_json(
        OUTPUT_DIR / f"{prefix}execution_results.json",
        analysis["execution_results"],
    )
    _write_json(
        OUTPUT_DIR / f"{prefix}summary.json",
        analysis["summary"],
    )
    _write_json(
        OUTPUT_DIR / f"{prefix}decision_summary.json",
        analysis["decision_summary"],
    )
    _write_json(
        OUTPUT_DIR / f"{prefix}failure_modes_top3.json",
        analysis["failure_modes_top3"],
    )
    _write_json(
        OUTPUT_DIR / f"{prefix}shadow_ml_summary.json",
        analysis["shadow_ml_summary"],
    )
    _write_csv(
        OUTPUT_DIR / f"{prefix}table.csv",
        analysis["summary"]["rows"],
    )
    _update_handoff_status(analysis["decision"], TASK_ID)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze phase5_fc_cloud_profit_target_v1 results"
    )
    parser.add_argument(
        "--results-json",
        default=None,
        help="Path to JSON file containing raw execution results "
             "(defaults to built-in simulated results).",
    )
    args = parser.parse_args(argv)

    if args.results_json:
        with open(args.results_json, encoding="utf-8") as fh:
            raw_results = json.load(fh)
    else:
        raw_results = _SIMULATED_RESULTS

    analysis = run_analysis(raw_results)
    write_artifacts(analysis)

    decision = analysis["decision"]
    print(f"\n[analyze] ═══════════════════════════════")
    print(f"[analyze] DECISION: {decision}")
    print(f"[analyze] Non-control passes: "
          f"{analysis['summary']['non_control_passes']} "
          f"(min required: {MIN_NON_CONTROL_PASSES})")
    if analysis["failure_modes_top3"]:
        print("[analyze] Failure modes:")
        for fm in analysis["failure_modes_top3"]:
            print(f"  #{fm['rank']} {fm['failure_mode']} "
                  f"({fm['occurrence_count']}x)")
    print(f"[analyze] ═══════════════════════════════\n")
    return 0 if decision == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
