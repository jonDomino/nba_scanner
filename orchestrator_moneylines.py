"""
Moneylines-only orchestrator: coordinates data fetching and table building for moneylines only.
This is a simplified entry point that only builds and outputs the moneylines table.

Supports both local CLI usage and Streamlit Cloud deployment.
"""

from typing import Dict, Any, List, Optional, Tuple

# Import data fetching modules
from data_build.slate import get_today_games_with_fairs_and_kalshi_tickers
from data_build.top_of_book import get_top_of_book_post_probs

# Import dashboard renderer (moneylines only)
from dashboard_html import open_dashboard_in_browser, render_dashboard_html


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


def build_moneylines_rows(debug: bool = False) -> List[Dict[str, Any]]:
    """
    Build moneylines table rows from fetched data.
    
    Pure function - no side effects, returns data only.
    Suitable for use in Streamlit or other frameworks.
    
    Args:
        debug: If True, print debug information
    
    Returns:
        List of moneyline row dicts, sorted by away_roto ascending
    """
    if debug:
        print("📊 Fetching today's games with Unabated fairs and Kalshi tickers...")
    
    # Step 1: Get today's games with fairs and Kalshi tickers
    games = get_today_games_with_fairs_and_kalshi_tickers()
    
    if not games:
        if debug:
            print("No NBA games found for today")
        return []
    
    if debug:
        print(f"Found {len(games)} game(s)")
    
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
    if debug:
        print(f"Fetching orderbook data for {len(event_tickers)} event(s)...")
    
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
        
        # Internal: YES-equivalent liquidity (from NO bids on opposite market, converted to YES prices)
        # Away: NO bids from Home market; Home: NO bids from Away market
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
            "away_top_price_cents": yes_bid_top_c_away,  # Price in cents for dollar liquidity calc
            "home_top_price_cents": yes_bid_top_c_home,  # Price in cents for dollar liquidity calc
            "away_ev_top": away_ev_top,
            "away_ev_topm1": away_ev_topm1,
            "home_ev_top": home_ev_top,
            "home_ev_topm1": home_ev_topm1,
        })
    
    # Sort by ROTO ascending
    moneyline_rows.sort(key=lambda x: (x.get('away_roto') is None, x.get('away_roto') or 0))
    
    return moneyline_rows


def build_dashboard_html_moneylines(rows: List[Dict[str, Any]]) -> str:
    """
    Build HTML dashboard string from moneyline rows.
    
    Pure function - no side effects, returns HTML string only.
    
    Args:
        rows: List of moneyline row dicts
    
    Returns:
        HTML content as string
    """
    return render_dashboard_html(rows, None, None)


def run_moneylines_pipeline(debug: bool = False) -> Tuple[List[Dict[str, Any]], str]:
    """
    Run complete moneylines pipeline: fetch data, build rows, generate HTML.
    
    Returns both rows and HTML string for maximum flexibility.
    
    Args:
        debug: If True, print debug information
    
    Returns:
        Tuple of (moneyline_rows, html_string)
    """
    rows = build_moneylines_rows(debug=debug)
    html = build_dashboard_html_moneylines(rows)
    return rows, html


def main():
    """Main entry point for local CLI usage - orchestrates data fetching and opens dashboard in browser."""
    moneyline_rows = build_moneylines_rows(debug=True)
    
    if not moneyline_rows:
        print("No NBA games found for today")
        return
    
    # Open dashboard in browser (local usage only)
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
