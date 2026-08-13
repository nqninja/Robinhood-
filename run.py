"""
run.py — Single execution entry point for account 628914509.

THREE sessions per day (agent reads this file at the start of each):
  1. MORNING   9:20 AM ET  — macro check, scan, ORB setup
  2. INTRADAY  9:45–11:00  — ORB monitoring (every 5 min)
  3. EOD       4:05 PM ET  — exit check, state update, commit

The agent follows AGENT_INSTRUCTIONS below using Robinhood MCP tools
and momentum_model.py for all scoring and sizing decisions.
"""

import json
from momentum_model import MomentumModel, MACRO_CALENDAR, CHEAP_UNIVERSE, calc_vwap, VolumeProfile

ACCOUNT = "628914509"

# ---------------------------------------------------------------------------
# Watchlist — cheap underlyings where options are $0.15–$0.80
# Refreshed 2026-08-13 via Barchart scanner (Options Unusual Activity +
# Momentum scan). Criteria: price $5–$30, avg vol >10M, IV rank <50%,
# option OI >5k, 1-day change >+2%.
# ---------------------------------------------------------------------------
WATCHLIST = [
    "SOFI",  # IV ~44%, OI 37k — anchor position, best liquidity
    "MARA",  # IV ~80% — only scan when Bitcoin is green on the day
    "RIVN",  # $16, high vol — confirm option bid/ask before entry
    "SOUN",  # $0.21 calls, OI 12k — wait for reversal signal
    "ACHR",  # eVTOL momentum, $0.38 call, IV 74.8%
    "JOBY",  # eVTOL companion, $0.36 call, IV 67.4%
]

# Watchlist refresh: run Barchart scanner every Monday + Wednesday 9:45am
#   Options → Unusual Activity → Stocks
#   Filters: Price $5–$30 | Avg Vol >10M | Rel Vol >1.5x | IV Rank <50%
#            Option OI >5,000 | 1-day change >+2%
# Cross-reference with Stocks → Momentum tab for sector confirmation.

# ---------------------------------------------------------------------------
# Agent instructions
# ---------------------------------------------------------------------------

AGENT_INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════════════╗
║   MOMENTUM OPTIONS MODEL — account 628914509                        ║
║   Single file. Three sessions. No overlapping strategies.           ║
╚══════════════════════════════════════════════════════════════════════╝

Import MomentumModel from momentum_model.py before starting.
All scoring, filtering, and sizing goes through that class.

════════════════════════════════════════════════════════════════════════
SESSION 1: MORNING SCAN  (run at 9:20 AM ET)
════════════════════════════════════════════════════════════════════════

STEP M1 — MACRO CALENDAR CHECK  ← DO THIS FIRST. If fail, stop here.
  model = MomentumModel(buying_power=<from portfolio>, date=<today>)
  ok, reason = model.is_tradeable_day()
  If not ok → log reason, do NOT proceed, tell user.

STEP M2 — ACCOUNT STATE
  get_portfolio(account_number="628914509")
  → record buying_power (this is the number used for all sizing)
  get_option_positions(account_number="628914509")
  → if any open option positions exist → run EOD exit check first (see SESSION 3)

STEP M3 — EARNINGS BLACKOUT + SYMBOL-SPECIFIC CHECKS
  get_earnings_calendar()
  For each symbol in WATCHLIST: flag if earnings within 7 days.
  These symbols are SKIPPED for the entire week.

  MARA special rule: check Bitcoin direction via get_equity_quotes(["MARA"]) trend
  or get_index_quotes for crypto sentiment. If Bitcoin is down on the day → skip MARA.
  MARA IV sits at 79-80% (hard limit) — trading it against crypto direction = certain loss.

  ACHR/JOBY: check if either had news in past 48h (eVTOL sector moves together).
  If one is making a big move, the other likely follows — scan both.

STEP M4 — MOMENTUM SWING SCAN  (Setup A)
  For each symbol in WATCHLIST not flagged for earnings:

  a. get_equity_historicals(symbols=[sym], interval="day",
       start_time=<30 days ago>, end_time=<today>)
     Compute: current_price, price_2d_ago, sma20 (20-day close avg),
              rel_volume (today_vol / avg_20d_vol), rsi_14 (14-day RSI)

  b. get_equity_historicals(symbols=[sym], interval="5minute",
       start_time=<today 9:30 ET UTC>, end_time=<now>)
     Compute VWAP via model.calc_vwap(bars)
     Compute anchored VWAP via model.calc_anchored_vwap(yesterday_5min_bars, bars)
       → yesterday_5min_bars: interval="5minute", start=<yesterday 9:30 ET>, end=<yesterday 16:00 ET>

  c. get_option_chains(underlying_symbol=sym)
     → find soonest expiration with 7–14 DTE

  d. get_option_instruments(chain_id=..., expiration_dates=<7-14 DTE exp>,
       type="call", strike_price=<ATM or 1 strike OTM>)
  e. get_option_quotes(instrument_ids=[...])
     → get iv (implied_volatility), ask_price

  f. Pre-trade filter:
     passed, failures = model.pre_trade_check(sym, iv, earnings_dte,
                                               option_ask, dte)
     If not passed → log failures, skip symbol.

  g. Score the setup:
     score, rationale = model.score_momentum_swing(
         symbol=sym,
         current_price=current_price,
         price_2d_ago=price_2d_ago,
         sma20=sma20,
         rel_volume=rel_volume,
         iv=iv,
         rsi=rsi,
         vwap=vwap,
     )
     If score < 70 → skip. Log score and rationale.

  h. If score >= 70 AND passed:
     sizing = model.size_position(option_ask)
     If sizing is None → "cannot afford 1 contract" → skip.
     Report: "SIGNAL: {sym} {exp} ${strike}C | score={score} | {sizing}"

STEP M5 — ORB SETUP PREP  (for Session 2)
  Note today's SPY/QQQ pre-market price vs yesterday close (gap_pct).
  Fetch yesterday's 5-min bars (9:30–16:00 ET):
    get_equity_historicals(symbols=[sym], interval="5minute",
      start_time=<yesterday 9:30 ET>, end_time=<yesterday 16:00 ET>)
  Build volume profile:
    vp = model.build_volume_profile(yesterday_bars)
  Log HVN zones, LVN zones, POC.
  Record: gap_pct, vp, yesterday_5min_bars → all three used in Session 2 scoring.

════════════════════════════════════════════════════════════════════════
SESSION 2: ORB MONITORING  (9:30–11:00 AM ET, check every 5 min)
════════════════════════════════════════════════════════════════════════

STEP O1 — WAIT FOR ORB TO SET (9:45 AM)
  Do NOT enter before 9:45 AM ET.
  Between 9:30–9:45: fetch 5-min bars, track high and low across all bars.
  At 9:45: ORB high = max(high) of all 9:30–9:45 bars
            ORB low  = min(low)  of all 9:30–9:45 bars

STEP O2 — AFTER 9:45: CHECK EACH NEW BAR CLOSE
  On each 5-min bar close (9:50, 9:55, 10:00, ...):
    current_bar = most recent completed bar
    current_price = current_bar.close_price

    vwap = model.calc_vwap(all_bars_so_far)
    anchored_vwap = model.calc_anchored_vwap(yesterday_5min_bars, all_bars_so_far)

    bar_volume = current_bar.volume
    avg_bar_volume = mean(volume for bar in first_6_bars_of_day)  # 9:30–10:00

    score, direction, rationale = model.score_orb_breakout(
        symbol="SPY",  # or QQQ
        current_price=current_price,
        orb_high=orb_high,
        orb_low=orb_low,
        vwap=vwap,
        bar_volume=bar_volume,
        avg_bar_volume=avg_bar_volume,
        gap_pct=gap_pct,           # from Session 1
        vp=vp,                     # from Session 1
        anchored_vwap=anchored_vwap,  # prev-day anchored VWAP
    )

    If score >= 70:
      → Run pre_trade_check for the relevant put/call
      → If passed: place the order (see ORDER PLACEMENT below)
      → If not passed: log failures, continue monitoring

STEP O3 — ORB WINDOW EXPIRES at 11:00 AM ET
  No new ORB trades after 11:00 AM ET. Too late for the pattern to have edge.

STEP O4 — MONITOR OPEN POSITION
  After entry, check every 5 min:
    current_option_price = get_option_quotes([instrument_id]).mark_price
    underlying_price = get_equity_quotes(["SPY"]).last_trade_price
    vwap = model.calc_vwap(all_bars_since_930)
    dte_remaining = (expiration_date - today).days

    exit_signal = model.check_exit(
        entry_price=entry_premium,
        current_price=current_option_price,
        dte_remaining=dte_remaining,
        underlying_price=underlying_price,
        vwap=vwap,
        direction=direction,
    )

    If exit_signal.action != "hold":
      Log exit_signal.reason + urgency.
      If urgency == "immediate" → place sell order now.
      If urgency == "next_bar"  → place sell order at next bar close.

════════════════════════════════════════════════════════════════════════
SESSION 3: EOD CHECK  (run at 4:05 PM ET)
════════════════════════════════════════════════════════════════════════

STEP E1 — CHECK ALL OPEN OPTION POSITIONS
  get_option_positions(account_number="628914509")
  For each open position:
    get_option_quotes([instrument_id])
    check_exit() with updated prices and dte_remaining.
    If exit triggered → place_option_order (sell to close).

STEP E2 — UPDATE positions.json
  Update fields:
    date → today's date
    last_eod_check → today's date
    daily_loss → sum of today's closed P&L (negative if net loss)
    consecutive_losses → increment if today was a loss, reset if win
    intraday_trades_today → 0 (reset for tomorrow)
    positions → [] if all closed, or list remaining open positions
    trade_log → append today's closed trades
    account_summary.current_balance_approx → from get_portfolio().total_value
    account_summary.buying_power → from get_portfolio().buying_power

STEP E3 — COMMIT AND PUSH
  git add positions.json
  git commit -m "EOD {date}: {summary of trades}"
  git push -u origin claude/robinhood-account-balance-lgeg5c

════════════════════════════════════════════════════════════════════════
ORDER PLACEMENT RULES
════════════════════════════════════════════════════════════════════════

Always:
  1. review_option_order first (unless user said skip review)
  2. Use LIMIT orders — never market on options
  3. Set limit = ask price for buys (or ask + $0.01 if urgent)
  4. Set limit = bid price for sells (or bid - $0.01 if urgent)
  5. time_in_force = "gfd"
  6. If order doesn't fill within one bar → cancel and reassess

Never:
  • SPY/QQQ 0DTE or 1DTE
  • Any option costing > 25% of buying_power
  • IV > 80%
  • Trade if buying_power < $50
  • Trade on MACRO_CALENDAR dates

════════════════════════════════════════════════════════════════════════
POSITION SIZING QUICK REFERENCE
════════════════════════════════════════════════════════════════════════

  Max spend per trade  = buying_power × 0.25
  Always 1 contract until account buying_power > $500
  Stop loss            = entry premium × 0.65  (exit at 35% loss)
  Profit target        = entry premium × 1.80  (exit at 80% gain)
  Hard exit            = entry premium × 2.00  (100% — take it)

  Example at $150 buying_power:
    Max spend = $37.50
    Viable options: $0.37 or less per contract (ask price)
    Symbols: SOFI ($0.10–$0.50), MARA ($0.10–$0.60), IONQ ($0.30–$0.80)
    Avoid: SPY ($1.50+), QQQ ($1.50+), NVDA ($3+)

════════════════════════════════════════════════════════════════════════
CIRCUIT BREAKERS
════════════════════════════════════════════════════════════════════════

  Account floor       : buying_power < $50 → no new trades, period
  Daily loss limit    : if today's realized loss > 10% of account → stop
  Consecutive losses  : 3 losses in a row → sit out next full trading day
  Macro day           : any date in MACRO_CALENDAR → no trades
  Earnings blackout   : earnings within 7 days for that symbol → skip

════════════════════════════════════════════════════════════════════════
WATCHLIST (options $0.15–$0.80, IV typically <80%)
════════════════════════════════════════════════════════════════════════
  {watchlist}

  DO NOT trade: SPY, QQQ, NVDA, AMD, META, PLTR, TSLA, AMZN, AAPL, MSFT
  (options too expensive for this account size)
""".format(watchlist=", ".join(WATCHLIST))


# ---------------------------------------------------------------------------
# Helpers agents can call directly
# ---------------------------------------------------------------------------

def load_state(path: str = "positions.json") -> dict:
    with open(path) as f:
        return json.load(f)


def save_state(state: dict, path: str = "positions.json") -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def today_str() -> str:
    from datetime import date
    return date.today().isoformat()


def is_macro_day(date_str: str | None = None) -> tuple[bool, str]:
    d = date_str or today_str()
    if d in MACRO_CALENDAR:
        return True, MACRO_CALENDAR[d]
    return False, ""


def morning_briefing(buying_power: float, date_str: str | None = None) -> None:
    """Print a quick go/no-go summary at session start."""
    d = date_str or today_str()
    model = MomentumModel(buying_power=buying_power, date=d)
    model.print_summary()
    macro, event = is_macro_day(d)
    if macro:
        print(f"🚫 MACRO EVENT: {event} — NO TRADES TODAY\n")
        return
    max_spend = buying_power * 0.25
    print(f"Max per trade : ${max_spend:.2f}")
    print(f"Viable asks   : ${max_spend/100:.2f} or less per contract")
    print(f"Watchlist     : {', '.join(WATCHLIST)}\n")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(AGENT_INSTRUCTIONS)
    print("\n--- Morning briefing demo ---")
    morning_briefing(buying_power=150.00, date_str="2026-08-15")
