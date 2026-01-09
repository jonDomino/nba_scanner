"""
Main orchestrator: coordinates data fetching and table building.
This is the root entry point that replaces nba_value_table.py main().
"""

from typing import Dict, Any, List, Optional

# Import data fetching modules
from data_build.slate import get_today_games_with_fairs_and_kalshi_tickers
from data_build.top_of_book import get_top_of_book_post_probs

# Import table builders
from spreads.builder import build_spreads_rows_for_today
from totals.builder import build_totals_rows_for_today

# Import dashboard renderer
from dashboard_html import render_dashboard, open_dashboard_in_browser


def main():
    """Main entry point - orchestrates data fetching and table building."""
    # Step 1: Get today's games with fairs and Kalshi tickers
    games = get_today_games_with_fairs_and_kalshi_tickers()
    
    if not games:
        print("No NBA games found for today")
        return
    
    # Step 2: Get top-of-book maker break-even probs for each event
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
            # Remove final -TEAM suffix
            parts = away_ticker.split("-")
            if len(parts) >= 3:
                event_ticker = "-".join(parts[:-1])
        elif home_ticker:
            parts = home_ticker.split("-")
            if len(parts) >= 3:
                event_ticker = "-".join(parts[:-1])
        
        if event_ticker:
            event_tickers.add(event_ticker)
            game_to_event[i] = event_ticker
    
    # Fetch orderbook data for each event
    for event_ticker in event_tickers:
        prob_result = get_top_of_book_post_probs(event_ticker)
        event_probs[event_ticker] = prob_result
    
    # Step 3: Build moneylines table rows
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
            "away_ev_top": away_ev_top,
            "away_ev_topm1": away_ev_topm1,
            "home_ev_top": home_ev_top,
            "home_ev_topm1": home_ev_topm1,
        })
    
    # Sort by ROTO ascending
    moneyline_rows.sort(key=lambda x: (x.get('away_roto') is None, x.get('away_roto') or 0))
    
    # Step 4: Get spreads data
    spread_rows = []
    try:
        spread_rows = build_spreads_rows_for_today()
    except Exception as e:
        print(f"\nNote: Spreads table unavailable ({e})\n")
    
    # Step 5: Get totals data
    totals_rows = []
    try:
        totals_rows = build_totals_rows_for_today()
    except Exception as e:
        print(f"\nNote: Totals table unavailable ({e})\n")
    
    # Step 6: Render and open dashboard
    open_dashboard_in_browser(moneyline_rows, spread_rows if spread_rows else None, totals_rows if totals_rows else None)
    
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