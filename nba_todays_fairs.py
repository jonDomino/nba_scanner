"""
Fetch today's NBA games from Unabated and display with Unabated fair probabilities.
"""

from datetime import datetime
from typing import Dict, Any, List

try:
    from zoneinfo import ZoneInfo
    USE_PYTZ = False
except ImportError:
    import pytz
    USE_PYTZ = True

from core.reusable_functions import fetch_unabated_snapshot
from utils import config
from pricing.conversion import american_to_cents


def utc_to_la_datetime(utc_timestamp: str) -> datetime:
    """Convert UTC timestamp to America/Los_Angeles datetime."""
    dt_utc = datetime.fromisoformat(utc_timestamp.replace("Z", "+00:00"))
    
    if USE_PYTZ:
        import pytz
        utc_tz = pytz.UTC
        la_tz = pytz.timezone("America/Los_Angeles")
        if dt_utc.tzinfo is None:
            dt_utc = utc_tz.localize(dt_utc)
        else:
            dt_utc = dt_utc.astimezone(utc_tz)
        return dt_utc.astimezone(la_tz)
    else:
        utc_tz = ZoneInfo("UTC")
        la_tz = ZoneInfo("America/Los_Angeles")
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=utc_tz)
        else:
            dt_utc = dt_utc.astimezone(utc_tz)
        return dt_utc.astimezone(la_tz)


def is_today_la(utc_timestamp: str) -> bool:
    """Check if UTC timestamp is today in America/Los_Angeles timezone."""
    la_dt = utc_to_la_datetime(utc_timestamp)
    
    if USE_PYTZ:
        import pytz
        la_today = datetime.now(pytz.timezone("America/Los_Angeles")).date()
    else:
        la_today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    
    return la_dt.date() == la_today


def get_team_name(team_id: int, teams: Dict[str, Any]) -> str:
    """Look up team name from team ID."""
    team_info = teams.get(str(team_id)) or teams.get(team_id)
    if team_info and isinstance(team_info, dict):
        return team_info.get("name") or team_info.get("teamName") or f"Team {team_id}"
    return f"Team {team_id}"


def extract_unabated_moneylines_by_team_id(
    event: Dict[str, Any], 
    teams: Dict[str, Any]
) -> Dict[int, float]:
    """
    Extract Unabated moneyline prices keyed by team_id (not away/home).
    
    Structure: Multiple ms49 keys exist (e.g., si1:ms49:an0, si0:ms49:an0)
    The si{index} prefix maps to eventTeams[str(index)].
    
    Returns:
        Dict mapping team_id -> probability (0.0-1.0)
    """
    market_lines = event.get("gameOddsMarketSourcesLines", {})
    if not isinstance(market_lines, dict):
        return {}
    
    event_teams = event.get("eventTeams", {})
    if not isinstance(event_teams, dict):
        return {}
    
    # Find ALL Unabated market source (ms49) keys
    ms49_keys = [k for k in market_lines.keys() if ":ms49:" in k]
    
    if not ms49_keys:
        return {}
    
    # Store prices by team_id (not by assumed away/home)
    prices_by_team_id = {}
    
    # Iterate through all ms49 blocks
    for ms49_key in ms49_keys:
        ms49_block = market_lines[ms49_key]
        if not isinstance(ms49_block, dict):
            continue
        
        # Parse side index from key prefix (e.g., "si1:ms49:an0" -> side_idx = 1)
        try:
            parts = ms49_key.split(":")
            side_token = parts[0]  # "si1"
            if side_token.startswith("si") and len(side_token) > 2:
                side_idx = int(side_token[2:])  # Extract "1" from "si1"
            else:
                continue
        except (ValueError, IndexError):
            continue
        
        # Get team_id from eventTeams using side_idx
        team_info = event_teams.get(str(side_idx), {})
        if not isinstance(team_info, dict):
            continue
        
        team_id = team_info.get("id")
        if team_id is None:
            continue
        
        # Get bt1 line from this ms49 block
        bt1_line = ms49_block.get("bt1")
        if bt1_line is None or not isinstance(bt1_line, dict):
            continue
        
        # Get price
        price_raw = (
            bt1_line.get("americanPrice") or
            bt1_line.get("unabatedPrice") or
            bt1_line.get("price")
        )
        
        if price_raw is None:
            continue
        
        # Convert to int safely (handle strings like " -150 " or "+130")
        try:
            if isinstance(price_raw, str):
                price = int(price_raw.strip())
            else:
                price = int(price_raw)
        except (ValueError, TypeError):
            continue
        
        # Convert to probability and store by team_id
        cents = american_to_cents(price)
        prob = cents / 100.0  # Convert to probability (0-1)
        prices_by_team_id[team_id] = prob
    
    return prices_by_team_id


def extract_nba_games_today(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract today's NBA games from Unabated snapshot."""
    games = []
    game_odds_events = snapshot.get("gameOddsEvents", {})
    
    # Find NBA full game pregame section
    nba_key = None
    for key in game_odds_events.keys():
        if key.startswith("lg3:") and ":pt1:" in key and ":pregame" in key:
            nba_key = key
            break
    
    if not nba_key:
        return games
    
    events = game_odds_events[nba_key]
    if not isinstance(events, list):
        return games
    
    for event in events:
        event_start = event.get("eventStart")
        if event_start and is_today_la(event_start):
            games.append(event)
    
    return games


def get_today_games_with_fairs() -> List[Dict[str, Any]]:
    """
    Get today's NBA games with Unabated fair probabilities.
    
    NOTE: This function does NOT determine away/home - it returns teams indexed by Unabated's
    eventTeams structure. The caller must determine true away/home using Kalshi event ticker.
    
    Returns:
        List of game dicts, each with:
        - game_date: LA date string (YYYY-MM-DD)
        - event_start: UTC timestamp string
        - event_teams_raw: Raw eventTeams dict (for inspection)
        - teams_by_id: Dict mapping team_id -> team_name
        - fairs_by_team_id: Dict mapping team_id -> fair probability
    """
    snapshot = fetch_unabated_snapshot()
    teams = snapshot.get("teams", {})
    today_games = extract_nba_games_today(snapshot)
    
    results = []
    
    for event in today_games:
        event_start = event.get("eventStart")
        la_dt = utc_to_la_datetime(event_start)
        game_date = la_dt.strftime("%Y-%m-%d")
        
        event_teams = event.get("eventTeams", {})
        
        # Extract all team info from eventTeams
        teams_by_id = {}
        if isinstance(event_teams, dict):
            for idx, team_info in event_teams.items():
                if isinstance(team_info, dict):
                    team_id = team_info.get("id")
                    if team_id:
                        team_name = get_team_name(team_id, teams)
                        teams_by_id[team_id] = team_name
        
        # Extract fairs keyed by team_id (not by assumed away/home)
        fairs_by_team_id = extract_unabated_moneylines_by_team_id(event, teams)
        
        results.append({
            "game_date": game_date,
            "event_start": event_start,
            "event_teams_raw": event_teams,
            "teams_by_id": teams_by_id,
            "fairs_by_team_id": fairs_by_team_id
        })
    
    return results


def main():
    """Main entry point."""
    games = get_today_games_with_fairs()
    
    if not games:
        print("No NBA games found for today")
        return
    
    print(f"Found {len(games)} game(s)")
    print("\nNOTE: This module no longer determines away/home. Use nba_today_xref_tickers.py for complete data.")
    for game in games[:3]:  # Show first 3 games for debugging
        print(f"\nGame date: {game['game_date']}")
        print(f"Teams by ID: {game['teams_by_id']}")
        print(f"Fairs by ID: {game['fairs_by_team_id']}")


if __name__ == "__main__":
    main()
