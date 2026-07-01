"""
Intraday Strategy Layer: 15-Minute EMA Pullback with Volume Confirmation

This layer runs on top of the daily strategy. It only trades stocks that:
  - Are already flagged as "near setup" by the daily scan, OR
  - Are currently held as open positions (for intraday exit refinement)

Intraday Entry Conditions (all must be true):
  1. Stock is on the daily watchlist (passed daily trend filter: price > EMA200, EMA50 > EMA200)
  2. Price pulls back to the 9-period EMA on the 15-min chart (±1% tolerance)
  3. RSI(14) on 15-min chart is between 40 and 60
  4. Current 15-min bar volume > 1.5× the 20-bar average volume
  5. Price is above the VWAP (institutional bias confirmation)
  6. Time is within allowed trading windows (9:45–11:30 AM or 1:30–3:30 PM ET)

Intraday Stop & Target:
  - Stop loss  : 1 × ATR(14) on the 15-min chart below entry
  - Take profit: 2R (same R-multiple as daily strategy)

Risk Controls (intraday):
  - Max 2 intraday trades per day (to avoid overtrading)
  - Only trade if combined daily + intraday positions < 5
  - Respect daily loss limit and consecutive loss halt from DailyRiskState
  - Do not open intraday positions after 3:30 PM ET
"""

import math
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

from strategy import Bar, StrategyConfig, DailyRiskState, ema, atr, rsi


# ---------------------------------------------------------------------------
# Trading windows (Eastern Time)
# ---------------------------------------------------------------------------

MORNING_WINDOW = (time(9, 45), time(11, 30))   # avoid open chaos
AFTERNOON_WINDOW = (time(13, 30), time(15, 30)) # avoid close volatility


def in_trading_window(now_et: time) -> tuple[bool, str]:
    """Returns (allowed, session_name)."""
    if MORNING_WINDOW[0] <= now_et <= MORNING_WINDOW[1]:
        return True, "morning"
    if AFTERNOON_WINDOW[0] <= now_et <= AFTERNOON_WINDOW[1]:
        return True, "afternoon"
    return False, "outside trading window"


# ---------------------------------------------------------------------------
# VWAP (session)
# ---------------------------------------------------------------------------

def vwap(bars: list[Bar]) -> list[float]:
    """Session VWAP — pass only today's intraday bars, oldest first."""
    cumvol = 0.0
    cumtpv = 0.0
    result = []
    for b in bars:
        typical = (b.high + b.low + b.close) / 3
        cumvol += b.volume
        cumtpv += typical * b.volume
        result.append(cumtpv / cumvol if cumvol else typical)
    return result


def avg_volume(bars: list[Bar], period: int = 20) -> float:
    """Simple average of the last `period` bars' volume."""
    vols = [b.volume for b in bars[-period:]]
    return sum(vols) / len(vols) if vols else 0


# ---------------------------------------------------------------------------
# Daily trend filter (uses daily bars to gate intraday entries)
# ---------------------------------------------------------------------------

def passes_daily_trend(daily_bars: list[Bar]) -> tuple[bool, str]:
    """
    Confirms the stock is in a long-term uptrend suitable for long entries.
    Returns (passes, reason).
    """
    closes = [b.close for b in daily_bars]
    if len(closes) < 215:
        return False, "insufficient daily history"

    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    if not (e50 and e200):
        return False, "EMA calculation failed"

    if closes[-1] <= e200[-1]:
        return False, f"daily close {closes[-1]:.2f} <= EMA200 {e200[-1]:.2f}"
    if e50[-1] <= e200[-1]:
        return False, f"daily EMA50 {e50[-1]:.2f} <= EMA200 {e200[-1]:.2f}"

    return True, f"uptrend confirmed (EMA50={e50[-1]:.2f} > EMA200={e200[-1]:.2f})"


# ---------------------------------------------------------------------------
# Intraday signal
# ---------------------------------------------------------------------------

@dataclass
class IntradaySignal:
    symbol: str
    entry_price: float        # current bar close (enter at market immediately)
    shares: int
    stop_loss: float          # 1×ATR below entry
    take_profit: float        # 2R above entry
    dollar_risk: float
    session: str              # "morning" or "afternoon"
    reason: str


def check_intraday_entry(
    symbol: str,
    intraday_bars: list[Bar],    # today's 15-min bars, oldest first
    daily_bars: list[Bar],       # daily bars for trend filter
    config: StrategyConfig,
    open_positions: int,
    intraday_trades_today: int,
    risk_state: DailyRiskState,
    now_et: time,
) -> Optional[IntradaySignal]:
    """
    Returns an IntradaySignal if all intraday entry conditions are met.

    intraday_bars: 15-min OHLCV bars for today only (at least 20 needed).
    daily_bars   : daily bars for the daily trend filter (needs ≥ 215).
    """
    # --- Pre-flight ---
    tradeable, reason = risk_state.can_trade(config.account_equity)
    if not tradeable:
        return None
    if open_positions >= config.max_positions:
        return None
    if intraday_trades_today >= 2:
        return None
    if len(intraday_bars) < 20:
        return None

    # --- Time window ---
    in_window, session = in_trading_window(now_et)
    if not in_window:
        return None

    # --- Daily trend gate ---
    trend_ok, trend_reason = passes_daily_trend(daily_bars)
    if not trend_ok:
        return None

    # --- Intraday indicators ---
    closes = [b.close for b in intraday_bars]
    ema9_vals = ema(closes, 9)
    rsi14_vals = rsi(closes, 14)
    atr14_vals = atr(intraday_bars, 14)
    vwap_vals = vwap(intraday_bars)
    avg_vol = avg_volume(intraday_bars, 20)

    if not all([ema9_vals, rsi14_vals, atr14_vals, vwap_vals]):
        return None

    cur_bar = intraday_bars[-1]
    cur_ema9 = ema9_vals[-1]
    cur_rsi = rsi14_vals[-1]
    cur_atr = atr14_vals[-1]
    cur_vwap = vwap_vals[-1]
    cur_vol = cur_bar.volume

    # --- Five intraday conditions ---
    lower = cur_ema9 * 0.99
    upper = cur_ema9 * 1.01
    c1 = lower <= cur_bar.close <= upper          # pullback to 9 EMA
    c2 = 40 <= cur_rsi <= 60                      # RSI in pullback zone
    c3 = avg_vol > 0 and cur_vol >= avg_vol * 1.5 # volume surge
    c4 = cur_bar.close > cur_vwap                 # above VWAP
    # c5 (daily trend) already checked above

    if not (c1 and c2 and c3 and c4):
        return None

    # --- Sizing ---
    stop_distance = cur_atr                        # 1×ATR stop
    stop_loss = cur_bar.close - stop_distance
    if stop_distance <= 0:
        return None

    dollar_risk = config.account_equity * config.risk_per_trade_pct
    shares = math.floor(dollar_risk / stop_distance)

    max_dollar = min(
        config.account_equity * config.max_position_pct,
        config.account_equity * config.max_portfolio_exposure_pct,
    )
    shares = min(shares, math.floor(max_dollar / cur_bar.close))

    if shares < 1:
        return None

    r_value = shares * stop_distance
    take_profit = cur_bar.close + (2 * stop_distance)

    return IntradaySignal(
        symbol=symbol,
        entry_price=cur_bar.close,
        shares=shares,
        stop_loss=round(stop_loss, 4),
        take_profit=round(take_profit, 4),
        dollar_risk=round(r_value, 2),
        session=session,
        reason=(
            f"15-min pullback to EMA9: close={cur_bar.close:.2f} "
            f"ema9={cur_ema9:.2f} rsi={cur_rsi:.1f} "
            f"vol={cur_vol:,} (avg={avg_vol:,.0f}, {cur_vol/avg_vol:.1f}×) "
            f"vwap={cur_vwap:.2f} atr={cur_atr:.2f} "
            f"stop={stop_loss:.2f} tp={take_profit:.2f} [{session}]"
        ),
    )


# ---------------------------------------------------------------------------
# Intraday exit check
# ---------------------------------------------------------------------------

@dataclass
class IntradayExitDecision:
    symbol: str
    shares_to_sell: int
    reason: str


def check_intraday_exit(
    symbol: str,
    intraday_bars: list[Bar],
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    shares: int,
) -> Optional[IntradayExitDecision]:
    """
    Check intraday exit conditions on the current 15-min bar.
    Called on every new bar for open intraday positions.
    """
    if not intraday_bars:
        return None

    cur = intraday_bars[-1]

    # Stop loss
    if cur.low <= stop_loss:
        return IntradayExitDecision(
            symbol=symbol,
            shares_to_sell=shares,
            reason=f"Intraday stop hit: low {cur.low:.2f} <= stop {stop_loss:.2f}",
        )

    # Take profit (2R)
    if cur.high >= take_profit:
        return IntradayExitDecision(
            symbol=symbol,
            shares_to_sell=shares,
            reason=f"Intraday 2R target hit: high {cur.high:.2f} >= tp {take_profit:.2f}",
        )

    # End-of-day forced exit (close all intraday positions by 3:45 PM ET)
    bar_time = cur.date  # "2026-07-01T19:45:00Z" → convert to ET
    if "T" in bar_time:
        utc_hour = int(bar_time[11:13])
        utc_min = int(bar_time[14:16])
        et_hour = utc_hour - 4   # EDT offset
        if et_hour >= 15 and utc_min >= 45:
            return IntradayExitDecision(
                symbol=symbol,
                shares_to_sell=shares,
                reason="End-of-day forced exit: 3:45 PM ET",
            )

    return None


# ---------------------------------------------------------------------------
# Scan runner
# ---------------------------------------------------------------------------

def run_intraday_scan(
    watchlist: list[str],
    intraday_bars_by_symbol: dict[str, list[Bar]],
    daily_bars_by_symbol: dict[str, list[Bar]],
    config: StrategyConfig,
    open_positions: int,
    intraday_trades_today: int,
    risk_state: DailyRiskState,
    now_et: time,
) -> list[IntradaySignal]:
    """Scan all watchlist symbols for intraday entry signals."""
    signals = []
    for symbol in watchlist:
        intraday = intraday_bars_by_symbol.get(symbol, [])
        daily = daily_bars_by_symbol.get(symbol, [])
        sig = check_intraday_entry(
            symbol, intraday, daily, config,
            open_positions + len(signals),
            intraday_trades_today,
            risk_state,
            now_et,
        )
        if sig:
            signals.append(sig)
    return signals
