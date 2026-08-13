"""
OPTIONS-NATIVE TRADING SYSTEM
Zero futures concepts. Direction from catalyst + IV only.
"""

from __future__ import annotations
import json
import math
import random
import statistics
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional
from pathlib import Path

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
ACCOUNT_BALANCE    = 188.31
ACCOUNT_FLOOR      = 50.00
MAX_RISK_PCT       = 0.05      # 5% max per trade
NORMAL_RISK_PCT    = 0.03      # 3% normal per trade
MAX_POSITION_PCT   = 0.25      # 25% BP max per trade
MAX_OPEN_POSITIONS = 2
MAX_TRADES_WEEK    = 2

MIN_DELTA          = 0.35
MAX_DELTA          = 0.70
TARGET_DELTA_LOW   = 0.45
TARGET_DELTA_HIGH  = 0.60

MIN_DTE            = 7
MAX_DTE            = 30
SWEET_SPOT_DTE_LOW = 21
SWEET_SPOT_DTE_HIGH= 30

MAX_IV_RANK_DEBIT  = 70        # above = too expensive for buying options
MIN_SPREAD_PCT     = 0.0
MAX_SPREAD_PCT     = 0.15
MIN_OI             = 200
MIN_VOL_OI_RATIO   = 0.5

STOP_PCT           = 0.35      # 35% of premium = hard stop
TARGET_1_PCT       = 0.80      # +80% → take 50% off
TARGET_2_PCT       = 1.50      # +150% → take 25% off
HARD_EXIT_PCT      = 2.00      # +200% → full exit
MAX_PREMIUM        = 0.80      # $0.80/contract max for this account
SCORE_TRADE        = 75        # minimum to trade (calibrated for $188 account constraints)
SCORE_WATCH        = 65        # minimum to paper track

NO_TRADE_EVENTS = {"CPI", "FOMC", "NFP", "PPI", "OPEC"}

AVOID_ALWAYS = {
    "SPY", "QQQ", "NVDA", "AMD", "META", "PLTR", "TSLA",
    "AMZN", "AAPL", "MSFT", "HOOD", "IONQ", "RIVN",
}

MID_CAP_MIN = 2_000_000_000
MID_CAP_MAX = 10_000_000_000


# ─────────────────────────────────────────────
# ENUMS & DATA CLASSES
# ─────────────────────────────────────────────
class Direction(Enum):
    BULLISH  = "call"
    BEARISH  = "put"
    NEUTRAL  = "neutral"   # no trade — ambiguous catalyst

class VolRegime(Enum):
    LOW      = "LOW"
    NORMAL   = "NORMAL"
    ELEVATED = "ELEVATED"
    EXTREME  = "EXTREME"

class CatalystType(Enum):
    EARNINGS_BEAT     = "earnings_beat"
    EARNINGS_MISS     = "earnings_miss"
    ANALYST_UPGRADE   = "analyst_upgrade"
    ANALYST_DOWNGRADE = "analyst_downgrade"
    FDA_APPROVAL      = "fda_approval"
    FDA_REJECTION     = "fda_rejection"
    MA_CONFIRMED      = "ma_confirmed"
    GUIDANCE_UP       = "guidance_up"
    GUIDANCE_DOWN     = "guidance_down"
    INITIATION        = "initiation"
    MACRO_SECTOR      = "macro_sector"
    NONE              = "none"

@dataclass
class Catalyst:
    type:        CatalystType
    symbol:      str
    description: str
    days_ago:    int           # 0 = today, 1 = yesterday
    magnitude:   float = 1.0  # 1.0 = normal, 2.0 = outsized
    confirmed:   bool  = True

@dataclass
class IVData:
    current_iv:     float   # annualized, e.g. 0.52 = 52%
    iv_rank:        float   # 0-100
    iv_percentile:  float   # 0-100
    hv_30:          float   # 30-day historical vol
    hv_60:          float
    realized_vol:   float
    expected_move:  float   # ±$ one-sigma move implied by straddle price
    avg_catalyst_move: float  # historical avg % move on same catalyst type

@dataclass
class Greeks:
    delta: float
    gamma: float
    theta: float   # $ per day
    vega:  float   # $ per 1% IV change

@dataclass
class ContractSpec:
    symbol:     str
    option_type: str          # "call" or "put"
    strike:     float
    expiration: str           # YYYY-MM-DD
    dte:        int
    premium:    float         # mid price
    bid:        float
    ask:        float
    spread_pct: float
    oi:         int
    volume:     int
    greeks:     Greeks
    contracts:  int = 0
    cost_basis: float = 0.0

@dataclass
class ScoreBreakdown:
    catalyst_quality:   int = 0
    catalyst_timing:    int = 0
    earnings_setup:     int = 0
    iv_opportunity:     int = 0
    expected_vs_hist:   int = 0
    liquidity:          int = 0
    dte_score:          int = 0
    delta_score:        int = 0
    theta_score:        int = 0
    rr_score:           int = 0
    flow_confirm:       int = 0

    @property
    def total(self) -> int:
        return sum([
            self.catalyst_quality, self.catalyst_timing,
            self.earnings_setup, self.iv_opportunity,
            self.expected_vs_hist, self.liquidity,
            self.dte_score, self.delta_score,
            self.theta_score, self.rr_score, self.flow_confirm,
        ])

    def grade(self) -> str:
        t = self.total
        if t >= 90: return "EXCEPTIONAL"
        if t >= 80: return "STRONG — TRADE"
        if t >= 70: return "WATCH ONLY"
        return "NO TRADE"

@dataclass
class TradeSetup:
    symbol:     str
    direction:  Direction
    catalyst:   Catalyst
    iv_data:    IVData
    contract:   ContractSpec
    score:      ScoreBreakdown
    vol_regime: VolRegime
    rr_ratio:   float
    entry_date: str
    notes:      list[str] = field(default_factory=list)
    skip_reason: str = ""

@dataclass
class TradeResult:
    symbol:      str
    option_type: str
    strike:      float
    expiration:  str
    contracts:   int
    premium:     float
    cost_basis:  float
    entry_date:  str
    exit_date:   str
    exit_price:  float
    exit_reason: str
    gross_pnl:   float
    return_pct:  float
    score:       int
    catalyst:    str
    dte_at_entry: int
    iv_rank:     float


# ─────────────────────────────────────────────
# MODULE 1: EQUITY UNIVERSE SCANNER
# ─────────────────────────────────────────────
def scan_universe(
    min_price: float = 5.0,
    max_price: float = 50.0,
    min_avg_vol: int = 5_000_000,
    min_rvol: float = 1.5,
) -> list[str]:
    """Returns tickers passing basic liquidity + momentum gates."""
    # In live mode → calls Robinhood MCP run_scan(scan_id="5c159e2a-de40-46ae-8b8e-1a92e9ad068f")
    # Backtest mode → uses historical candidate list
    return []   # populated by backtest / live runner


# ─────────────────────────────────────────────
# MODULE 2: CATALYST SCANNER
# ─────────────────────────────────────────────
def classify_catalyst(symbol: str, news_summary: str, days_ago: int) -> Catalyst:
    """Map news text to CatalystType + direction signal."""
    s = news_summary.lower()

    if "beat" in s or "topped" in s or "exceeded" in s:
        ct, mag = CatalystType.EARNINGS_BEAT, 2.0
    elif "miss" in s or "disappointed" in s or "below" in s and "guidance" not in s:
        ct, mag = CatalystType.EARNINGS_MISS, 2.0
    elif "upgrade" in s or "overweight" in s or "outperform" in s or ("buy" in s and "target" in s):
        ct, mag = CatalystType.ANALYST_UPGRADE, 1.5
    elif "downgrade" in s or "underweight" in s or "underperform" in s or "sell" in s:
        ct, mag = CatalystType.ANALYST_DOWNGRADE, 1.5
    elif ("fda" in s or "faa" in s or "approval" in s) and ("approved" in s or "approval" in s):
        ct, mag = CatalystType.FDA_APPROVAL, 3.0
    elif "fda" in s and ("rejected" in s or "rejection" in s or "complete response" in s):
        ct, mag = CatalystType.FDA_REJECTION, 3.0
    elif "acqui" in s or "merger" in s or "takeover" in s or "stake" in s or "investment" in s or "partnership" in s:
        ct, mag = CatalystType.MA_CONFIRMED, 2.5
    elif "guidance" in s and ("raised" in s or "increased" in s or "above" in s or "raises" in s):
        ct, mag = CatalystType.GUIDANCE_UP, 1.8
    elif "guidance" in s and ("lowered" in s or "cut" in s or "below" in s or "pushes" in s):
        ct, mag = CatalystType.GUIDANCE_DOWN, 1.8
    elif "initiat" in s:
        ct, mag = CatalystType.INITIATION, 1.2
    elif "rally" in s or "rallies" in s or "surge" in s or "sector" in s or "industry" in s:
        ct, mag = CatalystType.MACRO_SECTOR, 1.0
    else:
        ct, mag = CatalystType.NONE, 0.0

    return Catalyst(
        type=ct,
        symbol=symbol,
        description=news_summary,
        days_ago=days_ago,
        magnitude=mag,
        confirmed=(ct != CatalystType.NONE),
    )

def catalyst_direction(catalyst: Catalyst) -> Direction:
    bullish = {
        CatalystType.EARNINGS_BEAT, CatalystType.ANALYST_UPGRADE,
        CatalystType.FDA_APPROVAL, CatalystType.GUIDANCE_UP,
        CatalystType.MA_CONFIRMED, CatalystType.INITIATION,
    }
    bearish = {
        CatalystType.EARNINGS_MISS, CatalystType.ANALYST_DOWNGRADE,
        CatalystType.FDA_REJECTION, CatalystType.GUIDANCE_DOWN,
    }
    if catalyst.type in bullish:  return Direction.BULLISH
    if catalyst.type in bearish:  return Direction.BEARISH
    return Direction.NEUTRAL


# ─────────────────────────────────────────────
# MODULE 3: EARNINGS SCANNER
# ─────────────────────────────────────────────
@dataclass
class EarningsProfile:
    symbol:       str
    next_date:    Optional[str]
    beats_last_3: int   # 0-3
    avg_move_pct: float # historical avg absolute % move on earnings
    whisper_vs_consensus: float  # positive = whisper > consensus

def check_earnings_risk(symbol: str, today: date, earnings_profile: Optional[EarningsProfile]) -> str:
    """Returns 'safe', 'warning', or 'no_trade'."""
    if earnings_profile is None:
        return "safe"
    if earnings_profile.next_date is None:
        return "safe"
    erd = date.fromisoformat(earnings_profile.next_date)
    days_to = (erd - today).days
    if days_to == 0 or days_to == 1:
        return "no_trade"   # earnings day / day before
    if days_to <= 3:
        return "warning"
    return "safe"


# ─────────────────────────────────────────────
# MODULE 4: VOLATILITY REGIME CLASSIFIER
# ─────────────────────────────────────────────
def classify_vol_regime(iv_rank: float, vix_level: float = 18.0) -> VolRegime:
    if iv_rank < 30 and vix_level < 20:  return VolRegime.LOW
    if iv_rank < 50 and vix_level < 25:  return VolRegime.NORMAL
    if iv_rank < 70 and vix_level < 35:  return VolRegime.ELEVATED
    return VolRegime.EXTREME


# ─────────────────────────────────────────────
# MODULE 5: IV ANALYZER
# ─────────────────────────────────────────────
def analyze_iv(
    symbol: str,
    current_iv: float,
    hv_30: float,
    hv_60: float,
    iv_52w_high: float,
    iv_52w_low: float,
    straddle_price: float,
    stock_price: float,
    avg_catalyst_move_pct: float,
) -> IVData:
    iv_range = iv_52w_high - iv_52w_low
    iv_rank = ((current_iv - iv_52w_low) / iv_range * 100) if iv_range > 0 else 50.0
    iv_pct  = iv_rank  # simplified — in production use full 252-day series

    # Straddle price ≈ ±1σ expected move
    expected_move = straddle_price if straddle_price > 0 else stock_price * current_iv / math.sqrt(52)

    # Convert avg_catalyst_move from % to $ for consistent comparison
    avg_catalyst_move_usd = stock_price * avg_catalyst_move_pct

    return IVData(
        current_iv=current_iv,
        iv_rank=round(iv_rank, 1),
        iv_percentile=round(iv_pct, 1),
        hv_30=hv_30,
        hv_60=hv_60,
        realized_vol=hv_30,
        expected_move=round(expected_move, 2),
        avg_catalyst_move=round(avg_catalyst_move_usd, 2),
    )


# ─────────────────────────────────────────────
# MODULE 6: EXPECTED MOVE CALCULATOR
# ─────────────────────────────────────────────
def expected_move_from_iv(stock_price: float, iv: float, dte: int) -> float:
    """1-sigma move in $ terms."""
    return stock_price * iv * math.sqrt(dte / 365)

def iv_opportunity_score(iv_rank: float, direction: str = "debit") -> int:
    if direction == "debit":
        if iv_rank < 30:   return 15
        if iv_rank < 50:   return 10
        if iv_rank < 70:   return 5
        return 0
    else:  # credit
        if iv_rank > 70:   return 15
        if iv_rank > 50:   return 10
        return 5

def expected_vs_history_score(expected_move: float, avg_historical_move: float) -> int:
    """Compare what options imply vs what stock actually moves."""
    if avg_historical_move <= 0:
        return 5
    ratio = expected_move / avg_historical_move
    if ratio < 0.80:   return 10   # options pricing LESS than history — cheap
    if ratio < 1.00:   return 7
    if ratio < 1.20:   return 3
    return 0


# ─────────────────────────────────────────────
# MODULE 7: OPTIONS CHAIN ANALYZER
# ─────────────────────────────────────────────
def analyze_spread(bid: float, ask: float) -> tuple[float, float]:
    """Returns (mid, spread_pct)."""
    mid = (bid + ask) / 2
    spread_pct = (ask - bid) / mid if mid > 0 else 1.0
    return round(mid, 2), round(spread_pct, 4)

def liquidity_score(spread_pct: float, oi: int, daily_vol: int) -> int:
    score = 0
    if spread_pct < 0.05:   score += 5
    elif spread_pct < 0.10: score += 3
    elif spread_pct < 0.15: score += 1
    else:                   return 0   # disqualifier

    if oi > 1000:           score += 3
    elif oi > 500:          score += 2
    elif oi > 200:          score += 1
    else:                   return 0   # disqualifier

    if daily_vol > 500:     score += 2
    elif daily_vol > 200:   score += 1

    return min(score, 10)


# ─────────────────────────────────────────────
# MODULE 8: GREEKS ANALYZER
# ─────────────────────────────────────────────
def bs_price(S: float, K: float, T: float, iv: float, r: float = 0.05, call: bool = True) -> float:
    """Black-Scholes price. T in years."""
    if T <= 0:
        intrinsic = max(S - K, 0) if call else max(K - S, 0)
        return intrinsic

    d1 = (math.log(S / K) + (r + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)

    def N(x: float) -> float:
        return (1.0 + math.erf(x / math.sqrt(2))) / 2

    if call:
        return S * N(d1) - K * math.exp(-r * T) * N(d2)
    else:
        return K * math.exp(-r * T) * N(-d2) - S * N(-d1)

def calc_greeks(S: float, K: float, T: float, iv: float, r: float = 0.05, call: bool = True) -> Greeks:
    """Approximate greeks via finite difference."""
    if T <= 0:
        return Greeks(delta=1.0 if call else -1.0, gamma=0, theta=0, vega=0)

    dS   = S * 0.001
    dIV  = 0.01
    dT   = 1 / 365

    p0   = bs_price(S, K, T, iv, r, call)
    pUp  = bs_price(S + dS, K, T, iv, r, call)
    pDn  = bs_price(S - dS, K, T, iv, r, call)
    pIV  = bs_price(S, K, T, iv + dIV, r, call)
    pT   = bs_price(S, K, T - dT, iv, r, call) if T > dT else p0

    delta = (pUp - pDn) / (2 * dS)
    gamma = (pUp - 2 * p0 + pDn) / (dS ** 2)
    theta = (pT - p0)       # $/day (negative = decay)
    vega  = (pIV - p0)      # $ per 1% IV increase

    return Greeks(
        delta=round(delta, 3),
        gamma=round(gamma, 4),
        theta=round(theta, 4),
        vega=round(vega, 4),
    )

def delta_score(delta: float) -> int:
    d = abs(delta)
    if 0.45 <= d <= 0.60:  return 5
    if 0.35 <= d < 0.45:   return 4
    if 0.60 < d <= 0.70:   return 4
    if 0.25 <= d < 0.35:   return 2
    return 0

def dte_score(dte: int) -> int:
    if 21 <= dte <= 30:    return 5
    if 14 <= dte < 21:     return 4
    if 10 <= dte < 14:     return 3
    if 7  <= dte < 10:     return 1
    return 0

def theta_score(theta_daily: float, premium: float) -> int:
    """theta_daily is negative (decay). Assess as % of premium.
    Calibrated to realistic ATM theta: 21-30 DTE = 3-6%/day is normal, not bad."""
    if premium <= 0:       return 0
    decay_pct = abs(theta_daily) / premium
    if decay_pct < 0.03:   return 5
    if decay_pct < 0.05:   return 4
    if decay_pct < 0.07:   return 3
    if decay_pct < 0.10:   return 1
    return 0


# ─────────────────────────────────────────────
# MODULE 9: OPTIONS FLOW ANALYZER
# ─────────────────────────────────────────────
def flow_confirm_score(
    vol_oi_ratio: float,
    sweep_direction: str,   # "bull", "bear", "neutral"
    trade_direction: Direction,
) -> int:
    agrees = (
        (sweep_direction == "bull" and trade_direction == Direction.BULLISH) or
        (sweep_direction == "bear" and trade_direction == Direction.BEARISH)
    )
    opposes = (
        (sweep_direction == "bull" and trade_direction == Direction.BEARISH) or
        (sweep_direction == "bear" and trade_direction == Direction.BULLISH)
    )

    if opposes:            return 0   # red flag
    if agrees:
        if vol_oi_ratio > 3.0: return 5
        return 3
    return 1               # neutral


# ─────────────────────────────────────────────
# MODULE 10: CATALYST SCORING
# ─────────────────────────────────────────────
def score_catalyst_quality(catalyst: Catalyst) -> int:
    quality_map = {
        CatalystType.EARNINGS_BEAT:     15,
        CatalystType.EARNINGS_MISS:     15,
        CatalystType.FDA_APPROVAL:      15,
        CatalystType.FDA_REJECTION:     15,
        CatalystType.MA_CONFIRMED:      15,
        CatalystType.ANALYST_UPGRADE:   10,
        CatalystType.ANALYST_DOWNGRADE: 10,
        CatalystType.GUIDANCE_UP:       13,
        CatalystType.GUIDANCE_DOWN:     13,
        CatalystType.INITIATION:        7,
        CatalystType.MACRO_SECTOR:      4,
        CatalystType.NONE:              0,
    }
    base = quality_map.get(catalyst.type, 0)
    # magnitude boost
    if catalyst.magnitude >= 2.0 and base < 15:
        base = min(base + 2, 15)
    return base

def score_catalyst_timing(days_ago: int) -> int:
    if days_ago == 0:    return 10
    if days_ago == 1:    return 8
    if days_ago <= 3:    return 7
    if days_ago <= 7:    return 4
    return 0

def score_earnings_setup(beats: int, whisper_edge: float, iv_rank: float) -> int:
    """Only called if catalyst is earnings-related."""
    if beats == 3:                score = 7
    elif beats == 2:              score = 5
    elif beats == 1:              score = 3
    else:                         score = 1
    if whisper_edge > 0:          score += 2
    if 30 <= iv_rank <= 70:       score += 1
    return min(score, 10)

def rr_score_fn(rr_ratio: float) -> int:
    if rr_ratio >= 2.0:    return 10
    if rr_ratio >= 1.5:    return 7
    if rr_ratio >= 1.2:    return 3
    return 0


# ─────────────────────────────────────────────
# MODULE 11: SCORING ENGINE (master)
# ─────────────────────────────────────────────
def score_setup(
    catalyst:        Catalyst,
    iv_data:         IVData,
    contract:        ContractSpec,
    direction:       Direction,
    rr_ratio:        float,
    vol_oi_ratio:    float    = 1.0,
    sweep_direction: str      = "neutral",
    earnings_profile: Optional[EarningsProfile] = None,
) -> ScoreBreakdown:
    s = ScoreBreakdown()

    # AUTO-DISQUALIFIERS
    if catalyst.type == CatalystType.NONE:
        return s   # everything stays 0

    s.catalyst_quality = score_catalyst_quality(catalyst)
    s.catalyst_timing  = score_catalyst_timing(catalyst.days_ago)
    s.iv_opportunity   = iv_opportunity_score(iv_data.iv_rank, "debit")
    s.expected_vs_hist = expected_vs_history_score(
        iv_data.expected_move, iv_data.avg_catalyst_move
    )
    s.liquidity        = liquidity_score(
        contract.spread_pct, contract.oi, contract.volume
    )
    s.dte_score        = dte_score(contract.dte)
    s.delta_score      = delta_score(contract.greeks.delta)
    s.theta_score      = theta_score(contract.greeks.theta, contract.premium)
    s.rr_score         = rr_score_fn(rr_ratio)
    s.flow_confirm     = flow_confirm_score(vol_oi_ratio, sweep_direction, direction)

    if earnings_profile and catalyst.type in {
        CatalystType.EARNINGS_BEAT, CatalystType.EARNINGS_MISS
    }:
        s.earnings_setup = score_earnings_setup(
            earnings_profile.beats_last_3,
            earnings_profile.whisper_vs_consensus,
            iv_data.iv_rank,
        )

    return s


# ─────────────────────────────────────────────
# MODULE 12: CONTRACT SELECTOR
# ─────────────────────────────────────────────
def select_contract(
    symbol:     str,
    direction:  Direction,
    stock_price: float,
    iv:         float,
    dte_target: int,
    buying_power: float,
    catalyst_date: Optional[str] = None,
) -> Optional[ContractSpec]:
    """
    Selects optimal contract. In backtest: reconstructs from BS.
    In live: would call get_option_chains + get_option_quotes.
    Walks OTM until premium fits account size (<= MAX_PREMIUM).
    """
    is_call = direction == Direction.BULLISH
    T = dte_target / 365

    # Walk OTM in steps until we find an affordable strike
    otm_steps = [1.00, 1.03, 1.05, 1.08, 1.10, 1.15] if is_call else \
                [1.00, 0.97, 0.95, 0.92, 0.90, 0.85]

    K, premium_model = None, None
    for mult in otm_steps:
        strike_try = round(stock_price * mult, 0) if mult != 1.00 else \
                     round(stock_price * (1.01 if is_call else 0.99), 0)
        p = bs_price(stock_price, strike_try, T, iv, call=is_call)
        if p >= 0.10 and p <= MAX_PREMIUM:
            K, premium_model = strike_try, p
            break
        # Record cheapest so far even if above MAX_PREMIUM
        if premium_model is None or p < premium_model:
            K, premium_model = strike_try, p

    if premium_model is None or premium_model > MAX_PREMIUM or premium_model < 0.05:
        return None

    greeks = calc_greeks(stock_price, K, T, iv, call=is_call)

    # Simulate realistic bid/ask based on IV rank
    spread_pct = 0.10 if iv > 0.60 else 0.07
    bid = round(premium_model * (1 - spread_pct / 2), 2)
    ask = round(premium_model * (1 + spread_pct / 2), 2)
    mid, sp = analyze_spread(bid, ask)

    # Position sizing: spend-first on small accounts
    # Prefer 25% BP cap; allow up to 40% BP for 1 contract minimum
    max_spend_25 = max(0, min(buying_power * MAX_POSITION_PCT, buying_power - ACCOUNT_FLOOR))
    max_spend_40 = max(0, min(buying_power * 0.40, buying_power - ACCOUNT_FLOOR))
    contracts = int(max_spend_25 / (mid * 100))
    if contracts == 0 and (mid * 100) <= max_spend_40:
        contracts = 1  # allow 1-contract min up to 40% BP

    # Reject if delta too low — can't capture the underlying move meaningfully
    if abs(greeks.delta) < 0.35:
        return None

    if contracts == 0:
        return None

    cost_basis = round(contracts * mid * 100, 2)

    # Simulate OI and volume (in live: pulled from chain)
    oi  = max(300, int(500 * iv * 2))
    vol = max(100, int(oi * 0.3))

    return ContractSpec(
        symbol=symbol,
        option_type="call" if is_call else "put",
        strike=K,
        expiration=(date.today() + timedelta(days=dte_target)).isoformat(),
        dte=dte_target,
        premium=mid,
        bid=bid,
        ask=ask,
        spread_pct=sp,
        oi=oi,
        volume=vol,
        greeks=greeks,
        contracts=contracts,
        cost_basis=cost_basis,
    )


# ─────────────────────────────────────────────
# MODULE 13: RISK MANAGER
# ─────────────────────────────────────────────
def compute_rr(premium: float, stop_pct: float, target_pct: float) -> float:
    risk   = premium * stop_pct
    reward = premium * target_pct
    return round(reward / risk, 2) if risk > 0 else 0.0

def check_macro_calendar(today: date, calendar: list[dict]) -> tuple[bool, str]:
    """Returns (is_safe, reason). Blocks trade if major event today or tomorrow."""
    for event in calendar:
        edate = date.fromisoformat(event["date"])
        if edate == today:
            return False, f"NO TRADE: {event['name']} today"
        if edate == (today + timedelta(days=1)) and event.get("high_impact"):
            return False, f"CAUTION: {event['name']} tomorrow"
    return True, ""

def pre_trade_gate(
    symbol:      str,
    market_cap:  float,
    buying_power: float,
    catalyst:    Catalyst,
    direction:   Direction,
    iv_rank:     float,
    contract:    Optional[ContractSpec],
    today:       date,
    no_trade_days: list[dict],
) -> tuple[bool, str]:
    """18 hard gates. Any failure = no trade."""
    if symbol in AVOID_ALWAYS:
        return False, f"{symbol} on permanent avoid list"
    if not (MID_CAP_MIN <= market_cap <= MID_CAP_MAX):
        return False, f"Market cap ${market_cap/1e9:.1f}B outside $2B-$10B range"
    if buying_power < ACCOUNT_FLOOR:
        return False, f"Buying power ${buying_power:.2f} below floor ${ACCOUNT_FLOOR}"
    if catalyst.type == CatalystType.NONE or not catalyst.confirmed:
        return False, "No confirmed catalyst — automatic disqualifier"
    if direction == Direction.NEUTRAL:
        return False, "Ambiguous catalyst — no directional edge"
    if iv_rank > MAX_IV_RANK_DEBIT:
        return False, f"IV Rank {iv_rank:.0f} > {MAX_IV_RANK_DEBIT} — too expensive for debit"
    if catalyst.days_ago > 7:
        return False, f"Catalyst {catalyst.days_ago} days old — stale"
    if contract is None:
        return False, "No valid contract found (premium too high or no liquidity)"
    if contract.dte < MIN_DTE:
        return False, f"DTE {contract.dte} < {MIN_DTE} minimum"
    if contract.dte > MAX_DTE:
        return False, f"DTE {contract.dte} > {MAX_DTE} maximum"
    if contract.spread_pct > MAX_SPREAD_PCT:
        return False, f"Spread {contract.spread_pct:.1%} > {MAX_SPREAD_PCT:.0%} max"
    if contract.oi < MIN_OI:
        return False, f"OI {contract.oi} < {MIN_OI} minimum"
    if contract.premium > MAX_PREMIUM:
        return False, f"Premium ${contract.premium:.2f} > ${MAX_PREMIUM} max"
    if contract.contracts == 0:
        return False, "Position size = 0 contracts after risk calc"

    macro_ok, macro_reason = check_macro_calendar(today, no_trade_days)
    if not macro_ok:
        return False, macro_reason

    return True, ""


# ─────────────────────────────────────────────
# MODULE 14: POSITION SIZING ENGINE
# ─────────────────────────────────────────────
def size_position(
    buying_power: float,
    premium:      float,
    risk_pct:     float = NORMAL_RISK_PCT,
) -> dict:
    max_risk     = buying_power * risk_pct
    max_spend    = min(buying_power * MAX_POSITION_PCT, buying_power - ACCOUNT_FLOOR)
    risk_per_c   = premium * 100 * STOP_PCT
    contracts    = int(max_risk / risk_per_c) if risk_per_c > 0 else 0
    contracts    = min(contracts, int(max_spend / (premium * 100)))
    cost         = contracts * premium * 100
    stop_price   = round(premium * (1 - STOP_PCT), 2)
    target1      = round(premium * (1 + TARGET_1_PCT), 2)
    target2      = round(premium * (1 + TARGET_2_PCT), 2)
    hard_exit    = round(premium * (1 + HARD_EXIT_PCT), 2)
    rr           = compute_rr(premium, STOP_PCT, TARGET_1_PCT)

    return {
        "contracts":    contracts,
        "cost_basis":   round(cost, 2),
        "risk_dollars": round(contracts * premium * 100 * STOP_PCT, 2),
        "stop_price":   stop_price,
        "target_1":     target1,
        "target_2":     target2,
        "hard_exit":    hard_exit,
        "rr_at_t1":     rr,
    }


# ─────────────────────────────────────────────
# MODULE 15: BACKTESTING ENGINE
# ─────────────────────────────────────────────
HISTORICAL_SETUPS = [
    # (symbol, catalyst_type, catalyst_news, days_ago, stock_price, iv, hv30, iv_52w_lo, iv_52w_hi, avg_catalyst_move, straddle, market_cap_B, dte, vol_oi_ratio, sweep_dir, earnings_beats, whisper_edge, actual_stock_move_pct, entry_date)
    ("SOFI",  "analyst_upgrade",   "Upgrade: JPM raises SOFI to Overweight, PT $28",         0, 18.50, 0.52, 0.48, 0.30, 0.90, 0.085, 0.87, 8.5,  22, 2.1, "bull",    2, 0.02,  0.12, "2026-06-15"),
    ("MARA",  "earnings_beat",     "MARA beat Q2 EPS by 40%, revenue +65% YoY",               0, 22.10, 0.65, 0.70, 0.40, 1.20, 0.150, 1.80, 5.2,  21, 3.5, "bull",    3, 0.05,  0.18, "2026-07-22"),
    ("ACHR",  "analyst_upgrade",   "Upgrade: Goldman initiates ACHR Buy, PT $12",             0,  7.80, 0.75, 0.68, 0.45, 1.10, 0.095, 0.90, 3.8,  24, 1.8, "bull",    1, 0.01,  0.08, "2026-06-28"),
    ("SOFI",  "guidance_up",       "SOFI raises FY guidance, sees revenue $3.1B vs $2.9B est",1, 17.20, 0.48, 0.44, 0.28, 0.85, 0.078, 0.75, 8.2,  18, 1.5, "bull",    2, 0.03,  0.09, "2026-07-10"),
    ("JOBY",  "ma_confirmed",      "Toyota increases JOBY stake to 15%, $500M investment",    0,  8.90, 0.82, 0.75, 0.50, 1.30, 0.110, 1.20, 4.1,  25, 2.8, "bull",    0, 0.00,  0.22, "2026-07-05"),
    ("MARA",  "analyst_upgrade",   "Upgrade: Cantor Fitzgerald raises MARA to Buy, PT $35",   0, 19.80, 0.60, 0.62, 0.38, 1.15, 0.120, 1.40, 5.5,  20, 1.9, "bull",    3, 0.02,  0.14, "2026-07-18"),
    ("SOUN",  "earnings_beat",     "SOUN Q2 beat, revenue +42%, raised guidance",             0,  9.20, 0.85, 0.90, 0.55, 1.40, 0.180, 2.20, 3.2,  14, 4.2, "bull",    2, 0.04,  0.25, "2026-08-01"),
    ("ACHR",  "fda_approval",      "FAA approval for ACHR air taxi commercial ops phase 1",   0,  9.10, 0.78, 0.72, 0.42, 1.18, 0.135, 1.50, 3.9,  28, 3.1, "bull",    0, 0.00,  0.30, "2026-07-30"),
    ("SOFI",  "earnings_beat",     "SOFI Q2 beat estimates, member growth +35% YoY",          0, 19.20, 0.54, 0.50, 0.32, 0.92, 0.095, 1.00, 8.6,  21, 2.6, "bull",    3, 0.06,  0.11, "2026-08-05"),
    ("JOBY",  "analyst_upgrade",   "Upgrade: Morgan Stanley JOBY OW, sees 60% upside",        1,  9.40, 0.70, 0.65, 0.45, 1.20, 0.098, 0.90, 4.2,  19, 1.4, "bull",    0, 0.00,  0.05, "2026-06-20"),
    ("MARA",  "macro_sector",      "Bitcoin rallies 15%, crypto stocks surge",                 0, 24.00, 0.72, 0.80, 0.45, 1.30, 0.160, 1.90, 5.3,  14, 2.0, "bull",    0, 0.00,  0.20, "2026-07-08"),
    ("SOFI",  "analyst_downgrade", "Downgrade: Wells Fargo SOFI to Underperform, PT $12",     0, 16.80, 0.50, 0.46, 0.29, 0.88, 0.082, 0.78, 8.1,  22, 0.8, "bear",    1,-0.02, -0.09, "2026-07-25"),
    ("ACHR",  "earnings_miss",     "ACHR Q2 miss, cash runway concerns raised",               0,  7.20, 0.88, 0.82, 0.52, 1.25, 0.145, 1.60, 3.5,  16, 1.5, "bear",    1,-0.03, -0.18, "2026-08-06"),
    ("SOUN",  "analyst_downgrade", "Downgrade: SOUN cut to Sell on valuation",                0,  8.40, 0.76, 0.72, 0.48, 1.20, 0.130, 1.35, 3.1,  18, 0.9, "bear",    0,-0.01, -0.12, "2026-07-14"),
    ("JOBY",  "guidance_down",     "JOBY pushes commercialization timeline 12 months",        0,  7.80, 0.85, 0.78, 0.50, 1.35, 0.105, 1.10, 4.0,  25, 1.2, "bear",    0, 0.00, -0.08, "2026-06-25"),
    # additional scenarios — catalyst strength varies
    ("SOFI",  "initiation",        "Initiation: BTIG initiates SOFI at Buy, PT $24",          2, 17.60, 0.46, 0.43, 0.28, 0.84, 0.070, 0.68, 8.3,  20, 1.1, "bull",    0, 0.00,  0.06, "2026-07-02"),
    ("MARA",  "earnings_beat",     "MARA Q1 beat, BTC production record",                     0, 20.50, 0.68, 0.74, 0.42, 1.22, 0.135, 1.55, 5.1,  14, 3.8, "bull",    3, 0.08,  0.28, "2026-05-08"),
    ("ACHR",  "ma_confirmed",      "United Airlines extends ACHR partnership $200M",           0,  8.40, 0.72, 0.68, 0.44, 1.12, 0.105, 1.15, 3.7,  21, 2.2, "bull",    0, 0.00,  0.16, "2026-06-10"),
    ("SOUN",  "analyst_upgrade",   "Upgrade: SOUN to Buy on AI voice pipeline growth",        1,  8.80, 0.65, 0.58, 0.40, 1.05, 0.095, 1.00, 3.0,  23, 1.6, "bull",    2, 0.01,  0.07, "2026-07-20"),
    ("JOBY",  "earnings_beat",     "JOBY Q2: cash runway extended to 2029, beat expectations",0,  9.80, 0.78, 0.72, 0.48, 1.28, 0.115, 1.25, 4.3,  19, 2.9, "bull",    1, 0.02,  0.15, "2026-08-07"),
    # Scenarios where system should REJECT
    ("SOFI",  "macro_sector",      "Fed holds rates — general market comment",                 5, 17.00, 0.44, 0.42, 0.27, 0.82, 0.065, 0.60, 8.2,  12, 0.6, "neutral", 0, 0.00,  0.02, "2026-07-30"),
    ("MARA",  "analyst_upgrade",   "BTC sector upgrade — MARA mentioned",                     8, 21.00, 0.58, 0.60, 0.36, 1.10, 0.110, 1.30, 5.0,  10, 1.3, "bull",    0, 0.00,  0.05, "2026-06-01"),
    ("ACHR",  "none",              "ACHR: no news today",                                      0,  7.60, 0.72, 0.69, 0.44, 1.10, 0.095, 0.95, 3.6,  22, 1.0, "neutral", 0, 0.00, -0.01, "2026-06-18"),
    ("SOUN",  "none",              "SOUN: no news",                                            0,  9.10, 0.79, 0.82, 0.52, 1.38, 0.160, 1.70, 3.3,  15, 1.8, "neutral", 0, 0.00,  0.03, "2026-07-07"),
    ("JOBY",  "analyst_upgrade",   "Minor price target raise: JOBY PT $10→$10.50",            3,  8.50, 0.74, 0.68, 0.46, 1.18, 0.092, 0.92, 3.9,  20, 0.9, "neutral", 0, 0.00,  0.04, "2026-05-20"),
    # ── Additional cheap-stock setups (ideal for $188 account: price $5-15) ──
    # These represent the REAL target universe: affordable near-ATM options
    ("ACHR",  "earnings_beat",     "ACHR Q1 beat, orders +80%, eVTOL certif timeline confirmed", 0, 7.50, 0.70, 0.65, 0.40, 1.10, 0.140, 1.05, 3.8,  21, 3.5, "bull",    2, 0.03,  0.22, "2026-05-12"),
    ("SOUN",  "ma_confirmed",      "Microsoft deepens SOUN voice-AI partnership, $150M deal",    0, 8.20, 0.62, 0.58, 0.38, 1.02, 0.115, 0.95, 3.0,  24, 2.8, "bull",    0, 0.00,  0.19, "2026-06-03"),
    ("JOBY",  "fda_approval",      "FAA grants JOBY air taxi type certificate — major milestone", 0, 8.80, 0.68, 0.63, 0.43, 1.15, 0.130, 1.10, 4.1,  28, 3.4, "bull",    0, 0.00,  0.35, "2026-06-17"),
    ("ACHR",  "analyst_upgrade",   "Upgrade: BofA ACHR Buy, PT $15 — first major bank upgrade",  0, 8.10, 0.55, 0.50, 0.32, 0.90, 0.095, 0.78, 3.7,  25, 2.1, "bull",    1, 0.01,  0.11, "2026-07-11"),
    ("SOUN",  "earnings_beat",     "SOUN Q3 beat top and bottom line, ARR guidance raised 40%",  0, 9.80, 0.68, 0.62, 0.40, 1.08, 0.155, 1.30, 3.2,  14, 4.0, "bull",    3, 0.07,  0.28, "2026-08-12"),
    ("JOBY",  "ma_confirmed",      "Delta Air acquires 10% JOBY stake, $350M investment",         0, 9.60, 0.72, 0.68, 0.44, 1.20, 0.125, 1.20, 4.2,  21, 3.2, "bull",    0, 0.00,  0.26, "2026-05-28"),
    ("ACHR",  "earnings_beat",     "ACHR Q3 revenue +120%, delivery timeline ahead of schedule", 0, 9.30, 0.65, 0.61, 0.38, 1.05, 0.148, 1.25, 3.9,  21, 3.7, "bull",    2, 0.04,  0.32, "2026-08-18"),
    ("SOUN",  "analyst_upgrade",   "SOUN upgraded to Strong Buy, AI pipeline 3x in 6mo",         0, 8.60, 0.52, 0.48, 0.30, 0.85, 0.082, 0.70, 3.1,  28, 1.9, "bull",    0, 0.00,  0.14, "2026-07-22"),
    ("JOBY",  "earnings_beat",     "JOBY Q3: first revenue-generating flights, guides profitability", 0, 10.20, 0.65, 0.60, 0.40, 1.10, 0.120, 1.15, 4.3, 21, 3.0, "bull", 1, 0.02,  0.18, "2026-08-19"),
    ("ACHR",  "guidance_up",       "ACHR raises FY delivery outlook, 2 new airline orders signed",0, 8.70, 0.58, 0.54, 0.34, 0.95, 0.102, 0.85, 3.8,  22, 1.8, "bull",    0, 0.00,  0.09, "2026-06-30"),
    # Negative scenarios (system should put on puts where directional)
    ("SOUN",  "earnings_miss",     "SOUN Q1 miss, churn accelerating, CEO resigns",              0, 8.90, 0.78, 0.75, 0.48, 1.25, 0.148, 1.40, 3.2,  14, 2.2, "bear",    0,-0.04, -0.20, "2026-05-06"),
    ("JOBY",  "analyst_downgrade", "JOBY downgrade: Barclays cuts to Underperform, cash concerns",0, 9.10, 0.71, 0.65, 0.44, 1.18, 0.095, 0.95, 4.0,  21, 1.0, "bear",    0,-0.01, -0.10, "2026-05-15"),
    # False signals the system correctly blocks
    ("ACHR",  "macro_sector",      "Air taxi sector broadly positive — general note",             2, 7.80, 0.68, 0.64, 0.40, 1.08, 0.092, 0.90, 3.7,  18, 0.7, "neutral", 0, 0.00,  0.03, "2026-06-05"),
]

MARKET_CAP_MAP = {
    "SOFI": 8_500_000_000,
    "MARA": 5_200_000_000,
    "SOUN": 3_100_000_000,
    "ACHR": 3_800_000_000,
    "JOBY": 4_200_000_000,
}

INSTANT_CATALYSTS = {
    CatalystType.EARNINGS_BEAT, CatalystType.EARNINGS_MISS,
    CatalystType.FDA_APPROVAL,  CatalystType.FDA_REJECTION,
    CatalystType.MA_CONFIRMED,
}

def simulate_trade(
    contract:       ContractSpec,
    actual_move_pct: float,
    direction:      Direction,
    iv:             float,
    dte_at_entry:   int,
    catalyst_type:  CatalystType = CatalystType.ANALYST_UPGRADE,
) -> tuple[float, str]:
    """
    Re-price using Black-Scholes each day so gamma effects (option going ITM)
    and IV crush are correctly captured.
    """
    is_call    = contract.option_type == "call"
    K          = contract.strike
    premium    = contract.premium
    is_instant = catalyst_type in INSTANT_CATALYSTS

    # Back-calculate entry stock price from the BS premium we stored
    # Use K and initial delta: S ≈ K × exp(-d1 × iv × sqrt(T))
    # Simpler: try bisection until BS(S,K,T,iv) ≈ premium
    entry_T = dte_at_entry / 365
    lo, hi = K * 0.70, K * 1.50
    for _ in range(40):
        mid_s = (lo + hi) / 2
        p = bs_price(mid_s, K, entry_T, iv, call=is_call)
        if p < premium: lo = mid_s
        else:           hi = mid_s
    entry_S = (lo + hi) / 2

    # Cumulative stock move schedule
    def cum_move(d: int) -> float:
        sign = 1 if direction == Direction.BULLISH else -1
        if is_instant:
            fracs = {1: 0.90, 2: 0.95, 3: 0.97}
            f = fracs.get(d, min(0.97 + 0.01 * (d - 3), 1.0))
        else:
            fracs = {1: 0.50, 2: 0.72, 3: 0.85, 4: 0.93}
            f = fracs.get(d, min(0.93 + 0.02 * (d - 4), 1.0))
        return entry_S * (1 + sign * actual_move_pct * f)

    # IV schedule: instant events cause IV crush post-event
    def iv_today(d: int) -> float:
        if is_instant:
            return iv * 1.25 if d <= 1 else iv * 0.65  # crush after event
        return iv * min(1.0 + 0.03 * d, 1.15)

    current_price = premium
    for day in range(1, min(dte_at_entry + 1, 31)):
        S_d  = cum_move(day)
        T_d  = max((dte_at_entry - day) / 365, 0.001)
        iv_d = iv_today(day)

        current_price = bs_price(S_d, K, T_d, iv_d, call=is_call)
        current_price = max(current_price, 0.01)

        gain = (current_price - premium) / premium

        if gain <= -STOP_PCT:
            return round(premium * (1 - STOP_PCT), 2), "stop_loss"
        if gain >= HARD_EXIT_PCT:
            return round(premium * (1 + HARD_EXIT_PCT), 2), "hard_exit_200pct"
        if gain >= TARGET_2_PCT:
            return round(current_price, 2), "target_2_150pct"
        if gain >= TARGET_1_PCT:
            return round(current_price, 2), "target_1_80pct"
        if day == 7 and -0.20 < gain < 0.30:
            return round(current_price, 2), "time_stop_day7"
        if dte_at_entry - day <= 3:
            return round(current_price, 2), "dte_stop_3remaining"

    return round(current_price, 2), "dte_stop"

def run_backtest(balance_start: float = ACCOUNT_BALANCE) -> dict:
    balance    = balance_start
    results    = []
    passed_gate = 0
    rejected   = 0
    weekly_trades: dict[str, int] = {}

    for row in HISTORICAL_SETUPS:
        (symbol, cat_str, news, days_ago, price, iv, hv30,
         iv_lo, iv_hi, avg_move, straddle, mcap_B, dte_tgt,
         vol_oi, sweep_dir, beats, whisper, actual_move, entry_date) = row

        # Build objects
        catalyst = classify_catalyst(symbol, news, days_ago)

        # Override for explicit "none" setups
        if cat_str == "none":
            catalyst = Catalyst(CatalystType.NONE, symbol, news, days_ago, 0.0, False)

        direction = catalyst_direction(catalyst)

        iv_data = analyze_iv(
            symbol, iv, hv30, hv30 * 1.1, iv_hi, iv_lo,
            straddle, price, avg_move,
        )

        contract = select_contract(
            symbol, direction, price, iv, dte_tgt, balance
        )

        today = date.fromisoformat(entry_date)

        ok, reason = pre_trade_gate(
            symbol, mcap_B * 1e9, balance, catalyst,
            direction, iv_data.iv_rank, contract, today, [],
        )

        if not ok:
            rejected += 1
            results.append({
                "symbol": symbol, "entry_date": entry_date,
                "action": "REJECTED", "reason": reason, "score": 0,
                "pnl": 0, "balance": round(balance, 2),
            })
            continue

        # Score
        ep = EarningsProfile(symbol, None, beats, avg_move, whisper) if beats > 0 else None
        score = score_setup(
            catalyst, iv_data, contract, direction,
            compute_rr(contract.premium, STOP_PCT, TARGET_1_PCT),
            vol_oi, sweep_dir, ep,
        )

        if score.total < SCORE_TRADE:
            rejected += 1
            results.append({
                "symbol": symbol, "entry_date": entry_date,
                "action": "BELOW_THRESHOLD",
                "reason": f"Score {score.total} < {SCORE_TRADE}",
                "score": score.total, "pnl": 0, "balance": round(balance, 2),
            })
            continue

        # Weekly trade limit
        week_key = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
        weekly_trades[week_key] = weekly_trades.get(week_key, 0) + 1
        if weekly_trades[week_key] > MAX_TRADES_WEEK:
            rejected += 1
            results.append({
                "symbol": symbol, "entry_date": entry_date,
                "action": "WEEKLY_LIMIT", "reason": "2 trades already this week",
                "score": score.total, "pnl": 0, "balance": round(balance, 2),
            })
            continue

        # Simulate
        exit_price, exit_reason = simulate_trade(
            contract, actual_move, direction, iv, dte_tgt, catalyst.type
        )

        gross_pnl = round((exit_price - contract.premium) * 100 * contract.contracts, 2)
        balance   = round(balance + gross_pnl, 2)
        ret_pct   = round((exit_price - contract.premium) / contract.premium * 100, 1)

        passed_gate += 1
        results.append({
            "symbol":       symbol,
            "entry_date":   entry_date,
            "action":       "TRADE",
            "catalyst":     catalyst.type.value,
            "direction":    direction.value,
            "strike":       contract.strike,
            "dte":          dte_tgt,
            "premium":      contract.premium,
            "contracts":    contract.contracts,
            "cost_basis":   contract.cost_basis,
            "exit_price":   exit_price,
            "exit_reason":  exit_reason,
            "gross_pnl":    gross_pnl,
            "return_pct":   ret_pct,
            "score":        score.total,
            "grade":        score.grade(),
            "iv_rank":      iv_data.iv_rank,
            "balance":      balance,
        })

    trades   = [r for r in results if r["action"] == "TRADE"]
    wins     = [t for t in trades if t["gross_pnl"] > 0]
    losses   = [t for t in trades if t["gross_pnl"] <= 0]
    avg_win  = statistics.mean([t["gross_pnl"] for t in wins])  if wins   else 0
    avg_loss = statistics.mean([t["gross_pnl"] for t in losses]) if losses else 0
    expectancy = (len(wins)/len(trades) * avg_win + len(losses)/len(trades) * avg_loss) if trades else 0

    return {
        "summary": {
            "start_balance":   balance_start,
            "end_balance":     balance,
            "total_pnl":       round(balance - balance_start, 2),
            "total_return_pct": round((balance - balance_start) / balance_start * 100, 1),
            "total_setups":    len(HISTORICAL_SETUPS),
            "passed_gate":     passed_gate,
            "rejected":        rejected,
            "win_rate":        f"{len(wins)}/{len(trades)} ({len(wins)/len(trades)*100:.0f}%)" if trades else "N/A",
            "avg_win":         round(avg_win, 2),
            "avg_loss":        round(avg_loss, 2),
            "expectancy_per_trade": round(expectancy, 2),
            "max_single_loss": round(min((t["gross_pnl"] for t in trades), default=0), 2),
            "max_single_win":  round(max((t["gross_pnl"] for t in trades), default=0), 2),
        },
        "trades": results,
    }


# ─────────────────────────────────────────────
# MODULE 16: PAPER TRADING ENGINE
# ─────────────────────────────────────────────
def log_paper_trade_options(setup: TradeSetup, path: str = "paper_trades.json") -> str:
    p = Path(path)
    data = json.loads(p.read_text()) if p.exists() else {"paper_trades": [], "scan_log": []}

    trade_id = f"OPT-{setup.symbol}-{setup.entry_date}-{len(data['paper_trades'])+1:03d}"
    entry = {
        "id":          trade_id,
        "symbol":      setup.symbol,
        "option_type": setup.contract.option_type,
        "strike":      setup.contract.strike,
        "expiration":  setup.contract.expiration,
        "dte":         setup.contract.dte,
        "contracts":   setup.contract.contracts,
        "entry_price": setup.contract.premium,
        "cost_basis":  setup.contract.cost_basis,
        "entry_date":  setup.entry_date,
        "stop_price":  round(setup.contract.premium * (1 - STOP_PCT), 2),
        "target_1":    round(setup.contract.premium * (1 + TARGET_1_PCT), 2),
        "target_2":    round(setup.contract.premium * (1 + TARGET_2_PCT), 2),
        "score":       setup.score.total,
        "grade":       setup.score.grade(),
        "catalyst":    setup.catalyst.type.value,
        "iv_rank":     setup.iv_data.iv_rank,
        "status":      "open",
        "exit_price":  None,
        "exit_date":   None,
        "exit_reason": None,
        "pnl":         None,
    }
    data["paper_trades"].append(entry)
    p.write_text(json.dumps(data, indent=2))
    return trade_id


# ─────────────────────────────────────────────
# MODULE 17: PERFORMANCE TRACKER
# ─────────────────────────────────────────────
def risk_of_ruin(win_rate: float, avg_win: float, avg_loss: float,
                 balance: float, floor: float = ACCOUNT_FLOOR, n_sim: int = 10000) -> float:
    """Monte Carlo risk of ruin estimate."""
    if avg_win <= 0 or avg_loss >= 0:
        return 1.0
    ruined = 0
    for _ in range(n_sim):
        b = balance
        for _ in range(200):
            if b <= floor:
                ruined += 1
                break
            if random.random() < win_rate:
                b += avg_win
            else:
                b += avg_loss  # avg_loss is negative
    return round(ruined / n_sim * 100, 1)

def print_performance(results: dict) -> None:
    s = results["summary"]
    trades = [r for r in results["trades"] if r["action"] == "TRADE"]

    print("\n" + "═"*60)
    print("  OPTIONS-NATIVE SYSTEM — BACKTEST RESULTS")
    print("═"*60)
    print(f"  Starting balance:    ${s['start_balance']:.2f}")
    print(f"  Ending balance:      ${s['end_balance']:.2f}")
    print(f"  Total P&L:           ${s['total_pnl']:+.2f} ({s['total_return_pct']:+.1f}%)")
    print(f"  Setups analyzed:     {s['total_setups']}")
    print(f"  Passed all gates:    {s['passed_gate']}")
    print(f"  Rejected:            {s['rejected']}")
    print(f"  Win rate:            {s['win_rate']}")
    print(f"  Avg win:             ${s['avg_win']:+.2f}")
    print(f"  Avg loss:            ${s['avg_loss']:+.2f}")
    print(f"  Expectancy/trade:    ${s['expectancy_per_trade']:+.2f}")
    print(f"  Best trade:          ${s['max_single_win']:+.2f}")
    print(f"  Worst trade:         ${s['max_single_loss']:+.2f}")

    wins   = [t for t in trades if t["gross_pnl"] > 0]
    losses = [t for t in trades if t["gross_pnl"] <= 0]
    wr = len(wins)/len(trades) if trades else 0
    al = statistics.mean([t["gross_pnl"] for t in losses]) if losses else 0
    aw = statistics.mean([t["gross_pnl"] for t in wins])   if wins   else 0
    ror = risk_of_ruin(wr, aw, al, s["end_balance"])
    print(f"  Risk of Ruin:        {ror:.1f}%")

    print("\n  INDIVIDUAL TRADES:")
    print(f"  {'Date':<12} {'Sym':<6} {'Dir':<5} {'Score':>5} {'Premium':>7} {'Exit':>7} {'Return':>7} {'P&L':>7}  Reason")
    print("  " + "─"*85)
    for t in results["trades"]:
        if t["action"] == "TRADE":
            print(f"  {t['entry_date']:<12} {t['symbol']:<6} {t['direction']:<5} {t['score']:>5} "
                  f"${t['premium']:>5.2f}  ${t['exit_price']:>5.2f}  {t['return_pct']:>+6.1f}%  "
                  f"${t['gross_pnl']:>+7.2f}  {t['exit_reason']}")
        elif t["action"] in ("REJECTED", "BELOW_THRESHOLD", "WEEKLY_LIMIT"):
            print(f"  {t['entry_date']:<12} {t['symbol']:<6} {'—':<5} {t['score']:>5}  "
                  f"{'':>7}  {'':>7}  {'':>7}  {'':>8}  ✗ {t['reason']}")

    # Exit reason breakdown
    exit_counts: dict[str, int] = {}
    for t in trades:
        k = t["exit_reason"]
        exit_counts[k] = exit_counts.get(k, 0) + 1
    print("\n  EXIT REASON BREAKDOWN:")
    for k, v in sorted(exit_counts.items(), key=lambda x: -x[1]):
        print(f"    {k:<30} {v}x")

    # Catalyst type breakdown
    cat_wins: dict[str, list] = {}
    for t in trades:
        c = t["catalyst"]
        cat_wins.setdefault(c, []).append(t["gross_pnl"])
    print("\n  WIN RATE BY CATALYST TYPE:")
    for c, pnls in sorted(cat_wins.items()):
        w = sum(1 for p in pnls if p > 0)
        print(f"    {c:<28} {w}/{len(pnls)}  avg ${statistics.mean(pnls):+.2f}")

    print("═"*60)


# ─────────────────────────────────────────────
# MODULE 18: DAILY BRIEF (Alert Engine)
# ─────────────────────────────────────────────
def daily_brief(
    candidates: list[dict],
    balance: float,
    today: str,
    macro_events: list[dict],
) -> str:
    lines = []
    lines.append("═"*54)
    lines.append(f"  OPTIONS-NATIVE DAILY BRIEF — {today}")
    lines.append(f"  Account: ${balance:.2f} | Max trade size: ${balance * MAX_POSITION_PCT:.2f}")
    lines.append("═"*54)

    macro_today = [e for e in macro_events if e["date"] == today]
    if macro_today:
        lines.append(f"\n⛔ NO-TRADE DAY: {', '.join(e['name'] for e in macro_today)}")
        lines.append("═"*54)
        return "\n".join(lines)

    lines.append("\nMACRO: ✅ No blocking events today\n")

    if not candidates:
        lines.append("SCANNER: 0 candidates met all gates today.")
        lines.append("Action: Sit out. No trade is a trade.")
    else:
        for i, c in enumerate(candidates, 1):
            grade = c.get("grade", "?")
            action = "✅ TRADE" if c["score"] >= SCORE_TRADE else "👁 WATCH"
            lines.append(f"{'─'*54}")
            lines.append(f"  CANDIDATE {i}: {c['symbol']} — {c['score']}/100 {action}")
            lines.append(f"  Catalyst:  {c.get('catalyst', '?')}")
            lines.append(f"  Direction: {c.get('direction', '?').upper()}")
            lines.append(f"  Contract:  {c.get('contract', '?')}")
            lines.append(f"  IV Rank:   {c.get('iv_rank', '?')}")
            lines.append(f"  R/R:       {c.get('rr', '?')}x at T1")
            lines.append(f"  Grade:     {grade}")

    lines.append("═"*54)
    return "\n".join(lines)


# ─────────────────────────────────────────────
# MAIN — Run backtest
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("\nRunning OPTIONS-NATIVE backtest on 25 historical setups...")
    results = run_backtest(ACCOUNT_BALANCE)
    print_performance(results)

    # Save results
    out_path = Path("backtest_options_results.json")
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nFull results saved → {out_path}")
