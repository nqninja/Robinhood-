"""
Cheap Momentum Options Model — designed for $75–$500 accounts

Rule: BARCHART FINDS IT → PRICE ACTION CONFIRMS IT →
      OPTIONS CHAIN PICKS THE CONTRACT → RISK MANAGEMENT APPROVES

Grade thresholds (score_unusual_activity):
  80–100 = A+  → eligible to trade
  70–79  = A   → watch / optional trade
  60–69  = B   → watch only
  < 60   = NO TRADE

Required combination for any entry:
  Unusual options activity + abnormal stock volume +
  directional price movement + liquid contract
  ALL FOUR must be present. One missing = no trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Primary watchlist — options $0.15–$0.80, IV <80%, OI >5k, stock price $5–$30
# Refreshed via Barchart scanner (Options → Unusual Activity, Price $5-30, RelVol >1.5x, IVR <50%)
CHEAP_UNIVERSE = [
    "SOFI",  # IV ~44%, OI 37k — most liquid, best hold
    "MARA",  # IV ~80% (limit) — only trade on Bitcoin green days
    "RIVN",  # $16 range, high avg volume — confirm option pricing before entry
    "SOUN",  # $0.21 calls — cheapest; wait for reversal before entry
    "ACHR",  # eVTOL momentum, $0.38 call, IV 74.8% — new add
    "JOBY",  # eVTOL sector companion, $0.36 call, IV 67.4%, OI 4.5k — new add
]

# Removed from universe:
#   HOOD  — stock at $98, ATM calls $5+, incompatible with <$200 account
#   IONQ  — stock at $45, ATM calls $2.50+; re-add if price drops back below $20

# Never trade these — options too expensive for this account size
AVOID = ["SPY", "QQQ", "NVDA", "AMD", "META", "PLTR", "TSLA", "AMZN", "MSFT", "AAPL",
         "HOOD", "IONQ"]

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
    # Required
    date: str               # "YYYY-MM-DD"
    symbol: str
    buying_power: float
    iv: float               # 0.0–1.0
    earnings_dte: int       # days to next earnings (-1 = unknown)
    option_cost: float      # per contract (ask × 100)
    dte: int                # days to expiration

    # Option chain fields
    bid: float = 0.0
    ask: float = 0.0
    open_interest: int = 0
    options_volume: int = 0   # contracts traded today
    delta: float = 0.0        # absolute value

    # Stock fields
    stock_price: float = 0.0
    avg_daily_volume: float = 0.0    # shares, 20-day avg
    today_volume: float = 0.0        # shares traded today so far
    dollar_volume: float = 0.0       # stock_price × avg_daily_volume
    atr_pct: float = 0.0             # ATR as % of stock price (e.g. 0.025 = 2.5%)
    rel_volume: float = 0.0          # today_vol / avg_daily_vol
    price_direction: str = ""        # "up", "down", or "" (unknown)
    options_direction: str = ""      # "calls" or "puts" (dominant unusual activity)

    # Symbol-specific
    btc_negative_day: bool = False   # MARA only

    def run(self) -> tuple[bool, list[str]]:
        """Returns (passes, list_of_failures). Any failure = no trade."""
        failures = []

        # ── ACCOUNT / CALENDAR ───────────────────────────────────────────────
        if self.date in MACRO_CALENDAR:
            failures.append(f"MACRO EVENT: {MACRO_CALENDAR[self.date]} — no trades today")

        if self.buying_power < 50:
            failures.append(f"ACCOUNT FLOOR: ${self.buying_power:.2f} < $50 minimum")

        if self.symbol in AVOID:
            failures.append(f"WRONG UNIVERSE: {self.symbol} — options too expensive for account")

        if self.symbol == "MARA" and self.btc_negative_day:
            failures.append("MARA: Bitcoin down today — IV too high to fight direction")

        # ── EARNINGS ─────────────────────────────────────────────────────────
        if 0 <= self.earnings_dte <= 7:
            failures.append(f"EARNINGS BLACKOUT: {self.earnings_dte}d to earnings")

        # ── STOCK FILTERS ────────────────────────────────────────────────────
        if self.stock_price > 0 and self.stock_price < 5:
            failures.append(f"STOCK PRICE: ${self.stock_price:.2f} < $5 — avoid penny stocks")

        if self.avg_daily_volume > 0 and self.avg_daily_volume < 1_000_000:
            failures.append(
                f"LOW AVG VOLUME: {self.avg_daily_volume/1e6:.1f}M shares/day < 1M minimum"
            )

        if self.dollar_volume > 0 and self.dollar_volume < 50_000_000:
            failures.append(
                f"LOW DOLLAR VOLUME: ${self.dollar_volume/1e6:.1f}M < $50M minimum"
            )

        if self.rel_volume > 0 and self.rel_volume < 1.5:
            failures.append(
                f"LOW RVOL: {self.rel_volume:.1f}x < 1.5x — stock not moving with conviction"
            )

        if self.atr_pct > 0 and self.atr_pct < 0.02:
            failures.append(
                f"LOW ATR: {self.atr_pct:.1%} < 2% — not enough daily range for options profit"
            )

        # Stock must be moving in the same direction as the dominant options activity
        if self.price_direction and self.options_direction:
            direction_match = (
                (self.options_direction == "calls" and self.price_direction == "up") or
                (self.options_direction == "puts" and self.price_direction == "down")
            )
            if not direction_match:
                failures.append(
                    f"DIRECTION MISMATCH: {self.options_direction} activity but stock is "
                    f"going {self.price_direction} — options and price must agree"
                )

        # ── OPTION CONTRACT FILTERS ───────────────────────────────────────────
        if self.iv > 0.80:
            failures.append(f"IV TOO HIGH: {self.iv:.0%} > 80% cap")

        max_cost = self.buying_power * 0.25
        if self.option_cost > max_cost:
            failures.append(
                f"TOO EXPENSIVE: ${self.option_cost:.2f} > 25% of buying power (${max_cost:.2f})"
            )

        if self.dte < 7:
            failures.append(f"DTE TOO SHORT: {self.dte}d < 7 minimum")
        elif self.dte > 45:
            failures.append(f"DTE TOO LONG: {self.dte}d > 45 — too much time decay risk")

        if self.open_interest > 0 and self.open_interest < 1000:
            failures.append(f"LOW OI: {self.open_interest:,} < 1,000 — illiquid contract")

        if self.options_volume > 0 and self.options_volume < 1000:
            failures.append(
                f"LOW OPTIONS VOLUME: {self.options_volume:,} contracts < 1,000 today"
            )

        # Vol/OI ratio — unusual activity threshold
        if self.options_volume > 0 and self.open_interest > 0:
            vol_oi = self.options_volume / self.open_interest
            if vol_oi < 1.5:
                failures.append(
                    f"LOW VOL/OI: {vol_oi:.1f}x < 1.5x — not genuinely unusual activity"
                )

        # Bid/ask spread ≤ 5% of mid
        if self.bid > 0 and self.ask > 0:
            mid = (self.bid + self.ask) / 2
            spread_pct = (self.ask - self.bid) / mid if mid > 0 else 1.0
            if spread_pct > 0.05:
                failures.append(
                    f"SPREAD TOO WIDE: {spread_pct:.0%} (${self.bid:.2f}/${self.ask:.2f}) > 5% max"
                )

        # Delta: 0.45–0.70 (no lottery OTM, no deep ITM)
        if self.delta > 0:
            if self.delta < 0.35:
                failures.append(f"DELTA TOO LOW: {self.delta:.2f} < 0.35 — far OTM lottery")
            elif self.delta > 0.75:
                failures.append(f"DELTA TOO HIGH: {self.delta:.2f} > 0.75 — deep ITM")

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
# Unusual activity scanner score  (Step 1 of the trade pipeline)
# ---------------------------------------------------------------------------

def score_unusual_activity(
    symbol: str,
    # Options activity
    options_volume: int,        # contracts traded today
    open_interest: int,         # OI on target strike
    call_volume: int,           # call contracts today
    put_volume: int,            # put contracts today
    bid: float,
    ask: float,
    # Stock activity
    rel_volume: float,          # today vol / 20d avg vol
    price_change_pct: float,    # today's % move (positive = up)
    avg_daily_volume: float,    # 20d avg share volume
    dollar_volume: float,       # price × avg_daily_volume
    atr_pct: float,             # ATR / stock price
    # Context
    near_key_level: bool = False,    # near breakout/breakdown level
    volume_increasing: bool = False, # volume climbing as price moves
    spy_agrees: bool = True,         # market direction agrees with trade
    sector_agrees: bool = True,      # sector direction agrees
) -> tuple[int, str, list[str]]:
    """
    Score a candidate stock 0–100 for unusual options activity quality.
    Returns (score, direction, rationale).

    direction: "calls" or "puts" based on which dominates.

    Grade thresholds:
      80–100 = A+  → eligible to trade
      70–79  = A   → watch / optional trade
      60–69  = B   → watch only
      < 60   = NO TRADE

    Weights:
      Barchart options activity    25pts  (vol/OI ratio + volume)
      Relative volume              15pts
      Price momentum               15pts
      Options liquidity            15pts  (OI, spread, volume)
      Call/put directional confirm 10pts
      Breakout/breakdown setup     10pts
      Volatility/movement           5pts
      Market/sector confirmation    5pts
    """
    score = 0
    rationale = []

    # Determine dominant direction
    direction = "calls" if call_volume >= put_volume else "puts"
    bullish = direction == "calls"
    price_moving_right = (bullish and price_change_pct > 0) or (not bullish and price_change_pct < 0)

    # ── 1. BARCHART OPTIONS ACTIVITY  (25 pts) ────────────────────────────────
    vol_oi = options_volume / open_interest if open_interest > 0 else 0
    if vol_oi >= 5.0:
        score += 25
        rationale.append(f"Massive unusual activity: Vol/OI {vol_oi:.1f}x (25pts)")
    elif vol_oi >= 3.0:
        score += 20
        rationale.append(f"Strong unusual activity: Vol/OI {vol_oi:.1f}x (20pts)")
    elif vol_oi >= 1.5:
        score += 12
        rationale.append(f"Moderate unusual activity: Vol/OI {vol_oi:.1f}x (12pts)")
    else:
        score += 0
        rationale.append(f"Weak signal: Vol/OI {vol_oi:.1f}x < 1.5x threshold (0pts)")

    # ── 2. RELATIVE VOLUME  (15 pts) ─────────────────────────────────────────
    if rel_volume >= 3.0:
        score += 15
        rationale.append(f"Exceptional RVOL: {rel_volume:.1f}x (15pts)")
    elif rel_volume >= 2.0:
        score += 11
        rationale.append(f"Strong RVOL: {rel_volume:.1f}x (11pts)")
    elif rel_volume >= 1.5:
        score += 7
        rationale.append(f"Good RVOL: {rel_volume:.1f}x (7pts)")
    else:
        score += 0
        rationale.append(f"Low RVOL: {rel_volume:.1f}x < 1.5x minimum (0pts)")

    # ── 3. PRICE MOMENTUM  (15 pts) ──────────────────────────────────────────
    abs_chg = abs(price_change_pct)
    if price_moving_right:
        if abs_chg >= 0.05:
            score += 15
            rationale.append(f"Strong momentum: {price_change_pct:+.1%} in right direction (15pts)")
        elif abs_chg >= 0.03:
            score += 10
            rationale.append(f"Good momentum: {price_change_pct:+.1%} (10pts)")
        elif abs_chg >= 0.01:
            score += 5
            rationale.append(f"Mild momentum: {price_change_pct:+.1%} (5pts)")
        else:
            score += 2
            rationale.append(f"Flat — stock barely moving ({price_change_pct:+.1%}) (2pts)")
    else:
        score += 0
        rationale.append(
            f"DIRECTION MISMATCH: stock {price_change_pct:+.1%} but {direction} dominating (0pts)"
        )

    # ── 4. OPTIONS LIQUIDITY  (15 pts) ────────────────────────────────────────
    liq_score = 0
    if open_interest >= 10_000:
        liq_score += 7
        rationale.append(f"High OI: {open_interest:,} (7pts)")
    elif open_interest >= 5_000:
        liq_score += 5
        rationale.append(f"Good OI: {open_interest:,} (5pts)")
    elif open_interest >= 1_000:
        liq_score += 2
        rationale.append(f"Acceptable OI: {open_interest:,} (2pts)")
    else:
        rationale.append(f"Low OI: {open_interest:,} — illiquid (0pts)")

    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid
        if spread_pct <= 0.03:
            liq_score += 8
            rationale.append(f"Tight spread: {spread_pct:.0%} (8pts)")
        elif spread_pct <= 0.05:
            liq_score += 5
            rationale.append(f"Good spread: {spread_pct:.0%} (5pts)")
        elif spread_pct <= 0.10:
            liq_score += 2
            rationale.append(f"Wide spread: {spread_pct:.0%} (2pts)")
        else:
            rationale.append(f"Too wide: {spread_pct:.0%} spread (0pts)")

    score += min(liq_score, 15)

    # ── 5. CALL/PUT DIRECTIONAL CONFIRMATION  (10 pts) ────────────────────────
    total_vol = call_volume + put_volume
    if total_vol > 0:
        dominant_pct = max(call_volume, put_volume) / total_vol
        if dominant_pct >= 0.75:
            score += 10
            rationale.append(
                f"Strong directional conviction: {direction} {dominant_pct:.0%} of volume (10pts)"
            )
        elif dominant_pct >= 0.60:
            score += 6
            rationale.append(
                f"Moderate conviction: {direction} {dominant_pct:.0%} of volume (6pts)"
            )
        else:
            score += 2
            rationale.append(f"Weak conviction: calls/puts near even split (2pts)")

    # ── 6. BREAKOUT/BREAKDOWN SETUP  (10 pts) ────────────────────────────────
    if near_key_level and volume_increasing:
        score += 10
        rationale.append("Near key level + volume increasing — textbook breakout setup (10pts)")
    elif near_key_level:
        score += 6
        rationale.append("Near key level (6pts)")
    elif volume_increasing:
        score += 4
        rationale.append("Volume increasing as price moves (4pts)")
    else:
        rationale.append("No clear breakout setup (0pts)")

    # ── 7. VOLATILITY / MOVEMENT  (5 pts) ────────────────────────────────────
    if atr_pct >= 0.04:
        score += 5
        rationale.append(f"High ATR: {atr_pct:.1%} — big daily ranges (5pts)")
    elif atr_pct >= 0.02:
        score += 3
        rationale.append(f"Good ATR: {atr_pct:.1%} (3pts)")
    else:
        rationale.append(f"Low ATR: {atr_pct:.1%} < 2% — not enough movement (0pts)")

    # ── 8. MARKET/SECTOR CONFIRMATION  (5 pts) ───────────────────────────────
    if spy_agrees and sector_agrees:
        score += 5
        rationale.append("SPY + sector both confirm direction (5pts)")
    elif spy_agrees or sector_agrees:
        score += 3
        rationale.append("One of SPY/sector confirms direction (3pts)")
    else:
        rationale.append("Neither SPY nor sector confirms — fighting the tape (0pts)")

    # ── GRADE ─────────────────────────────────────────────────────────────────
    final = max(0, min(100, score))
    if final >= 80:
        grade = "A+ — ELIGIBLE TO TRADE"
    elif final >= 70:
        grade = "A — watch / optional trade"
    elif final >= 60:
        grade = "B — watch only"
    else:
        grade = "NO TRADE"
    rationale.append(f"{'='*40}")
    rationale.append(f"SCORE: {final}/100 | GRADE: {grade}")

    return final, direction, rationale


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
        bid: float = 0.0,
        open_interest: int = 0,
        options_volume: int = 0,
        delta: float = 0.0,
        stock_price: float = 0.0,
        avg_daily_volume: float = 0.0,
        today_volume: float = 0.0,
        dollar_volume: float = 0.0,
        atr_pct: float = 0.0,
        rel_volume: float = 0.0,
        price_direction: str = "",
        options_direction: str = "",
        btc_negative_day: bool = False,
    ) -> tuple[bool, list[str]]:
        f = PreTradeFilter(
            date=self.date,
            symbol=symbol,
            buying_power=self.buying_power,
            iv=iv,
            earnings_dte=earnings_dte,
            option_cost=option_ask * 100,
            dte=dte,
            bid=bid,
            ask=option_ask,
            open_interest=open_interest,
            options_volume=options_volume,
            delta=delta,
            stock_price=stock_price,
            avg_daily_volume=avg_daily_volume,
            today_volume=today_volume,
            dollar_volume=dollar_volume,
            atr_pct=atr_pct,
            rel_volume=rel_volume,
            price_direction=price_direction,
            options_direction=options_direction,
            btc_negative_day=btc_negative_day,
        )
        return f.run()

    def score_unusual_activity(
        self,
        symbol: str,
        options_volume: int,
        open_interest: int,
        call_volume: int,
        put_volume: int,
        bid: float,
        ask: float,
        rel_volume: float,
        price_change_pct: float,
        avg_daily_volume: float,
        dollar_volume: float,
        atr_pct: float,
        near_key_level: bool = False,
        volume_increasing: bool = False,
        spy_agrees: bool = True,
        sector_agrees: bool = True,
    ) -> tuple[int, str, list[str]]:
        return score_unusual_activity(
            symbol=symbol,
            options_volume=options_volume,
            open_interest=open_interest,
            call_volume=call_volume,
            put_volume=put_volume,
            bid=bid,
            ask=ask,
            rel_volume=rel_volume,
            price_change_pct=price_change_pct,
            avg_daily_volume=avg_daily_volume,
            dollar_volume=dollar_volume,
            atr_pct=atr_pct,
            near_key_level=near_key_level,
            volume_increasing=volume_increasing,
            spy_agrees=spy_agrees,
            sector_agrees=sector_agrees,
        )

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

    # Unusual activity scorer — A+ setup
    print("\n--- Unusual activity score: SOFI strong setup (should be A+) ---")
    score, direction, rationale = score_unusual_activity(
        symbol="SOFI",
        options_volume=95_000, open_interest=37_000,   # Vol/OI = 2.6x — genuinely unusual
        call_volume=78_000, put_volume=17_000,
        bid=0.54, ask=0.56,   # tight spread
        rel_volume=2.8, price_change_pct=0.047,
        avg_daily_volume=45_000_000, dollar_volume=15 * 45_000_000,
        atr_pct=0.032,
        near_key_level=True, volume_increasing=True,
        spy_agrees=True, sector_agrees=True,
    )
    for r in rationale:
        print(f"  {r}")

    # Unusual activity scorer — weak setup (should be B or NO TRADE)
    print("\n--- Unusual activity score: weak setup (should be B/NO TRADE) ---")
    score2, direction2, rationale2 = score_unusual_activity(
        symbol="SOUN",
        options_volume=800, open_interest=12_000,
        call_volume=500, put_volume=300,
        bid=0.19, ask=0.24,
        rel_volume=1.1, price_change_pct=-0.008,
        avg_daily_volume=8_000_000, dollar_volume=6 * 8_000_000,
        atr_pct=0.038,
        near_key_level=False, volume_increasing=False,
        spy_agrees=False, sector_agrees=True,
    )
    for r in rationale2:
        print(f"  {r}")

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
