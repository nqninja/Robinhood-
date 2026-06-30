"""
Daily Trading Strategy: EMA Pullback with RSI Confirmation

Entry Conditions (all must be true):
  1. Price > 200-day EMA
  2. 50-day EMA > 200-day EMA
  3. Price pulls back to or slightly below 20-day EMA
  4. RSI(14) between 40 and 55
  5. Today's close > previous day's high

Position Sizing:
  - Risk 1% of account equity per trade
  - Max 5 open positions
  - Max 20% of portfolio per position
"""

import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class Signal:
    symbol: str
    entry_price: float        # next open (caller fills this in)
    shares: int
    dollar_risk: float
    stop_loss: float
    reason: str


@dataclass
class StrategyConfig:
    account_equity: float
    risk_per_trade_pct: float = 0.01   # 1%
    max_positions: int = 5
    max_position_pct: float = 0.20     # 20% of portfolio
    ema20_tolerance: float = 0.02      # allow 2% below 20-day EMA
    rsi_low: float = 40.0
    rsi_high: float = 55.0


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def ema(prices: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(prices[:period]) / period]
    for p in prices[period:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def rsi(closes: list[float], period: int = 14) -> list[float]:
    """Wilder RSI."""
    if len(closes) < period + 1:
        return []
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    result = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))

    return result


# ---------------------------------------------------------------------------
# Core signal check
# ---------------------------------------------------------------------------

def check_entry(symbol: str, bars: list[Bar], config: StrategyConfig,
                open_positions: int) -> Optional[Signal]:
    """
    Returns a Signal if all entry conditions are met, else None.

    bars: chronological list of daily OHLCV bars (oldest first).
          Needs at least 201 bars for a valid 200-day EMA.
    open_positions: number of currently open positions.
    """
    if open_positions >= config.max_positions:
        return None

    closes = [b.close for b in bars]

    if len(closes) < 202:
        return None  # not enough history

    ema20_vals = ema(closes, 20)
    ema50_vals = ema(closes, 50)
    ema200_vals = ema(closes, 200)
    rsi_vals = rsi(closes, 14)

    if not (ema20_vals and ema50_vals and ema200_vals and rsi_vals):
        return None

    today = bars[-1]
    prev = bars[-2]

    cur_ema20 = ema20_vals[-1]
    cur_ema50 = ema50_vals[-1]
    cur_ema200 = ema200_vals[-1]
    cur_rsi = rsi_vals[-1]

    # --- Entry conditions ---
    if today.close <= cur_ema200:                         # 1. price > 200 EMA
        return None
    if cur_ema50 <= cur_ema200:                           # 2. 50 EMA > 200 EMA
        return None
    lower_band = cur_ema20 * (1 - config.ema20_tolerance)
    if not (lower_band <= today.close <= cur_ema20 * 1.005):  # 3. pullback to 20 EMA
        return None
    if not (config.rsi_low <= cur_rsi <= config.rsi_high):   # 4. RSI 40-55
        return None
    if today.close <= prev.high:                          # 5. close > prev high
        return None

    # --- Position sizing ---
    # Stop loss: 1 ATR below entry (simple: use low of the pullback bar)
    stop_loss = today.low
    stop_distance = today.close - stop_loss
    if stop_distance <= 0:
        return None

    dollar_risk = config.account_equity * config.risk_per_trade_pct  # 1%
    shares = math.floor(dollar_risk / stop_distance)

    # Apply max-position cap
    max_dollar = config.account_equity * config.max_position_pct
    max_shares_by_cap = math.floor(max_dollar / today.close)
    shares = min(shares, max_shares_by_cap)

    if shares < 1:
        return None

    return Signal(
        symbol=symbol,
        entry_price=0.0,   # filled at next open by the caller
        shares=shares,
        dollar_risk=round(shares * stop_distance, 2),
        stop_loss=round(stop_loss, 4),
        reason=(
            f"EMA pullback confirmed: close={today.close:.2f} "
            f"ema20={cur_ema20:.2f} ema50={cur_ema50:.2f} "
            f"ema200={cur_ema200:.2f} rsi={cur_rsi:.1f}"
        ),
    )


# ---------------------------------------------------------------------------
# Daily runner
# ---------------------------------------------------------------------------

def run_daily_scan(
    watchlist: list[str],
    bars_by_symbol: dict[str, list[Bar]],
    config: StrategyConfig,
    open_positions: int,
) -> list[Signal]:
    """
    Scan all symbols and return signals sorted by RSI (lowest = deepest
    pullback = highest priority).
    """
    signals: list[Signal] = []
    for symbol in watchlist:
        bars = bars_by_symbol.get(symbol, [])
        sig = check_entry(symbol, bars, config, open_positions + len(signals))
        if sig:
            signals.append(sig)

    # Sort: prefer symbols with the deepest pullback (lowest RSI within 40-55)
    closes_map = {s: bars_by_symbol[s][-1].close for s in watchlist if bars_by_symbol.get(s)}
    signals.sort(key=lambda s: closes_map.get(s.symbol, 999))

    return signals


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_signals(signals: list[Signal], config: StrategyConfig) -> None:
    if not signals:
        print("No signals today.")
        return

    print(f"\n{'='*60}")
    print(f"DAILY SIGNALS  |  Account equity: ${config.account_equity:,.2f}")
    print(f"{'='*60}")
    for sig in signals:
        alloc = sig.shares * sig.entry_price if sig.entry_price else "TBD (next open)"
        print(f"\n  {sig.symbol}")
        print(f"    Shares      : {sig.shares}")
        print(f"    Entry       : next market open")
        print(f"    Stop loss   : ${sig.stop_loss:.4f}")
        print(f"    Dollar risk : ${sig.dollar_risk:.2f}")
        print(f"    Reason      : {sig.reason}")
    print(f"\n{'='*60}\n")
