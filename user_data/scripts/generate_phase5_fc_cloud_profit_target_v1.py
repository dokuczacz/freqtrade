"""
generate_phase5_fc_cloud_profit_target_v1.py
============================================
Generates bounded parameter variants for the BTCFuturesCalmLS strategy
targeting monthly_profit_norm_pct in [2.0, 2.5]%.

Optimisation policy:
  - Bounded optimisation only (no broad random sweep)
  - Primary axis: EXIT_DELAY (small deltas around current best family)
  - Baseline A is the control run (index 0) and is NEVER modified
  - Shadow ML is telemetry-only (weight always 0.0 in all variants)
  - Worker usage capped at 75% CPU

Usage:
    python user_data/scripts/generate_phase5_fc_cloud_profit_target_v1.py

Outputs (written to user_data/backtest_results/phase5_fc_cloud_profit_target_v1/):
    phase5_fc_cloud_profit_target_v1_manifest.json
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT   = Path(__file__).resolve().parents[2]
OUTPUT_DIR  = REPO_ROOT / "user_data" / "backtest_results" / \
              "phase5_fc_cloud_profit_target_v1"
MANIFEST_PATH = OUTPUT_DIR / "phase5_fc_cloud_profit_target_v1_manifest.json"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Baseline A (Anchor — must not be overwritten)
# ---------------------------------------------------------------------------
BASELINE_A = {
    "run_id":              "baseline_A",
    "is_control":          True,
    "exit_delay":          2,
    "atr_calm_threshold":  0.010,
    "rsi_entry_long":      40,
    "rsi_entry_short":     60,
    "shadow_ml_weight":    0.0,
    "description":         "Baseline A — anchor (EXIT_DELAY=2)",
}

# ---------------------------------------------------------------------------
# Bounded variant grid (EXIT_DELAY axis only, small deltas)
# Near-threshold deltas: ±1 from baseline (0–8 valid range)
# ---------------------------------------------------------------------------
EXIT_DELAY_VARIANTS = [1, 2, 3, 4, 5]   # bounded around baseline default=2

VARIANTS: list[dict] = [BASELINE_A]

for ed in EXIT_DELAY_VARIANTS:
    if ed == BASELINE_A["exit_delay"]:
        continue  # baseline_A already covers exit_delay=2
    VARIANTS.append({
        "run_id":             f"variant_ed{ed}",
        "is_control":         False,
        "exit_delay":         ed,
        "atr_calm_threshold": 0.010,   # held fixed for bounded sweep
        "rsi_entry_long":     40,
        "rsi_entry_short":    60,
        "shadow_ml_weight":   0.0,     # telemetry-only, never affects decisions
        "description":        f"EXIT_DELAY={ed} variant",
    })

# ---------------------------------------------------------------------------
# Evaluation windows
# ---------------------------------------------------------------------------
WINDOWS = {
    "LW6M":  {"timerange": "20230601-20231201", "label": "6-month window"},
    "LW12M": {"timerange": "20230101-20231231", "label": "12-month window"},
}

# ---------------------------------------------------------------------------
# Hard constraints (gate values used by the analyze script)
# ---------------------------------------------------------------------------
HARD_CONSTRAINTS = {
    "pf_min":                          1.10,
    "max_drawdown_pct_max":            3.0,
    "tail_worsening_count_guard":      0,
    "economic_metrics_valid_ratio":    1.0,
    "monthly_profit_norm_pct_min":     2.0,
    "monthly_profit_norm_pct_max":     2.5,
    "require_both_windows":            True,
}

# ---------------------------------------------------------------------------
# Worker / infra policy
# ---------------------------------------------------------------------------
EXECUTION_POLICY = {
    "max_cpu_pct":      75,
    "fallback_workers": 1,
    "rerun_on_infra_fail": True,
    "stop_on_invalid_economics": True,
}

# ---------------------------------------------------------------------------
# Build manifest
# ---------------------------------------------------------------------------
manifest = {
    "task_id":          "phase5_fc_cloud_profit_target_v1",
    "strategy":         "BTCFuturesCalmLS",
    "generated_at":     datetime.now(timezone.utc).isoformat(),
    "optimisation_axis": "EXIT_DELAY",
    "shadow_ml_mode":   "telemetry_only",
    "hard_constraints": HARD_CONSTRAINTS,
    "execution_policy": EXECUTION_POLICY,
    "windows":          WINDOWS,
    "variants":         VARIANTS,
    "total_runs":       len(VARIANTS) * len(WINDOWS),
    "non_control_runs": (len(VARIANTS) - 1) * len(WINDOWS),
}

with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2)

print(f"[generate] Manifest written → {MANIFEST_PATH}")
print(f"[generate] Total variants : {len(VARIANTS)}  "
      f"(including 1 control / baseline A)")
print(f"[generate] Evaluation windows : {list(WINDOWS.keys())}")
print(f"[generate] Total backtest runs : {manifest['total_runs']}")

if __name__ == "__main__":
    # Validate JSON round-trip (dual JSON validation requirement)
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded["task_id"] == "phase5_fc_cloud_profit_target_v1", \
        "Manifest task_id mismatch after round-trip"
    print("[generate] Dual JSON validation PASS")
    sys.exit(0)
