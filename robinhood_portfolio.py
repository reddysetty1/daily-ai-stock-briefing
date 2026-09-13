"""
robinhood_portfolio.py — Pull your Robinhood portfolio and print a full breakdown.

Shows for each position:
  - Company name & ticker
  - Shares owned
  - Average buy price (what you paid)
  - Total invested
  - Current price (live)
  - Current value
  - Gain / Loss in $ and %

Run locally only — never commit credentials to git.
"""

import os
import sys
import getpass

# Add the project's site-packages explicitly — handles broken Python installs
import site
site.addsitedir(r"D:\Personal\My projects\Stock Analysis\Lib\site-packages")

try:
    import robin_stocks.robinhood as rh
except ImportError:
    print("robin_stocks not installed. Run:")
    print("  C:\\Python314\\python.exe -m pip install robin_stocks")
    sys.exit(1)


def login():
    """Login to Robinhood interactively."""
    print("\n🔐 Robinhood Login")
    print("   (credentials are used locally only — never stored or sent anywhere)\n")
    username = input("   Email: ").strip()
    password = getpass.getpass("   Password: ")
    print()

    rh.login(username, password, expiresIn=86400)

    # Verify login actually worked — robin_stocks can silently fail
    try:
        test = rh.account.load_account_profile()
        if not test:
            raise Exception("empty profile — login silently failed")
        print("✅ Logged in successfully.\n")
    except Exception as e:
        print(f"\n❌ Login failed or session invalid: {e}")
        print("\nThis is likely a Robinhood API change. Try exporting your")
        print("portfolio as CSV from the Robinhood app instead:")
        print("  App → Account → Statements & History → Export")
        sys.exit(1)


def fetch_portfolio():
    """Fetch all open positions from Robinhood."""
    try:
        positions = rh.account.get_open_stock_positions()
    except Exception as e:
        print(f"❌ Could not fetch positions: {e}")
        sys.exit(1)

    if not positions:
        print("No open positions found.")
        return []

    rows = []
    print(f"Fetching data for {len(positions)} position(s)...\n")

    for pos in positions:
        try:
            shares      = float(pos["quantity"])
            avg_price   = float(pos["average_buy_price"])
            instrument  = pos.get("instrument", "")

            # Get ticker and name from instrument URL
            inst_data   = rh.stocks.get_instrument_by_url(instrument)
            ticker      = inst_data.get("symbol", "???")
            name        = inst_data.get("simple_name") or inst_data.get("name", ticker)

            # Current price (live quote)
            quote       = rh.stocks.get_latest_price(ticker)
            cur_price   = float(quote[0]) if quote else 0.0

            invested    = round(shares * avg_price, 2)
            cur_value   = round(shares * cur_price, 2)
            gain_loss   = round(cur_value - invested, 2)
            gain_pct    = round((gain_loss / invested) * 100, 2) if invested else 0.0

            rows.append({
                "ticker":    ticker,
                "name":      name,
                "shares":    shares,
                "avg_price": avg_price,
                "invested":  invested,
                "cur_price": cur_price,
                "cur_value": cur_value,
                "gain_loss": gain_loss,
                "gain_pct":  gain_pct,
            })

            arrow = "▲" if gain_loss >= 0 else "▼"
            print(f"  ✓ {ticker:<6}  {arrow} ${cur_price:.2f}  ({gain_pct:+.1f}%)")

        except Exception as e:
            print(f"  ⚠ Skipped one position: {e}")
            continue

    return rows


def print_portfolio(rows: list):
    if not rows:
        return

    # Sort by current value descending
    rows.sort(key=lambda x: -x["cur_value"])

    total_invested = sum(r["invested"]  for r in rows)
    total_value    = sum(r["cur_value"] for r in rows)
    total_gl       = total_value - total_invested
    total_pct      = (total_gl / total_invested * 100) if total_invested else 0

    print("\n" + "━" * 80)
    print(f"  📊  ROBINHOOD PORTFOLIO SNAPSHOT")
    print("━" * 80)
    print(f"  {'TICKER':<7} {'COMPANY':<28} {'SHARES':>7} {'AVG PRICE':>10} {'INVESTED':>10} {'CUR PRICE':>10} {'CUR VALUE':>10} {'GAIN/LOSS':>12}")
    print("  " + "-" * 78)

    for r in rows:
        arrow  = "▲" if r["gain_loss"] >= 0 else "▼"
        gl_str = f"{arrow} ${abs(r['gain_loss']):.2f} ({r['gain_pct']:+.1f}%)"
        print(
            f"  {r['ticker']:<7} "
            f"{r['name'][:27]:<28} "
            f"{r['shares']:>7.4f} "
            f"${r['avg_price']:>9.2f} "
            f"${r['invested']:>9.2f} "
            f"${r['cur_price']:>9.2f} "
            f"${r['cur_value']:>9.2f} "
            f"  {gl_str}"
        )

    print("  " + "-" * 78)
    total_arrow = "▲" if total_gl >= 0 else "▼"
    print(f"  {'TOTAL':<7} {'':<28} {'':<7} {'':<10} ${total_invested:>9.2f} {'':<10} ${total_value:>9.2f}  {total_arrow} ${abs(total_gl):.2f} ({total_pct:+.1f}%)")
    print("━" * 80)

    # Winners and losers
    winners = [r for r in rows if r["gain_loss"] > 0]
    losers  = [r for r in rows if r["gain_loss"] < 0]
    print(f"\n  Winners: {len(winners)}  |  Losers: {len(losers)}  |  Positions: {len(rows)}")

    if winners:
        best = max(winners, key=lambda x: x["gain_pct"])
        print(f"  🏆 Best performer:  {best['ticker']} ({best['gain_pct']:+.1f}%)")
    if losers:
        worst = min(losers, key=lambda x: x["gain_pct"])
        print(f"  📉 Worst performer: {worst['ticker']} ({worst['gain_pct']:+.1f}%)")

    print()


def main():
    login()
    rows = fetch_portfolio()
    print_portfolio(rows)
    rh.logout()


if __name__ == "__main__":
    main()
