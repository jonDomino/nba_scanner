"""Quick test of KXNBASPREAD series"""

from core.reusable_functions import fetch_kalshi_events, fetch_kalshi_markets_for_event
from utils.kalshi_api import load_creds

api_key_id, private_key_pem = load_creds()

print("Testing KXNBASPREAD series...")
events = fetch_kalshi_events(api_key_id, private_key_pem, "KXNBASPREAD")
print(f"Found {len(events)} events")

if events:
    event = events[0]
    event_ticker = event.get("event_ticker") or event.get("eventTicker") or event.get("ticker")
    print(f"\nFirst event: {event_ticker}")
    
    markets = fetch_kalshi_markets_for_event(api_key_id, private_key_pem, event_ticker)
    print(f"Markets count: {len(markets)}")
    
    print("\nFirst 5 markets:")
    for i, m in enumerate(markets[:5]):
        ticker = m.get("ticker") or m.get("market_ticker")
        title = m.get("title") or m.get("market_title")
        print(f"  {i+1}. {ticker}: {title}")
