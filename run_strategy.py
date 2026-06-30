"""
run_strategy.py — Daily execution script for the Claude agent.

Wire to a scheduled trigger at 09:25 ET Mon–Fri (before market open)
and again at 16:05 ET (after close) for exit checks.

The agent executes these steps using Robinhood MCP tools.
"""

AGENTIC_ACCOUNT = "628914509"

WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
    "META", "TSLA", "JPM", "V", "UNH",
    "HD", "PG", "MA", "COST", "LLY",
]

# ---------------------------------------------------------------------------
# AGENT EXECUTION INSTRUCTIONS
# ---------------------------------------------------------------------------

AGENT_INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════════════╗
║          DAILY STRATEGY EXECUTION — FULL RULESET                    ║
╚══════════════════════════════════════════════════════════════════════╝

Run this script TWICE per trading day:
  • Morning  : 09:25 ET (5 min before open) → entry scan + exit pre-check
  • Afternoon: 16:05 ET (5 min after close)  → exit check on closing prices

════════════════════════════════════════════════════════════════════════
STEP 0 — REFRESH ACCOUNT STATE
════════════════════════════════════════════════════════════════════════
  a. get_portfolio(account_number="628914509")
     → record account_equity and buying_power
  b. get_equity_positions(account_number="628914509")
     → record open positions and compute:
         open_position_count = number of open lots
         current_exposure    = sum(shares × current_price) for all open positions
  c. Load or initialize DailyRiskState from storage:
         daily_loss          (reset to 0.0 each morning)
         consecutive_losses  (reset to 0 each morning)

════════════════════════════════════════════════════════════════════════
STEP 1 — RISK GATE (check before anything else)
════════════════════════════════════════════════════════════════════════
  • If daily_loss >= account_equity × 0.03  → STOP. Do not trade today.
  • If consecutive_losses >= 3              → STOP. Do not trade today.
  Log the outcome.

════════════════════════════════════════════════════════════════════════
STEP 2 — MARKET FILTER (SPY)
════════════════════════════════════════════════════════════════════════
  get_equity_historicals(symbol="SPY", interval="day", span="year")
  Compute 200-day EMA of closes (use ema() from strategy.py).
  • If SPY close <= EMA200 → skip all new entries. Log reason.
  • If SPY close >  EMA200 → proceed to entry scan.

════════════════════════════════════════════════════════════════════════
STEP 3 — EXIT CHECK (run every session, before entries)
════════════════════════════════════════════════════════════════════════
  For EACH open position retrieved in STEP 0:

    a. get_equity_historicals(symbol=<symbol>, interval="day", span="year")
    b. Import check_exits() from strategy.py and evaluate.
    c. If ExitDecision returned:
         i.  review_equity_order (present review to user if interactive).
         ii. place_equity_order:
               account_number = "628914509"
               symbol         = position.symbol
               side           = "sell"
               type           = "market"
               quantity       = exit_decision.shares_to_sell
               time_in_force  = "gfd"
         iii. Record PnL = (exit_price - entry_price) × shares_sold
         iv.  Update DailyRiskState.record_trade(pnl)
         v.   If is_partial=True → update position:
                  shares_remaining -= shares_sold
                  breakeven_stop = True
                  stop_loss = entry_price  (move to breakeven)

════════════════════════════════════════════════════════════════════════
STEP 4 — EARNINGS FILTER
════════════════════════════════════════════════════════════════════════
  get_earnings_calendar() for the next 5 trading days.
  Mark any symbol in WATCHLIST with earnings within 3 trading days
  as earnings_within_3_days=True → skip in entry scan.

════════════════════════════════════════════════════════════════════════
STEP 5 — ENTRY SCAN (morning session only)
════════════════════════════════════════════════════════════════════════
  Skip entirely if:
    • Risk gate failed (STEP 1)
    • SPY filter failed (STEP 2)
    • open_position_count >= 5
    • current_exposure >= account_equity × 0.80

  For each symbol in WATCHLIST (that isn't already held):
    a. get_equity_historicals(symbol=<symbol>, interval="day", span="year")
    b. Call check_entry() from strategy.py with all parameters.
    c. Collect all Signal objects returned.

  Sort signals by RSI proximity to 40 (lowest RSI = deepest pullback = first).
  Take only as many signals as (5 - open_position_count) allows.

  For each Signal:
    a. review_equity_order → present review.
    b. On confirmation, place_equity_order:
         account_number = "628914509"
         symbol         = signal.symbol
         side           = "buy"
         type           = "market"
         quantity       = signal.shares
         time_in_force  = "gfd"
    c. Record entry:
         entry_price  = filled price (from order confirmation)
         stop_loss    = signal.stop_loss
         r_value      = signal.r_value
         entry_atr    = ATR at entry
         breakeven_stop = False

════════════════════════════════════════════════════════════════════════
STEP 6 — POSITION LOG (end of session)
════════════════════════════════════════════════════════════════════════
  Print a table for each open position:
    Symbol | Entry | Current | Stop | Target½ | Shares | PnL $

  Print daily summary:
    New entries today | Exits today | Daily PnL | Consecutive losses

════════════════════════════════════════════════════════════════════════
POSITION STATE SCHEMA (persist between sessions)
════════════════════════════════════════════════════════════════════════
  Store as positions.json in the repo or a scratch file:

  {
    "account": "628914509",
    "date": "YYYY-MM-DD",
    "daily_loss": 0.0,
    "consecutive_losses": 0,
    "positions": [
      {
        "symbol": "AAPL",
        "entry_price": 185.00,
        "shares": 5,
        "shares_remaining": 5,
        "stop_loss": 182.00,
        "breakeven_stop": false,
        "r_value": 15.00,
        "entry_atr": 3.00
      }
    ]
  }

  Reset daily_loss and consecutive_losses to 0 each morning.
  Persist position updates after every order fill.
"""

RISK_CONTROLS_SUMMARY = """
RISK CONTROLS SUMMARY
─────────────────────
  Max daily loss          : 3% of equity  (= $3.00 on $100 account)
  Consecutive loss limit  : 3 trades → halt for the day
  Max portfolio exposure  : 80%           (= $80.00 on $100 account)
  Max per position        : 20%           (= $20.00 on $100 account)
  Risk per trade          : 1%            (= $1.00 on $100 account)
  Stop loss               : 2 × ATR(14) below entry
  Take profit (first half): 2R
  Remainder stop          : breakeven after first exit
  Trailing stop (remainder): tighter of 3×ATR or 20-day EMA
  Earnings blackout       : skip if earnings within 3 trading days
  SPY filter              : no new longs if SPY < 200-day EMA
"""


def main():
    print(AGENT_INSTRUCTIONS)
    print(RISK_CONTROLS_SUMMARY)
    print(f"WATCHLIST : {', '.join(WATCHLIST)}")
    print(f"ACCOUNT   : {AGENTIC_ACCOUNT}")


if __name__ == "__main__":
    main()
