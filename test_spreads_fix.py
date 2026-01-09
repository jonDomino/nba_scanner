"""Test the fixed spreads discovery"""

from nba_spreads_dashboard import discover_kalshi_spread_markets

DEBUG_SPREADS = True

# Test with a known event ticker
event_ticker = "KXNBAGAME-26JAN09MILLAL"
print(f"Testing spreads discovery for: {event_ticker}\n")

markets = discover_kalshi_spread_markets(event_ticker)
print(f"\nFound {len(markets)} spread market(s)")

if markets:
    print("\nFirst 5 markets:")
    for i, m in enumerate(markets[:5]):
        ticker = m.get("ticker", "N/A")
        title = m.get("title", "N/A")
        strike = m.get("parsed_strike", "N/A")
        print(f"  {i+1}. {ticker}")
        print(f"     Title: {title}")
        print(f"     Strike: {strike}")
        print()
