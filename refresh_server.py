"""
HTTP server for handling dashboard refresh requests.
Serves the dashboard HTML and handles refresh endpoints for Kalshi and Unabated data.
"""

import threading
from typing import Dict, Any, List, Optional
from flask import Flask, request, jsonify
import webbrowser

from data_build.slate import get_today_games_with_fairs_and_kalshi_tickers
from data_build.top_of_book import get_top_of_book_post_probs
from moneylines.table import create_html_dashboard, print_dashboard

app = Flask(__name__)

# Global state to store current dashboard data
current_moneyline_rows: List[Dict[str, Any]] = []
cache_unabated: Optional[List[Dict[str, Any]]] = None
cache_event_probs: Dict[str, Dict[str, Any]] = {}


def derive_event_ticker(market_ticker: str) -> Optional[str]:
    """Derive event ticker from market ticker by removing final -TEAM suffix."""
    if not market_ticker:
        return None
    parts = market_ticker.split("-")
    if len(parts) >= 3:
        return "-".join(parts[:-1])
    return None


def fetch_and_build_moneylines(refresh_unabated: bool = False, refresh_kalshi: bool = False) -> List[Dict[str, Any]]:
    """
    Fetch data and build moneylines table rows.
    
    Args:
        refresh_unabated: If True, re-fetch Unabated data
        refresh_kalshi: If True, re-fetch Kalshi orderbook data
    
    Returns:
        List of moneyline row dicts
    """
    global cache_unabated, cache_event_probs
    
    # Step 1: Get today's games with fairs and Kalshi tickers (only refresh if requested or not cached)
    if refresh_unabated or cache_unabated is None:
        print("📡 Fetching Unabated data...")
        games = get_today_games_with_fairs_and_kalshi_tickers()
        cache_unabated = games
    else:
        print("♻️ Using cached Unabated data...")
        games = cache_unabated
    
    if not games:
        print("No NBA games found for today")
        return []
    
    # Step 2: Get top-of-book maker break-even probs for each event (only refresh if requested)
    event_probs = {} if refresh_kalshi else cache_event_probs.copy()
    
    # Collect unique event tickers
    event_tickers = set()
    game_to_event = {}
    
    for i, game in enumerate(games):
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
    
    # Fetch orderbook data for each event (only if refreshing Kalshi)
    if refresh_kalshi:
        print("📡 Fetching Kalshi orderbook data...")
        for event_ticker in event_tickers:
            prob_result = get_top_of_book_post_probs(event_ticker)
            event_probs[event_ticker] = prob_result
        cache_event_probs = event_probs.copy()
    else:
        print("♻️ Using cached Kalshi data...")
    
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
        
        # Internal: YES bid liquidity
        yes_bid_top_liq_away = prob_data.get("yes_bid_top_liq_away") if prob_data else None
        yes_bid_top_p1_liq_away = prob_data.get("yes_bid_top_p1_liq_away") if prob_data else None
        yes_bid_top_liq_home = prob_data.get("yes_bid_top_liq_home") if prob_data else None
        yes_bid_top_p1_liq_home = prob_data.get("yes_bid_top_p1_liq_home") if prob_data else None
        
        # Compute EVs
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
    
    return moneyline_rows


@app.route('/')
def dashboard():
    """Serve the dashboard HTML."""
    global current_moneyline_rows
    html_content = create_html_dashboard(current_moneyline_rows, None, None)
    return html_content


@app.route('/refresh-kalshi', methods=['POST'])
def refresh_kalshi():
    """Refresh Kalshi orderbook data."""
    global current_moneyline_rows
    print("\n🔄 Refreshing Kalshi data...")
    
    try:
        current_moneyline_rows = fetch_and_build_moneylines(refresh_unabated=False, refresh_kalshi=True)
        print(f"✅ Refreshed Kalshi data: {len(current_moneyline_rows)} game(s)")
        return jsonify({"status": "success", "message": "Kalshi data refreshed", "games": len(current_moneyline_rows)})
    except Exception as e:
        print(f"❌ Error refreshing Kalshi data: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/refresh-unabated', methods=['POST'])
def refresh_unabated():
    """Refresh Unabated consensus data."""
    global current_moneyline_rows
    print("\n🔄 Refreshing Unabated data...")
    
    try:
        current_moneyline_rows = fetch_and_build_moneylines(refresh_unabated=True, refresh_kalshi=False)
        print(f"✅ Refreshed Unabated data: {len(current_moneyline_rows)} game(s)")
        return jsonify({"status": "success", "message": "Unabated data refreshed", "games": len(current_moneyline_rows)})
    except Exception as e:
        print(f"❌ Error refreshing Unabated data: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


def run_server(port: int = 8000, open_browser: bool = True):
    """Run the refresh server."""
    # Initial data fetch
    print("📊 Initial data fetch...")
    global current_moneyline_rows
    current_moneyline_rows = fetch_and_build_moneylines(refresh_unabated=True, refresh_kalshi=True)
    print(f"✅ Loaded {len(current_moneyline_rows)} game(s)")
    
    if open_browser:
        # Open browser after a short delay to allow server to start
        def open_browser_delayed():
            import time
            time.sleep(1)
            webbrowser.open(f"http://localhost:{port}")
        
        threading.Thread(target=open_browser_delayed, daemon=True).start()
    
    print(f"\n🌐 Server running at http://localhost:{port}")
    print("📡 Refresh endpoints:")
    print(f"   POST http://localhost:{port}/refresh-kalshi")
    print(f"   POST http://localhost:{port}/refresh-unabated")
    print("\nPress Ctrl+C to stop the server\n")
    
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_server()
