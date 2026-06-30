"""
run_strategy.py — Daily execution script.

Usage:
    python run_strategy.py

What it does:
  1. Fetches historical bars for each symbol in WATCHLIST via Robinhood MCP.
  2. Runs the EMA-pullback/RSI strategy scan.
  3. For each signal, places a market buy order at the next open on the
     Agentic account (628914509).
  4. Prints a summary of orders placed.

Wire this to a scheduled trigger (e.g. 09:30 ET Mon–Fri) to automate fully.
"""

import asyncio
import json
from datetime import date, timedelta

# MCP client would be injected in the agentic context; this script is designed
# to be called by the Claude agent using the robinhood MCP tools directly.

AGENTIC_ACCOUNT = "628914509"

WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
    "META", "TSLA", "JPM", "V", "UNH",
    "HD", "PG", "MA", "COST", "LLY",
]

# Strategy config mirrors account equity — update daily before running.
# The agent will call get_portfolio to refresh this automatically.
ACCOUNT_EQUITY = 100.00


# ---------------------------------------------------------------------------
# Instructions for the Claude agent executing this strategy
# ---------------------------------------------------------------------------
AGENT_INSTRUCTIONS = """
DAILY STRATEGY EXECUTION — EMA PULLBACK / RSI CONFIRMATION

Run each market day before 09:30 ET (or immediately after open):

STEP 1 — Refresh equity
  Call get_portfolio(account_number="628914509") and update ACCOUNT_EQUITY.

STEP 2 — Count open positions
  Call get_equity_positions(account_number="628914509") and count open lots.
  If open_positions >= 5, skip to STEP 5.

STEP 3 — Fetch bars
  For each symbol in WATCHLIST call get_equity_historicals with:
    interval="day", span="year" (gives ~252 bars).

STEP 4 — Run scan
  Import strategy.py and call run_daily_scan() with the bars.
  For each Signal returned:
    a. Call review_equity_order and present the review to the user.
    b. If user confirms (or auto-confirmed), call place_equity_order:
         account_number = "628914509"
         symbol         = signal.symbol
         side           = "buy"
         type           = "market"
         quantity       = signal.shares
         time_in_force  = "gfd"
    c. Log the order_id and stop_loss price for tracking.

STEP 5 — Manage exits (check every open position)
  Call get_equity_positions(account_number="628914509").
  For each position:
    a. Get current quote via get_equity_quotes.
    b. If current price <= stop_loss recorded at entry → place sell market order.
    c. Optional trailing stop: if price > entry * 1.10 (10% profit),
       raise stop to entry (break-even).

STEP 6 — Report
  Print a summary: signals found, orders placed, positions closed.
"""


def main():
    print(AGENT_INSTRUCTIONS)
    print("\nWATCHLIST:", ", ".join(WATCHLIST))
    print(f"ACCOUNT   : {AGENTIC_ACCOUNT}")
    print(f"EQUITY    : ${ACCOUNT_EQUITY:.2f}  (refresh via get_portfolio before trading)")


if __name__ == "__main__":
    main()
