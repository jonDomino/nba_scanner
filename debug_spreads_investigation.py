"""
Debug investigation for why spreads table finds 0 spread markets.

This script will investigate:
1. What markets we actually get from nested_markets
2. Whether spreads exist in Kalshi at all
3. What series tickers exist for NBA
4. Whether spreads need explicit /markets call
5. Whether Unabated spread extraction is working
"""

import sys
from typing import Dict, Any, List

from core.reusable_functions import (
    fetch_kalshi_events,
    fetch_kalshi_markets_for_event
)
from utils.kalshi_api import load_creds
from utils import config

DEBUG_SPREADS = True

def step1_inspect_nested_markets():
    """Step 1: Confirm what markets we are actually getting from Kalshi."""
    print("\n" + "="*80)
    print("STEP 1: Inspecting nested markets from KXNBAGAME series")
    print("="*80)
    
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        print(f"❌ Failed to load credentials: {e}")
        return
    
    # Fetch events with nested markets
    events = fetch_kalshi_events(api_key_id, private_key_pem, "KXNBAGAME")
    
    if not events:
        print("❌ No events found for KXNBAGAME series")
        return
    
    print(f"\nFound {len(events)} event(s) for KXNBAGAME series")
    
    # Inspect first event in detail
    if events:
        event = events[0]
        event_ticker = (
            event.get("event_ticker") or
            event.get("eventTicker") or
            event.get("ticker")
        )
        
        print(f"\n{'='*80}")
        print(f"INSPECTING FIRST EVENT: {event_ticker}")
        print(f"{'='*80}")
        
        print(f"\nEvent keys: {list(event.keys())[:20]}")
        print(f"Event series_ticker: {event.get('series_ticker') or event.get('seriesTicker') or 'N/A'}")
        
        # Get nested markets
        nested_markets = (
            event.get("nested_markets") or
            event.get("markets") or
            event.get("nestedMarkets")
        )
        
        if isinstance(nested_markets, list):
            print(f"\nNested markets count: {len(nested_markets)}")
            
            for i, market in enumerate(nested_markets[:5]):  # First 5 markets
                print(f"\n--- Market {i+1} ---")
                if isinstance(market, dict):
                    print(f"  Keys: {list(market.keys())[:15]}")
                    print(f"  ticker: {market.get('ticker') or market.get('market_ticker')}")
                    print(f"  title: {market.get('title') or market.get('market_title')}")
                    print(f"  subtitle: {market.get('subtitle') or market.get('sub_title')}")
                    print(f"  market_type: {market.get('market_type') or market.get('marketType')}")
                    print(f"  type: {market.get('type')}")
                    print(f"  category: {market.get('category')}")
                    print(f"  strike: {market.get('strike')}")
                    print(f"  rules_primary: {market.get('rules_primary') or market.get('rulesPrimary')}")
                    
                    # Check for spread indicators
                    title_lower = (market.get('title') or '').lower()
                    if 'spread' in title_lower or 'wins by' in title_lower or 'points' in title_lower:
                        print(f"  ⚠️ SPREAD INDICATOR FOUND in title!")
        else:
            print(f"Nested markets type: {type(nested_markets)}")
            print(f"Nested markets value: {nested_markets}")


def step2_search_all_markets():
    """Step 2: Validate whether spreads exist in Kalshi at all."""
    print("\n" + "="*80)
    print("STEP 2: Searching all markets for spread indicators")
    print("="*80)
    
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        print(f"❌ Failed to load credentials: {e}")
        return
    
    # Try to fetch markets without event filter (if API supports it)
    from utils.kalshi_api import make_request
    
    try:
        # Try /markets endpoint without event_ticker
        path = "/markets"
        params = {
            "status": "open",
            "limit": 100  # Limit to avoid huge response
        }
        
        print("\nAttempting to fetch markets without event filter...")
        resp = make_request(api_key_id, private_key_pem, "GET", path, body=params)
        markets = resp.get("markets", [])
        
        print(f"Found {len(markets)} total open markets (first 100)")
        
        # Filter for spread-like markets
        spread_markets = []
        for market in markets:
            if not isinstance(market, dict):
                continue
            
            title = (market.get("title") or market.get("market_title") or "").lower()
            ticker = (market.get("ticker") or market.get("market_ticker") or "").lower()
            
            if any(indicator in title for indicator in ["wins by over", "wins by", "points", "spread"]):
                spread_markets.append(market)
            elif any(indicator in ticker for indicator in ["spread", "pts", "point"]):
                spread_markets.append(market)
        
        print(f"\nFound {len(spread_markets)} market(s) with spread indicators:")
        
        for i, market in enumerate(spread_markets[:10]):  # First 10
            print(f"\n  Spread Market {i+1}:")
            print(f"    ticker: {market.get('ticker') or market.get('market_ticker')}")
            print(f"    title: {market.get('title') or market.get('market_title')}")
            print(f"    event_ticker: {market.get('event_ticker') or market.get('eventTicker')}")
            print(f"    series_ticker: {market.get('series_ticker') or market.get('seriesTicker')}")
            print(f"    category: {market.get('category')}")
            print(f"    market_type: {market.get('market_type') or market.get('marketType')}")
            
    except Exception as e:
        print(f"⚠️ Could not fetch markets without filter: {e}")
        print("  (This endpoint may require event_ticker parameter)")


def step3_enumerate_series():
    """Step 3: Enumerate all Kalshi basketball series tickers."""
    print("\n" + "="*80)
    print("STEP 3: Enumerating NBA-related series tickers")
    print("="*80)
    
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        print(f"❌ Failed to load credentials: {e}")
        return
    
    from utils.kalshi_api import make_request
    
    # Try /series endpoint if it exists
    try:
        path = "/series"
        params = {"status": "open"}
        print("\nAttempting to fetch series list...")
        resp = make_request(api_key_id, private_key_pem, "GET", path, body=params)
        series_list = resp.get("series", []) or resp.get("data", [])
        
        print(f"Found {len(series_list)} series")
        
        # Filter for NBA-related
        nba_series = []
        for series in series_list:
            if not isinstance(series, dict):
                continue
            
            ticker = (series.get("ticker") or series.get("series_ticker") or "").upper()
            title = (series.get("title") or series.get("name") or "").lower()
            category = (series.get("category") or "").lower()
            
            if "NBA" in ticker or "nba" in title or "basketball" in category:
                nba_series.append(series)
        
        print(f"\nFound {len(nba_series)} NBA-related series:")
        for series in nba_series:
            ticker = series.get('ticker') or series.get('series_ticker') or 'N/A'
            title = (series.get('title') or series.get('name') or 'N/A').encode('ascii', 'ignore').decode('ascii')
            print(f"  - {ticker}: {title}")
            
        # Highlight spread series
        spread_series = [s for s in nba_series if 'SPREAD' in (s.get('ticker') or s.get('series_ticker') or '').upper()]
        if spread_series:
            print(f"\n*** FOUND SPREAD SERIES: {spread_series[0].get('ticker')} - {spread_series[0].get('title')} ***")
            
    except Exception as e:
        print(f"WARNING: /series endpoint not available or failed: {e}")
        print("  Trying alternative: fetch events and extract unique series_tickers")
        
        # Alternative: fetch events and collect series tickers
        try:
            path = "/events"
            params = {"status": "open", "with_nested_markets": "true"}
            resp = make_request(api_key_id, private_key_pem, "GET", path, body=params)
            events = resp.get("events", [])
            
            series_tickers = set()
            for event in events:
                series_ticker = (
                    event.get("series_ticker") or
                    event.get("seriesTicker") or
                    event.get("series")
                )
                if series_ticker:
                    series_tickers.add(series_ticker)
            
            print(f"\nFound {len(series_tickers)} unique series_ticker(s) from events:")
            nba_series_tickers = [s for s in series_tickers if "NBA" in s.upper() or "BASKETBALL" in s.upper()]
            print(f"NBA-related: {nba_series_tickers}")
            print(f"All series: {sorted(series_tickers)}")
            
        except Exception as e2:
            print(f"❌ Alternative also failed: {e2}")


def step4_compare_nested_vs_explicit():
    """Step 4: Compare nested markets vs explicit /markets call AND test KXNBASPREAD series."""
    print("\n" + "="*80)
    print("STEP 4: Comparing nested markets vs explicit /markets call + Testing KXNBASPREAD")
    print("="*80)
    
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        print(f"Failed to load credentials: {e}")
        return
    
    # Test 4A: Compare nested vs explicit for KXNBAGAME
    print("\n" + "-"*80)
    print("4A: Testing KXNBAGAME event (nested vs explicit)")
    print("-"*80)
    
    events = fetch_kalshi_events(api_key_id, private_key_pem, "KXNBAGAME")
    
    if events:
        event = events[0]
        event_ticker = (
            event.get("event_ticker") or
            event.get("eventTicker") or
            event.get("ticker")
        )
        
        print(f"\nUsing event: {event_ticker}")
        
        # Count nested markets
        nested_markets = (
            event.get("nested_markets") or
            event.get("markets") or
            event.get("nestedMarkets")
        )
        
        nested_count = len(nested_markets) if isinstance(nested_markets, list) else 0
        print(f"Nested markets count: {nested_count}")
        
        # Count explicit /markets call
        explicit_markets = fetch_kalshi_markets_for_event(api_key_id, private_key_pem, event_ticker)
        explicit_count = len(explicit_markets) if isinstance(explicit_markets, list) else 0
        print(f"Explicit /markets count: {explicit_count}")
        
        if explicit_count > nested_count:
            print(f"\nWARNING: Explicit /markets returns {explicit_count - nested_count} MORE markets!")
            print("  Spreads likely need explicit /markets call, not nested_markets")
            
            # Show markets in explicit but not in nested (by ticker)
            nested_tickers = set()
            if isinstance(nested_markets, list):
                for m in nested_markets:
                    ticker = m.get("ticker") or m.get("market_ticker")
                    if ticker:
                        nested_tickers.add(ticker)
            
            print(f"\nMarkets in explicit call but NOT in nested:")
            for market in explicit_markets:
                if not isinstance(market, dict):
                    continue
                ticker = market.get("ticker") or market.get("market_ticker")
                if ticker not in nested_tickers:
                    title = market.get("title") or market.get("market_title") or ""
                    print(f"  - {ticker}: {title}")
                    
                    # Check if it's a spread
                    title_lower = title.lower()
                    if any(indicator in title_lower for indicator in ["wins by over", "wins by", "points", "spread"]):
                        print(f"    *** THIS IS A SPREAD MARKET! ***")
    
    # Test 4B: Try KXNBASPREAD series
    print("\n" + "-"*80)
    print("4B: Testing KXNBASPREAD series")
    print("-"*80)
    
    try:
        spread_events = fetch_kalshi_events(api_key_id, private_key_pem, "KXNBASPREAD")
        print(f"\nFound {len(spread_events)} event(s) for KXNBASPREAD series")
        
        if spread_events:
            spread_event = spread_events[0]
            spread_event_ticker = (
                spread_event.get("event_ticker") or
                spread_event.get("eventTicker") or
                spread_event.get("ticker")
            )
            
            print(f"\nFirst KXNBASPREAD event: {spread_event_ticker}")
            
            # Check nested markets
            nested_spread = (
                spread_event.get("nested_markets") or
                spread_event.get("markets") or
                spread_event.get("nestedMarkets")
            )
            nested_spread_count = len(nested_spread) if isinstance(nested_spread, list) else 0
            print(f"Nested markets count: {nested_spread_count}")
            
            # Get explicit markets
            explicit_spread_markets = fetch_kalshi_markets_for_event(api_key_id, private_key_pem, spread_event_ticker)
            explicit_spread_count = len(explicit_spread_markets) if isinstance(explicit_spread_markets, list) else 0
            print(f"Explicit /markets count: {explicit_spread_count}")
            
            # Show first few markets
            if explicit_spread_markets:
                print(f"\nFirst 5 spread markets:")
                for i, market in enumerate(explicit_spread_markets[:5]):
                    if isinstance(market, dict):
                        ticker = market.get("ticker") or market.get("market_ticker")
                        title = market.get("title") or market.get("market_title") or ""
                        print(f"  {i+1}. {ticker}")
                        print(f"     {title}")
    except Exception as e:
        print(f"WARNING: Could not fetch KXNBASPREAD events: {e}")


def step5_inspect_unabated_spreads():
    """Step 5: Confirm Unabated spread extraction is correct."""
    print("\n" + "="*80)
    print("STEP 5: Inspecting Unabated spread extraction")
    print("="*80)
    
    from core.reusable_functions import fetch_unabated_snapshot
    from nba_todays_fairs import extract_nba_games_today
    
    snapshot = fetch_unabated_snapshot()
    teams = snapshot.get("teams", {})
    today_games = extract_nba_games_today(snapshot)
    
    if not today_games:
        print("❌ No today games found")
        return
    
    print(f"\nFound {len(today_games)} today game(s)")
    
    # Inspect first game
    if today_games:
        game = today_games[0]
        event_start = game.get("eventStart")
        event_teams = game.get("eventTeams", {})
        
        print(f"\n{'='*80}")
        print(f"INSPECTING FIRST GAME: {event_start}")
        print(f"{'='*80}")
        
        market_lines = game.get("gameOddsMarketSourcesLines", {})
        
        print(f"\nMarket lines keys (showing first 30):")
        keys = list(market_lines.keys())[:30]
        for key in keys:
            print(f"  {key}")
        
        # Find all ms49 keys
        ms49_keys = [k for k in market_lines.keys() if ":ms49:" in k]
        print(f"\nFound {len(ms49_keys)} ms49 key(s):")
        for key in ms49_keys[:10]:
            print(f"  {key}")
        
        # Inspect first ms49 block
        if ms49_keys:
            first_ms49_key = ms49_keys[0]
            ms49_block = market_lines[first_ms49_key]
            
            print(f"\n{'='*80}")
            print(f"INSPECTING MS49 BLOCK: {first_ms49_key}")
            print(f"{'='*80}")
            
            if isinstance(ms49_block, dict):
                print(f"MS49 block keys: {list(ms49_block.keys())}")
                
                # Check for bt1 (moneyline) and bt2 (spread)
                bt1 = ms49_block.get("bt1")
                bt2 = ms49_block.get("bt2")
                bt3 = ms49_block.get("bt3")
                
                print(f"\nbt1 (moneyline): {bt1 is not None}")
                if bt1:
                    print(f"  bt1 keys: {list(bt1.keys()) if isinstance(bt1, dict) else type(bt1)}")
                
                print(f"\nbt2 (spread?): {bt2 is not None}")
                if bt2:
                    print(f"  bt2 keys: {list(bt2.keys()) if isinstance(bt2, dict) else type(bt2)}")
                    print(f"  bt2 value: {bt2}")
                
                print(f"\nbt3: {bt3 is not None}")
                if bt3:
                    print(f"  bt3 keys: {list(bt3.keys()) if isinstance(bt3, dict) else type(bt3)}")
        
        # Try to extract spreads
        from nba_spreads_dashboard import extract_unabated_spreads
        spreads = extract_unabated_spreads(game, teams)
        
        print(f"\n{'='*80}")
        print(f"EXTRACTED SPREADS:")
        print(f"{'='*80}")
        print(f"Spreads by team_id: {spreads}")
        
        # Show team info
        print(f"\nEvent teams:")
        for idx, team_info in event_teams.items():
            if isinstance(team_info, dict):
                team_id = team_info.get("id")
                team_name = team_info.get("name")
                spread = spreads.get(team_id) if team_id else None
                print(f"  Team {idx}: id={team_id}, name={team_name}, spread={spread}")


def main():
    """Run all investigation steps."""
    print("\n" + "="*80)
    print("SPREADS DISCOVERY INVESTIGATION")
    print("="*80)
    
    step1_inspect_nested_markets()
    step2_search_all_markets()
    step3_enumerate_series()
    step4_compare_nested_vs_explicit()
    step5_inspect_unabated_spreads()
    
    print("\n" + "="*80)
    print("INVESTIGATION COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()
