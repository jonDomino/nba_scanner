"""
Pure moneylines table builder - reads from bundle only, no network calls.
"""

from typing import Dict, Any, List
from data_build.bundle import Bundle, GameInfo, BundleOrderbookSnapshot


def build_table(bundle: Bundle) -> List[Dict[str, Any]]:
    """
    Build moneylines table rows from bundle.
    
    Pure function - no network calls, only reads from bundle.
    
    Args:
        bundle: Complete data bundle with games, unabated consensus, and orderbooks
    
    Returns:
        List of moneyline row dicts with same structure as before
    """
    rows = []
    
    for game in bundle.games:
        game_id = _get_game_id(game)
        
        # Get Unabated consensus
        unabated_data = bundle.unabated.get(game_id) or bundle.unabated.get(game.event_ticker or "")
        if not unabated_data:
            continue
        
        # Get moneylines by team_id
        moneylines_by_team_id = unabated_data.moneylines
        
        # Map to away/home fairs
        away_fair = None
        home_fair = None
        if game.away_team_id and game.away_team_id in moneylines_by_team_id:
            away_fair = moneylines_by_team_id[game.away_team_id]
        if game.home_team_id and game.home_team_id in moneylines_by_team_id:
            home_fair = moneylines_by_team_id[game.home_team_id]
        
        # Get Kalshi markets for this game
        kalshi_data = bundle.kalshi_markets.get(game_id) or bundle.kalshi_markets.get(game.event_ticker or "")
        if not kalshi_data:
            continue
        
        # Get market tickers
        away_ticker = kalshi_data.moneyline_tickers.get("away")
        home_ticker = kalshi_data.moneyline_tickers.get("home")
        
        # Get orderbook snapshots
        away_snapshot = bundle.orderbooks.get(away_ticker) if away_ticker else None
        home_snapshot = bundle.orderbooks.get(home_ticker) if home_ticker else None
        
        # Extract YES break-even probs and liquidity
        yes_be_top_away = away_snapshot.yes_be_top if away_snapshot else None
        yes_be_topm1_away = away_snapshot.yes_be_top_p1 if away_snapshot else None
        yes_be_top_home = home_snapshot.yes_be_top if home_snapshot else None
        yes_be_topm1_home = home_snapshot.yes_be_top_p1 if home_snapshot else None
        
        yes_bid_top_liq_away = away_snapshot.yes_bid_top_liq if away_snapshot else None
        yes_bid_top_p1_liq_away = away_snapshot.yes_bid_top_p1_liq if away_snapshot else None
        yes_bid_top_liq_home = home_snapshot.yes_bid_top_liq if home_snapshot else None
        yes_bid_top_p1_liq_home = home_snapshot.yes_bid_top_p1_liq if home_snapshot else None
        
        # Compute EVs
        away_ev_top = (away_fair - yes_be_top_away) * 100.0 if (away_fair is not None and yes_be_top_away is not None) else None
        away_ev_topm1 = (away_fair - yes_be_topm1_away) * 100.0 if (away_fair is not None and yes_be_topm1_away is not None) else None
        home_ev_top = (home_fair - yes_be_top_home) * 100.0 if (home_fair is not None and yes_be_top_home is not None) else None
        home_ev_topm1 = (home_fair - yes_be_topm1_home) * 100.0 if (home_fair is not None and yes_be_topm1_home is not None) else None
        
        rows.append({
            "game_date": game.game_date,
            "event_start": game.event_start,
            "away_roto": game.away_roto,
            "away_team": game.away_team_name,
            "home_team": game.home_team_name,
            "away_fair": away_fair,
            "home_fair": home_fair,
            "event_ticker": game.event_ticker or "N/A",
            "away_ticker": away_ticker or "N/A",
            "home_ticker": home_ticker or "N/A",
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
    rows.sort(key=lambda x: (x.get('away_roto') is None, x.get('away_roto') or 0))
    
    return rows


def _get_game_id(game: GameInfo) -> str:
    """Get a unique identifier for a game."""
    if game.event_start:
        return game.event_start
    return f"{game.away_team_name}_{game.home_team_name}"