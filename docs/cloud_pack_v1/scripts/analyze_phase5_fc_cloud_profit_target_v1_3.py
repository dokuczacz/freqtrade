"""
Analyze backtest results for phase5_fc_cloud_profit_target_v1_3.

Reads manifest + execution_results, processes per-run backtest zip files,
and produces summary / table / failure_modes / shadow_ml / validation artifacts.

Usage (from repo root):
    cd docs/cloud_pack_v1/scripts
    python analyze_phase5_fc_cloud_profit_target_v1_3.py

STRICT REAL-RUN POLICY: financial metrics are extracted exclusively from
backtest zip-primary JSON files.  Any run without a readable zip triggers
fail_state = NON_AUDITABLE_METRICS_SOURCE and halts analysis.
"""

import csv
import json
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST = Path("user_data/phase5_fc_cloud_profit_target_v1_3_manifest.json")
EXECUTION = Path("user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_execution_results.json")
RESULTS_ROOT = Path("user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3")

OUT_SUMMARY = Path("user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_summary.json")
OUT_TABLE = Path("user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_table.csv")
OUT_FAILURE_MODES = Path("user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_failure_modes_top3.json")
OUT_SHADOW = Path("user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_shadow_ml_summary.json")
OUT_DECISION = Path("user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_decision_summary.json")
OUT_VALIDATION = Path("user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_validation_report.json")
OUT_SOT_PATCH = Path("user_data/backtest_results/phase5_fc_cloud_profit_target_v1_3_sot_update_patch.json")

TARGET_WINDOWS = ["LW6M", "LW12M"]
COMPARE_VARIANTS = ["PROFIT_TARGET_A", "PROFIT_TARGET_B", "PROFIT_TARGET_C"]

HARD_PF_MIN = 1.10
HARD_DD_MAX_PCT = 3.0
HARD_PROFIT_MIN = 2.0
HARD_PROFIT_MAX = 2.5
MIN_PASSING_VARIANTS = 2
REQUIRED_VALID_RATIO = 1.0

# Normalized manifest keys required in experiments[].overrides
NORMALIZED_KEYS = [
    "trend_ema_fast", "trend_ema_slow",
    "rsi_long_entry_max", "rsi_short_entry_min",
    "exit_rsi_long_min", "exit_rsi_short_max", "exit_delay_bars",
    "vol_shock_kill_enable", "negative_exit_cut_enable", "shadow_ml_enable",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def extract_metrics_from_zip(
    run_row: dict[str, Any],
    results_root: Path,
) -> dict[str, Any]:
    """
    Extract economic metrics exclusively from the backtest zip primary JSON.
    Returns economic_metrics_valid=False if the zip is absent or unreadable.
    """
    run = str(run_row.get("run", ""))
    zip_name = str(run_row.get("backtest_file", ""))
    empty: dict[str, Any] = {
        "economic_metrics_valid": False,
        "metric_source": "zip_primary",
        "backtest_file": zip_name,
        "zip_sha256": run_row.get("zip_sha256", ""),
        "profit_total_pct": 0.0,
        "monthly_profit_norm_pct": 0.0,
        "pf": 0.0,
        "max_drawdown_pct": 0.0,
    }

    if not run or not zip_name:
        return empty

    zip_path = results_root / run / zip_name
    if not zip_path.exists():
        return empty

    try:
        with zipfile.ZipFile(zip_path, "r") as zh:
            json_members = [
                n for n in zh.namelist()
                if n.endswith(".json") and "_config" not in n
            ]
            if not json_members:
                return empty

            payload = json.loads(zh.read(json_members[0]))
            comparison = payload.get("strategy_comparison")
            if not isinstance(comparison, list) or not comparison:
                return empty

            strategy_name = str(run_row.get("strategy", ""))
            metric_row = None
            for item in comparison:
                if str(item.get("key", "")) == strategy_name:
                    metric_row = item
                    break
            if metric_row is None:
                metric_row = comparison[0]

            profit_total_pct = to_float(metric_row.get("profit_total_pct"))
            duration_days = to_float(metric_row.get("backtest_days", 30.0), 30.0)
            monthly_norm = (profit_total_pct / max(duration_days, 1.0)) * 30.0

            max_dd = to_float(metric_row.get("max_drawdown_account"), 0.0) * 100.0

            return {
                "economic_metrics_valid": True,
                "metric_source": "zip_primary",
                "backtest_file": zip_name,
                "zip_sha256": run_row.get("zip_sha256", ""),
                "profit_total_pct": profit_total_pct,
                "monthly_profit_norm_pct": round(monthly_norm, 4),
                "pf": to_float(metric_row.get("profit_factor")),
                "max_drawdown_pct": round(max_dd, 4),
            }
    except Exception:
        return empty


def check_traceability(manifest: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (ok, missing_fields) for the traceability contract."""
    missing: list[str] = []
    for exp in manifest.get("experiments", []):
        overrides = exp.get("overrides")
        if not overrides or overrides == {}:
            missing.append(f"{exp.get('run', '?')}: overrides empty")
        for key in NORMALIZED_KEYS:
            if key not in exp:
                missing.append(f"{exp.get('run', '?')}: missing normalized key {key}")
    return len(missing) == 0, missing


def validate_applied_overrides(results: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Verify execution_results[].applied_overrides populated and parseable."""
    issues: list[str] = []
    for row in results:
        ao = row.get("applied_overrides")
        if not ao or ao == {} or ao == "{}":
            issues.append(f"{row.get('run', '?')}: applied_overrides empty")
        if isinstance(ao, str):
            try:
                parsed = json.loads(ao)
                if not parsed:
                    issues.append(f"{row.get('run', '?')}: applied_overrides is empty JSON")
            except json.JSONDecodeError:
                issues.append(f"{row.get('run', '?')}: applied_overrides not valid JSON")
    return len(issues) == 0, issues


def scan_for_synthetic_patterns(scripts_dir: Path) -> tuple[bool, list[str]]:
    """
    Scan all .py files in scripts_dir for forbidden synthetic-metric patterns.
    Returns (clean, findings).
    """
    forbidden = ["METRICS =", "synthetic", "mock metrics", "_inline_profit_total_pct as source"]
    findings: list[str] = []
    for py_file in scripts_dir.glob("*.py"):
        text = py_file.read_text(encoding="utf-8", errors="replace")
        for pattern in forbidden:
            if pattern in text:
                findings.append(f"{py_file.name}: contains '{pattern}'")
    return len(findings) == 0, findings


def main() -> None:  # noqa: PLR0912, PLR0915 – intentionally comprehensive
    scripts_dir = Path(__file__).parent

    # ------------------------------------------------------------------ #
    # Pre-flight                                                          #
    # ------------------------------------------------------------------ #
    index_path = scripts_dir.parent / "index.json"
    index_ok = index_path.exists()

    strategy_dep = scripts_dir.parent / "strategies" / "BTCFuturesCalmLS.py"
    stake_dep = scripts_dir / "stake_guard_tools.py"
    deps_ok = strategy_dep.exists() and stake_dep.exists()
    missing_deps = []
    if not strategy_dep.exists():
        missing_deps.append(str(strategy_dep))
    if not stake_dep.exists():
        missing_deps.append(str(stake_dep))

    synthetic_clean, synthetic_findings = scan_for_synthetic_patterns(scripts_dir)

    # ------------------------------------------------------------------ #
    # Load inputs                                                         #
    # ------------------------------------------------------------------ #
    manifest = read_json(MANIFEST)
    execution = read_json(EXECUTION)

    meta_by_run = {item["run"]: item for item in manifest.get("experiments", [])}
    results = execution.get("results", [])

    traceability_ok, missing_traceability = check_traceability(manifest)
    applied_ok, applied_issues = validate_applied_overrides(results)

    # ------------------------------------------------------------------ #
    # Extract metrics – STRICT: zip_primary only                         #
    # ------------------------------------------------------------------ #
    non_auditable_runs: list[str] = []
    eval_rows: list[dict[str, Any]] = []
    per_run_audit: list[dict[str, Any]] = []

    for row in results:
        run = str(row.get("run", ""))
        meta = meta_by_run.get(run)
        if not meta:
            continue

        zip_metrics = extract_metrics_from_zip(row, RESULTS_ROOT)
        audit_ok = (
            zip_metrics["economic_metrics_valid"]
            and zip_metrics.get("metric_source") == "zip_primary"
            and bool(zip_metrics.get("backtest_file"))
        )
        if not audit_ok:
            non_auditable_runs.append(run)

        per_run_audit.append({
            "run": run,
            "backtest_file": zip_metrics.get("backtest_file", ""),
            "zip_sha256": zip_metrics.get("zip_sha256", ""),
            "metric_source": zip_metrics.get("metric_source", "unknown"),
            "audit_ok": audit_ok,
        })

        eval_rows.append(
            {
                "run": run,
                "window_id": str(meta.get("window_id", "")),
                "variant": str(meta.get("variant", "")),
                "is_control": bool(meta.get("is_control", False)),
                "economic_metrics_valid": zip_metrics["economic_metrics_valid"],
                "backtest_file": zip_metrics.get("backtest_file", ""),
                "zip_sha256": zip_metrics.get("zip_sha256", ""),
                "metric_source": zip_metrics.get("metric_source", "unknown"),
                "profit_total_pct": zip_metrics["profit_total_pct"],
                "monthly_profit_norm_pct": zip_metrics["monthly_profit_norm_pct"],
                "pf": zip_metrics["pf"],
                "max_drawdown_pct": zip_metrics["max_drawdown_pct"],
                **{k: meta.get(k) for k in NORMALIZED_KEYS},
            }
        )

    # ------------------------------------------------------------------ #
    # Metrics-source audit gate                                           #
    # ------------------------------------------------------------------ #
    if non_auditable_runs and results:
        metrics_source_audit_status = "FAIL"
        fail_state = "NON_AUDITABLE_METRICS_SOURCE"
    elif not results:
        metrics_source_audit_status = "FAIL_NO_BACKTEST_RUNS"
        fail_state = "NON_AUDITABLE_METRICS_SOURCE"
    else:
        metrics_source_audit_status = "PASS"
        fail_state = None

    total_runs = len(results)
    valid_count = sum(1 for r in eval_rows if r["economic_metrics_valid"])
    valid_ratio = (valid_count / total_runs) if total_runs > 0 else 0.0

    if valid_ratio < REQUIRED_VALID_RATIO and total_runs > 0:
        fail_state = fail_state or "INSUFFICIENT_VALID_ECONOMICS"

    # ------------------------------------------------------------------ #
    # Per-window delta analysis vs CTRL                                   #
    # ------------------------------------------------------------------ #
    per_window: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in eval_rows:
        per_window[row["window_id"]][row["variant"]] = row

    table_rows: list[dict[str, Any]] = []
    failure_modes_top3: dict[str, list[dict[str, Any]]] = {}

    variants_pass_count: dict[str, int] = {v: 0 for v in COMPARE_VARIANTS}

    for window_id in TARGET_WINDOWS:
        bucket = per_window.get(window_id, {})
        ctrl = bucket.get("CTRL")
        scored: list[tuple[float, dict[str, Any]]] = []

        for variant in COMPARE_VARIANTS:
            cand = bucket.get(variant)
            if not ctrl or not cand:
                continue

            delta_profit = cand["monthly_profit_norm_pct"] - ctrl["monthly_profit_norm_pct"]
            delta_pf = cand["pf"] - ctrl["pf"]
            delta_dd = cand["max_drawdown_pct"] - ctrl["max_drawdown_pct"]

            in_profit_range = HARD_PROFIT_MIN <= cand["monthly_profit_norm_pct"] <= HARD_PROFIT_MAX
            pf_ok = cand["pf"] >= HARD_PF_MIN
            dd_ok = cand["max_drawdown_pct"] <= HARD_DD_MAX_PCT
            passes = in_profit_range and pf_ok and dd_ok and cand["economic_metrics_valid"]
            if passes:
                variants_pass_count[variant] = variants_pass_count.get(variant, 0) + 1

            reasons: list[str] = []
            if not cand["economic_metrics_valid"]:
                reasons.append("no_zip_primary_data")
            if not in_profit_range:
                reasons.append(
                    f"profit_out_of_range({cand['monthly_profit_norm_pct']:.3f} "
                    f"not in [{HARD_PROFIT_MIN},{HARD_PROFIT_MAX}])"
                )
            if not pf_ok:
                reasons.append(f"pf_below_min({cand['pf']:.3f}<{HARD_PF_MIN})")
            if not dd_ok:
                reasons.append(
                    f"drawdown_exceeded({cand['max_drawdown_pct']:.2f}%>{HARD_DD_MAX_PCT}%)"
                )

            severity = 0.0
            if not cand["economic_metrics_valid"]:
                severity += 1000.0
            if not in_profit_range:
                severity += 200.0
            if not pf_ok:
                severity += 100.0
            if not dd_ok:
                severity += 50.0

            scored.append(
                (
                    severity,
                    {
                        "variant": variant,
                        "severity_score": round(severity, 4),
                        "monthly_profit_norm_pct": cand["monthly_profit_norm_pct"],
                        "pf": cand["pf"],
                        "max_drawdown_pct": cand["max_drawdown_pct"],
                        "passes_all_hard_constraints": passes,
                        "reasons": reasons,
                    },
                )
            )

            table_rows.append(
                {
                    "window_id": window_id,
                    "variant": variant,
                    "economic_metrics_valid": cand["economic_metrics_valid"],
                    "metric_source": cand.get("metric_source", ""),
                    "backtest_file": cand.get("backtest_file", ""),
                    "zip_sha256": cand.get("zip_sha256", ""),
                    "profit_total_pct": cand["profit_total_pct"],
                    "monthly_profit_norm_pct": cand["monthly_profit_norm_pct"],
                    "ctrl_monthly_profit_norm_pct": ctrl["monthly_profit_norm_pct"],
                    "delta_profit_vs_ctrl": round(delta_profit, 6),
                    "pf": cand["pf"],
                    "ctrl_pf": ctrl["pf"],
                    "delta_pf_vs_ctrl": round(delta_pf, 6),
                    "max_drawdown_pct": cand["max_drawdown_pct"],
                    "ctrl_max_drawdown_pct": ctrl["max_drawdown_pct"],
                    "delta_dd_vs_ctrl": round(delta_dd, 6),
                    "in_profit_range": in_profit_range,
                    "pf_ok": pf_ok,
                    "dd_ok": dd_ok,
                    "passes_all": passes,
                    **{f"param_{k}": cand.get(k) for k in NORMALIZED_KEYS},
                }
            )

        scored.sort(key=lambda item: item[0], reverse=True)
        failure_modes_top3[window_id] = [item[1] for item in scored[:3]]

    # ------------------------------------------------------------------ #
    # Gate evaluation                                                     #
    # ------------------------------------------------------------------ #
    variants_pass_both = [v for v, cnt in variants_pass_count.items() if cnt >= len(TARGET_WINDOWS)]
    enough_passing = len(variants_pass_both) >= MIN_PASSING_VARIANTS

    if fail_state is None and not enough_passing and total_runs > 0:
        fail_state = "INSUFFICIENT_PASSING_VARIANTS"

    all_constraints_pass = (
        fail_state is None
        and traceability_ok
        and metrics_source_audit_status == "PASS"
        and enough_passing
    )
    status = "PASS_CLEAR" if all_constraints_pass else "FAIL_CLEAR"

    missing_traceability_fields = missing_traceability + applied_issues

    # ------------------------------------------------------------------ #
    # Shadow ML summary                                                   #
    # ------------------------------------------------------------------ #
    shadow_per_run = []
    for row in eval_rows:
        shadow_per_run.append(
            {
                "run": row["run"],
                "window_id": row["window_id"],
                "variant": row["variant"],
                "shadow_ml_enabled": bool(row.get("shadow_ml_enable")),
                "economic_metrics_valid": row["economic_metrics_valid"],
            }
        )

    shadow_summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "stage": "PROFIT_TARGET_V1_3",
        "shadow_ml_mode": "telemetry-only",
        "runs_total": len(eval_rows),
        "runs_with_shadow_enabled": sum(1 for r in eval_rows if r.get("shadow_ml_enable")),
        "per_run": shadow_per_run,
    }

    # ------------------------------------------------------------------ #
    # Validation report                                                   #
    # ------------------------------------------------------------------ #
    validation_report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "stage": "PROFIT_TARGET_V1_3",
        "index_integrity_status": "PASS" if index_ok else "FAIL_INDEX_MISSING",
        "runtime_deps_status": "PASS" if deps_ok else f"FAIL_MISSING_DEPS:{missing_deps}",
        "synthetic_metric_scan_status": "PASS" if synthetic_clean else f"FAIL:{synthetic_findings}",
        "metrics_source_audit_status": metrics_source_audit_status,
        "traceability_status": "PASS" if traceability_ok else "FAIL",
        "applied_overrides_status": "PASS" if applied_ok else "FAIL",
        "per_run_audit": per_run_audit,
        "non_auditable_runs": non_auditable_runs,
        "missing_traceability_fields": missing_traceability_fields,
    }

    # ------------------------------------------------------------------ #
    # Summary                                                             #
    # ------------------------------------------------------------------ #
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "stage": "PROFIT_TARGET_V1_3",
        "status": status,
        "fail_state": fail_state,
        "gate": {
            "status": "PASS" if all_constraints_pass else "FAIL",
            "rule": (
                f"monthly_profit_norm_pct in [{HARD_PROFIT_MIN},{HARD_PROFIT_MAX}] "
                f"AND pf>={HARD_PF_MIN} AND dd<={HARD_DD_MAX_PCT}% "
                f"on BOTH windows, >=2 variants pass"
            ),
            "economic_valid_ratio": round(valid_ratio, 6),
            "required_economic_valid_ratio": REQUIRED_VALID_RATIO,
            "metrics_source_audit": metrics_source_audit_status,
            "variants_pass_both_windows": variants_pass_both,
            "enough_passing_variants": enough_passing,
            "traceability_ok": traceability_ok,
        },
        "total_runs": total_runs,
        "valid_runs": valid_count,
        "failure_modes_top3": failure_modes_top3,
        "non_auditable_runs": non_auditable_runs,
        "missing_traceability_fields": missing_traceability_fields,
        "recommended_next_step": (
            "Execute generate_phase5_fc_cloud_profit_target_v1_3.py, "
            "run backtests for all experiments, then re-run this analyzer."
            if fail_state == "NON_AUDITABLE_METRICS_SOURCE"
            else "Review failure_modes_top3 and adjust variant parameters."
        ),
    }

    # ------------------------------------------------------------------ #
    # Decision summary                                                    #
    # ------------------------------------------------------------------ #
    decision_summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "stage": "PROFIT_TARGET_V1_3",
        "decision": status,
        "fail_state": fail_state,
        "failure_modes_top3": failure_modes_top3,
        "missing_traceability_fields": missing_traceability_fields,
        "non_auditable_runs": non_auditable_runs,
        "recommended_next_step": summary["recommended_next_step"],
    }

    # ------------------------------------------------------------------ #
    # SOT update patch                                                    #
    # ------------------------------------------------------------------ #
    sot_patch = {
        "generated_at": datetime.now(UTC).isoformat(),
        "target_file": "docs/cloud_pack_v1/sot/poziom2_handoff_status.json",
        "effective_state": "cloud_profit_target_v1_3_realrun",
        "stage": "PROFIT_TARGET_V1_3",
        "decision": status,
        "fail_state": fail_state,
        "apply_if": "PASS_CLEAR",
        "patch_instructions": (
            "Set sot.effective_state='cloud_profit_target_v1_3_realrun' and "
            "append this run summary to sot.run_history after PASS_CLEAR."
        ),
    }

    # ------------------------------------------------------------------ #
    # Write outputs                                                       #
    # ------------------------------------------------------------------ #
    OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)

    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FAILURE_MODES.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "stage": "PROFIT_TARGET_V1_3",
                "target_windows": TARGET_WINDOWS,
                "failure_modes_top3": failure_modes_top3,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    OUT_SHADOW.write_text(json.dumps(shadow_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_DECISION.write_text(json.dumps(decision_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_VALIDATION.write_text(json.dumps(validation_report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_SOT_PATCH.write_text(json.dumps(sot_patch, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "window_id", "variant",
        "economic_metrics_valid", "metric_source", "backtest_file", "zip_sha256",
        "profit_total_pct", "monthly_profit_norm_pct", "ctrl_monthly_profit_norm_pct",
        "delta_profit_vs_ctrl",
        "pf", "ctrl_pf", "delta_pf_vs_ctrl",
        "max_drawdown_pct", "ctrl_max_drawdown_pct", "delta_dd_vs_ctrl",
        "in_profit_range", "pf_ok", "dd_ok", "passes_all",
        *[f"param_{k}" for k in NORMALIZED_KEYS],
    ]
    with OUT_TABLE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in table_rows:
            writer.writerow(row)

    print(f"Status: {summary['status']}")
    print(f"Fail state: {summary['fail_state']}")
    print(f"Metrics source audit: {metrics_source_audit_status}")
    print(f"Total runs: {total_runs}  Valid: {valid_count}")
    print(f"Variants passing both windows: {variants_pass_both}")


if __name__ == "__main__":
    main()
