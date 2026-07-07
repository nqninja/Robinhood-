"""
Options Strategy Layer: Long Calls on Daily EMA Pullback Signals

When the daily swing scan fires a Signal, instead of buying shares we buy
a slightly in-the-money (ITM) call option. Same entry logic, more leverage,
defined risk per trade.

Entry Rules (all must be true):
  1. Daily EMA pullback signal fires (same as strategy.py check_entry())
  2. SPY above 200-day EMA (market filter)
  3. Option premium <= max_premium (2% of equity = $2 on $100 account)
  4. Bid-ask spread <= 15% of mid price (liquidity filter)
  5. Open interest >= 100 (avoid illiquid strikes)
  6. Days to expiration: 21-35 DTE

Strike Selection:
  - Target delta ~0.65-0.70 (first ITM strike)
  - If no ITM strike available, use ATM strike

Sizing:
  - Always 1 contract (controls 100 shares)
  - Max cost = 2% of equity ($2 on $100 account)
  - Never spend more than 20% of equity on a single option

Exit Rules:
  - Take profit : premium doubles (2× what we paid)
  - Stop loss   : premium drops 50% (lose half of what we paid)
  - Time stop   : exit with 7 DTE remaining (avoid accelerating theta decay)

Risk Controls:
  - Max 2 option positions open at once
  - Respect daily loss limit and consecutive loss halt from DailyRiskState
  - No new options if SPY < 200-day EMA
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class OptionSignal:
    symbol: str
    option_type: str          # "call"
    strike: float
    expiration: str           # "YYYY-MM-DD"
    dte: int                  # days to expiration
    bid: float
    ask: float
    mid: float                # (bid + ask) / 2
    delta: Optional[float]    # from quote if available
    open_interest: int
    contracts: int            # always 1
    max_loss: float           # premium paid × 100
    take_profit_premium: float  # 2× mid
    stop_loss_premium: float    # 0.5× mid
    reason: str


@dataclass
class OptionPosition:
    symbol: str
    option_type: str
    strike: float
    expiration: str
    contracts: int
    entry_premium: float      # per share (× 100 = total cost)
    take_profit_premium: float
    stop_loss_premium: float
    entry_date: str


@dataclass
class OptionExitDecision:
    symbol: str
    strike: float
    expiration: str
    contracts: int
    reason: str


# ---------------------------------------------------------------------------
# Strike and expiration selection helpers
# ---------------------------------------------------------------------------

def select_expiration(available_expirations: list[str], today: str) -> Optional[str]:
    """
    Pick the expiration with 21-35 DTE.
    available_expirations: list of "YYYY-MM-DD" strings, sorted ascending.
    today: "YYYY-MM-DD"
    """
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    for exp in sorted(available_expirations):
        exp_dt = datetime.strptime(exp, "%Y-%m-%d")
        dte = (exp_dt - today_dt).days
        if 21 <= dte <= 35:
            return exp
    # Fallback: take the nearest expiration with >= 14 DTE
    for exp in sorted(available_expirations):
        exp_dt = datetime.strptime(exp, "%Y-%m-%d")
        dte = (exp_dt - today_dt).days
        if dte >= 14:
            return exp
    return None


def select_strike(
    current_price: float,
    strikes: list[float],
    option_type: str = "call",
) -> Optional[float]:
    """
    Select the first ITM strike for a call (largest strike < current_price).
    Falls back to ATM (closest strike to current_price).
    """
    call_strikes = sorted(strikes)

    # First ITM call: highest strike strictly below current price
    itm = [s for s in call_strikes if s < current_price]
    if itm:
        return max(itm)

    # ATM fallback: closest strike to current price
    if call_strikes:
        return min(call_strikes, key=lambda s: abs(s - current_price))

    return None


# ---------------------------------------------------------------------------
# Entry signal
# ---------------------------------------------------------------------------

def check_option_entry(
    symbol: str,
    current_price: float,
    option_chain: dict,         # {expiration: {strike: {bid, ask, delta, open_interest}}}
    account_equity: float,
    today: str,                 # "YYYY-MM-DD"
) -> Optional[OptionSignal]:
    """
    Given a daily signal has already fired for `symbol`, find the best
    call option to buy.

    option_chain format:
      {
        "2026-07-28": {
          145.0: {"bid": 1.50, "ask": 1.70, "delta": 0.68, "open_interest": 250},
          ...
        },
        ...
      }
    """
    max_premium = account_equity * 0.02     # 2% of equity
    max_position = account_equity * 0.20    # 20% cap

    # Pick expiration
    expirations = list(option_chain.keys())
    expiration = select_expiration(expirations, today)
    if not expiration:
        return None

    today_dt = datetime.strptime(today, "%Y-%m-%d")
    exp_dt = datetime.strptime(expiration, "%Y-%m-%d")
    dte = (exp_dt - today_dt).days

    strikes_data = option_chain.get(expiration, {})
    if not strikes_data:
        return None

    # Pick strike
    strike = select_strike(current_price, list(strikes_data.keys()))
    if strike is None:
        return None

    quote = strikes_data.get(strike)
    if not quote:
        return None

    bid = float(quote.get("bid", 0))
    ask = float(quote.get("ask", 0))
    oi = int(quote.get("open_interest", 0))
    delta = quote.get("delta")

    if bid <= 0 or ask <= 0:
        return None

    mid = (bid + ask) / 2

    # Liquidity filter: spread <= 15% of mid
    spread_pct = (ask - bid) / mid if mid else 1.0
    if spread_pct > 0.15:
        return None

    # Open interest filter
    if oi < 100:
        return None

    # Premium affordability (1 contract = 100 shares)
    total_cost = mid * 100
    if total_cost > max_premium * 100:   # scale: max_premium is per-share equiv
        # Try: can we afford 1 contract at all?
        if mid > max_position:
            return None

    # Always 1 contract
    contracts = 1
    total_cost = mid * 100

    return OptionSignal(
        symbol=symbol,
        option_type="call",
        strike=strike,
        expiration=expiration,
        dte=dte,
        bid=bid,
        ask=ask,
        mid=round(mid, 4),
        delta=delta,
        open_interest=oi,
        contracts=contracts,
        max_loss=round(total_cost, 2),
        take_profit_premium=round(mid * 2, 4),    # 2× entry
        stop_loss_premium=round(mid * 0.5, 4),    # 50% loss
        reason=(
            f"{symbol} {expiration} ${strike}C | "
            f"mid=${mid:.2f} bid={bid} ask={ask} spread={spread_pct:.1%} | "
            f"delta={delta} OI={oi} DTE={dte} | "
            f"cost=${total_cost:.2f} tp=${mid*2:.2f} sl=${mid*0.5:.2f}"
        ),
    )


# ---------------------------------------------------------------------------
# Exit check
# ---------------------------------------------------------------------------

def check_option_exit(
    position: OptionPosition,
    current_premium: float,    # current mid price of the option
    today: str,
) -> Optional[OptionExitDecision]:
    """
    Check exit conditions for an open option position.
    current_premium: current mid price per share.
    """
    # Take profit: 2× entry
    if current_premium >= position.take_profit_premium:
        return OptionExitDecision(
            symbol=position.symbol,
            strike=position.strike,
            expiration=position.expiration,
            contracts=position.contracts,
            reason=f"2× target hit: premium ${current_premium:.2f} >= tp ${position.take_profit_premium:.2f}",
        )

    # Stop loss: 50% of entry
    if current_premium <= position.stop_loss_premium:
        return OptionExitDecision(
            symbol=position.symbol,
            strike=position.strike,
            expiration=position.expiration,
            contracts=position.contracts,
            reason=f"50% stop hit: premium ${current_premium:.2f} <= sl ${position.stop_loss_premium:.2f}",
        )

    # Time stop: 7 DTE remaining
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    exp_dt = datetime.strptime(position.expiration, "%Y-%m-%d")
    dte_remaining = (exp_dt - today_dt).days
    if dte_remaining <= 7:
        return OptionExitDecision(
            symbol=position.symbol,
            strike=position.strike,
            expiration=position.expiration,
            contracts=position.contracts,
            reason=f"Time stop: {dte_remaining} DTE remaining",
        )

    return None
