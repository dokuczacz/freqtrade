"""
BTCFuturesCalmLS - BTC Futures Calm Long/Short strategy.

Calm RSI-based long/short strategy for BTC/USDT:USDT perpetual futures.
Uses EMA trend gate, ADX strength filter, Bollinger Band confirmation,
vol-shock kill switch, and optional shadow-ML telemetry scoring.

Shadow ML is telemetry-only: it emits scores and trace data but has
NO impact on entry/exit decisions.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
import pandas_ta as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, BooleanParameter

logger = logging.getLogger(__name__)


class BTCFuturesCalmLS(IStrategy):
    """BTC Futures Calm Long/Short strategy with vol-shock kill and shadow ML telemetry."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "15m"
    stoploss = -0.05
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    minimal_roi = {"0": 0.04, "30": 0.025, "60": 0.015, "120": 0.01}

    # --- RSI entry thresholds ---
    rsi_entry_long_max = IntParameter(45, 70, default=60, space="buy", optimize=True)
    rsi_entry_short_min = IntParameter(30, 55, default=41, space="buy", optimize=True)

    # --- RSI near-case filter ---
    rsi_near_case_only_enable = BooleanParameter(default=False, space="buy", optimize=True)
    rsi_near_case_max_distance = IntParameter(1, 5, default=2, space="buy", optimize=True)

    # --- ADX filters ---
    adx_min = IntParameter(5, 30, default=8, space="buy", optimize=True)
    trend_gate_adx_min = IntParameter(5, 30, default=8, space="buy", optimize=True)

    # --- Decoupled exit ---
    decouple_exit_cooldown_bars = IntParameter(0, 5, default=0, space="sell", optimize=True)
    decouple_asymmetry_long = BooleanParameter(default=False, space="sell", optimize=True)

    # --- Vol-shock kill ---
    vol_shock_kill_enable = BooleanParameter(default=False, space="sell", optimize=True)
    vol_shock_atrp_mult = DecimalParameter(0.1, 2.0, default=0.85, decimals=2, space="sell", optimize=True)
    vol_shock_adx_min = IntParameter(0, 30, default=8, space="sell", optimize=True)
    vol_shock_lookback_bars = IntParameter(1, 10, default=3, space="sell", optimize=True)
    vol_shock_loss_floor_pct = DecimalParameter(-0.05, 0.0, default=-0.002, decimals=4, space="sell", optimize=True)

    # --- Negative exit cut ---
    negative_exit_cut_enable = BooleanParameter(default=False, space="sell", optimize=True)

    # --- Shadow ML (telemetry only) ---
    shadow_ml_enable = BooleanParameter(default=False, space="buy", optimize=False)
    shadow_ml_threshold = DecimalParameter(0.3, 0.9, default=0.55, decimals=2, space="buy", optimize=False)
    shadow_ml_weight_trend = DecimalParameter(0.0, 1.0, default=0.35, decimals=2, space="buy", optimize=False)
    shadow_ml_weight_adx = DecimalParameter(0.0, 1.0, default=0.25, decimals=2, space="buy", optimize=False)
    shadow_ml_weight_rsi = DecimalParameter(0.0, 1.0, default=0.25, decimals=2, space="buy", optimize=False)
    shadow_ml_weight_vol = DecimalParameter(0.0, 1.0, default=0.15, decimals=2, space="buy", optimize=False)

    # --- Diagnostics ---
    diag_trace_enable = BooleanParameter(default=False, space="buy", optimize=False)
    diag_sample_limit = IntParameter(0, 500, default=200, space="buy", optimize=False)

    def informative_pairs(self) -> list[tuple[str, str]]:
        return [("BTC/USDT:USDT", "1h")]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # RSI
        dataframe["rsi"] = ta.rsi(dataframe["close"], length=14)

        # ADX
        adx_result = ta.adx(dataframe["high"], dataframe["low"], dataframe["close"], length=14)
        if adx_result is not None and "ADX_14" in adx_result.columns:
            dataframe["adx"] = adx_result["ADX_14"]
        else:
            dataframe["adx"] = 25.0

        # ATR percentage
        atr_result = ta.atr(dataframe["high"], dataframe["low"], dataframe["close"], length=14)
        if atr_result is not None:
            dataframe["atrp"] = atr_result / dataframe["close"]
        else:
            dataframe["atrp"] = 0.005

        # EMAs
        dataframe["ema9"] = ta.ema(dataframe["close"], length=9)
        dataframe["ema21"] = ta.ema(dataframe["close"], length=21)
        dataframe["sma12"] = ta.sma(dataframe["close"], length=12)
        dataframe["sma48"] = ta.sma(dataframe["close"], length=48)
        dataframe["tema"] = ta.tema(dataframe["close"], length=9)
        dataframe["ema_spread"] = (dataframe["ema9"] - dataframe["ema21"]).abs() / dataframe["ema21"]

        # Bollinger Bands
        bb_result = ta.bbands(dataframe["close"], length=20, std=2.0)
        if bb_result is not None:
            dataframe["bb_mid"] = bb_result["BBM_20_2.0"]
            dataframe["bb_upper"] = bb_result["BBU_20_2.0"]
            dataframe["bb_lower"] = bb_result["BBL_20_2.0"]
        else:
            dataframe["bb_mid"] = dataframe["close"]
            dataframe["bb_upper"] = dataframe["close"]
            dataframe["bb_lower"] = dataframe["close"]

        # Volume SMA
        dataframe["volume_sma20"] = ta.sma(dataframe["volume"], length=20)

        # Volatility gate
        dataframe["volatility_gate"] = dataframe["atrp"] > 0.003

        # 1h trend signals (use close as proxy when informative not loaded)
        dataframe["trend_up_1h"] = dataframe["ema9"] > dataframe["ema21"]
        dataframe["trend_down_1h"] = dataframe["ema9"] < dataframe["ema21"]
        dataframe["regime_up_1h"] = dataframe["sma12"] > dataframe["sma48"]
        dataframe["regime_down_1h"] = dataframe["sma12"] < dataframe["sma48"]

        # Shadow ML scores (telemetry only - never used for entry/exit)
        if self.shadow_ml_enable.value:
            w_trend = float(self.shadow_ml_weight_trend.value)
            w_adx = float(self.shadow_ml_weight_adx.value)
            w_rsi = float(self.shadow_ml_weight_rsi.value)
            w_vol = float(self.shadow_ml_weight_vol.value)
            total_w = w_trend + w_adx + w_rsi + w_vol
            if total_w > 0:
                trend_score = dataframe["trend_up_1h"].astype(float)
                adx_score = (dataframe["adx"] / 50.0).clip(0.0, 1.0)
                rsi_long_score = 1.0 - (dataframe["rsi"] / 100.0)
                rsi_short_score = dataframe["rsi"] / 100.0
                vol_score = dataframe["volatility_gate"].astype(float)
                dataframe["shadow_ml_long_score"] = (
                    (w_trend * trend_score + w_adx * adx_score + w_rsi * rsi_long_score + w_vol * vol_score)
                    / total_w
                ).round(6)
                dataframe["shadow_ml_short_score"] = (
                    (w_trend * (1.0 - trend_score) + w_adx * adx_score + w_rsi * rsi_short_score + w_vol * vol_score)
                    / total_w
                ).round(6)
            else:
                dataframe["shadow_ml_long_score"] = 0.5
                dataframe["shadow_ml_short_score"] = 0.5
        else:
            dataframe["shadow_ml_long_score"] = 0.0
            dataframe["shadow_ml_short_score"] = 0.0

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        rsi_long_max = int(self.rsi_entry_long_max.value)
        rsi_short_min = int(self.rsi_entry_short_min.value)
        adx_threshold = int(self.adx_min.value)
        trend_adx_min = int(self.trend_gate_adx_min.value)

        long_cond = (
            (dataframe["rsi"] < rsi_long_max)
            & (dataframe["adx"] >= adx_threshold)
            & (dataframe["trend_up_1h"] | (dataframe["adx"] >= trend_adx_min))
            & (dataframe["regime_up_1h"] | dataframe["volatility_gate"])
            & (dataframe["volume"] > 0)
        )

        short_cond = (
            (dataframe["rsi"] > rsi_short_min)
            & (dataframe["adx"] >= adx_threshold)
            & (dataframe["trend_down_1h"] | (dataframe["adx"] >= trend_adx_min))
            & (dataframe["regime_down_1h"] | dataframe["volatility_gate"])
            & (dataframe["volume"] > 0)
        )

        if self.rsi_near_case_only_enable.value:
            near_dist = int(self.rsi_near_case_max_distance.value)
            long_cond = long_cond & (abs(dataframe["rsi"] - rsi_long_max) <= near_dist)
            short_cond = short_cond & (abs(dataframe["rsi"] - rsi_short_min) <= near_dist)

        dataframe.loc[long_cond, ["enter_long", "enter_tag"]] = (1, "rsi_long")
        dataframe.loc[short_cond, ["enter_short", "enter_tag"]] = (1, "rsi_short")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        rsi_long_max = int(self.rsi_entry_long_max.value)
        rsi_short_min = int(self.rsi_entry_short_min.value)

        exit_long_cond = dataframe["rsi"] > rsi_long_max + 10
        exit_short_cond = dataframe["rsi"] < rsi_short_min - 10

        if self.vol_shock_kill_enable.value:
            atrp_mult = float(self.vol_shock_atrp_mult.value)
            vol_adx_min = int(self.vol_shock_adx_min.value)
            lookback = int(self.vol_shock_lookback_bars.value)
            loss_floor = float(self.vol_shock_loss_floor_pct.value)

            atrp_ma = dataframe["atrp"].rolling(lookback).mean()
            vol_spike = dataframe["atrp"] > atrp_ma * (1.0 + atrp_mult)
            adx_ok = dataframe["adx"] >= vol_adx_min
            vol_shock = vol_spike & adx_ok

            exit_long_cond = exit_long_cond | vol_shock
            exit_short_cond = exit_short_cond | vol_shock

        if self.decouple_asymmetry_long.value:
            cooldown = int(self.decouple_exit_cooldown_bars.value)
            if cooldown > 0:
                exit_long_cond = exit_long_cond & (
                    dataframe["rsi"].shift(cooldown) > rsi_long_max
                )

        if self.negative_exit_cut_enable.value:
            exit_long_cond = exit_long_cond | (dataframe["close"] < dataframe["bb_lower"])
            exit_short_cond = exit_short_cond | (dataframe["close"] > dataframe["bb_upper"])

        dataframe.loc[exit_long_cond, ["exit_long", "exit_tag"]] = (1, "rsi_exit_long")
        dataframe.loc[exit_short_cond, ["exit_short", "exit_tag"]] = (1, "rsi_exit_short")

        return dataframe

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: Any,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> bool:
        return True

    def confirm_trade_exit(
        self,
        pair: str,
        trade: Any,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time: Any,
        **kwargs: Any,
    ) -> bool:
        return True
