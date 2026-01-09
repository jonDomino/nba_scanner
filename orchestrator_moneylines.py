"""
Moneylines-only orchestrator: coordinates data fetching and table building for moneylines only.
This is a simplified entry point that only builds and outputs the moneylines table.
"""

from typing import Dict, Any, List, Optional

# Import data fetching modules
from data_build.slate import get_today_games_with_fairs_and_kalshi_tickers
from data_build.top_of_book import get_top_of_book_post_probs

# Import dashboard renderer (moneylines only)
from dashboard_html import open_dashboard_in_browser


def derive_event_ticker(market_ticker: str) -> Optional[str]:
    """Derive event ticker from market ticker by removing final -TEAM suffix."""
    if not market_ticker:
        return None
    parts = market_ticker.split("-")
    if len(parts) >= 3:
        return "-".join(parts[:-1])
    return None


def main():
    """Main entry point - orchestrates data fetching and table building for moneylines only."""
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
            event_ticker = derive_event_ticker(away_ticker)
        elif home_ticker:
            event_ticker = derive_event_ticker(home_ticker)
        
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
    
    # Step 4: Render and open dashboard (moneylines only - no spreads or totals)
    open_dashboard_in_browser(moneyline_rows, None, None)
    
    # Also print console version
    from moneylines.table import print_dashboard
    print_dashboard(moneyline_rows)


def run_with_server(port: int = 8000):
    """Run orchestrator with HTTP server for refresh functionality."""
    from refresh_server import run_server
    run_server(port=port, open_browser=True)


if __name__ == "__main__":
    import sys
    
    # Check if --server flag is provided
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
        run_with_server(port=port)
    else:
        # Run normally (one-time generation)
        main()
