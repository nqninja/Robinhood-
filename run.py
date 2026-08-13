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
    "SOFI",  # IV ~44%, OI 37k — anchor position, best liquidity | ~$10B cap
    "MARA",  # IV ~80% — only scan when Bitcoin is green on the day | ~$4B cap
    "RIVN",  # $16, high vol — cap ~$12B, slightly over limit; re-check before adding
    "SOUN",  # $0.21 calls, OI 12k — wait for reversal signal | ~$2B cap
    "ACHR",  # eVTOL momentum, $0.38 call, IV 74.8% | ~$3B cap
    "JOBY",  # eVTOL companion, $0.36 call, IV 67.4% | ~$3B cap
]

# MID-CAP RULE: $2B–$10B market cap ONLY — sweet spot for max volatility + real options liquidity
#   > $10B: too much institutional dampening, options get expensive
#   < $2B:  thin options market, wide spreads, manipulation risk
#   RIVN is borderline (~$12B) — always verify market cap before trading

# Watchlist refresh: run Barchart scanner every Monday + Wednesday 9:45am
#   Options → Unusual Activity → Stocks
#   Filters: Price $5–$30 | Avg Vol >10M | Rel Vol >1.5x | IV Rank <50%
#            Option OI >5,000 | 1-day change >+2% | Market Cap $2B–$10B
# Cross-reference with Stocks → Momentum tab for sector confirmation.

# ---------------------------------------------------------------------------
# Agent instructions
# ---------------------------------------------------------------------------

AGENT_INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════════════╗
║   MOMENTUM OPTIONS MODEL — account 628914509                        ║
║   BARCHART FINDS IT → PRICE ACTION CONFIRMS IT →                   ║
║   OPTIONS CHAIN PICKS THE CONTRACT → RISK MANAGEMENT APPROVES      ║
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
  For each symbol in WATCHLIST: flag if earnings within 7 days → SKIP that symbol.

  MARA special rule: if Bitcoin is down on the day → skip MARA entirely.
  MARA IV sits at 79-80% — trading it against crypto direction = certain loss.

  ACHR/JOBY: eVTOL sector moves together. If one is running, check the other too.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP M4 — FIND THE STOCK  (Barchart unusual activity scan)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Check unusual options activity across WATCHLIST:
    get_option_chains + get_option_quotes for each symbol
    Look for: volume significantly above normal (Vol/OI ratio > 2x)
    Identify: are calls or puts dominating?
    Flag: any far-OTM lottery activity (skip those — not real signals)
    Result: rank symbols by unusual activity strength

  Barchart criteria to simulate:
    ✓ Options volume >> normal (Vol/OI > 2x)
    ✓ Calls dominating → bullish lean
    ✓ Puts dominating → bearish lean
    ✗ Skip: random far-OTM strikes with tiny OI

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP M5 — ELIMINATE BAD STOCKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  For each candidate from M4, verify ALL of these or drop it:
    ✓ Options liquidity: OI > 5,000 on target strike
    ✓ Bid/ask spread: (ask - bid) / mid ≤ 15%
    ✓ Stock volume: rel_volume > 1.5x 20-day average
    ✓ Stock is actually moving: ≥ +2% on day for calls, ≤ -2% for puts
    ✓ No earnings within 7 days
    ✓ Not far-OTM lottery activity (strike within 5% of current price)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP M6 — CONFIRM THE STOCK  (price action check)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  get_equity_historicals(sym, interval="day", start=<30 days ago>)
  Compute: current_price, price_2d_ago, sma20, rel_volume, rsi_14

  get_equity_historicals(sym, interval="5minute", start=<today 9:30 ET>)
  Compute: intraday VWAP, anchored VWAP (prev-day bars + today bars)

  Confirm ALL of these:
    ✓ Clear bullish or bearish momentum (not just choppy)
    ✓ Relative volume > 1.5x
    ✓ Breaking or rejecting an important level (SMA20, prior high/low, VWAP)
    ✓ Price above VWAP for calls / below VWAP for puts
    ✓ Price above prev-day AVWAP for calls / below for puts
    ✓ Market (SPY) direction not fighting the trade

  Score via model.score_momentum_swing(...)
  If score < 70 → skip. No exceptions.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP M7 — CONFIRM OPTIONS ACTIVITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  get_option_quotes for the target contract:
    ✓ Unusual volume confirmed (Vol/OI > 2x)
    ✓ Volume substantially above average
    ✓ Calls dominating → agrees with bullish setup
    ✓ Puts dominating → agrees with bearish setup
    ✗ Activity is NOT just cheap lottery contracts (far OTM, tiny OI)
    ✓ OI > 5,000 (real liquidity, not a trap)

  If calls are screaming but price action is bearish = SKIP.
  If puts are screaming but price action is bullish = SKIP.
  Options activity must agree with price action. Always.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP M8 — PICK THE CONTRACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Target contract criteria:
    ✓ ATM or slightly OTM (within 2-3% of current price)
    ✓ Delta 0.40–0.65 (not deep ITM, not lottery OTM)
    ✓ DTE: 7–14 days (enough time, not theta-crushed)
    ✓ IV < 80%
    ✓ Bid/ask spread ≤ 15% of mid
    ✓ OI > 5,000
    ✓ Ask price ≤ 25% of buying_power (fits the account)

  get_option_instruments(chain_id, expiration, type, strike)
  get_option_quotes(instrument_ids)
  → verify: iv, bid, ask, open_interest, delta

  Run model.pre_trade_check(sym, iv, earnings_dte, option_ask, dte)
  If not passed → stop here regardless of everything else.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP M9 — FINAL CHECK BEFORE ENTRY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Answer YES to every question or DO NOT trade:
    ☐ Stock setup confirmed (score ≥ 70)?
    ☐ Options activity agrees with direction?
    ☐ Entry price known and limit set?
    ☐ Stop loss defined (entry × 0.65)?
    ☐ Profit target defined (entry × 1.80)?
    ☐ Risk/reward ≥ 2:1?
    ☐ NOT chasing a move already made?
    ☐ NOT buying simply because Barchart flagged "unusual"?
    ☐ Confluence gate: VWAP + AVWAP + gap all agree?

  If any box is unchecked → NO TRADE. Wait for the next setup.

  sizing = model.size_position(option_ask)
  Report: "SIGNAL: {sym} {exp} ${strike}C | score={score} | {sizing}"

STEP M10 — ORB SETUP PREP  (for Session 2)
  Note today's gap_pct (pre-market price vs yesterday close).
  Fetch yesterday's 5-min bars (9:30–16:00 ET):
    get_equity_historicals(sym, interval="5minute",
      start=<yesterday 9:30 ET>, end=<yesterday 16:00 ET>)
  vp = model.build_volume_profile(yesterday_bars)
  Log HVN zones, LVN zones, POC.
  Record: gap_pct, vp, yesterday_5min_bars → used in Session 2.

════════════════════════════════════════════════════════════════════════
SESSION 2: ORB MONITORING  (9:45–11:00 AM ET, check every 5 min)
════════════════════════════════════════════════════════════════════════

STEP O1 — WAIT FOR ORB TO SET (9:45 AM)
  Do NOT enter before 9:45 AM ET.
  Between 9:30–9:45: fetch 5-min bars, track high and low.
  At 9:45: ORB high = max(high) of 9:30–9:45 bars
            ORB low  = min(low)  of 9:30–9:45 bars

STEP O2 — AFTER 9:45: CHECK EACH NEW BAR CLOSE
  On each 5-min bar close (9:50, 9:55, 10:00, ...):
    current_price = most recent completed bar close
    vwap = model.calc_vwap(all_bars_so_far)
    anchored_vwap = model.calc_anchored_vwap(yesterday_5min_bars, all_bars_so_far)
    bar_volume = current_bar.volume
    avg_bar_volume = mean(volume for bar in first_6_bars_of_day)

    score, direction, rationale = model.score_orb_breakout(
        symbol=sym,
        current_price=current_price,
        orb_high=orb_high, orb_low=orb_low,
        vwap=vwap, bar_volume=bar_volume,
        avg_bar_volume=avg_bar_volume,
        gap_pct=gap_pct, vp=vp,
        anchored_vwap=anchored_vwap,
    )

    If score >= 70 → run STEP M7–M9 for the breakout contract → place if approved.

STEP O3 — ORB WINDOW EXPIRES at 11:00 AM ET. No new trades after this.

STEP O4 — MONITOR OPEN POSITION (every 5 min after entry)
    exit_signal = model.check_exit(entry_price, current_option_price,
                                   dte_remaining, underlying_price, vwap, direction)
    If urgency == "immediate" → sell now.
    If urgency == "next_bar"  → sell at next bar close.

════════════════════════════════════════════════════════════════════════
SESSION 3: EOD CHECK  (run at 4:05 PM ET)
════════════════════════════════════════════════════════════════════════

STEP E1 — CHECK ALL OPEN OPTION POSITIONS
  get_option_positions(account_number="628914509")
  For each open position: get_option_quotes → check_exit → sell if triggered.

STEP E2 — UPDATE positions.json
  date, last_eod_check, daily_loss, consecutive_losses,
  intraday_trades_today → 0, positions, trade_log,
  account_summary.current_balance_approx, account_summary.buying_power

STEP E3 — COMMIT AND PUSH
  git add positions.json
  git commit -m "EOD {date}: {summary}"
  git push -u origin claude/robinhood-account-balance-lgeg5c

════════════════════════════════════════════════════════════════════════
ORDER PLACEMENT RULES
════════════════════════════════════════════════════════════════════════

Always:
  1. review_option_order first
  2. LIMIT orders only — never market on options
  3. Buy limit = ask price (or ask + $0.01 if urgent)
  4. Sell limit = bid price (or bid - $0.01 if urgent)
  5. time_in_force = "gfd"
  6. No fill within one bar → cancel and reassess

Never:
  • 0DTE or 1DTE options
  • Any option > 25% of buying_power
  • IV > 80%
  • Trade if buying_power < $50
  • Trade on MACRO_CALENDAR dates
  • Enter simply because something shows on Barchart

════════════════════════════════════════════════════════════════════════
POSITION SIZING
════════════════════════════════════════════════════════════════════════
  Max spend    = buying_power × 0.25
  Contracts    = 1 (until buying_power > $500)
  Stop loss    = entry × 0.65  (-35%)
  Target       = entry × 1.80  (+80%)
  Hard exit    = entry × 2.00  (+100% — always take it)

════════════════════════════════════════════════════════════════════════
CIRCUIT BREAKERS
════════════════════════════════════════════════════════════════════════
  buying_power < $50          → no new trades
  daily loss > 10% of account → stop for the day
  3 consecutive losses        → sit out next full trading day
  macro event day             → no trades
  earnings within 7 days      → skip that symbol

════════════════════════════════════════════════════════════════════════
WATCHLIST  {watchlist}
════════════════════════════════════════════════════════════════════════
  SOFI — IV ~44%, OI 37k, anchor position
  MARA — IV ~80%, Bitcoin green days only
  RIVN — $16 range, verify option pricing before entry
  SOUN — cheapest calls (~$0.21), wait for reversal
  ACHR — eVTOL momentum, $0.38 call, IV 74.8%
  JOBY — eVTOL companion, $0.36 call, IV 67.4%

  DO NOT trade: SPY, QQQ, NVDA, AMD, META, PLTR, TSLA, AMZN, AAPL, MSFT, HOOD, IONQ
""".format(watchlist=str(WATCHLIST))


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
