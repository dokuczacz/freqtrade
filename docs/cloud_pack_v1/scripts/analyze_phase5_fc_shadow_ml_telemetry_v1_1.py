"""Analyse shadow ML telemetry for phase5_fc_cloud_profit_target_v1_1.

Coverage denominator priority:
  primary:  entry_emitted_count
  fallback: entry_long_count + entry_short_count (when emitted == 0)

Reports:
  - coverage with denominator
  - overlap / disagreement per window
  - score bins + short interpretation
"""

import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXECUTION = Path(
    "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_execution_results.json"
)
OUT_SUMMARY = Path(
    "user_data/backtest_results/phase5_fc_cloud_profit_target_v1_1_shadow_ml_summary.json"
)

STAGE = "CLOUD_PROFIT_TARGET_V1_1"
TARGET_WINDOWS = {"LW6M", "LW12M"}
RUN_TAG = "FC-PROFIT-V1_1"


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        if not math.isfinite(v):
            return default
        return v
    except Exception:
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def safe_div(n: float, d: float) -> float:
    return n / d if d > 0 else 0.0


def bucket_label(score: float) -> str:
    s = max(0.0, min(1.0, score))
    low = math.floor(s * 10) / 10
    high = min(1.0, low + 0.1)
    return f"{low:.1f}-{high:.1f}"


def parse_run_identity(run: str) -> tuple[str, str]:
    """Return (window_id, variant) from a run string like FC-PROFIT-V1_1-LW6M-CTRL."""
    parts = run.split("-")
    # Find window token (starts with LW or W followed by digits/letters)
    window_id = ""
    variant = ""
    for idx, token in enumerate(parts):
        if token.startswith("LW") or (token.startswith("W") and len(token) >= 4 and token[1:].isdigit()):
            window_id = token
            if idx + 1 < len(parts):
                variant = "-".join(parts[idx + 1:])
            break
    return window_id, variant


def interpret_score_bins(bins: dict[str, int]) -> str:
    total = sum(bins.values())
    if total == 0:
        return "no_data"
    high = sum(v for k, v in bins.items() if float(k.split("-")[0]) >= 0.7)
    low = sum(v for k, v in bins.items() if float(k.split("-")[0]) < 0.4)
    if high / total > 0.5:
        return "score_skewed_high: shadow_ml_agrees_with_majority_of_entries"
    if low / total > 0.5:
        return "score_skewed_low: shadow_ml_tends_to_disagree"
    return "score_balanced: shadow_ml_has_mixed_alignment"


def main() -> None:
    execution = json.loads(EXECUTION.read_text(encoding="utf-8"))
    rows = execution.get("results", []) if isinstance(execution, dict) else []

    runs_total = 0
    runs_with_shadow_enabled = 0

    sum_shadow_long_pick = 0
    sum_shadow_short_pick = 0
    sum_overlap_long = 0
    sum_overlap_short = 0
    sum_emitted = 0
    sum_final_long = 0
    sum_final_short = 0

    score_hist: dict[str, int] = defaultdict(int)
    disagreement: dict[str, dict[str, Any]] = {
        w: {"count": 0, "examples": []} for w in sorted(TARGET_WINDOWS)
    }
    per_run: list[dict[str, Any]] = []

    for row in rows:
        run = str(row.get("run", ""))
        if RUN_TAG not in run:
            continue
        runs_total += 1

        window_id, variant = parse_run_identity(run)
        fc_counts = row.get("fc_entry_trace_counts") or {}
        preconfirm_counts = row.get("fc_preconfirm_counts") or {}
        events = row.get("fc_entry_trace_sample") or []

        shadow_long_count = to_int(fc_counts.get("shadow_ml_long_count", 0))
        shadow_short_count = to_int(fc_counts.get("shadow_ml_short_count", 0))
        overlap_long_count = to_int(fc_counts.get("shadow_ml_overlap_long_final_count", 0))
        overlap_short_count = to_int(fc_counts.get("shadow_ml_overlap_short_final_count", 0))

        emitted_count = to_int(preconfirm_counts.get("entry_emitted_count", 0))
        final_long_count = to_int(preconfirm_counts.get("entry_long_count", 0))
        final_short_count = to_int(preconfirm_counts.get("entry_short_count", 0))
        intent_count = to_int(preconfirm_counts.get("entry_attempt_intent_count", 0))

        # Coverage denominator: emitted first, fallback to long+short
        emitted_denominator = (
            emitted_count if emitted_count > 0 else (final_long_count + final_short_count)
        )

        shadow_enabled = False
        run_disagreement_count = 0

        for event in events:
            if not isinstance(event, dict):
                continue
            if bool(event.get("shadow_ml_enabled", False)):
                shadow_enabled = True
            long_score = to_float(event.get("shadow_ml_long_score", 0.0))
            short_score = to_float(event.get("shadow_ml_short_score", 0.0))
            score_hist[bucket_label(long_score)] += 1
            score_hist[bucket_label(short_score)] += 1

            side = str(event.get("side", ""))
            pick = str(event.get("shadow_ml_pick", "none"))
            blocked_at = str(event.get("blocked_at", ""))
            if blocked_at in {"final", "intent"}:
                if side == "long" and pick != "long":
                    run_disagreement_count += 1
                if side == "short" and pick != "short":
                    run_disagreement_count += 1

        if shadow_enabled:
            runs_with_shadow_enabled += 1

        sum_shadow_long_pick += shadow_long_count
        sum_shadow_short_pick += shadow_short_count
        sum_overlap_long += overlap_long_count
        sum_overlap_short += overlap_short_count
        sum_emitted += emitted_denominator
        sum_final_long += final_long_count
        sum_final_short += final_short_count

        if window_id in TARGET_WINDOWS and run_disagreement_count > 0:
            disagreement[window_id]["count"] += run_disagreement_count
            if len(disagreement[window_id]["examples"]) < 5:
                disagreement[window_id]["examples"].append(
                    {
                        "run": run,
                        "variant": variant,
                        "disagreement_count": run_disagreement_count,
                    }
                )

        per_run.append(
            {
                "run": run,
                "window_id": window_id,
                "variant": variant,
                "shadow_ml_enabled": shadow_enabled,
                "shadow_ml_long_count": shadow_long_count,
                "shadow_ml_short_count": shadow_short_count,
                "shadow_ml_overlap_long_final_count": overlap_long_count,
                "shadow_ml_overlap_short_final_count": overlap_short_count,
                "entry_attempt_intent_count": intent_count,
                "entry_emitted_count": emitted_count,
                "coverage_denominator_emitted": emitted_denominator,
                "entry_long_count": final_long_count,
                "entry_short_count": final_short_count,
            }
        )

    score_bins = {k: score_hist[k] for k in sorted(score_hist.keys())}
    interpretation = interpret_score_bins(score_bins)

    summary: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "stage": STAGE,
        "runs_total": runs_total,
        "runs_with_shadow_enabled": runs_with_shadow_enabled,
        "shadow_enabled_coverage": round(safe_div(runs_with_shadow_enabled, runs_total), 6),
        "coverage_denominator": "entry_emitted_count_or_entry_long_plus_entry_short",
        "shadow_coverage_long": round(safe_div(sum_shadow_long_pick, sum_emitted), 6),
        "shadow_coverage_short": round(safe_div(sum_shadow_short_pick, sum_emitted), 6),
        "shadow_overlap_final_long": round(safe_div(sum_overlap_long, sum_final_long), 6),
        "shadow_overlap_final_short": round(safe_div(sum_overlap_short, sum_final_short), 6),
        "shadow_disagreement_on_target_windows": disagreement,
        "score_histogram_bins": score_bins,
        "score_bins_interpretation": interpretation,
        "per_run": per_run,
    }

    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Shadow runs: {runs_with_shadow_enabled}/{runs_total}")
    print(f"Score interpretation: {interpretation}")
    print(f"Output: {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
