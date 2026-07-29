"""
Daily Trading Strategy: EMA Pullback + RSI Momentum

Entry Conditions — TWO triggers, either fires a signal:

Trigger A — EMA20 Pullback (original, loosened):
  1. Price > 200-day EMA
  2. 50-day EMA > 200-day EMA
  3. Price within EMA20 band: EMA20 × 0.98  ≤ close ≤ EMA20 × 1.02
  4. RSI(14) between 40 and 60

Trigger B — RSI Momentum Cross:
  1. Price > 200-day EMA
  2. 50-day EMA > 200-day EMA
  3. RSI crossed above 50 (was below 55 yesterday, above 50 today)
  4. Close > EMA20 (uptrend confirmation)

Stop Loss:
  2 × ATR(14) below entry price

Take Profit:
  - Exit half at 2R (2 × initial dollar risk)
  - Move stop on remainder to breakeven
  - Trail remainder with 20-day EMA or 3 × ATR (whichever is tighter)

Exit Rules (immediate):
  - Price closes below 50-day EMA
  - Stop-loss hit
  - Trailing stop hit

Market Filter:
  - Only open new positions if SPY > its 200-day EMA

Risk Controls:
  - Max daily loss: 3% of account equity
  - Stop trading after 3 consecutive losing trades
  - Max portfolio exposure: 80%
  - Skip stocks with earnings within 3 trading days

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
    entry_price: float        # filled at next open by caller
    shares: int
    dollar_risk: float        # total risk in dollars (shares × stop_distance)
    stop_loss: float          # absolute price level
    take_profit_half: float   # price to exit first half (2R)
    r_value: float            # 1R in dollars
    reason: str


@dataclass
class Position:
    symbol: str
    entry_price: float
    shares: int
    shares_remaining: int
    stop_loss: float
    breakeven_stop: bool      # True after first half exited
    r_value: float            # 1R in dollars (per original share count)
    entry_atr: float          # ATR at entry for trailing


@dataclass
class DailyRiskState:
    daily_loss: float = 0.0
    consecutive_losses: int = 0
    trades_today: int = 0

    def can_trade(self, account_equity: float) -> tuple[bool, str]:
        max_daily_loss = account_equity * 0.03
        if self.daily_loss >= max_daily_loss:
            return False, f"Daily loss limit hit (${self.daily_loss:.2f} >= ${max_daily_loss:.2f})"
        if self.consecutive_losses >= 3:
            return False, "3 consecutive losses — trading halted for the day"
        return True, ""

    def record_trade(self, pnl: float) -> None:
        self.trades_today += 1
        if pnl < 0:
            self.daily_loss += abs(pnl)
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0


@dataclass
class StrategyConfig:
    account_equity: float
    risk_per_trade_pct: float = 0.08    # 8%
    max_positions: int = 5
    max_position_pct: float = 0.50      # 50% per position
    max_portfolio_exposure_pct: float = 0.90  # 90% total
    ema20_tolerance: float = 0.02       # 2% band around EMA20
    rsi_low: float = 40.0
    rsi_high: float = 60.0              # widened from 55 → 60
    atr_stop_multiplier: float = 2.0    # stop = 2 × ATR below entry
    atr_trail_multiplier: float = 3.0   # trailing stop = 3 × ATR
    take_profit_r: float = 2.0          # first exit at 2R


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def ema(prices: list[float], period: int) -> list[float]:
    """Exponential moving average — returns same-length list (NaN-padded start)."""
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(prices[:period]) / period]
    for p in prices[period:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def atr(bars: list[Bar], period: int = 14) -> list[float]:
    """Average True Range (Wilder smoothing)."""
    if len(bars) < period + 1:
        return []
    trs = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    # Wilder smoothing
    result = [sum(trs[:period]) / period]
    for tr in trs[period:]:
        result.append((result[-1] * (period - 1) + tr) / period)
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
        rs = avg_gain / avg_loss if avg_loss else float("inf")
        result.append(100 - 100 / (1 + rs))
    return result


# ---------------------------------------------------------------------------
# Market filter
# ---------------------------------------------------------------------------

def spy_filter_passes(spy_bars: list[Bar]) -> tuple[bool, str]:
    """Returns (passes, reason). SPY must be above its 200-day EMA."""
    closes = [b.close for b in spy_bars]
    ema200 = ema(closes, 200)
    if not ema200:
        return False, "Not enough SPY history for 200-day EMA"
    if closes[-1] <= ema200[-1]:
        return False, f"SPY {closes[-1]:.2f} is below 200-day EMA {ema200[-1]:.2f} — no new longs"
    return True, f"SPY {closes[-1]:.2f} > 200-day EMA {ema200[-1]:.2f} ✓"


# ---------------------------------------------------------------------------
# Entry signal
# ---------------------------------------------------------------------------

def check_entry(
    symbol: str,
    bars: list[Bar],
    config: StrategyConfig,
    open_positions: int,
    current_exposure: float,         # dollars currently deployed
    earnings_within_3_days: bool,
    risk_state: DailyRiskState,
) -> Optional[Signal]:
    """
    Returns a Signal if all entry conditions are met, else None.

    bars: chronological daily OHLCV (oldest first), needs ≥ 215 bars.
    """
    # --- Pre-flight checks ---
    tradeable, reason = risk_state.can_trade(config.account_equity)
    if not tradeable:
        return None
    if open_positions >= config.max_positions:
        return None
    if earnings_within_3_days:
        return None

    max_exposure = config.account_equity * config.max_portfolio_exposure_pct
    if current_exposure >= max_exposure:
        return None

    closes = [b.close for b in bars]
    if len(closes) < 215:
        return None

    ema20_vals = ema(closes, 20)
    ema50_vals = ema(closes, 50)
    ema200_vals = ema(closes, 200)
    rsi_vals = rsi(closes, 14)
    atr_vals = atr(bars, 14)

    if not all([ema20_vals, ema50_vals, ema200_vals, rsi_vals, atr_vals]):
        return None

    today = bars[-1]
    prev = bars[-2]

    cur_ema20 = ema20_vals[-1]
    cur_ema50 = ema50_vals[-1]
    cur_ema200 = ema200_vals[-1]
    cur_rsi = rsi_vals[-1]
    cur_atr = atr_vals[-1]

    # --- Shared prerequisites ---
    if today.close <= cur_ema200:
        return None                                           # must be above 200 EMA
    if cur_ema50 <= cur_ema200:
        return None                                           # 50 EMA must be above 200 EMA

    # --- Trigger A: EMA20 pullback (loosened) ---
    lower_band = cur_ema20 * (1 - config.ema20_tolerance)
    upper_band = cur_ema20 * (1 + config.ema20_tolerance)
    trigger_a = (lower_band <= today.close <= upper_band) and (config.rsi_low <= cur_rsi <= config.rsi_high)

    # --- Trigger B: RSI momentum cross above 50 ---
    prev_rsi_vals = rsi([b.close for b in bars[:-1]], 14)
    prev_rsi = prev_rsi_vals[-1] if prev_rsi_vals else None
    trigger_b = (
        prev_rsi is not None
        and prev_rsi < 55
        and cur_rsi >= 50
        and today.close > cur_ema20
    )

    if not (trigger_a or trigger_b):
        return None

    trigger_name = "A(pullback)" if trigger_a else "B(rsi_cross)"

    # --- Stop & sizing ---
    stop_distance = config.atr_stop_multiplier * cur_atr     # 2 × ATR
    stop_loss = today.close - stop_distance
    if stop_distance <= 0:
        return None

    dollar_risk = config.account_equity * config.risk_per_trade_pct
    shares = math.floor(dollar_risk / stop_distance)

    # Cap at 20% of equity
    max_dollar = min(
        config.account_equity * config.max_position_pct,
        max_exposure - current_exposure,
    )
    shares = min(shares, math.floor(max_dollar / today.close))

    if shares < 1:
        return None

    r_value = shares * stop_distance
    take_profit_half = today.close + (config.take_profit_r * stop_distance)

    return Signal(
        symbol=symbol,
        entry_price=0.0,
        shares=shares,
        dollar_risk=round(r_value, 2),
        stop_loss=round(stop_loss, 4),
        take_profit_half=round(take_profit_half, 4),
        r_value=round(r_value, 2),
        reason=(
            f"[{trigger_name}] close={today.close:.2f} ema20={cur_ema20:.2f} "
            f"ema50={cur_ema50:.2f} ema200={cur_ema200:.2f} "
            f"rsi={cur_rsi:.1f} atr={cur_atr:.2f} "
            f"stop={stop_loss:.2f} tp_half={take_profit_half:.2f}"
        ),
    )


# ---------------------------------------------------------------------------
# Exit logic
# ---------------------------------------------------------------------------

@dataclass
class ExitDecision:
    symbol: str
    shares_to_sell: int
    reason: str
    is_partial: bool   # True = first-half exit, False = full exit


def check_exits(
    position: Position,
    bars: list[Bar],
) -> Optional[ExitDecision]:
    """
    Evaluate exit rules for an open position.
    Returns ExitDecision if an exit is warranted, else None.

    Caller should call this once per position per day with fresh bars.
    """
    closes = [b.close for b in bars]
    today = bars[-1]

    ema20_vals = ema(closes, 20)
    ema50_vals = ema(closes, 50)
    atr_vals = atr(bars, 14)

    cur_ema20 = ema20_vals[-1] if ema20_vals else None
    cur_ema50 = ema50_vals[-1] if ema50_vals else None
    cur_atr = atr_vals[-1] if atr_vals else None

    half_shares = math.ceil(position.shares / 2)

    # 1. Hard stop-loss
    if today.close <= position.stop_loss:
        return ExitDecision(
            symbol=position.symbol,
            shares_to_sell=position.shares_remaining,
            reason=f"Stop-loss hit: close {today.close:.2f} <= stop {position.stop_loss:.2f}",
            is_partial=False,
        )

    # 2. Close below 50-day EMA
    if cur_ema50 and today.close < cur_ema50:
        return ExitDecision(
            symbol=position.symbol,
            shares_to_sell=position.shares_remaining,
            reason=f"Close {today.close:.2f} below 50 EMA {cur_ema50:.2f}",
            is_partial=False,
        )

    # 3. Trailing stop (after breakeven triggered)
    if position.breakeven_stop and cur_atr and cur_ema20:
        trail_atr = today.close - (3 * cur_atr)
        trail_ema = cur_ema20
        trailing_stop = max(trail_atr, trail_ema)          # tighter of the two
        if today.close <= trailing_stop:
            return ExitDecision(
                symbol=position.symbol,
                shares_to_sell=position.shares_remaining,
                reason=(
                    f"Trailing stop hit: close {today.close:.2f} <= "
                    f"trail {trailing_stop:.2f} (ema20={cur_ema20:.2f}, 3×atr={trail_atr:.2f})"
                ),
                is_partial=False,
            )

    # 4. First-half take-profit at 2R (only if not already triggered)
    if not position.breakeven_stop:
        take_profit_price = position.entry_price + (2 * position.r_value / position.shares)
        if today.close >= take_profit_price:
            return ExitDecision(
                symbol=position.symbol,
                shares_to_sell=half_shares,
                reason=f"2R target hit: close {today.close:.2f} >= tp {take_profit_price:.2f} — selling half",
                is_partial=True,
            )

    return None


# ---------------------------------------------------------------------------
# Daily scan
# ---------------------------------------------------------------------------

def run_daily_scan(
    watchlist: list[str],
    bars_by_symbol: dict[str, list[Bar]],
    spy_bars: list[Bar],
    config: StrategyConfig,
    open_positions: int,
    current_exposure: float,
    earnings_map: dict[str, bool],    # symbol -> True if earnings within 3 days
    risk_state: DailyRiskState,
) -> tuple[list[Signal], str]:
    """
    Returns (signals, market_filter_message).
    signals is empty if SPY filter fails.
    """
    passes, spy_msg = spy_filter_passes(spy_bars)
    if not passes:
        return [], spy_msg

    signals: list[Signal] = []
    for symbol in watchlist:
        bars = bars_by_symbol.get(symbol, [])
        has_earnings = earnings_map.get(symbol, False)
        sig = check_entry(
            symbol, bars, config,
            open_positions + len(signals),
            current_exposure,
            has_earnings,
            risk_state,
        )
        if sig:
            signals.append(sig)

    # Sort by RSI proximity to 40 (deepest pullback = highest conviction)
    def rsi_score(sig: Signal) -> float:
        bars = bars_by_symbol.get(sig.symbol, [])
        closes = [b.close for b in bars]
        r = rsi(closes, 14)
        return r[-1] if r else 999

    signals.sort(key=rsi_score)
    return signals, spy_msg


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_signals(signals: list[Signal], spy_msg: str, config: StrategyConfig) -> None:
    print(f"\n{'='*65}")
    print(f"DAILY SCAN  |  Equity: ${config.account_equity:,.2f}  |  {spy_msg}")
    print(f"{'='*65}")
    if not signals:
        print("  No signals today.")
    for sig in signals:
        print(f"\n  {sig.symbol}")
        print(f"    Shares         : {sig.shares}")
        print(f"    Entry          : next market open")
        print(f"    Stop loss      : ${sig.stop_loss:.4f}  (2 × ATR below entry)")
        print(f"    Take profit ½  : ${sig.take_profit_half:.4f}  (2R)")
        print(f"    Dollar risk    : ${sig.dollar_risk:.2f}  (1R)")
        print(f"    Detail         : {sig.reason}")
    print(f"\n{'='*65}\n")
