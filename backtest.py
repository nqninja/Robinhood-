#!/usr/bin/env python3
"""
Momentum Options Backtest
Symbols: SOFI, MARA, SOUN, ACHR, JOBY, RIVN
Period: May-Aug 2026
"""

import json
import sys
from datetime import datetime, date
from collections import defaultdict

DATA_FILE = "/root/.claude/projects/-home-user-Robinhood-/9368c864-f865-5dc3-882b-973ee844e2ce/tool-results/mcp-robinhood-get_equity_historicals-1786642795974.txt"

MACRO_DAYS = {"2026-08-12", "2026-08-13"}

RVOL_THRESHOLD = 1.5
ATR_THRESHOLD = 0.02
PROFIT_TARGET_STOCK = 0.15   # +15% stock move → option +80%
STOP_LOSS_STOCK = -0.10      # -10% stock move → option -35%
OPTION_WIN_PCT = 0.80
OPTION_LOSS_PCT = -0.35
OPTION_LEVERAGE = 4.0        # fallback: stock % × 4

STARTING_BALANCE = 75.0
POSITION_SIZE_PCT = 0.25
MAX_CONTRACT_COST = 0.80
MIN_CONTRACT_COST = 0.15
SHARES_PER_CONTRACT = 100


def load_data():
    with open(DATA_FILE, "r") as f:
        raw = json.load(f)
    symbols = {}
    for entry in raw["data"]["results"]:
        sym = entry["symbol"]
        bars = []
        for b in entry["bars"]:
            bars.append({
                "date": b["begins_at"][:10],
                "open": float(b["open_price"]),
                "high": float(b["high_price"]),
                "low": float(b["low_price"]),
                "close": float(b["close_price"]),
                "volume": int(b["volume"]),
            })
        bars.sort(key=lambda x: x["date"])
        symbols[sym] = bars
    return symbols


def compute_signals(bars):
    """Return list of signal dicts for bars that meet criteria."""
    signals = []
    n = len(bars)
    for i in range(20, n):
        today = bars[i]
        prev = bars[i - 1]
        window = bars[i - 20:i]

        avg_vol = sum(b["volume"] for b in window) / 20
        rvol = today["volume"] / avg_vol if avg_vol > 0 else 0
        atr_pct = (today["high"] - today["low"]) / today["close"] if today["close"] > 0 else 0
        gap_pct = (today["open"] - prev["close"]) / prev["close"] if prev["close"] > 0 else 0
        direction = "up" if today["close"] > today["open"] else "down"

        if today["date"] in MACRO_DAYS:
            continue
        if rvol >= RVOL_THRESHOLD and atr_pct >= ATR_THRESHOLD:
            signals.append({
                "date": today["date"],
                "bar_idx": i,
                "open": today["open"],
                "high": today["high"],
                "low": today["low"],
                "close": today["close"],
                "volume": today["volume"],
                "avg_vol": avg_vol,
                "rvol": rvol,
                "atr_pct": atr_pct,
                "gap_pct": gap_pct,
                "direction": direction,
            })
    return signals


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
            stock_move = -stock_move  # for put, we want price to go down

        if stock_move >= PROFIT_TARGET_STOCK:
            outcome_pct = OPTION_WIN_PCT
            exit_reason = "profit_target"
            break
        elif stock_move <= STOP_LOSS_STOCK:
            outcome_pct = OPTION_LOSS_PCT
            exit_reason = "stop_loss"
            break

    if outcome_pct is None:
        # exit at day 3 (or last available)
        if hold_days == 0:
            outcome_pct = 0.0
            exit_reason = "no_future_data"
        else:
            future = bars[idx + hold_days]
            stock_move = (future["close"] - entry_close) / entry_close
            if direction == "down":
                stock_move = -stock_move
            outcome_pct = stock_move * OPTION_LEVERAGE
            # cap at reasonable bounds
            outcome_pct = max(-0.95, min(2.0, outcome_pct))

    return outcome_pct, hold_days, exit_reason


def get_month(date_str):
    return date_str[:7]  # "2026-05"


def main():
    print("=" * 70)
    print("MOMENTUM OPTIONS BACKTEST — May–Aug 2026")
    print("=" * 70)

    symbols_data = load_data()

    all_trades = []
    per_symbol = {}

    account = STARTING_BALANCE

    # Collect all signals across symbols
    all_signals = []
    for sym, bars in symbols_data.items():
        sigs = compute_signals(bars)
        for s in sigs:
            s["symbol"] = sym
            s["bars"] = bars
        all_signals.extend(sigs)

    # Sort by date so we process chronologically (affects account balance)
    all_signals.sort(key=lambda x: x["date"])

    print(f"\nTotal signals detected (before account sim): {len(all_signals)}")
    print(f"Starting balance: ${account:.2f}\n")

    for sig in all_signals:
        sym = sig["symbol"]
        bars = sig["bars"]

        # Option premium approximation
        premium = sig["atr_pct"] * sig["close"] * 0.4
        premium = max(MIN_CONTRACT_COST, min(MAX_CONTRACT_COST, premium))

        # Position sizing
        position_cost = account * POSITION_SIZE_PCT  # $ to spend
        contracts = int(position_cost / (premium * SHARES_PER_CONTRACT))
        if contracts < 1:
            contracts = 1
        actual_cost = contracts * premium * SHARES_PER_CONTRACT

        if actual_cost > account:
            # Can't afford even 1 contract
            continue

        outcome_pct, hold_days, exit_reason = simulate_trade(sig, bars)
        pnl = actual_cost * outcome_pct
        account += pnl

        trade = {
            "symbol": sym,
            "date": sig["date"],
            "month": get_month(sig["date"]),
            "direction": sig["direction"],
            "rvol": sig["rvol"],
            "atr_pct": sig["atr_pct"],
            "gap_pct": sig["gap_pct"],
            "premium": premium,
            "contracts": contracts,
            "actual_cost": actual_cost,
            "outcome_pct": outcome_pct,
            "pnl": pnl,
            "hold_days": hold_days,
            "exit_reason": exit_reason,
            "balance_after": account,
            "win": outcome_pct > 0,
        }
        all_trades.append(trade)
        if sym not in per_symbol:
            per_symbol[sym] = []
        per_symbol[sym].append(trade)

    # ── Per-Symbol Summary ──────────────────────────────────────────────────
    print("PER-SYMBOL SUMMARY")
    print("-" * 70)
    print(f"{'Symbol':<8} {'Signals':>7} {'Wins':>5} {'Losses':>7} {'WinRate':>8} {'AvgRet':>8} {'TotalPnL':>10}")
    print("-" * 70)
    for sym in ["SOFI", "MARA", "SOUN", "ACHR", "JOBY", "RIVN"]:
        trades = per_symbol.get(sym, [])
        if not trades:
            print(f"{sym:<8} {'0':>7} {'0':>5} {'0':>7} {'N/A':>8} {'N/A':>8} {'N/A':>10}")
            continue
        wins = sum(1 for t in trades if t["win"])
        losses = len(trades) - wins
        wr = wins / len(trades) * 100
        avg_ret = sum(t["outcome_pct"] for t in trades) / len(trades) * 100
        total_pnl = sum(t["pnl"] for t in trades)
        print(f"{sym:<8} {len(trades):>7} {wins:>5} {losses:>7} {wr:>7.1f}% {avg_ret:>7.1f}% {total_pnl:>+10.2f}")

    # ── Month-by-Month ──────────────────────────────────────────────────────
    print("\nMONTH-BY-MONTH BREAKDOWN")
    print("-" * 70)
    month_labels = {"2026-05": "May 2026", "2026-06": "Jun 2026", "2026-07": "Jul 2026", "2026-08": "Aug 2026"}
    month_trades = defaultdict(list)
    for t in all_trades:
        month_trades[t["month"]].append(t)

    for m in sorted(month_trades.keys()):
        mt = month_trades[m]
        wins = sum(1 for t in mt if t["win"])
        losses = len(mt) - wins
        wr = wins / len(mt) * 100 if mt else 0
        pnl = sum(t["pnl"] for t in mt)
        avg_ret = sum(t["outcome_pct"] for t in mt) / len(mt) * 100 if mt else 0
        label = month_labels.get(m, m)
        print(f"{label}: {len(mt)} trades | {wins}W/{losses}L | WR {wr:.1f}% | Avg {avg_ret:+.1f}% | PnL ${pnl:+.2f}")

    # ── Overall Summary ─────────────────────────────────────────────────────
    print("\nOVERALL SUMMARY")
    print("-" * 70)
    total = len(all_trades)
    if total == 0:
        print("No trades executed.")
        return

    wins = sum(1 for t in all_trades if t["win"])
    losses = total - wins
    wr = wins / total * 100
    avg_ret = sum(t["outcome_pct"] for t in all_trades) / total * 100
    total_pnl = account - STARTING_BALANCE
    total_ret_pct = total_pnl / STARTING_BALANCE * 100

    print(f"Total trades:       {total}")
    print(f"Wins / Losses:      {wins} / {losses}")
    print(f"Win rate:           {wr:.1f}%")
    print(f"Avg return/trade:   {avg_ret:+.2f}%")
    print(f"Starting balance:   ${STARTING_BALANCE:.2f}")
    print(f"Final balance:      ${account:.2f}")
    print(f"Total P&L:          ${total_pnl:+.2f}")
    print(f"Total return:       {total_ret_pct:+.1f}%")

    # Best / worst
    best = max(all_trades, key=lambda t: t["outcome_pct"])
    worst = min(all_trades, key=lambda t: t["outcome_pct"])
    print(f"\nBest trade:  {best['symbol']} on {best['date']} | {best['direction'].upper()} | "
          f"RVOL {best['rvol']:.2f}x | Return {best['outcome_pct']*100:+.1f}% | P&L ${best['pnl']:+.2f}")
    print(f"Worst trade: {worst['symbol']} on {worst['date']} | {worst['direction'].upper()} | "
          f"RVOL {worst['rvol']:.2f}x | Return {worst['outcome_pct']*100:+.1f}% | P&L ${worst['pnl']:+.2f}")

    # ── Trade Log ───────────────────────────────────────────────────────────
    print("\nFULL TRADE LOG")
    print("-" * 105)
    print(f"{'#':>3} {'Sym':<5} {'Date':<12} {'Dir':<5} {'RVOL':>6} {'ATR%':>6} {'Gap%':>6} "
          f"{'Prem':>6} {'Ctrs':>5} {'Cost':>7} {'Ret%':>7} {'PnL':>8} {'Bal':>8} {'Exit':<14}")
    print("-" * 105)
    for i, t in enumerate(all_trades, 1):
        print(f"{i:>3} {t['symbol']:<5} {t['date']:<12} {t['direction'][:2].upper():<5} "
              f"{t['rvol']:>6.2f} {t['atr_pct']*100:>5.1f}% {t['gap_pct']*100:>5.1f}% "
              f"{t['premium']:>6.2f} {t['contracts']:>5} {t['actual_cost']:>7.2f} "
              f"{t['outcome_pct']*100:>+6.1f}% ${t['pnl']:>+7.2f} ${t['balance_after']:>7.2f} {t['exit_reason']:<14}")

    # ── Honest Assessment ───────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("HONEST ASSESSMENT: CAN THIS SYSTEM SUPPORT 10%/MONTH COMPOUNDING?")
    print("=" * 70)

    monthly_pnls = []
    for m in sorted(month_trades.keys()):
        mt = month_trades[m]
        monthly_pnls.append(sum(t["pnl"] for t in mt))

    avg_monthly = sum(monthly_pnls) / len(monthly_pnls) if monthly_pnls else 0
    # Rough monthly return % relative to starting capital
    # (using starting balance as base since account is small)
    avg_monthly_ret = avg_monthly / STARTING_BALANCE * 100

    ten_pct_needed = STARTING_BALANCE * 0.10
    print(f"\n10%/month on $75 = ${ten_pct_needed:.2f}/month needed")
    print(f"Actual avg monthly P&L: ${avg_monthly:+.2f} ({avg_monthly_ret:+.1f}% of starting capital)")
    print(f"Win rate: {wr:.1f}% (need ~55%+ to be consistently profitable with 80%/-35% payoff)")

    # Kelly criterion rough estimate
    p = wr / 100
    b = OPTION_WIN_PCT / abs(OPTION_LOSS_PCT)
    kelly = (p * b - (1 - p)) / b if b > 0 else 0
    print(f"\nKelly fraction (theoretical): {kelly*100:.1f}%")
    print(f"(Positive Kelly = edge exists; negative = system loses money long-term)")

    if wr >= 55 and avg_monthly_ret >= 10:
        verdict = "PROMISING — backtest supports 10%/month target, but real-world slippage and IV crush will reduce returns."
    elif wr >= 45 and avg_monthly_ret >= 5:
        verdict = "MARGINAL — system shows some edge but does NOT reliably hit 10%/month. Needs refinement."
    else:
        verdict = "INSUFFICIENT EDGE — win rate and/or returns too low to support 10%/month compounding at this account size."

    print(f"\nVerdict: {verdict}")
    print("\nKey risks not captured in backtest:")
    print("  1. IV crush: options lose value even when direction is right")
    print("  2. Bid-ask spread: real fills worse than mid-price")
    print("  3. Limited contracts at $0.15-$0.80: max 1 contract on $75 account")
    print("  4. Small account: $75 → need $15 profits/month; 1 contract limits upside")
    print("  5. Survivorship: these 6 symbols were pre-selected; forward picks may differ")
    print("=" * 70)


if __name__ == "__main__":
    main()
