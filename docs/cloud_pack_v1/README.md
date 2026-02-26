# cloud_pack_v1

Decision-grade cloud input pack for minimal V5-V7 tail-guard continuation.

## Expected Cloud Entrypoint
Use `docs/cloud_pack_v1/index.json` as the single source of truth for payload contents and integrity.

## Required Files Checklist
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

## Preflight Checks
1. Validate `index.json` and SOT JSON parse.
2. Recompute SHA256 for every `items[].pack_path` and match `items[].sha256`.
3. Confirm presence of execution results and latest `v7_summary` before cloud run.
