"""
run_intraday.py — Intraday execution script (runs every 30 min during market hours).

Schedule: Every 30 minutes, 9:45 AM – 3:30 PM ET, Mon–Fri
Cron    : */30 13-19 * * 1-5  (UTC; covers 9:30 AM – 3:30 PM ET)

What it does:
  1. Checks risk state and daily loss limit
  2. Fetches 15-min bars for today for each watchlist symbol
  3. Fetches daily bars for the trend filter
  4. Scans for intraday EMA9 pullback signals
  5. Checks open intraday positions for exits
  6. Places orders on the Agentic account
"""

AGENTIC_ACCOUNT = "628914509"

WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
    "META", "TSLA", "JPM", "V", "UNH",
    "HD", "PG", "MA", "COST", "LLY",
]

AGENT_INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════════════╗
║         INTRADAY SCAN — EMA9 PULLBACK / VOLUME / VWAP              ║
╚══════════════════════════════════════════════════════════════════════╝

Runs every 30 minutes between 9:45 AM and 3:30 PM ET.

════════════════════════════════════════════════════════════════════════
STEP 0 — STATE CHECK
════════════════════════════════════════════════════════════════════════
  a. Load positions.json — get daily_loss, consecutive_losses,
     open positions, and intraday_trades_today.
  b. get_portfolio(account_number="628914509") → refresh equity.
  c. get_equity_positions(account_number="628914509") → open_position_count.
  d. Check current ET time:
     • If before 9:45 AM ET → exit (too early)
     • If after 3:30 PM ET  → exit (too late for new entries)
  e. If daily_loss >= equity × 0.03 → exit (daily loss limit hit)
  f. If consecutive_losses >= 3     → exit (halt rule active)
  g. If intraday_trades_today >= 2  → exit (intraday trade cap hit)
  h. If open_position_count >= 5    → exit (max positions)

════════════════════════════════════════════════════════════════════════
STEP 1 — EXIT CHECK ON OPEN INTRADAY POSITIONS
════════════════════════════════════════════════════════════════════════
  For each intraday position in positions.json (type="intraday"):
    a. get_equity_historicals(symbol=<sym>, interval="15minute",
         start_time=<today 09:30 ET in UTC>)
    b. Run check_intraday_exit() from intraday_strategy.py.
    c. If exit triggered:
         place_equity_order(account_number="628914509", symbol=sym,
           side="sell", type="market", quantity=shares, time_in_force="gfd")
         Record PnL, update DailyRiskState, remove from positions.json.

════════════════════════════════════════════════════════════════════════
STEP 2 — ENTRY SCAN
════════════════════════════════════════════════════════════════════════
  For each symbol in WATCHLIST not already held:

    a. DAILY TREND GATE — get_equity_historicals(symbol, interval="day",
         start_time="2025-06-01T00:00:00Z")
       Run passes_daily_trend() from intraday_strategy.py.
       Skip if trend not confirmed.

    b. INTRADAY BARS — get_equity_historicals(symbol, interval="15minute",
         start_time=<today 09:30 ET in UTC>)
       Run check_intraday_entry() from intraday_strategy.py.

    c. If IntradaySignal returned:
         review_equity_order → present to user.
         On confirmation:
           place_equity_order(account_number="628914509", symbol=sym,
             side="buy", type="market", quantity=sig.shares,
             time_in_force="gfd")
         Save to positions.json with type="intraday",
           entry_price, stop_loss, take_profit, shares.
         Increment intraday_trades_today in positions.json.

════════════════════════════════════════════════════════════════════════
STEP 3 — FORCED EOD EXIT (run at 3:30 PM ET scan)
════════════════════════════════════════════════════════════════════════
  At the 3:30 PM ET scan, sell ALL open intraday positions at market.
  Intraday positions are never held overnight.

════════════════════════════════════════════════════════════════════════
INTRADAY ENTRY CONDITIONS (all 5 must be true)
════════════════════════════════════════════════════════════════════════
  1. Daily trend confirmed: price > EMA200(daily), EMA50 > EMA200(daily)
  2. 15-min close within ±1%% of 9-period EMA
  3. 15-min RSI(14) between 40 and 60
  4. Current 15-min bar volume >= 1.5× 20-bar average volume
  5. 15-min close above VWAP (session)

INTRADAY EXITS
  • Stop loss   : 1×ATR(14) on 15-min below entry
  • Take profit : 2R (2× stop distance above entry)
  • Forced exit : 3:45 PM ET (no overnight holds)

RISK CONTROLS (intraday)
  • Max 2 intraday trades per day
  • Respects shared daily loss limit (3%% of equity)
  • Respects 3-consecutive-loss halt
  • Max 5 total positions (daily + intraday combined)
"""

POSITIONS_SCHEMA_ADDITION = """
Add these fields to each intraday position in positions.json:
  {
    "type": "intraday",          // vs "swing" for daily positions
    "take_profit": 195.50,       // absolute price (2R target)
    "intraday_trades_today": 1   // top-level field, reset each morning
  }
"""

def main():
    print(AGENT_INSTRUCTIONS)
    print(POSITIONS_SCHEMA_ADDITION)
    print(f"WATCHLIST : {', '.join(WATCHLIST)}")
    print(f"ACCOUNT   : {AGENTIC_ACCOUNT}")

if __name__ == "__main__":
    main()
