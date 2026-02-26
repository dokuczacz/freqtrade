import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXECUTION = Path("user_data/backtest_results/phase5_fc_anchor_tail2x3_v5_execution_results.json")
OUT_SUMMARY = Path("user_data/backtest_results/phase5_fc_anchor_tail2x3_v5_shadow_ml_summary.json")
TARGET_WINDOWS = {"W002", "W012"}


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
    if d <= 0:
        return 0.0
    return n / d


def get_nested_int(obj: dict[str, Any], key: str) -> int:
    return to_int(obj.get(key, 0), 0)


def parse_run_identity(run: str) -> tuple[str, str]:
    parts = run.split("-")
    if len(parts) < 5:
        return "", ""
    window_id = ""
    variant = ""
    for idx, token in enumerate(parts):
        if token.startswith("W") and len(token) >= 4 and token[1:].isdigit():
            window_id = token
            if idx + 1 < len(parts):
                variant = "-".join(parts[idx + 1 :])
            break
    return window_id, variant


def bucket_label(score: float) -> str:
    s = max(0.0, min(1.0, score))
    low = math.floor(s * 10) / 10
    high = min(1.0, low + 0.1)
    return f"{low:.1f}-{high:.1f}"


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

    score_hist = defaultdict(int)

    disagreement = {window: {"count": 0, "examples": []} for window in sorted(TARGET_WINDOWS)}

    per_run = []

    for row in rows:
        run = str(row.get("run", ""))
        if "TAIL2x3V5" not in run:
            continue
        runs_total += 1

        window_id, variant = parse_run_identity(run)
        fc_counts = row.get("fc_entry_trace_counts") or {}
        preconfirm_counts = row.get("fc_preconfirm_counts") or {}
        events = row.get("fc_entry_trace_sample") or []

        shadow_long_count = get_nested_int(fc_counts, "shadow_ml_long_count")
        shadow_short_count = get_nested_int(fc_counts, "shadow_ml_short_count")
        overlap_long_count = get_nested_int(fc_counts, "shadow_ml_overlap_long_final_count")
        overlap_short_count = get_nested_int(fc_counts, "shadow_ml_overlap_short_final_count")

        intent_count = get_nested_int(preconfirm_counts, "entry_attempt_intent_count")
        emitted_count = get_nested_int(preconfirm_counts, "entry_emitted_count")
        final_long_count = get_nested_int(preconfirm_counts, "entry_long_count")
        final_short_count = get_nested_int(preconfirm_counts, "entry_short_count")
        emitted_denominator = emitted_count if emitted_count > 0 else (final_long_count + final_short_count)

        shadow_enabled = False
        run_disagreement_count = 0

        for event in events:
            if not isinstance(event, dict):
                continue
            if bool(event.get("shadow_ml_enabled", False)):
                shadow_enabled = True
            long_score = to_float(event.get("shadow_ml_long_score", 0.0), 0.0)
            short_score = to_float(event.get("shadow_ml_short_score", 0.0), 0.0)
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

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "stage": "ANCHOR_TAIL2x3_V5",
        "runs_total": runs_total,
        "runs_with_shadow_enabled": runs_with_shadow_enabled,
        "shadow_enabled_coverage": round(safe_div(runs_with_shadow_enabled, runs_total), 6),
        "coverage_denominator": "entry_emitted_count_or_entry_long_plus_entry_short",
        "shadow_coverage_long": round(safe_div(sum_shadow_long_pick, sum_emitted), 6),
        "shadow_coverage_short": round(safe_div(sum_shadow_short_pick, sum_emitted), 6),
        "shadow_overlap_final_long": round(safe_div(sum_overlap_long, sum_final_long), 6),
        "shadow_overlap_final_short": round(safe_div(sum_overlap_short, sum_final_short), 6),
        "shadow_disagreement_on_fail_windows": disagreement,
        "score_histogram_bins": {k: score_hist[k] for k in sorted(score_hist.keys())},
        "per_run": per_run,
    }

    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Shadow runs: {runs_with_shadow_enabled}/{runs_total}")
    print(f"Output: {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
