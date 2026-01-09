"""
Fetch all NBA Kalshi market tickers.
"""

from typing import List, Set

from core.reusable_functions import fetch_kalshi_events, fetch_kalshi_markets_for_event
from utils.kalshi_api import load_creds

# NBA series ticker on Kalshi
NBA_SERIES_TICKER = "KXNBAGAME"


def get_all_nba_kalshi_tickers() -> List[str]:
    """
    Fetch all NBA Kalshi market tickers.
    
    Returns:
        List of unique market ticker strings
    """
    # Load Kalshi credentials
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        print(f"Error loading Kalshi credentials: {e}")
        return []
    
    # Fetch all NBA events (paginated)
    print(f"Fetching NBA events from series {NBA_SERIES_TICKER}...")
    events = fetch_kalshi_events(api_key_id, private_key_pem, NBA_SERIES_TICKER)
    
    if not events:
        print("No NBA events found")
        return []
    
    print(f"Found {len(events)} NBA event(s)")
    
    # Debug: Print first event structure
    if events:
        first_event = events[0]
        print(f"\nFirst event keys: {list(first_event.keys())}")
        event_ticker = first_event.get("event_ticker") or first_event.get("eventTicker") or first_event.get("ticker")
        print(f"First event ticker field: {event_ticker}")
        
        # Check for nested markets
        nested_markets = first_event.get("nested_markets") or first_event.get("markets") or first_event.get("nestedMarkets")
        if nested_markets:
            print(f"Nested markets found: {len(nested_markets) if isinstance(nested_markets, list) else 'yes'}")
        else:
            print("No nested markets found")
    
    print(f"\nCollecting market tickers...\n")
    
    all_tickers: Set[str] = set()
    
    # Fetch markets for each event
    for event in events:
        # Get event ticker (try multiple field names)
        event_ticker = (
            event.get("event_ticker") or
            event.get("eventTicker") or
            event.get("ticker")
        )
        
        if not event_ticker:
            continue
        
        # First try nested markets (if available from with_nested_markets=true)
        markets = (
            event.get("nested_markets") or
            event.get("markets") or
            event.get("nestedMarkets")
        )
        
        # If no nested markets, fetch via API
        if not markets:
            markets = fetch_kalshi_markets_for_event(api_key_id, private_key_pem, event_ticker)
        
        if not isinstance(markets, list):
            continue
        
        # Extract tickers from markets
        for market in markets:
            if not isinstance(market, dict):
                continue
            ticker = market.get("ticker") or market.get("market_ticker") or market.get("marketTicker")
            if ticker:
                all_tickers.add(ticker)
    
    return sorted(all_tickers)


def main():
    """Main entry point."""
    tickers = get_all_nba_kalshi_tickers()
    
    if not tickers:
        print("No NBA Kalshi tickers found")
        return
    
    print(f"Found {len(tickers)} unique NBA Kalshi market ticker(s):\n")
    
    # Print tickers in sorted order
    for ticker in tickers:
        print(ticker)


if __name__ == "__main__":
    main()
