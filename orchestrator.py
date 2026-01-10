"""
Main orchestrator: coordinates data fetching and table building.
This is the root entry point that replaces nba_value_table.py main().
"""

from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
import time

# Import data fetching modules
from data_build.slate import get_today_games_with_fairs_and_kalshi_tickers
from data_build.top_of_book import get_top_of_book_post_probs

# Import table builders
from spreads.builder import build_spreads_rows_for_today
from totals.builder import build_totals_rows_for_today

# Import dashboard renderer
from dashboard_html import render_dashboard_html, open_dashboard_in_browser


def derive_event_ticker(market_ticker: str) -> Optional[str]:
    """
    Derive event ticker from market ticker by removing final -TEAM suffix.
    
    Example: KXNBAGAME-26JAN09TORBOS-TOR -> KXNBAGAME-26JAN09TORBOS
    
    Args:
        market_ticker: Market ticker string (e.g., "KXNBAGAME-26JAN09TORBOS-TOR")
    
    Returns:
        Event ticker string or None if parsing fails
    """
    if not market_ticker:
        return None
    parts = market_ticker.split("-")
    if len(parts) >= 3:
        return "-".join(parts[:-1])
    return None


def build_moneylines_rows(games: List[Dict[str, Any]], debug: bool = False) -> List[Dict[str, Any]]:
    """
    Build moneylines table rows from games and fetched orderbook data.
    
    Args:
        games: Pre-fetched games list
        debug: If True, print debug information
    
    Returns:
        List of moneyline row dicts
    """
    # Get top-of-book maker break-even probs for each event
    event_probs = {}  # event_ticker -> prob dict
    
    # Collect unique event tickers
    event_tickers = set()
    game_to_event = {}  # Map game index to event ticker
    
    for i, game in enumerate(games):
        # Derive event ticker from market tickers
        away_ticker = game.get("away_kalshi_ticker")
        home_ticker = game.get("home_kalshi_ticker")
        
        event_ticker = None
        if away_ticker:
            event_ticker = derive_event_ticker(away_ticker)
        elif home_ticker:
            event_ticker = derive_event_ticker(home_ticker)
        
        if event_ticker:
            event_tickers.add(event_ticker)
            game_to_event[i] = event_ticker
    
    # Fetch orderbook data for each event in parallel
    if debug:
        print(f"Fetching orderbook data for {len(event_tickers)} event(s) in parallel...")
    
    # Parallelize fetching orderbooks for multiple events
    with ThreadPoolExecutor(max_workers=min(len(event_tickers), 10)) as executor:
        future_to_ticker = {
            executor.submit(get_top_of_book_post_probs, event_ticker): event_ticker
            for event_ticker in event_tickers
        }
        
        for future in future_to_ticker:
            event_ticker = future_to_ticker[future]
            try:
                prob_result = future.result()
                event_probs[event_ticker] = prob_result
            except Exception as e:
                if debug:
                    print(f"⚠️ Error fetching {event_ticker}: {e}")
                # Store error result
                event_probs[event_ticker] = {"error": str(e)}
    
    # Build moneylines table rows
    moneyline_rows = []
    
    for i, game in enumerate(games):
        event_ticker = game_to_event.get(i)
        prob_data = event_probs.get(event_ticker) if event_ticker else None
        
        # Get YES break-even probs and YES bid liquidity
        yes_be_top_away = prob_data.get("yes_be_top_away") if prob_data else None
        yes_be_topm1_away = prob_data.get("yes_be_topm1_away") if prob_data else None
        yes_be_top_home = prob_data.get("yes_be_top_home") if prob_data else None
        yes_be_topm1_home = prob_data.get("yes_be_topm1_home") if prob_data else None
        
        # Internal: YES bid liquidity (from orderbook["yes"] bids, maker prices)
        yes_bid_top_liq_away = prob_data.get("yes_bid_top_liq_away") if prob_data else None
        yes_bid_top_p1_liq_away = prob_data.get("yes_bid_top_p1_liq_away") if prob_data else None
        yes_bid_top_liq_home = prob_data.get("yes_bid_top_liq_home") if prob_data else None
        yes_bid_top_p1_liq_home = prob_data.get("yes_bid_top_p1_liq_home") if prob_data else None
        
        # YES bid prices in cents (needed for dollar liquidity calculation)
        yes_bid_top_c_away = prob_data.get("yes_bid_top_c_away") if prob_data else None
        yes_bid_top_c_home = prob_data.get("yes_bid_top_c_home") if prob_data else None
        
        # Compute EVs (buyer/YES exposure perspective)
        away_fair = game.get("away_fair")
        home_fair = game.get("home_fair")
        
        away_ev_top = (away_fair - yes_be_top_away) * 100.0 if (away_fair is not None and yes_be_top_away is not None) else None
        away_ev_topm1 = (away_fair - yes_be_topm1_away) * 100.0 if (away_fair is not None and yes_be_topm1_away is not None) else None
        home_ev_top = (home_fair - yes_be_top_home) * 100.0 if (home_fair is not None and yes_be_top_home is not None) else None
        home_ev_topm1 = (home_fair - yes_be_topm1_home) * 100.0 if (home_fair is not None and yes_be_topm1_home is not None) else None
        
        moneyline_rows.append({
            "game_date": game.get("game_date", "N/A"),
            "event_start": game.get("event_start"),
            "away_roto": game.get("away_roto"),
            "away_team": game.get("away_team_name", "N/A"),
            "home_team": game.get("home_team_name", "N/A"),
            "away_fair": away_fair,
            "home_fair": home_fair,
            "event_ticker": event_ticker or "N/A",
            "away_ticker": game.get("away_kalshi_ticker") or "N/A",
            "home_ticker": game.get("home_kalshi_ticker") or "N/A",
            "away_top_prob": yes_be_top_away,
            "away_topm1_prob": yes_be_topm1_away,
            "home_top_prob": yes_be_top_home,
            "home_topm1_prob": yes_be_topm1_home,
            "away_top_liq": yes_bid_top_liq_away,
            "away_topm1_liq": yes_bid_top_p1_liq_away,
            "home_top_liq": yes_bid_top_liq_home,
            "home_topm1_liq": yes_bid_top_p1_liq_home,
            "away_top_price_cents": yes_bid_top_c_away,
            "home_top_price_cents": yes_bid_top_c_home,
            "away_ev_top": away_ev_top,
            "away_ev_topm1": away_ev_topm1,
            "home_ev_top": home_ev_top,
            "home_ev_topm1": home_ev_topm1,
        })
    
    # Sort by ROTO ascending
    moneyline_rows.sort(key=lambda x: (x.get('away_roto') is None, x.get('away_roto') or 0))
    
    return moneyline_rows


def build_all_rows(debug: bool = False, use_parallel: bool = True) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Build all table rows (moneylines, spreads, totals) from fetched data.
    
    Uses parallel execution to build spreads and totals tables simultaneously,
    while sharing the pre-fetched games list and Unabated snapshot.
    
    Pure function - no side effects, returns data only.
    Suitable for use in Streamlit or other frameworks.
    
    Args:
        debug: If True, print debug information
        use_parallel: If True, build spreads and totals in parallel (default: True)
    
    Returns:
        Tuple of (moneyline_rows, spread_rows, totals_rows)
    """
    start_time = time.time()
    
    if debug:
        print("📊 Fetching shared data (games + snapshot)...")
    
    # Step 1: Fetch shared data once (used by all builders)
    games = get_today_games_with_fairs_and_kalshi_tickers()
    
    if not games:
        if debug:
            print("No NBA games found for today")
        return [], [], []
    
    if debug:
        print(f"Found {len(games)} game(s)")
    
    # Fetch Unabated snapshot once (used by spreads and totals)
    from core.reusable_functions import fetch_unabated_snapshot
    snapshot = fetch_unabated_snapshot()
    
    if debug:
        shared_fetch_time = time.time() - start_time
        print(f"✓ Shared data fetched in {shared_fetch_time:.2f}s")
    
    # Step 2: Build moneylines (sequential, as it needs to complete before spreads/totals can use shared data)
    if debug:
        print("📊 Building moneylines table...")
    
    moneyline_start = time.time()
    moneyline_rows = build_moneylines_rows(games, debug=debug)
    
    if debug:
        moneyline_time = time.time() - moneyline_start
        print(f"✓ Moneylines built in {moneyline_time:.2f}s ({len(moneyline_rows)} row(s))")
    
    # Step 3: Build spreads and totals in parallel (they use different Kalshi market types)
    if use_parallel and debug:
        print("🚀 Building spreads and totals tables in parallel...")
    
    parallel_start = time.time()
    
    if use_parallel:
        # Use ThreadPoolExecutor to run spreads and totals builders in parallel
        with ThreadPoolExecutor(max_workers=2) as executor:
            # Submit both tasks
            spreads_future = executor.submit(build_spreads_rows_for_today, games, snapshot)
            totals_future = executor.submit(build_totals_rows_for_today, games, snapshot)
            
            # Wait for both to complete and handle errors
            spread_rows = []
            totals_rows = []
            
            try:
                spread_rows = spreads_future.result()
                if debug:
                    print(f"✓ Spreads built ({len(spread_rows)} row(s))")
            except Exception as e:
                if debug:
                    print(f"⚠️ Spreads table unavailable ({e})")
            
            try:
                totals_rows = totals_future.result()
                if debug:
                    print(f"✓ Totals built ({len(totals_rows)} row(s))")
            except Exception as e:
                if debug:
                    print(f"⚠️ Totals table unavailable ({e})")
    else:
        # Sequential fallback (for debugging or compatibility)
        spread_rows = []
        try:
            spread_rows = build_spreads_rows_for_today(games, snapshot)
            if debug:
                print(f"Found {len(spread_rows)} spread row(s)")
        except Exception as e:
            if debug:
                print(f"\nNote: Spreads table unavailable ({e})\n")
        
        totals_rows = []
        try:
            totals_rows = build_totals_rows_for_today(games, snapshot)
            if debug:
                print(f"Found {len(totals_rows)} totals row(s)")
        except Exception as e:
            if debug:
                print(f"\nNote: Totals table unavailable ({e})\n")
    
    if debug:
        parallel_time = time.time() - parallel_start
        total_time = time.time() - start_time
        print(f"✓ Parallel builds completed in {parallel_time:.2f}s")
        print(f"✓ Total time: {total_time:.2f}s")
    
    return moneyline_rows, spread_rows, totals_rows


def build_dashboard_html_all(
    moneyline_rows: List[Dict[str, Any]],
    spread_rows: Optional[List[Dict[str, Any]]] = None,
    totals_rows: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Build HTML dashboard string from all table rows.
    
    Pure function - no side effects, returns HTML string only.
    
    Args:
        moneyline_rows: List of moneyline row dicts
        spread_rows: Optional list of spread row dicts
        totals_rows: Optional list of totals row dicts
    
    Returns:
        HTML content as string
    """
    return render_dashboard_html(moneyline_rows, spread_rows, totals_rows)


def main():
    """Main entry point - orchestrates data fetching and table building."""
    moneyline_rows, spread_rows, totals_rows = build_all_rows(debug=True)
    
    if not moneyline_rows:
        print("No NBA games found for today")
        return
    
    # Render and open dashboard
    open_dashboard_in_browser(
        moneyline_rows,
        spread_rows if spread_rows else None,
        totals_rows if totals_rows else None
    )
    
    # Also print console version
    from moneylines.table import print_dashboard
    print_dashboard(moneyline_rows)
    
    # Print spreads table if available
    if spread_rows:
        from spreads.builder import print_spreads_table
        print_spreads_table(spread_rows)
    
    # Print totals table if available
    if totals_rows:
        from totals.builder import print_totals_table
        print_totals_table(totals_rows)


if __name__ == "__main__":
    main()