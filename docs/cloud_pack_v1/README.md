# cloud_pack_v1

Decision-grade cloud input pack for V5-V7 tail-guard continuation and
V1.1 profit-target search.

## Expected Cloud Entrypoint
Use `docs/cloud_pack_v1/index.json` as the single source of truth for payload contents and integrity.

## Runtime Dependencies (required for PRE-FLIGHT)
- `strategies/BTCFuturesCalmLS.py`
- `scripts/stake_guard_tools.py`

## Required Files Checklist

### Tail-guard phases (V5–V7)
- `artifacts/phase5_fc_anchor_tail2x3_v5_*`
- `artifacts/phase5_fc_anchor_tail2x3_v6_*`
- `artifacts/phase5_fc_anchor_tail2x3_v7_*`
- `scripts/generate_phase5_fc_anchor_tail2x3_v5.py`
- `scripts/analyze_phase5_fc_anchor_tail2x3_v5.py`
- `scripts/analyze_phase5_fc_shadow_ml_telemetry_v5.py`
- `scripts/generate_phase5_fc_anchor_tail2x3_v6.py`
- `scripts/analyze_phase5_fc_anchor_tail2x3_v6.py`
- `scripts/analyze_phase5_fc_shadow_ml_telemetry_v6.py`
- `scripts/generate_phase5_fc_anchor_tail2x3_v7.py`
- `scripts/analyze_phase5_fc_anchor_tail2x3_v7.py`
- `sot/poziom2_handoff_status.json`

### Profit-target phase (V1.1)
- `artifacts/phase5_fc_cloud_profit_target_v1_1_manifest.json`
- `artifacts/phase5_fc_cloud_profit_target_v1_1_execution_results.json`
- `artifacts/phase5_fc_cloud_profit_target_v1_1_summary.json`
- `artifacts/phase5_fc_cloud_profit_target_v1_1_decision_summary.json`
- `artifacts/phase5_fc_cloud_profit_target_v1_1_table.csv`
- `artifacts/phase5_fc_cloud_profit_target_v1_1_failure_modes_top3.json`
- `artifacts/phase5_fc_cloud_profit_target_v1_1_shadow_ml_summary.json`
- `artifacts/phase5_fc_cloud_profit_target_v1_1_sot_update_patch.json`
- `artifacts/phase5_fc_cloud_profit_target_v1_1_validation_report.json`
- `scripts/generate_phase5_fc_cloud_profit_target_v1_1.py`
- `scripts/analyze_phase5_fc_cloud_profit_target_v1_1.py`
- `scripts/analyze_phase5_fc_shadow_ml_telemetry_v1_1.py`

## V1.1 Phase Summary
**Task:** `phase5_fc_cloud_profit_target_v1_1` (FC-only, GitHub-pack mode)

**Goal:** Find BTCFuturesCalmLS variants with `monthly_profit_norm_pct` in [2.0, 2.5]
on LW6M and LW12M long windows.

**Hard constraints:**
- PF >= 1.10
- max_drawdown_pct <= 3.0
- tail_worsening_count_guard == 0
- economic_metrics_valid_ratio == 1.0
- Pass on both LW6M and LW12M
- At least 2 non-control runs pass economics

**Outcome:** `PASS_CLEAR` – `PROFIT_TARGET_REACHED_READY_FOR_DEPLOYMENT_GATE`
- `PROFIT_TUNE_A` and `PROFIT_TUNE_B` pass all hard constraints on both windows.

**PRIMARY KPI priority:** `delta_profit_vs_ctrl` → `delta_tail_vs_ctrl` → `delta_pf_vs_ctrl` → activation counts.

## Preflight Checks
1. Validate `index.json` and SOT JSON parse.
2. Recompute SHA256 for every `items[].pack_path` and match `items[].sha256`.
3. Assert `strategies/BTCFuturesCalmLS.py` and `scripts/stake_guard_tools.py` exist.
4. Confirm presence of execution results and latest summary before cloud run.
