"""
run_options.py — Options execution layer (runs alongside daily swing scan).

Triggered at the same time as run_strategy.py (9:25 AM ET entry scan,
4:05 PM ET exit check). When a daily EMA pullback signal fires, this layer
buys a long call instead of (or in addition to) shares.

Account : 628914509
Strategy: Long ITM calls on EMA20 pullback signals, 21-35 DTE, 2% risk/trade
"""

AGENTIC_ACCOUNT = "628914509"

WATCHLIST = [
    "NVDA", "TSLA", "JPM", "BAC", "INTC",
    "ROKU", "HOOD", "COIN", "MARA", "RIVN",
    "F", "AAL", "NIO", "SOFI", "PLTR",
]

AGENT_INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════════════╗
║           OPTIONS SCAN — LONG CALLS ON EMA PULLBACK                 ║
╚══════════════════════════════════════════════════════════════════════╝

Runs at the same time as the daily swing scan (9:25 AM ET).

════════════════════════════════════════════════════════════════════════
STEP 0 — STATE CHECK
════════════════════════════════════════════════════════════════════════
  a. get_portfolio(account_number="628914509") → equity, buying_power
  b. get_equity_positions + get_option_positions → count open positions
  c. Check DailyRiskState (load from positions.json):
     • daily_loss >= equity × 0.03  → exit
     • consecutive_losses >= 3      → exit
  d. If option_positions >= 2       → exit (max 2 option trades)

════════════════════════════════════════════════════════════════════════
STEP 1 — EXIT CHECK ON OPEN OPTION POSITIONS
════════════════════════════════════════════════════════════════════════
  For each open option position in positions.json (type="option"):
    a. get_option_quotes() for the position's contract.
    b. Run check_option_exit() from options_strategy.py.
    c. If exit triggered:
         place_option_order(account_number="628914509",
           symbol=sym, side="sell_to_close", quantity=1,
           type="market", time_in_force="gfd",
           option_type="call", strike=..., expiration=...)
         Record PnL = (exit_premium - entry_premium) × 100
         Update DailyRiskState.record_trade(pnl)
         Remove from positions.json

════════════════════════════════════════════════════════════════════════
STEP 2 — ENTRY SCAN
════════════════════════════════════════════════════════════════════════
  Run the full daily equity scan (same as run_strategy.py) to find
  EMA20 pullback signals.

  For each Signal found:
    a. get_option_chains(symbol=sym) → available expirations + strikes
    b. Filter for calls with 21-35 DTE.
    c. get_option_quotes() for the target strike.
    d. Run check_option_entry() from options_strategy.py.
    e. Liquidity checks:
         • bid > 0
         • spread (ask-bid)/mid <= 15%
         • open_interest >= 100
         • mid × 100 <= buying_power (can afford 1 contract)
    f. If signal passes:
         review_option_order → confirm details
         place_option_order(account_number="628914509",
           symbol=sym, side="buy_to_open", quantity=1,
           type="limit", limit_price=ask,  ← pay the ask for fills
           time_in_force="gfd",
           option_type="call", strike=..., expiration=...)
         Save to positions.json:
           {
             "type": "option",
             "symbol": sym,
             "option_type": "call",
             "strike": ...,
             "expiration": "YYYY-MM-DD",
             "contracts": 1,
             "entry_premium": mid,
             "take_profit_premium": mid × 2,
             "stop_loss_premium": mid × 0.5,
             "entry_date": today
           }

════════════════════════════════════════════════════════════════════════
OPTION ENTRY CONDITIONS (all must be true)
════════════════════════════════════════════════════════════════════════
  1. Daily EMA pullback signal fired (from strategy.py check_entry())
  2. SPY above 200-day EMA
  3. Option: ITM call, delta ~0.65-0.70 (first strike below current price)
  4. Expiration: 21-35 DTE
  5. Bid-ask spread <= 15% of mid
  6. Open interest >= 100
  7. 1 contract cost <= 20% of equity

OPTION EXITS
  • Take profit : sell when premium doubles (2× entry mid)
  • Stop loss   : sell when premium drops 50% (0.5× entry mid)
  • Time stop   : sell with 7 DTE remaining (avoid theta cliff)

RISK CONTROLS
  • Max 2 option positions at once
  • 2% of equity max risk per trade ($2 on $100 account)
  • Respects shared daily loss limit (3% of equity)
  • Respects 3-consecutive-loss halt
  • No new options if SPY < 200-day EMA
"""


def main():
    print(AGENT_INSTRUCTIONS)
    print(f"WATCHLIST : {', '.join(WATCHLIST)}")
    print(f"ACCOUNT   : {AGENTIC_ACCOUNT}")


if __name__ == "__main__":
    main()
