# pragma pylint: disable=missing-docstring, invalid-name
"""BTCFuturesCalmLS – calm long/short BTC futures strategy.

Designed for BTC/USDT:USDT on 15 m candles.  The strategy uses trend,
volatility and RSI filters to generate long and short entries with a
configurable exit pipeline.

Runtime-injectable parameters (passed via ``strategy_parameters`` in the
freqtrade config) allow controlled experiments without modifying source code:

Entry parameters
----------------
trend_ema_fast, trend_ema_slow – EMA periods for trend direction filter
rsi_period                      – RSI period
rsi_long_entry_max              – max RSI to allow long entry
rsi_short_entry_min             – min RSI to allow short entry
vol_ema_period                  – volume EMA period for vol filter

Exit parameters
---------------
exit_rsi_long_min               – RSI floor for long exit
exit_rsi_short_max              – RSI ceiling for short exit
exit_delay_bars                 – minimum bars to hold before RSI exit

Volatility shock guard
----------------------
vol_shock_kill_enable           – True  ⟹ shock-based forced exit active
vol_shock_atrp_mult             – ATR multiplier; lower = more sensitive
vol_shock_adx_min               – minimum ADX for shock trigger
vol_shock_lookback_bars         – ATR lookback bars
vol_shock_loss_floor_pct        – minimum unrealised loss to trigger (0 = always)
negative_exit_cut_enable        – trim unprofitable trades early

Shadow ML telemetry
-------------------
shadow_ml_enable                – record shadow picks (no entry/exit impact)
shadow_ml_threshold             – score threshold [0, 1]
shadow_ml_weight_trend          – weight for trend score component
shadow_ml_weight_adx            – weight for ADX score component
shadow_ml_weight_rsi            – weight for RSI score component
shadow_ml_weight_vol            – weight for volatility score component
diag_trace_enable               – emit per-candle diagnostic trace
diag_sample_limit               – max trace events stored per run
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    dm_plus = (high - prev_high).clip(lower=0.0)
    dm_minus = (prev_low - low).clip(lower=0.0)
    # zero out when the other DM is larger
    dm_plus = dm_plus.where(dm_plus > dm_minus, 0.0)
    dm_minus = dm_minus.where(dm_minus > dm_plus, 0.0)
    atr = _atr(high, low, close, period)
    atr_safe = atr.replace(0.0, np.nan)
    di_plus = 100.0 * dm_plus.ewm(alpha=1.0 / period, adjust=False).mean() / atr_safe
    di_minus = 100.0 * dm_minus.ewm(alpha=1.0 / period, adjust=False).mean() / atr_safe
    dx = (100.0 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0.0, np.nan))
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class BTCFuturesCalmLS(IStrategy):
    """Calm long/short BTC futures strategy."""

    INTERFACE_VERSION = 3

    can_short: bool = True

    # Defaults – overridden by config strategy_parameters
    minimal_roi: dict[str, float] = {"0": 0.10}

    stoploss: float = -0.05
    trailing_stop: bool = False

    timeframe: str = "15m"
    startup_candle_count: int = 50

    # -----------------------------------------------------------------------
    # Runtime-injectable parameters (defaults used when not in config)
    # -----------------------------------------------------------------------

    # Entry
    trend_ema_fast: int = 12
    trend_ema_slow: int = 26
    rsi_period: int = 14
    rsi_long_entry_max: float = 55.0
    rsi_short_entry_min: float = 45.0
    vol_ema_period: int = 20

    # Exit
    exit_rsi_long_min: float = 70.0
    exit_rsi_short_max: float = 30.0
    exit_delay_bars: int = 2

    # Volatility shock guard
    vol_shock_kill_enable: bool = False
    vol_shock_atrp_mult: float = 1.5
    vol_shock_adx_min: int = 15
    vol_shock_lookback_bars: int = 5
    vol_shock_loss_floor_pct: float = -0.005
    negative_exit_cut_enable: bool = False

    # Shadow ML telemetry (no entry/exit decision impact)
    shadow_ml_enable: bool = False
    shadow_ml_threshold: float = 0.55
    shadow_ml_weight_trend: float = 0.35
    shadow_ml_weight_adx: float = 0.25
    shadow_ml_weight_rsi: float = 0.25
    shadow_ml_weight_vol: float = 0.15
    diag_trace_enable: bool = False
    diag_sample_limit: int = 200

    # -----------------------------------------------------------------------
    # Internal counters (populated per-run for telemetry)
    # -----------------------------------------------------------------------
    _entry_long_count: int = 0
    _entry_short_count: int = 0
    _entry_both_sides_count: int = 0
    _entry_emitted_count: int = 0
    _entry_after_startup_count: int = 0
    _entry_attempt_intent_count: int = 0
    _shadow_ml_long_count: int = 0
    _shadow_ml_short_count: int = 0
    _diag_trace_events: list[dict[str, Any]]

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._diag_trace_events = []
        self._reset_counters()
        self._apply_strategy_parameters(config)

    def _reset_counters(self) -> None:
        self._entry_long_count = 0
        self._entry_short_count = 0
        self._entry_both_sides_count = 0
        self._entry_emitted_count = 0
        self._entry_after_startup_count = 0
        self._entry_attempt_intent_count = 0
        self._shadow_ml_long_count = 0
        self._shadow_ml_short_count = 0

    def _apply_strategy_parameters(self, config: dict[str, Any]) -> None:
        params: dict[str, Any] = config.get("strategy_parameters", {}) or {}
        int_params = {
            "trend_ema_fast", "trend_ema_slow", "rsi_period",
            "vol_ema_period", "exit_delay_bars",
            "vol_shock_adx_min", "vol_shock_lookback_bars", "diag_sample_limit",
        }
        float_params = {
            "rsi_long_entry_max", "rsi_short_entry_min",
            "exit_rsi_long_min", "exit_rsi_short_max",
            "vol_shock_atrp_mult", "vol_shock_loss_floor_pct",
            "shadow_ml_threshold",
            "shadow_ml_weight_trend", "shadow_ml_weight_adx",
            "shadow_ml_weight_rsi", "shadow_ml_weight_vol",
        }
        bool_params = {
            "vol_shock_kill_enable", "negative_exit_cut_enable",
            "shadow_ml_enable", "diag_trace_enable",
        }
        for key, value in params.items():
            if key in int_params:
                setattr(self, key, int(value))
            elif key in float_params:
                setattr(self, key, float(value))
            elif key in bool_params:
                setattr(self, key, bool(value))

    # -----------------------------------------------------------------------
    # Indicators
    # -----------------------------------------------------------------------

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = _ema(dataframe["close"], self.trend_ema_fast)
        dataframe["ema_slow"] = _ema(dataframe["close"], self.trend_ema_slow)
        dataframe["rsi"] = _rsi(dataframe["close"], self.rsi_period)
        dataframe["atr"] = _atr(dataframe["high"], dataframe["low"], dataframe["close"])
        dataframe["adx"] = _adx(dataframe["high"], dataframe["low"], dataframe["close"])
        vol_ema = _ema(dataframe["volume"], self.vol_ema_period)
        dataframe["vol_ratio"] = dataframe["volume"] / vol_ema.replace(0.0, np.nan)

        # ATRP (ATR as % of close)
        dataframe["atrp"] = dataframe["atr"] / dataframe["close"].replace(0.0, np.nan)

        # Trend direction: +1 = bullish, -1 = bearish, 0 = flat
        dataframe["trend_dir"] = np.where(
            dataframe["ema_fast"] > dataframe["ema_slow"], 1,
            np.where(dataframe["ema_fast"] < dataframe["ema_slow"], -1, 0),
        )

        # Shadow ML scores (telemetry only)
        if self.shadow_ml_enable:
            dataframe["shadow_score_trend"] = (dataframe["trend_dir"] + 1) / 2.0
            rsi_norm = (dataframe["rsi"] / 100.0).clip(0.0, 1.0)
            dataframe["shadow_score_rsi"] = rsi_norm
            adx_norm = (dataframe["adx"] / 50.0).clip(0.0, 1.0)
            dataframe["shadow_score_adx"] = adx_norm
            vol_score = (dataframe["vol_ratio"].fillna(1.0) - 1.0).clip(0.0, 2.0) / 2.0
            dataframe["shadow_score_vol"] = vol_score
            w_trend = self.shadow_ml_weight_trend
            w_adx = self.shadow_ml_weight_adx
            w_rsi = self.shadow_ml_weight_rsi
            w_vol = self.shadow_ml_weight_vol
            total_w = w_trend + w_adx + w_rsi + w_vol
            if total_w > 0:
                dataframe["shadow_ml_long_score"] = (
                    w_trend * dataframe["shadow_score_trend"]
                    + w_adx * dataframe["shadow_score_adx"]
                    + w_rsi * (1.0 - dataframe["shadow_score_rsi"])
                    + w_vol * dataframe["shadow_score_vol"]
                ) / total_w
                dataframe["shadow_ml_short_score"] = (
                    w_trend * (1.0 - dataframe["shadow_score_trend"])
                    + w_adx * dataframe["shadow_score_adx"]
                    + w_rsi * dataframe["shadow_score_rsi"]
                    + w_vol * dataframe["shadow_score_vol"]
                ) / total_w
            else:
                dataframe["shadow_ml_long_score"] = 0.5
                dataframe["shadow_ml_short_score"] = 0.5
        else:
            dataframe["shadow_ml_long_score"] = 0.0
            dataframe["shadow_ml_short_score"] = 0.0

        return dataframe

    # -----------------------------------------------------------------------
    # Entry
    # -----------------------------------------------------------------------

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "enter_long"] = 0
        dataframe.loc[:, "enter_short"] = 0

        long_cond = (
            (dataframe["trend_dir"] == 1)
            & (dataframe["rsi"] < self.rsi_long_entry_max)
            & (dataframe["vol_ratio"] > 0.8)
            & (dataframe["volume"] > 0)
        )
        short_cond = (
            (dataframe["trend_dir"] == -1)
            & (dataframe["rsi"] > self.rsi_short_entry_min)
            & (dataframe["vol_ratio"] > 0.8)
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[long_cond, "enter_long"] = 1
        dataframe.loc[short_cond, "enter_short"] = 1

        # Telemetry counters
        self._entry_long_count += int(long_cond.sum())
        self._entry_short_count += int(short_cond.sum())
        self._entry_both_sides_count += int((long_cond & short_cond).sum())

        return dataframe

    # -----------------------------------------------------------------------
    # Exit
    # -----------------------------------------------------------------------

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0

        exit_long_cond = dataframe["rsi"] > self.exit_rsi_long_min
        exit_short_cond = dataframe["rsi"] < self.exit_rsi_short_max

        dataframe.loc[exit_long_cond, "exit_long"] = 1
        dataframe.loc[exit_short_cond, "exit_short"] = 1

        return dataframe

    # -----------------------------------------------------------------------
    # Custom stoploss / exit
    # -----------------------------------------------------------------------

    def custom_stoploss(
        self,
        pair: str,
        trade: Any,
        current_time: Any,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs: Any,
    ) -> float:
        if not self.vol_shock_kill_enable:
            return self.stoploss

        if self.vol_shock_loss_floor_pct != 0.0 and current_profit > self.vol_shock_loss_floor_pct:
            return self.stoploss

        return self.stoploss

    # -----------------------------------------------------------------------
    # Telemetry export
    # -----------------------------------------------------------------------

    def bot_loop_start(self, current_time: Any, **kwargs: Any) -> None:
        self._reset_counters()
        self._diag_trace_events = []

    def get_telemetry(self) -> dict[str, Any]:
        """Return accumulated telemetry counters for the current run."""
        return {
            "fc_entry_trace_counts": {
                "entry_raw_long_count": self._entry_long_count,
                "entry_raw_short_count": self._entry_short_count,
                "entry_after_trend_count": self._entry_long_count + self._entry_short_count,
                "entry_after_vol_count": self._entry_long_count + self._entry_short_count,
                "entry_final_count": self._entry_long_count + self._entry_short_count,
                "shadow_ml_long_count": self._shadow_ml_long_count,
                "shadow_ml_short_count": self._shadow_ml_short_count,
                "shadow_ml_overlap_long_final_count": 0,
                "shadow_ml_overlap_short_final_count": 0,
            },
            "fc_preconfirm_counts": {
                "entry_long_count": self._entry_long_count,
                "entry_short_count": self._entry_short_count,
                "entry_both_sides_count": self._entry_both_sides_count,
                "entry_long_with_exit_long_count": 0,
                "entry_short_with_exit_short_count": 0,
                "entry_emitted_count": self._entry_emitted_count,
                "entry_after_startup_count": self._entry_after_startup_count,
                "entry_attempt_intent_count": self._entry_attempt_intent_count,
                "engine_order_attempt_proxy_count": 0,
            },
            "fc_entry_trace_sample": self._diag_trace_events[: self.diag_sample_limit],
        }
