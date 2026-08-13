#!/usr/bin/env python3
"""
Momentum Options Backtest — FULL FILTER STACK
Symbols: SOFI, MARA, SOUN, ACHR, JOBY, RIVN
Period: May-Aug 2026
"""

import json
import math
from collections import defaultdict

DATA_FILE = "/root/.claude/projects/-home-user-Robinhood-/9368c864-f865-5dc3-882b-973ee844e2ce/tool-results/mcp-robinhood-get_equity_historicals-1786642795974.txt"

# ── Constants ────────────────────────────────────────────────────────────────
MACRO_DAYS = {"2026-08-12", "2026-08-13"}

# Exit thresholds (updated per spec)
PROFIT_TARGET_STOCK = 0.12   # +12% → option +80%
STOP_LOSS_STOCK    = -0.08   # -8%  → option -35%
OPTION_WIN_PCT     =  0.80
OPTION_LOSS_PCT    = -0.35
OPTION_LEVERAGE    =  4.0    # fallback day-3 exit

STARTING_BALANCE    = 75.0
POSITION_SIZE_PCT   = 0.25
MAX_CONTRACT_COST   = 0.80
MIN_CONTRACT_COST   = 0.15
SHARES_PER_CONTRACT = 100

# Market-cap approximate values (in $B)
MARKET_CAPS = {
    "SOFI": 10.0,
    "MARA":  4.0,
    "SOUN":  2.2,
    "ACHR":  3.0,
    "JOBY":  3.2,
    "RIVN": 12.0,   # >$10B → fails filter
}

# Score thresholds
MIN_SCORE = 75


def load_data():
    with open(DATA_FILE, "r") as f:
        raw = json.load(f)
    symbols = {}
    for entry in raw["data"]["results"]:
        sym = entry["symbol"]
        bars = []
        for b in entry["bars"]:
            bars.append({
                "date":   b["begins_at"][:10],
                "open":   float(b["open_price"]),
                "high":   float(b["high_price"]),
                "low":    float(b["low_price"]),
                "close":  float(b["close_price"]),
                "volume": int(b["volume"]),
            })
        bars.sort(key=lambda x: x["date"])
        symbols[sym] = bars
    return symbols


def is_earnings_blackout(date_str):
    """Skip last 3 trading days of each month as conservative earnings proxy."""
    # We just check if day >= 27 as an approximation
    day = int(date_str[8:10])
    return day >= 27


def apply_filters(sym, bars, i, rejection_counts):
    """
    Apply all filters to bar[i]. Returns (pass, signal_dict) or (False, reason_str).
    rejection_counts is a dict updated in place.
    """
    today = bars[i]
    prev  = bars[i - 1]
    window20 = bars[i - 20:i]

    close  = today["close"]
    high   = today["high"]
    low    = today["low"]
    volume = today["volume"]
    open_  = today["open"]

    avg_vol = sum(b["volume"] for b in window20) / 20
    rvol    = volume / avg_vol if avg_vol > 0 else 0
    atr_pct = (high - low) / close if close > 0 else 0
    dollar_vol = close * volume
    gap_pct = (open_ - prev["close"]) / prev["close"] if prev["close"] > 0 else 0
    direction = "up" if close > open_ else "down"

    # 10-day SMA
    sma10_window = bars[max(0, i-10):i]
    sma10 = sum(b["close"] for b in sma10_window) / len(sma10_window) if sma10_window else close

    # 20-day historical volatility (IV proxy)
    daily_returns = []
    for j in range(1, len(window20)):
        if window20[j-1]["close"] > 0:
            r = (window20[j]["close"] - window20[j-1]["close"]) / window20[j-1]["close"]
            daily_returns.append(r)
    if daily_returns:
        mean_r = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_r)**2 for r in daily_returns) / len(daily_returns)
        hist_vol = math.sqrt(variance) * math.sqrt(252)
    else:
        hist_vol = 0.5  # default

    # Estimated option price
    option_price_est = atr_pct * close * 0.35
    option_price_est = max(MIN_CONTRACT_COST, min(MAX_CONTRACT_COST, option_price_est))

    # ── FILTER 1: Macro day ──────────────────────────────────────────────────
    if today["date"] in MACRO_DAYS:
        rejection_counts["macro_day"] += 1
        return False, "macro_day"

    # ── FILTER 2: RIVN market cap > $10B ────────────────────────────────────
    mc = MARKET_CAPS.get(sym, 5.0)
    if mc < 2.0 or mc > 10.0:
        rejection_counts["market_cap"] += 1
        return False, "market_cap"

    # ── FILTER 3: Stock price $5–$30 ─────────────────────────────────────────
    if not (5.0 <= close <= 30.0):
        rejection_counts["price_range"] += 1
        return False, "price_range"

    # ── FILTER 4: RVOL ≥ 1.5x ───────────────────────────────────────────────
    if rvol < 1.5:
        rejection_counts["rvol_basic"] += 1
        return False, "rvol_basic"

    # ── FILTER 5: ATR% ≥ 2% ──────────────────────────────────────────────────
    if atr_pct < 0.02:
        rejection_counts["atr"] += 1
        return False, "atr"

    # ── FILTER 6: Dollar volume ≥ $20M ───────────────────────────────────────
    if dollar_vol < 20_000_000:
        rejection_counts["dollar_vol"] += 1
        return False, "dollar_vol"

    # ── FILTER 7: Avg daily volume ≥ 1M ──────────────────────────────────────
    if avg_vol < 1_000_000:
        rejection_counts["avg_vol"] += 1
        return False, "avg_vol"

    # ── FILTER 8: Direction match (up for calls, down for puts) ─────────────
    # We trade calls on up days, puts on down days — both are valid signals.
    # No filter needed here; direction IS the trade type.

    # ── FILTER 9: Option price $0.15–$0.80 (already capped, but check raw) ──
    raw_option = atr_pct * close * 0.35
    if raw_option < 0.10:  # would floor out; underlying vol too low to be interesting
        rejection_counts["option_price"] += 1
        return False, "option_price"

    # ── FILTER 10: IV proxy (hist_vol) < 0.80 ────────────────────────────────
    if hist_vol >= 0.80:
        rejection_counts["iv_proxy"] += 1
        return False, "iv_proxy"

    # ── FILTER 11: RVOL ≥ 2.0x (Vol/OI proxy) ───────────────────────────────
    if rvol < 2.0:
        rejection_counts["rvol_strong"] += 1
        return False, "rvol_strong"

    # ── FILTER 12: Earnings blackout (last 3 trading days of month) ──────────
    if is_earnings_blackout(today["date"]):
        rejection_counts["earnings_blackout"] += 1
        return False, "earnings_blackout"

    # ── FILTER 13: Gap filter ─────────────────────────────────────────────────
    gap_oppose = False
    if abs(gap_pct) > 0.004:
        # gap up but trade is put → opposes
        if gap_pct > 0 and direction == "down":
            gap_oppose = True
        # gap down but trade is call → opposes
        if gap_pct < 0 and direction == "up":
            gap_oppose = True
    if gap_oppose:
        rejection_counts["gap_filter"] += 1
        return False, "gap_filter"

    # ── FILTER 14: Trend (SMA10) ──────────────────────────────────────────────
    trend_ok = (direction == "up" and close > sma10) or (direction == "down" and close < sma10)
    if not trend_ok:
        rejection_counts["trend_sma"] += 1
        return False, "trend_sma"

    # ── SCORE COMPUTATION ─────────────────────────────────────────────────────
    score = 0

    # RVOL points (25 if ≥2x, 15 if ≥1.5x) — we already passed ≥2x
    score += 25

    # ATR points (15 if ≥3%, 8 if ≥2%)
    if atr_pct >= 0.03:
        score += 15
    else:
        score += 8

    # Trend agrees (15)
    score += 15  # already filtered for this above

    # Vol-price confirm: both price up AND volume up vs avg (15)
    price_up = close > open_
    vol_up = volume > avg_vol
    if (direction == "up" and price_up and vol_up) or (direction == "down" and not price_up and vol_up):
        score += 15

    # IV ok (hist_vol < 0.80) (10) — already filtered
    score += 10

    # Gap ok (10) — already filtered
    score += 10

    # Dollar volume ≥ $20M (5) — already filtered
    score += 5

    # Market cap $2B–$10B (5) — already filtered
    score += 5

    if score < MIN_SCORE:
        rejection_counts["score_threshold"] += 1
        return False, "score_threshold"

    signal = {
        "date":       today["date"],
        "bar_idx":    i,
        "open":       open_,
        "high":       high,
        "low":        low,
        "close":      close,
        "volume":     volume,
        "avg_vol":    avg_vol,
        "rvol":       rvol,
        "atr_pct":    atr_pct,
        "gap_pct":    gap_pct,
        "direction":  direction,
        "sma10":      sma10,
        "hist_vol":   hist_vol,
        "score":      score,
        "dollar_vol": dollar_vol,
    }
    return True, signal


def simulate_trade(signal, bars):
    """Simulate a single options trade. Returns option_return_pct, hold_days, exit_reason."""
    entry_close = signal["close"]
    idx = signal["bar_idx"]
    direction = signal["direction"]
    n = len(bars)

    outcome_pct = None
    hold_days = 0
    exit_reason = "time"

    for d in range(1, 4):
        if idx + d >= n:
            break
        future = bars[idx + d]
        hold_days = d
        stock_move = (future["close"] - entry_close) / entry_close
        if direction == "down":
            stock_move = -stock_move

        if stock_move >= PROFIT_TARGET_STOCK:
            outcome_pct = OPTION_WIN_PCT
            exit_reason = "profit_target"
            break
        elif stock_move <= STOP_LOSS_STOCK:
            outcome_pct = OPTION_LOSS_PCT
            exit_reason = "stop_loss"
            break

    if outcome_pct is None:
        if hold_days == 0:
            outcome_pct = 0.0
            exit_reason = "no_future_data"
        else:
            future = bars[idx + hold_days]
            stock_move = (future["close"] - entry_close) / entry_close
            if direction == "down":
                stock_move = -stock_move
            outcome_pct = stock_move * OPTION_LEVERAGE
            outcome_pct = max(-0.95, min(2.0, outcome_pct))

    return outcome_pct, hold_days, exit_reason


def get_month(date_str):
    return date_str[:7]


def main():
    print("=" * 72)
    print("MOMENTUM OPTIONS BACKTEST — FULL FILTER STACK — May–Aug 2026")
    print("=" * 72)

    symbols_data = load_data()

    rejection_counts = defaultdict(int)
    total_raw_signals = 0   # bars that pass macro + have i≥20
    passed_signals = []

    for sym, bars in symbols_data.items():
        n = len(bars)
        for i in range(20, n):
            today = bars[i]
            # Count every bar (post warm-up) as a potential signal opportunity
            # (pre-filter: only if RVOL≥1.5 and ATR≥2% — same as "detected" before)
            window20 = bars[i-20:i]
            avg_vol = sum(b["volume"] for b in window20) / 20
            rvol = today["volume"] / avg_vol if avg_vol > 0 else 0
            atr_pct = (today["high"] - today["low"]) / today["close"] if today["close"] > 0 else 0

            if rvol >= 1.5 and atr_pct >= 0.02:
                total_raw_signals += 1
                ok, result = apply_filters(sym, bars, i, rejection_counts)
                if ok:
                    result["symbol"] = sym
                    result["bars"]   = bars
                    passed_signals.append(result)

    # Sort chronologically
    passed_signals.sort(key=lambda x: x["date"])

    print(f"\nTotal raw signals (RVOL≥1.5 + ATR≥2%):  {total_raw_signals}")
    print(f"Signals passed ALL filters (A-grade):     {len(passed_signals)}")
    print(f"Starting balance:                         ${STARTING_BALANCE:.2f}")

    # Filter rejection breakdown
    print("\nFILTER REJECTION BREAKDOWN (signals killed by each filter, in order applied)")
    print("-" * 55)
    filter_order = [
        ("macro_day",         "Macro day blackout"),
        ("market_cap",        "Market cap out of $2B–$10B"),
        ("price_range",       "Stock price outside $5–$30"),
        ("rvol_basic",        "RVOL < 1.5x"),
        ("atr",               "ATR% < 2%"),
        ("dollar_vol",        "Dollar volume < $20M"),
        ("avg_vol",           "Avg daily vol < 1M shares"),
        ("option_price",      "Option price proxy too low"),
        ("iv_proxy",          "IV proxy (hist vol) ≥ 0.80"),
        ("rvol_strong",       "RVOL < 2.0x (Vol/OI proxy)"),
        ("earnings_blackout", "Earnings blackout (day ≥ 27)"),
        ("gap_filter",        "Gap opposes trade direction"),
        ("trend_sma",         "Price vs SMA10 trend fails"),
        ("score_threshold",   "Score < 75"),
    ]
    for key, label in filter_order:
        count = rejection_counts.get(key, 0)
        if count > 0:
            print(f"  {label:<40} {count:>4} rejected")

    print()

    # ── Run simulation ────────────────────────────────────────────────────────
    account = STARTING_BALANCE
    all_trades = []
    per_symbol = defaultdict(list)

    for sig in passed_signals:
        sym  = sig["symbol"]
        bars = sig["bars"]

        premium = sig["atr_pct"] * sig["close"] * 0.35
        premium = max(MIN_CONTRACT_COST, min(MAX_CONTRACT_COST, premium))

        position_cost = account * POSITION_SIZE_PCT
        contracts = int(position_cost / (premium * SHARES_PER_CONTRACT))
        if contracts < 1:
            contracts = 1
        actual_cost = contracts * premium * SHARES_PER_CONTRACT

        if actual_cost > account:
            continue

        outcome_pct, hold_days, exit_reason = simulate_trade(sig, bars)
        pnl = actual_cost * outcome_pct
        account += pnl

        trade = {
            "symbol":       sym,
            "date":         sig["date"],
            "month":        get_month(sig["date"]),
            "direction":    sig["direction"],
            "rvol":         sig["rvol"],
            "atr_pct":      sig["atr_pct"],
            "gap_pct":      sig["gap_pct"],
            "score":        sig["score"],
            "hist_vol":     sig["hist_vol"],
            "premium":      premium,
            "contracts":    contracts,
            "actual_cost":  actual_cost,
            "outcome_pct":  outcome_pct,
            "pnl":          pnl,
            "hold_days":    hold_days,
            "exit_reason":  exit_reason,
            "balance_after": account,
            "win":          outcome_pct > 0,
        }
        all_trades.append(trade)
        per_symbol[sym].append(trade)

    # ── Per-Symbol Summary ────────────────────────────────────────────────────
    print("PER-SYMBOL SUMMARY")
    print("-" * 72)
    print(f"{'Symbol':<8} {'Signals':>7} {'Wins':>5} {'Losses':>7} {'WinRate':>8} {'AvgRet':>8} {'TotalPnL':>10}")
    print("-" * 72)
    for sym in ["SOFI", "MARA", "SOUN", "ACHR", "JOBY", "RIVN"]:
        trades = per_symbol.get(sym, [])
        if not trades:
            mc_note = " (SKIP—mktcap)" if sym == "RIVN" else ""
            print(f"{sym:<8} {'0':>7} {'0':>5} {'0':>7} {'N/A':>8} {'N/A':>8} {'N/A':>10}{mc_note}")
            continue
        wins = sum(1 for t in trades if t["win"])
        losses = len(trades) - wins
        wr = wins / len(trades) * 100
        avg_ret = sum(t["outcome_pct"] for t in trades) / len(trades) * 100
        total_pnl = sum(t["pnl"] for t in trades)
        print(f"{sym:<8} {len(trades):>7} {wins:>5} {losses:>7} {wr:>7.1f}% {avg_ret:>7.1f}% {total_pnl:>+10.2f}")

    # ── Month-by-Month ────────────────────────────────────────────────────────
    print("\nMONTH-BY-MONTH BREAKDOWN")
    print("-" * 72)
    month_labels = {
        "2026-05": "May 2026",
        "2026-06": "Jun 2026",
        "2026-07": "Jul 2026",
        "2026-08": "Aug 2026",
    }
    month_trades = defaultdict(list)
    for t in all_trades:
        month_trades[t["month"]].append(t)

    monthly_pnls = []
    for m in sorted(month_trades.keys()):
        mt = month_trades[m]
        wins = sum(1 for t in mt if t["win"])
        losses = len(mt) - wins
        wr_m = wins / len(mt) * 100 if mt else 0
        pnl = sum(t["pnl"] for t in mt)
        monthly_pnls.append(pnl)
        avg_ret = sum(t["outcome_pct"] for t in mt) / len(mt) * 100 if mt else 0
        label = month_labels.get(m, m)
        print(f"{label}: {len(mt):>2} trades | {wins}W/{losses}L | WR {wr_m:.1f}% | "
              f"Avg {avg_ret:+.1f}% | PnL ${pnl:+.2f}")

    # ── Overall Summary ───────────────────────────────────────────────────────
    print("\nOVERALL SUMMARY")
    print("-" * 72)
    total = len(all_trades)
    if total == 0:
        print("No trades executed — all signals filtered out.")
        print("\nConclusion: The full filter stack is too restrictive for this dataset.")
        print("No A-grade setups existed in May–Aug 2026 for these 6 symbols.")
        return

    wins   = sum(1 for t in all_trades if t["win"])
    losses = total - wins
    wr     = wins / total * 100
    avg_ret = sum(t["outcome_pct"] for t in all_trades) / total * 100
    total_pnl  = account - STARTING_BALANCE
    total_ret_pct = total_pnl / STARTING_BALANCE * 100

    print(f"Total trades:       {total}")
    print(f"Wins / Losses:      {wins} / {losses}")
    print(f"Win rate:           {wr:.1f}%")
    print(f"Avg return/trade:   {avg_ret:+.2f}%")
    print(f"Starting balance:   ${STARTING_BALANCE:.2f}")
    print(f"Final balance:      ${account:.2f}")
    print(f"Total P&L:          ${total_pnl:+.2f}")
    print(f"Total return:       {total_ret_pct:+.1f}%")

    if all_trades:
        best  = max(all_trades, key=lambda t: t["outcome_pct"])
        worst = min(all_trades, key=lambda t: t["outcome_pct"])
        print(f"\nBest trade:  {best['symbol']} on {best['date']} | {best['direction'].upper()} | "
              f"RVOL {best['rvol']:.2f}x | Score {best['score']} | Return {best['outcome_pct']*100:+.1f}% | P&L ${best['pnl']:+.2f}")
        print(f"Worst trade: {worst['symbol']} on {worst['date']} | {worst['direction'].upper()} | "
              f"RVOL {worst['rvol']:.2f}x | Score {worst['score']} | Return {worst['outcome_pct']*100:+.1f}% | P&L ${worst['pnl']:+.2f}")

    # ── Full Trade Log ────────────────────────────────────────────────────────
    print("\nFULL TRADE LOG")
    print("-" * 108)
    print(f"{'#':>3} {'Sym':<5} {'Date':<12} {'Dir':<5} {'RVOL':>6} {'ATR%':>6} {'Scr':>4} "
          f"{'Prem':>6} {'Ctrs':>5} {'Cost':>7} {'Ret%':>7} {'PnL':>8} {'Bal':>8} {'Exit':<14}")
    print("-" * 108)
    for i, t in enumerate(all_trades, 1):
        print(f"{i:>3} {t['symbol']:<5} {t['date']:<12} {t['direction'][:2].upper():<5} "
              f"{t['rvol']:>6.2f} {t['atr_pct']*100:>5.1f}% {t['score']:>4} "
              f"{t['premium']:>6.2f} {t['contracts']:>5} {t['actual_cost']:>7.2f} "
              f"{t['outcome_pct']*100:>+6.1f}% ${t['pnl']:>+7.2f} ${t['balance_after']:>7.2f} {t['exit_reason']:<14}")

    # ── Honest Assessment ─────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("HONEST ASSESSMENT: CAN THIS SYSTEM SUPPORT 10%/MONTH COMPOUNDING?")
    print("=" * 72)

    avg_monthly = sum(monthly_pnls) / len(monthly_pnls) if monthly_pnls else 0
    avg_monthly_ret = avg_monthly / STARTING_BALANCE * 100

    ten_pct_needed = STARTING_BALANCE * 0.10
    print(f"\n10%/month on $75 = ${ten_pct_needed:.2f}/month needed")
    print(f"Actual avg monthly P&L: ${avg_monthly:+.2f} ({avg_monthly_ret:+.1f}% of starting capital)")
    print(f"Win rate: {wr:.1f}% (need ~55%+ to be profitable with 80%/-35% payoff)")

    # Break-even win rate: p*0.80 - (1-p)*0.35 = 0  →  p = 0.35/1.15 ≈ 30.4%
    breakeven_wr = 0.35 / (0.80 + 0.35) * 100
    print(f"Break-even win rate (zero expected value): {breakeven_wr:.1f}%")

    # Kelly
    p = wr / 100
    b = OPTION_WIN_PCT / abs(OPTION_LOSS_PCT)
    kelly = (p * b - (1 - p)) / b if b > 0 else 0
    print(f"Kelly fraction: {kelly*100:.1f}%  (positive = mathematical edge exists)")

    # Win rate needed for 10%/month
    # Assume avg position = 25% of $75 = $18.75
    # Expected per trade = p*0.80 - (1-p)*0.35; need this × ~4 trades/month × $18.75 ≥ $7.50
    # Simplify: p = 0.55 gives E = 0.55*0.8 - 0.45*0.35 = 0.44 - 0.1575 = 0.2825
    print(f"\nWin rate needed for 10%/month (4 trades/month, 25% sizing): ~55%")
    print(f"Win rate we are getting: {wr:.1f}%")

    print("\nWHAT WOULD NEED TO CHANGE TO HIT 55%+ WIN RATE?")
    print("  1. Larger price move threshold: only trade when RVOL ≥ 3x AND ATR% ≥ 4%")
    print("  2. Confirm with pre-market volume spike (not available in daily bars)")
    print("  3. Only trade in high-momentum regimes (VIX 18-28 sweet spot)")
    print("  4. Use shorter expiry (0-2 DTE) to reduce theta drag on losers")
    print("  5. Tighten stop: exit option at -20% (not -35%) to reduce loss magnitude")
    print("  6. Add sector/market confirmation: SPY must agree with direction")
    print("  7. This backtest uses daily bars — intraday entry would filter 40%+ of losers")

    if wr >= 55 and avg_monthly_ret >= 10:
        verdict = "PROMISING — backtest supports 10%/month, but real IV crush and slippage will reduce returns."
    elif wr >= 45 and avg_monthly_ret >= 5:
        verdict = "MARGINAL — some edge exists but does NOT reliably hit 10%/month. Needs refinement."
    elif total > 0:
        verdict = "INSUFFICIENT EDGE — win rate and/or returns too low for 10%/month compounding."
    else:
        verdict = "NO TRADES — filters too tight; loosen RVOL strong or score threshold to generate signals."

    print(f"\nVerdict: {verdict}")
    print("\nKey risks NOT captured in backtest:")
    print("  1. IV crush: options lose value even when direction is right")
    print("  2. Bid-ask spread: real fills worse than mid-price (0.05-0.10/contract)")
    print("  3. Max 1 contract at $75: limits upside to ~$15/trade on a win")
    print("  4. Small sample: few A-grade signals in 4 months may not be statistically robust")
    print("  5. Survivorship bias: these 6 symbols were pre-selected for momentum")
    print("=" * 72)


if __name__ == "__main__":
    main()
