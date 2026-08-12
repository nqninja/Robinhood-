"""
Cheap Momentum Options Model — designed for $75–$500 accounts

Built around three hard lessons:
  1. SPY/QQQ options cost too much at this account size. Trade cheap underlyings.
  2. Never fight macro event days. Check the calendar before anything else.
  3. The only winning trade was a patient 3-day swing on cheap options with low IV.
     Copy that. Don't chase intraday noise.

Two setups, scored 0–100. Only enter at 70+.
Setup A: Momentum Continuation (multi-day swing)
Setup B: ORB Breakout (intraday, non-macro days only, 5+ DTE)

Usage (in a session):
    from momentum_model import MomentumModel
    model = MomentumModel(buying_power=150.00, date="2026-08-13")
    model.check_macro_calendar()  # prints go/no-go
    signal = model.score_setup(...)
    sizing = model.size_position(signal)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Stocks with options in the $0.15–$0.80 range, IV typically <80% on quiet days
CHEAP_UNIVERSE = ["SOFI", "MARA", "IONQ", "RIVN", "HOOD", "SOUN"]

# SPY/QQQ and large-caps — options too expensive for this account
AVOID = ["SPY", "QQQ", "NVDA", "AMD", "META", "PLTR", "TSLA", "AMZN", "MSFT", "AAPL"]

# Known macro event dates — update each week
# Format: "YYYY-MM-DD": "event name"
MACRO_CALENDAR: dict[str, str] = {
    "2026-08-12": "CPI",
    "2026-08-13": "PPI",
    # Add FOMC, NFP, etc. as they're announced
}


# ---------------------------------------------------------------------------
# Pre-trade hard filters — any single failure = no trade
# ---------------------------------------------------------------------------

@dataclass
class PreTradeFilter:
    date: str           # "YYYY-MM-DD"
    symbol: str
    buying_power: float
    iv: float           # 0.0–1.0 (e.g. 0.55 = 55%)
    earnings_dte: int   # days until next earnings (-1 = unknown)
    option_cost: float  # per contract (ask price × 100)
    dte: int            # days to expiration on the option

    def run(self) -> tuple[bool, list[str]]:
        """Returns (passes, list_of_failures)."""
        failures = []

        # Hard rule 1: macro event day
        if self.date in MACRO_CALENDAR:
            failures.append(f"MACRO EVENT DAY: {MACRO_CALENDAR[self.date]} — no trades")

        # Hard rule 2: earnings blackout (7 days)
        if 0 <= self.earnings_dte <= 7:
            failures.append(f"EARNINGS BLACKOUT: {self.earnings_dte} days to earnings")

        # Hard rule 3: IV cap
        if self.iv > 0.80:
            failures.append(f"IV TOO HIGH: {self.iv:.0%} > 80% max")

        # Hard rule 4: position size cap (25% of buying power)
        max_cost = self.buying_power * 0.25
        if self.option_cost > max_cost:
            failures.append(
                f"TOO EXPENSIVE: ${self.option_cost:.2f} > 25% of buying power "
                f"(${max_cost:.2f})"
            )

        # Hard rule 5: min DTE
        if self.dte < 5:
            failures.append(f"DTE TOO SHORT: {self.dte} DTE < 5 minimum")

        # Hard rule 6: account floor
        if self.buying_power < 50:
            failures.append(f"ACCOUNT TOO LOW: ${self.buying_power:.2f} < $50 floor — no trades")

        # Hard rule 7: wrong universe
        if self.symbol in AVOID:
            failures.append(f"WRONG VEHICLE: {self.symbol} options too expensive for this account")

        return len(failures) == 0, failures


# ---------------------------------------------------------------------------
# VWAP calculation from intraday bars
# ---------------------------------------------------------------------------

def calc_vwap(bars: list[dict]) -> float:
    """
    bars: list of {open_price, high_price, low_price, close_price, volume}
    Returns VWAP as a float. Returns 0.0 if no bars.
    """
    cum_tpv = 0.0
    cum_vol = 0
    for b in bars:
        tp = (float(b["high_price"]) + float(b["low_price"]) + float(b["close_price"])) / 3
        vol = int(b["volume"])
        cum_tpv += tp * vol
        cum_vol += vol
    return cum_tpv / cum_vol if cum_vol > 0 else 0.0


def calc_anchored_vwap(prev_day_bars: list[dict], today_bars: list[dict]) -> float:
    """
    Anchored VWAP starting from yesterday's open bar through current bar.

    Pass yesterday's 5-min bars + today's 5-min bars so far.
    The anchor point is the first bar of the previous session (9:30 yesterday).
    This level acts as a multi-session equilibrium — stronger than intraday VWAP.

    Returns 0.0 if no bars provided.
    """
    return calc_vwap(prev_day_bars + today_bars)


# ---------------------------------------------------------------------------
# Volume profile from daily bars
# ---------------------------------------------------------------------------

@dataclass
class VolumeProfile:
    poc: float          # point of control (highest volume price)
    hvn_zones: list[tuple[float, float]]   # [(low, high), ...] high-volume nodes
    lvn_zones: list[tuple[float, float]]   # [(low, high), ...] low-volume nodes

    @staticmethod
    def from_bars(bars: list[dict], bucket_size: float = 0.50) -> "VolumeProfile":
        """
        Build a coarse volume profile by bucketing daily bars into price ranges.
        bars: daily OHLCV. bucket_size in dollars.
        """
        if not bars:
            return VolumeProfile(poc=0.0, hvn_zones=[], lvn_zones=[])

        # Distribute each bar's volume evenly across its high-low range
        volume_map: dict[float, float] = {}
        for b in bars:
            hi = float(b["high_price"])
            lo = float(b["low_price"])
            vol = float(b["volume"])
            n_buckets = max(1, round((hi - lo) / bucket_size))
            vol_per_bucket = vol / n_buckets
            bucket = lo
            while bucket <= hi + 0.001:
                key = round(round(bucket / bucket_size) * bucket_size, 2)
                volume_map[key] = volume_map.get(key, 0) + vol_per_bucket
                bucket += bucket_size

        if not volume_map:
            return VolumeProfile(poc=0.0, hvn_zones=[], lvn_zones=[])

        poc = max(volume_map, key=lambda k: volume_map[k])
        avg_vol = sum(volume_map.values()) / len(volume_map)

        hvn_zones = []
        lvn_zones = []
        sorted_keys = sorted(volume_map.keys())
        for price in sorted_keys:
            vol = volume_map[price]
            zone = (price, price + bucket_size)
            if vol >= avg_vol * 1.5:
                hvn_zones.append(zone)
            elif vol <= avg_vol * 0.5:
                lvn_zones.append(zone)

        return VolumeProfile(poc=poc, hvn_zones=hvn_zones, lvn_zones=lvn_zones)

    def nearest_hvn(self, price: float) -> Optional[float]:
        """Return midpoint of the nearest HVN zone to price."""
        if not self.hvn_zones:
            return None
        return min(
            ((lo + hi) / 2 for lo, hi in self.hvn_zones),
            key=lambda m: abs(m - price)
        )

    def price_in_lvn(self, price: float) -> bool:
        """True if price is inside a low-volume node (expect fast move)."""
        return any(lo <= price <= hi for lo, hi in self.lvn_zones)


# ---------------------------------------------------------------------------
# Setup scoring
# ---------------------------------------------------------------------------

@dataclass
class SetupScore:
    symbol: str
    setup_type: str       # "momentum_swing" or "orb_breakout"
    score: int            # 0–100
    direction: str        # "calls" or "puts"
    rationale: list[str]  # reasons for score
    filters_passed: bool
    filter_failures: list[str]

    @property
    def tradeable(self) -> bool:
        return self.filters_passed and self.score >= 70


def score_momentum_swing(
    symbol: str,
    current_price: float,
    price_2d_ago: float,
    sma20: float,
    rel_volume: float,   # today's volume / 20-day avg volume
    iv: float,
    rsi: float,
    vwap: float,
    vp: Optional[VolumeProfile] = None,
) -> tuple[int, list[str]]:
    """
    Score a momentum continuation swing setup. Returns (score, rationale).

    Weights (total 100 points):
      - 2-day price change (25 pts)
      - Above SMA20 (15 pts)
      - Volume confirmation (20 pts)
      - RSI zone (15 pts)
      - IV quality (15 pts)
      - VWAP position (10 pts)
    """
    score = 0
    rationale = []

    # 2-day momentum (25 pts)
    chg_2d = (current_price - price_2d_ago) / price_2d_ago if price_2d_ago else 0
    if chg_2d >= 0.08:
        score += 25
        rationale.append(f"Strong 2-day move: +{chg_2d:.1%} (full 25pts)")
    elif chg_2d >= 0.05:
        score += 18
        rationale.append(f"Good 2-day move: +{chg_2d:.1%} (18pts)")
    elif chg_2d >= 0.03:
        score += 10
        rationale.append(f"Weak 2-day move: +{chg_2d:.1%} (10pts)")
    elif chg_2d <= -0.03:
        score -= 10
        rationale.append(f"Bearish 2-day move: {chg_2d:.1%} (-10pts)")
    else:
        rationale.append(f"Flat 2-day move: {chg_2d:.1%} (0pts)")

    # Above SMA20 (15 pts)
    if current_price > sma20:
        score += 15
        rationale.append(f"Above SMA20 (${sma20:.2f}) +15pts")
    else:
        score -= 5
        rationale.append(f"Below SMA20 (${sma20:.2f}) -5pts")

    # Relative volume (20 pts)
    if rel_volume >= 2.5:
        score += 20
        rationale.append(f"Exceptional volume: {rel_volume:.1f}x avg (20pts)")
    elif rel_volume >= 1.5:
        score += 13
        rationale.append(f"Good volume: {rel_volume:.1f}x avg (13pts)")
    elif rel_volume >= 1.0:
        score += 5
        rationale.append(f"Average volume: {rel_volume:.1f}x (5pts)")
    else:
        score -= 5
        rationale.append(f"Low volume: {rel_volume:.1f}x (-5pts)")

    # RSI zone (15 pts) — want 45-65 for continuation, not overbought
    if 45 <= rsi <= 65:
        score += 15
        rationale.append(f"RSI ideal zone: {rsi:.0f} (15pts)")
    elif 35 <= rsi < 45 or 65 < rsi <= 72:
        score += 7
        rationale.append(f"RSI acceptable: {rsi:.0f} (7pts)")
    elif rsi > 72:
        score -= 5
        rationale.append(f"RSI overbought: {rsi:.0f} (-5pts)")
    else:
        score -= 5
        rationale.append(f"RSI weak: {rsi:.0f} (-5pts)")

    # IV quality (15 pts) — lower is better (cheaper premium, less decay)
    if iv <= 0.40:
        score += 15
        rationale.append(f"Low IV: {iv:.0%} (15pts)")
    elif iv <= 0.60:
        score += 10
        rationale.append(f"Moderate IV: {iv:.0%} (10pts)")
    elif iv <= 0.80:
        score += 4
        rationale.append(f"High IV: {iv:.0%} (4pts — borderline)")
    else:
        score += 0
        rationale.append(f"IV too high: {iv:.0%} — filter should have blocked this")

    # VWAP position (10 pts)
    if current_price > vwap * 1.005:
        score += 10
        rationale.append(f"Above VWAP (${vwap:.2f}) +10pts")
    elif current_price > vwap:
        score += 5
        rationale.append(f"Just above VWAP (${vwap:.2f}) +5pts")
    else:
        score -= 5
        rationale.append(f"Below VWAP (${vwap:.2f}) -5pts")

    # Volume profile bonus (up to +5)
    if vp:
        if vp.price_in_lvn(current_price):
            score += 5
            rationale.append("Price in LVN zone — fast breakout expected (+5pts bonus)")
        nearest_hvn = vp.nearest_hvn(current_price)
        if nearest_hvn and abs(nearest_hvn - current_price) / current_price < 0.01:
            score -= 5
            rationale.append(f"HVN magnet nearby at ${nearest_hvn:.2f} — could stall (-5pts)")

    return max(0, min(100, score)), rationale


def score_orb_breakout(
    symbol: str,
    current_price: float,
    orb_high: float,
    orb_low: float,
    vwap: float,
    bar_volume: float,      # volume on the breakout bar
    avg_bar_volume: float,  # average 5-min bar volume
    gap_pct: float,         # today's gap vs yesterday close (positive = gap up)
    vp: Optional[VolumeProfile] = None,
    anchored_vwap: Optional[float] = None,  # prev-day anchored VWAP
) -> tuple[int, str, list[str]]:
    """
    Score an ORB breakout. Returns (score, direction, rationale).
    direction: "calls" or "puts" — only meaningful if score >= 70.

    HARD CONFLUENCE GATE (runs before scoring):
    All four signals must agree with the breakout direction or the trade
    is blocked (score forced to 0). No exceptions, no overrides by volume.

      Signal 1: ORB direction        (broke high → calls; broke low → puts)
      Signal 2: Intraday VWAP        (price above → bullish; below → bearish)
      Signal 3: Prev-day AVWAP       (price above → bullish; below → bearish)
      Signal 4: Gap direction        (gap up → bullish; gap down → bearish;
                                      flat ±0.1% = neutral, does not block)

    If anchored_vwap is not provided, Signal 3 is skipped (only 3 required).

    Scoring (only reached if confluence passes):
      - Intraday VWAP confirm   : 30 pts
      - Prev-day AVWAP confirm  : 20 pts
      - Gap confirm             : 15 pts
      - Breakout bar volume     : 20 pts
      - ORB range quality       : 15 pts
      - Volume profile bonus    : up to +10 pts
    """
    score = 0
    rationale = []

    orb_range = orb_high - orb_low
    broke_high = current_price > orb_high
    broke_low = current_price < orb_low
    above_vwap = current_price > vwap
    direction = "calls" if broke_high else "puts"

    # ── HARD CONFLUENCE GATE ─────────────────────────────────────────────────
    # Every signal that exists must agree. One conflict = 0, no trade.

    conflicts = []

    # No ORB break at all
    if not broke_high and not broke_low:
        rationale.append("No ORB break yet — wait")
        return 0, direction, rationale

    # Signal 2: intraday VWAP
    vwap_agrees = (broke_high and above_vwap) or (broke_low and not above_vwap)
    if not vwap_agrees:
        conflicts.append(
            f"Intraday VWAP (${vwap:.2f}) opposes {direction} — "
            f"price {'above' if above_vwap else 'below'} VWAP on {'call' if broke_high else 'put'} break"
        )

    # Signal 3: prev-day anchored VWAP
    if anchored_vwap and anchored_vwap > 0:
        above_avwap = current_price > anchored_vwap
        avwap_agrees = (broke_high and above_avwap) or (broke_low and not above_avwap)
        if not avwap_agrees:
            conflicts.append(
                f"Prev-day AVWAP (${anchored_vwap:.2f}) opposes {direction} — "
                f"price {'above' if above_avwap else 'below'} AVWAP on {'call' if broke_high else 'put'} break"
            )

    # Signal 4: gap direction — only hard-block on significant macro gaps (≥0.4%)
    # Small gaps (< 0.4%) are noise, not a real headwind. The 8/12 CPI gap was +0.8%.
    GAP_BLOCK_THRESHOLD = 0.004
    gap_conflict = (
        (broke_high and gap_pct < -GAP_BLOCK_THRESHOLD) or
        (broke_low and gap_pct > GAP_BLOCK_THRESHOLD)
    )
    if gap_conflict:
        conflicts.append(
            f"Significant gap ({gap_pct:+.2%}) opposes {direction} — "
            f"{'gap-down on call break' if broke_high else 'gap-up on put break'} (macro headwind)"
        )

    if conflicts:
        rationale.append(f"⛔ CONFLUENCE GATE FAILED — {len(conflicts)} conflict(s):")
        rationale.extend(f"  ✗ {c}" for c in conflicts)
        rationale.append("Score forced to 0. All signals must agree before entry.")
        return 0, direction, rationale

    rationale.append(f"✅ Confluence gate passed — all signals agree ({direction})")

    # ── SCORING (only reached when all signals agree) ─────────────────────────

    # Intraday VWAP confirm (30 pts)
    score += 30
    rationale.append(f"Intraday VWAP (${vwap:.2f}) confirms direction (30pts)")

    # Prev-day AVWAP confirm (20 pts)
    if anchored_vwap and anchored_vwap > 0:
        score += 20
        rationale.append(f"Prev-day AVWAP (${anchored_vwap:.2f}) confirms direction (20pts)")
    else:
        rationale.append("Prev-day AVWAP not provided — 0pts (provide for full score)")

    # Gap direction (15 pts)
    # Confirms direction: full 15pts. Small/flat: 8pts. Modest opposing: 4pts.
    # Large opposing gaps (≥0.4%) already blocked at gate — won't reach here.
    gap_confirms = (broke_high and gap_pct >= 0.001) or (broke_low and gap_pct <= -0.001)
    gap_flat = abs(gap_pct) < 0.001
    if gap_confirms:
        score += 15
        rationale.append(f"Gap confirms direction ({gap_pct:+.2%}) (15pts)")
    elif gap_flat:
        score += 8
        rationale.append(f"Gap flat ({gap_pct:+.2%}) — neutral (8pts)")
    else:
        score += 4
        rationale.append(f"Small opposing gap ({gap_pct:+.2%}) — minor headwind, passed gate (4pts)")

    # Breakout bar volume (20 pts)
    vol_ratio = bar_volume / avg_bar_volume if avg_bar_volume > 0 else 1
    if vol_ratio >= 3.0:
        score += 20
        rationale.append(f"Massive breakout volume: {vol_ratio:.1f}x avg (20pts)")
    elif vol_ratio >= 2.0:
        score += 13
        rationale.append(f"Good breakout volume: {vol_ratio:.1f}x avg (13pts)")
    elif vol_ratio >= 1.3:
        score += 5
        rationale.append(f"Modest breakout volume: {vol_ratio:.1f}x avg (5pts)")
    else:
        score += 0
        rationale.append(f"Weak breakout volume: {vol_ratio:.1f}x — low conviction (0pts)")

    # ORB range quality (15 pts)
    range_pct = orb_range / ((orb_high + orb_low) / 2)
    if range_pct <= 0.003:
        score += 15
        rationale.append(f"Tight ORB: {range_pct:.2%} range (15pts)")
    elif range_pct <= 0.006:
        score += 8
        rationale.append(f"Normal ORB: {range_pct:.2%} range (8pts)")
    else:
        score += 0
        rationale.append(f"Wide ORB: {range_pct:.2%} — choppy open, low confidence (0pts)")

    # Volume profile bonus (up to +10 pts)
    if vp:
        if vp.price_in_lvn(current_price):
            score += 10
            rationale.append("Price in LVN — fast extension expected (+10pts)")
        nearest_hvn = vp.nearest_hvn(current_price)
        if nearest_hvn:
            dist_pct = abs(nearest_hvn - current_price) / current_price
            if dist_pct < 0.005:
                score -= 10
                rationale.append(f"HVN wall at ${nearest_hvn:.2f} immediately ahead — may stall (-10pts)")
            elif broke_high and nearest_hvn > current_price:
                score += 5
                rationale.append(f"Next HVN at ${nearest_hvn:.2f} = upside target (+5pts)")
            elif broke_low and nearest_hvn < current_price:
                score += 5
                rationale.append(f"Next HVN at ${nearest_hvn:.2f} = downside target (+5pts)")

    return max(0, min(100, score)), direction, rationale


# ---------------------------------------------------------------------------
# Position sizer
# ---------------------------------------------------------------------------

@dataclass
class PositionSize:
    contracts: int
    max_spend: float        # total cost (contracts × premium × 100)
    stop_price: float       # exit if option drops to this
    target_price: float     # exit at this (80% gain)
    hard_target_price: float  # exit here no matter what (100% gain)
    risk_dollars: float     # max loss at stop

    def __str__(self) -> str:
        return (
            f"{self.contracts} contract(s) | cost ${self.max_spend:.2f} | "
            f"stop ${self.stop_price:.2f} | target ${self.target_price:.2f} "
            f"(+80%) / ${self.hard_target_price:.2f} (+100%) | "
            f"risk ${self.risk_dollars:.2f}"
        )


def size_position(
    buying_power: float,
    option_ask: float,
    max_pct: float = 0.25,
    stop_pct: float = 0.35,
    target_pct: float = 0.80,
) -> Optional[PositionSize]:
    """
    Calculate position size for a long option.
    - Never spend more than max_pct of buying power
    - stop_pct: exit if premium drops this much from entry
    - target_pct: exit at this gain
    Returns None if we can't afford even 1 contract.
    """
    max_spend = buying_power * max_pct
    cost_per_contract = option_ask * 100

    if cost_per_contract > max_spend or cost_per_contract == 0:
        return None  # can't afford it

    # Always 1 contract at this account size — no reason to size up until >$500
    contracts = 1
    total_cost = cost_per_contract

    stop_price = round(option_ask * (1 - stop_pct), 2)
    target_price = round(option_ask * (1 + target_pct), 2)
    hard_target = round(option_ask * 2.0, 2)
    risk = round((option_ask - stop_price) * 100, 2)

    return PositionSize(
        contracts=contracts,
        max_spend=total_cost,
        stop_price=stop_price,
        target_price=target_price,
        hard_target_price=hard_target,
        risk_dollars=risk,
    )


# ---------------------------------------------------------------------------
# Exit monitor
# ---------------------------------------------------------------------------

@dataclass
class ExitSignal:
    action: str     # "hold", "take_profit", "stop_loss", "time_stop", "thesis_broken"
    reason: str
    urgency: str    # "immediate", "next_bar", "monitor"


def check_exit(
    entry_price: float,       # option premium at entry
    current_price: float,     # current option mark
    dte_remaining: int,
    underlying_price: float,
    vwap: float,
    direction: str,           # "calls" or "puts"
    stop_pct: float = 0.35,
    target_pct: float = 0.80,
) -> ExitSignal:
    """
    Check all exit conditions for an open position.
    Priority: stop > target > time > thesis.
    """
    gain_pct = (current_price - entry_price) / entry_price

    # Stop loss
    if gain_pct <= -stop_pct:
        return ExitSignal(
            action="stop_loss",
            reason=f"Premium down {gain_pct:.1%} from entry — stop at -{stop_pct:.0%}",
            urgency="immediate",
        )

    # Take profit — scale out logic
    if gain_pct >= 1.0:
        return ExitSignal(
            action="take_profit",
            reason=f"Premium up {gain_pct:.1%} — 100% gain, full exit",
            urgency="immediate",
        )
    if gain_pct >= target_pct:
        return ExitSignal(
            action="take_profit",
            reason=f"Premium up {gain_pct:.1%} — 80% target hit, exit",
            urgency="immediate",
        )

    # Time stop — exit with 3 DTE to avoid gamma risk and theta bleed
    if dte_remaining <= 3:
        return ExitSignal(
            action="time_stop",
            reason=f"{dte_remaining} DTE remaining — exit to avoid theta decay",
            urgency="next_bar",
        )

    # Thesis broken — underlying crossed back through VWAP against position
    if direction == "calls" and underlying_price < vwap * 0.998:
        return ExitSignal(
            action="thesis_broken",
            reason=f"Underlying ${underlying_price:.2f} broke below VWAP ${vwap:.2f}",
            urgency="next_bar",
        )
    if direction == "puts" and underlying_price > vwap * 1.002:
        return ExitSignal(
            action="thesis_broken",
            reason=f"Underlying ${underlying_price:.2f} reclaimed above VWAP ${vwap:.2f}",
            urgency="next_bar",
        )

    # Still good
    return ExitSignal(
        action="hold",
        reason=f"P&L: {gain_pct:+.1%} | DTE: {dte_remaining} | vs VWAP: OK",
        urgency="monitor",
    )


# ---------------------------------------------------------------------------
# Main model class
# ---------------------------------------------------------------------------

class MomentumModel:
    """
    Entry point for the trading session.

    Typical workflow:
        model = MomentumModel(buying_power=150.0, date="2026-08-13")

        # 1. Macro check — run this first, stop if it fails
        ok, reason = model.is_tradeable_day()
        if not ok: return

        # 2. Score a setup
        score, rationale = model.score_momentum_swing(...)
        # or
        score, direction, rationale = model.score_orb_breakout(...)

        # 3. Pre-trade filter
        passed, failures = model.pre_trade_check(symbol, iv, earnings_dte, option_ask, dte)

        # 4. Size position
        sizing = model.size_position(option_ask)

        # 5. Monitor exit
        exit_signal = model.check_exit(...)
    """

    def __init__(self, buying_power: float, date: str):
        self.buying_power = buying_power
        self.date = date  # "YYYY-MM-DD"

    def is_tradeable_day(self) -> tuple[bool, str]:
        if self.date in MACRO_CALENDAR:
            return False, f"NO TRADE: {MACRO_CALENDAR[self.date]} today — stay flat"
        if self.buying_power < 50:
            return False, f"NO TRADE: Buying power ${self.buying_power:.2f} below $50 floor"
        return True, "Day is clear — proceed to setup scan"

    def pre_trade_check(
        self,
        symbol: str,
        iv: float,
        earnings_dte: int,
        option_ask: float,
        dte: int,
    ) -> tuple[bool, list[str]]:
        f = PreTradeFilter(
            date=self.date,
            symbol=symbol,
            buying_power=self.buying_power,
            iv=iv,
            earnings_dte=earnings_dte,
            option_cost=option_ask * 100,
            dte=dte,
        )
        return f.run()

    def score_momentum_swing(
        self,
        symbol: str,
        current_price: float,
        price_2d_ago: float,
        sma20: float,
        rel_volume: float,
        iv: float,
        rsi: float,
        vwap: float,
        vp: Optional[VolumeProfile] = None,
    ) -> tuple[int, list[str]]:
        return score_momentum_swing(
            symbol=symbol,
            current_price=current_price,
            price_2d_ago=price_2d_ago,
            sma20=sma20,
            rel_volume=rel_volume,
            iv=iv,
            rsi=rsi,
            vwap=vwap,
            vp=vp,
        )

    def score_orb_breakout(
        self,
        symbol: str,
        current_price: float,
        orb_high: float,
        orb_low: float,
        vwap: float,
        bar_volume: float,
        avg_bar_volume: float,
        gap_pct: float,
        vp: Optional[VolumeProfile] = None,
        anchored_vwap: Optional[float] = None,
    ) -> tuple[int, str, list[str]]:
        return score_orb_breakout(
            symbol=symbol,
            current_price=current_price,
            orb_high=orb_high,
            orb_low=orb_low,
            vwap=vwap,
            bar_volume=bar_volume,
            avg_bar_volume=avg_bar_volume,
            gap_pct=gap_pct,
            vp=vp,
            anchored_vwap=anchored_vwap,
        )

    def calc_anchored_vwap(
        self, prev_day_bars: list[dict], today_bars: list[dict]
    ) -> float:
        return calc_anchored_vwap(prev_day_bars, today_bars)

    def size_position(self, option_ask: float) -> Optional[PositionSize]:
        return size_position(self.buying_power, option_ask)

    def check_exit(
        self,
        entry_price: float,
        current_price: float,
        dte_remaining: int,
        underlying_price: float,
        vwap: float,
        direction: str,
    ) -> ExitSignal:
        return check_exit(
            entry_price=entry_price,
            current_price=current_price,
            dte_remaining=dte_remaining,
            underlying_price=underlying_price,
            vwap=vwap,
            direction=direction,
        )

    def calc_vwap(self, bars: list[dict]) -> float:
        return calc_vwap(bars)

    def build_volume_profile(self, daily_bars: list[dict]) -> VolumeProfile:
        return VolumeProfile.from_bars(daily_bars)

    def print_summary(self) -> None:
        ok, reason = self.is_tradeable_day()
        print(f"\n{'='*60}")
        print(f"MOMENTUM MODEL — {self.date}")
        print(f"Buying power: ${self.buying_power:.2f}")
        print(f"Max per trade: ${self.buying_power * 0.25:.2f} (25%)")
        print(f"Day status: {'✅ TRADEABLE' if ok else '🚫 NO TRADE'} — {reason}")
        print(f"Universe: {', '.join(CHEAP_UNIVERSE)}")
        print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    model = MomentumModel(buying_power=150.00, date="2026-08-13")
    model.print_summary()

    # Simulate the SOFI 7/31 trade — the one that worked
    score, rationale = model.score_momentum_swing(
        symbol="SOFI",
        current_price=16.50,
        price_2d_ago=15.20,
        sma20=15.80,
        rel_volume=2.8,
        iv=0.50,
        rsi=58,
        vwap=16.30,
    )
    print(f"SOFI Momentum Score: {score}/100")
    for r in rationale:
        print(f"  {r}")

    passed, failures = model.pre_trade_check("SOFI", iv=0.50, earnings_dte=30,
                                              option_ask=0.36, dte=7)
    print(f"\nPre-trade filter: {'PASS' if passed else 'FAIL'}")
    for f in failures:
        print(f"  ❌ {f}")

    sizing = model.size_position(option_ask=0.36)
    print(f"\nPosition sizing: {sizing}")

    # ORB confluence test — all signals agree (should pass gate and score high)
    print("\n--- ORB confluence: all agree (should pass) ---")
    score, direction, rationale = score_orb_breakout(
        symbol="SOFI",
        current_price=16.80,
        orb_high=16.60,
        orb_low=16.20,
        vwap=16.55,
        bar_volume=500_000,
        avg_bar_volume=200_000,
        gap_pct=0.008,
        anchored_vwap=16.40,
    )
    print(f"Score: {score}/100 | Direction: {direction}")
    for r in rationale:
        print(f"  {r}")

    # ORB confluence test — AVWAP conflict (should be blocked at gate)
    print("\n--- ORB confluence: AVWAP conflict (should be blocked) ---")
    score, direction, rationale = score_orb_breakout(
        symbol="SOFI",
        current_price=16.80,
        orb_high=16.60,
        orb_low=16.20,
        vwap=16.55,
        bar_volume=500_000,
        avg_bar_volume=200_000,
        gap_pct=0.008,
        anchored_vwap=17.10,  # price BELOW prev-day AVWAP on a call break = conflict
    )
    print(f"Score: {score}/100 | Direction: {direction}")
    for r in rationale:
        print(f"  {r}")

    # Simulate today's bad SPY put trade — should fail filters
    print("\n--- SPY put 8/12 (should fail) ---")
    ok, reason = MomentumModel(buying_power=210.41, date="2026-08-12").is_tradeable_day()
    print(f"Day check: {'PASS' if ok else 'FAIL — ' + reason}")
    passed, failures = model.pre_trade_check("SPY", iv=0.12, earnings_dte=999,
                                              option_ask=1.78, dte=1)
    print(f"Pre-trade: {'PASS' if passed else 'FAIL'}")
    for f in failures:
        print(f"  ❌ {f}")
