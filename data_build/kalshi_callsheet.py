"""
Kalshi callsheet: discover all Kalshi markets needed for today's slate.
One-shot fetch that discovers moneyline, spread, and totals markets.
"""

from typing import Dict, Any, List
from data_build.bundle import GameInfo, KalshiMarkets
from data_build.kalshi_markets import get_all_nba_kalshi_tickers
from data_build.slate import build_ticker_lookup, parse_kalshi_ticker, map_unabated_to_kalshi_code, load_team_xref
from data_build.top_of_book import parse_event_ticker
from spreads.builder import discover_kalshi_spread_markets
from totals.builder import discover_kalshi_totals_markets
from core.reusable_functions import fetch_kalshi_markets_for_event
from utils.kalshi_api import load_creds
from utils import config


def fetch_kalshi_callsheet_for_slate(games: List[GameInfo]) -> Dict[str, Any]:
    """
    One-shot fetch of all Kalshi markets needed for the slate.
    
    Discovers:
    - Moneyline market tickers (away + home per game)
    - Spread markets (all strikes per game)
    - Totals markets (all strikes per game)
    
    Args:
        games: List of GameInfo from Unabated slate
    
    Returns:
        Dict with:
        - markets: Dict[str, KalshiMarkets] (keyed by game_id or event_ticker)
        - telemetry: Dict with call counts
    """
    if not games:
        return {"markets": {}, "telemetry": {"calls": 0}}
    
    # Load xref for team mapping
    xref = load_team_xref()
    
    # Get all moneyline tickers once
    all_tickers = get_all_nba_kalshi_tickers()
    ticker_lookup = build_ticker_lookup(all_tickers)
    
    # Load credentials
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        print(f"❌ Failed to load Kalshi credentials: {e}")
        return {"markets": {}, "telemetry": {"calls": 0}}
    
    markets_dict = {}
    call_count = 0
    
    # For each game, discover all relevant markets
    for game in games:
        game_id = _get_game_id(game)
        
        # Map Unabated teams to Kalshi codes
        away_kalshi_code = None
        home_kalshi_code = None
        event_ticker = None
        
        if game.away_team_name:
            away_kalshi_code = map_unabated_to_kalshi_code(game.away_team_name, game.away_team_id, xref)
        if game.home_team_name:
            home_kalshi_code = map_unabated_to_kalshi_code(game.home_team_name, game.home_team_id, xref)
        
        # Find matching matchup in ticker lookup
        if away_kalshi_code and home_kalshi_code:
            matchup_codes = (away_kalshi_code, home_kalshi_code)
            matchup_data = ticker_lookup.get(matchup_codes)
            
            if not matchup_data:
                # Try swapped
                matchup_codes = (home_kalshi_code, away_kalshi_code)
                matchup_data = ticker_lookup.get(matchup_codes)
            
            if matchup_data:
                # Extract event ticker from first ticker
                first_ticker = list(matchup_data.values())[0] if matchup_data else None
                if first_ticker:
                    parts = first_ticker.split("-")
                    if len(parts) >= 3:
                        event_ticker = "-".join(parts[:-1])
                        
                        # Update game with Kalshi data
                        game.away_kalshi_code = away_kalshi_code
                        game.home_kalshi_code = home_kalshi_code
                        game.event_ticker = event_ticker
                        
                        # Determine away/home from event ticker
                        try:
                            parsed = parse_event_ticker(event_ticker)
                            canonical_away_code = parsed["away_code"]
                            canonical_home_code = parsed["home_code"]
                            
                            # Swap if needed
                            if away_kalshi_code != canonical_away_code:
                                game.away_kalshi_code, game.home_kalshi_code = game.home_kalshi_code, game.away_kalshi_code
                                game.away_team_id, game.home_team_id = game.home_team_id, game.away_team_id
                                game.away_team_name, game.home_team_name = game.home_team_name, game.away_team_name
                        except (ValueError, KeyError):
                            pass
        
        if not event_ticker:
            # Skip this game - can't match to Kalshi
            continue
        
        # Get moneyline tickers
        moneyline_tickers = {}
        if game.away_kalshi_code and game.home_kalshi_code:
            away_market = f"{event_ticker}-{game.away_kalshi_code}"
            home_market = f"{event_ticker}-{game.home_kalshi_code}"
            moneyline_tickers["away"] = away_market
            moneyline_tickers["home"] = home_market
        
        # Discover spread markets (one API call per game)
        spread_markets = []
        if game.away_team_name and game.home_team_name:
            spread_markets = discover_kalshi_spread_markets(
                event_ticker, game.away_team_name, game.home_team_name, xref
            )
            call_count += 1
        
        # Discover totals markets (one API call per game)
        totals_markets = []
        totals_markets = discover_kalshi_totals_markets(event_ticker)
        call_count += 1
        
        # Build KalshiMarkets
        kalshi_markets = KalshiMarkets(
            moneyline_tickers=moneyline_tickers,
            spread_markets=spread_markets,
            totals_markets=totals_markets
        )
        
        markets_dict[game_id] = kalshi_markets
        # Also key by event_ticker for convenience
        if event_ticker:
            markets_dict[event_ticker] = kalshi_markets
    
    return {
        "markets": markets_dict,
        "telemetry": {"calls": call_count}
    }


def _get_game_id(game: GameInfo) -> str:
    """Get a unique identifier for a game."""
    if game.event_start:
        return game.event_start
    return f"{game.away_team_name}_{game.home_team_name}"