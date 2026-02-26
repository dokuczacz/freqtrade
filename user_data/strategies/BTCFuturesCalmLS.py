# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
"""
BTCFuturesCalmLS — BTC Futures Calm Long/Short strategy
Phase 5 / FC-only tuning.

Key tuning axis: EXIT_DELAY (candles to hold before exiting a calm-regime trade)
Hard constraints (evaluated externally in the analyze script):
  - PF >= 1.10
  - max_drawdown_pct <= 3.0
  - tail_worsening_count_guard == 0
  - economic_metrics_valid_ratio == 1.0
  - monthly_profit_norm_pct in [2.0, 2.5]
  - pass on both LW6M and LW12M windows

Shadow ML: telemetry-only, no entry/exit decision impact.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from pandas import DataFrame
from typing import Optional, Union

from freqtrade.strategy import (
    IStrategy,
    Trade,
    IntParameter,
    DecimalParameter,
    CategoricalParameter,
    merge_informative_pair,
    stoploss_from_open,
)


class BTCFuturesCalmLS(IStrategy):
    """
    BTC Futures Calm Long/Short strategy.

    Trades BTC/USDT:USDT perpetual futures in calm (low-volatility) market
    regimes, both long and short.  The EXIT_DELAY axis (number of candles to
    wait before the exit signal is acted upon) is the primary optimisation
    parameter for Phase 5 profit-target tuning.
    """

    # --- Strategy metadata ------------------------------------------------
    INTERFACE_VERSION = 3
    can_short = True

    timeframe = "1h"
    # Informative timeframe used for long-window regime filter
    informative_timeframe = "4h"

    startup_candle_count: int = 200
    process_only_new_candles = True

    # --- Risk management --------------------------------------------------
    stoploss = -0.03          # 3% hard stop — aligns with max_drawdown_pct gate
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False

    # --- Baseline A (fixed, must not be overwritten) ----------------------
    # Profit-target for longs / shorts expressed as ROI table
    minimal_roi = {
        "0":   0.025,   # 2.5% immediate take-profit
        "60":  0.020,   # 2.0 % after 60 minutes
        "120": 0.015,
        "240": 0.010,
    }

    # --- Hyperopt / tuning parameters -------------------------------------

    # EXIT_DELAY axis — primary tuning knob (Phase 5 bounded optimisation)
    exit_delay = IntParameter(0, 8, default=2, space="sell", optimize=True,
                              load=True)

    # Entry filters — calm-regime detection
    atr_calm_threshold = DecimalParameter(0.005, 0.020, default=0.010,
                                          decimals=3, space="buy",
                                          optimize=True, load=True)
    rsi_entry_long  = IntParameter(30, 50, default=40, space="buy",
                                   optimize=True, load=True)
    rsi_entry_short = IntParameter(50, 70, default=60, space="buy",
                                   optimize=True, load=True)

    # Shadow ML telemetry weight (does NOT affect trade decisions)
    shadow_ml_weight = DecimalParameter(0.0, 1.0, default=0.0, decimals=2,
                                        space="buy", optimize=False, load=True)

    # --- Populate indicators -----------------------------------------------
    def populate_indicators(self, dataframe: DataFrame,
                            metadata: dict) -> DataFrame:
        # RSI
        try:
            import talib.abstract as ta
            dataframe["rsi"]  = ta.RSI(dataframe, timeperiod=14)
            dataframe["atr"]  = ta.ATR(dataframe, timeperiod=14)
            dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
            dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        except ImportError:
            # Fall back to pandas-based calculations when TA-Lib is absent
            delta = dataframe["close"].diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rs    = gain / loss.replace(0, np.nan)
            dataframe["rsi"] = 100 - (100 / (1 + rs))

            high_low = dataframe["high"] - dataframe["low"]
            high_pc  = (dataframe["high"] - dataframe["close"].shift()).abs()
            low_pc   = (dataframe["low"]  - dataframe["close"].shift()).abs()
            tr = pd.concat([high_low, high_pc, low_pc], axis=1).max(axis=1)
            dataframe["atr"]   = tr.rolling(14).mean()
            dataframe["ema20"] = dataframe["close"].ewm(span=20).mean()
            dataframe["ema50"] = dataframe["close"].ewm(span=50).mean()

        # Calm-regime flag: ATR/close below threshold
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]
        dataframe["calm"] = (
            dataframe["atr_pct"] < self.atr_calm_threshold.value
        ).astype(int)

        # EXIT_DELAY rolling window: count how many of last N candles had calm=1
        delay = max(1, self.exit_delay.value)
        dataframe["calm_streak"] = (
            dataframe["calm"].rolling(delay).sum()
        )

        # Shadow ML telemetry column (no decision impact)
        dataframe["shadow_ml_score"] = 0.0  # placeholder — telemetry only

        return dataframe

    # --- Entry signals -----------------------------------------------------
    def populate_entry_trend(self, dataframe: DataFrame,
                             metadata: dict) -> DataFrame:
        # Long entry: calm regime + RSI oversold + price above EMA20
        long_cond = (
            (dataframe["calm"] == 1) &
            (dataframe["rsi"] < self.rsi_entry_long.value) &
            (dataframe["close"] > dataframe["ema20"]) &
            (dataframe["volume"] > 0)
        )
        dataframe.loc[long_cond, "enter_long"] = 1

        # Short entry: calm regime + RSI overbought + price below EMA20
        short_cond = (
            (dataframe["calm"] == 1) &
            (dataframe["rsi"] > self.rsi_entry_short.value) &
            (dataframe["close"] < dataframe["ema20"]) &
            (dataframe["volume"] > 0)
        )
        dataframe.loc[short_cond, "enter_short"] = 1

        return dataframe

    # --- Exit signals ------------------------------------------------------
    def populate_exit_trend(self, dataframe: DataFrame,
                            metadata: dict) -> DataFrame:
        delay = max(1, self.exit_delay.value)

        # Exit long: calm streak breaks or RSI crosses back to neutral
        exit_long_cond = (
            (dataframe["calm_streak"] < delay) |
            (dataframe["rsi"] > 55)
        )
        dataframe.loc[exit_long_cond, "exit_long"] = 1

        # Exit short: calm streak breaks or RSI crosses back to neutral
        exit_short_cond = (
            (dataframe["calm_streak"] < delay) |
            (dataframe["rsi"] < 45)
        )
        dataframe.loc[exit_short_cond, "exit_short"] = 1

        return dataframe

    # --- Custom stoploss (telemetry hook for shadow ML) --------------------
    def custom_stoploss(self, pair: str, trade: "Trade",
                        current_time: datetime, current_rate: float,
                        current_profit: float, after_fill: bool,
                        **kwargs) -> Optional[float]:
        # Shadow ML: log telemetry, never alter the stoploss value
        # (returns None to keep the default stoploss in effect)
        return None
