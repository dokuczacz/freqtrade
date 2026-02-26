"""
BTCFuturesCalmLS – BTC/USDT:USDT calm long/short futures strategy.

This module is the reference copy kept in docs/cloud_pack_v1/strategies/.
The canonical runtime copy lives in user_data/strategies/.

Parameter contract (cloud_pack_v1 normalized keys):
  trend_ema_fast            int   fast EMA period for trend gate
  trend_ema_slow            int   slow EMA period for trend gate
  rsi_long_entry_max        int   RSI upper bound for long entries
  rsi_short_entry_min       int   RSI lower bound for short entries
  exit_rsi_long_min         int   RSI threshold to exit long
  exit_rsi_short_max        int   RSI threshold to exit short
  exit_delay_bars           int   bars to delay exit signal confirmation
  vol_shock_kill_enable     bool  enable volatility-shock kill switch
  negative_exit_cut_enable  bool  enable early cut on negative momentum
  shadow_ml_enable          bool  enable shadow ML telemetry layer
"""

from freqtrade.strategy import IStrategy, IntParameter, BooleanParameter
import pandas as pd


class BTCFuturesCalmLS(IStrategy):
    """Calm long/short futures strategy for BTC/USDT:USDT."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"
    stoploss = -0.10
    trailing_stop = False

    # ------------------------------------------------------------------ #
    #  Optimisable parameters (cloud_pack_v1 normalized keys)             #
    # ------------------------------------------------------------------ #
    trend_ema_fast = IntParameter(5, 20, default=9, space="buy", optimize=False)
    trend_ema_slow = IntParameter(15, 50, default=21, space="buy", optimize=False)

    rsi_long_entry_max = IntParameter(45, 70, default=60, space="buy", optimize=True)
    rsi_short_entry_min = IntParameter(30, 55, default=41, space="buy", optimize=True)

    exit_rsi_long_min = IntParameter(50, 75, default=55, space="sell", optimize=True)
    exit_rsi_short_max = IntParameter(25, 50, default=45, space="sell", optimize=True)
    exit_delay_bars = IntParameter(0, 5, default=0, space="sell", optimize=True)

    vol_shock_kill_enable = BooleanParameter(default=False, space="buy", optimize=False)
    negative_exit_cut_enable = BooleanParameter(default=False, space="sell", optimize=False)
    shadow_ml_enable = BooleanParameter(default=True, space="buy", optimize=False)

    # shadow ML weights – telemetry-only, no entry/exit decision impact
    shadow_ml_threshold = 0.55
    shadow_ml_weight_trend = 0.35
    shadow_ml_weight_adx = 0.25
    shadow_ml_weight_rsi = 0.25
    shadow_ml_weight_vol = 0.15

    minimal_roi = {"0": 0.05}

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:  # type: ignore[override]
        from ta.trend import EMAIndicator
        from ta.momentum import RSIIndicator
        from ta.volatility import AverageTrueRange

        dataframe["ema_fast"] = EMAIndicator(
            dataframe["close"], window=int(self.trend_ema_fast.value)
        ).ema_indicator()
        dataframe["ema_slow"] = EMAIndicator(
            dataframe["close"], window=int(self.trend_ema_slow.value)
        ).ema_indicator()
        dataframe["rsi"] = RSIIndicator(dataframe["close"], window=14).rsi()
        dataframe["atr"] = AverageTrueRange(
            dataframe["high"], dataframe["low"], dataframe["close"], window=14
        ).average_true_range()
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:  # type: ignore[override]
        dataframe.loc[
            (dataframe["ema_fast"] > dataframe["ema_slow"])
            & (dataframe["rsi"] <= int(self.rsi_long_entry_max.value)),
            "enter_long",
        ] = 1
        dataframe.loc[
            (dataframe["ema_fast"] < dataframe["ema_slow"])
            & (dataframe["rsi"] >= int(self.rsi_short_entry_min.value)),
            "enter_short",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:  # type: ignore[override]
        dataframe.loc[
            dataframe["rsi"] >= int(self.exit_rsi_long_min.value),
            "exit_long",
        ] = 1
        dataframe.loc[
            dataframe["rsi"] <= int(self.exit_rsi_short_max.value),
            "exit_short",
        ] = 1
        return dataframe
