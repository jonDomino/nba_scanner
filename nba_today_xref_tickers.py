"""
Print today's NBA games from Unabated with Unabated fair probabilities and matched Kalshi tickers.
"""

import csv
from typing import Dict, Any, List, Tuple, Optional, Set

from nba_todays_fairs import get_today_games_with_fairs
from nba_kalshi_tickers import get_all_nba_kalshi_tickers
from kalshi_top_of_book_probs import parse_event_ticker
from utils import config


def load_team_xref(path: str = None) -> Dict[str, str]:
    """
    Load NBA team xref CSV mapping Unabated team names to Kalshi codes.
    
    CSV format: league,unabated_name,kalshi_code
    
    Returns:
        Dict mapping normalized Unabated team name -> Kalshi code
    """
    if path is None:
        path = config.NBA_XREF_FILE
    
    xref = {}
    try:
        print(f"Loading team xref from: {path}")
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            print(f"Xref fieldnames: {reader.fieldnames}")
            
            for row in reader:
                # Handle BOM and header variants
                league = (
                    row.get("league") or 
                    row.get("\ufeffleague") or 
                    row.get("League") or 
                    ""
                ).strip()
                
                if league.upper() != "NBA":
                    continue
                
                unabated_name = (
                    row.get("unabated_name") or 
                    row.get("unabatedName") or 
                    row.get("Unabated_Name") or
                    row.get("\ufeffunabated_name") or
                    ""
                ).strip()
                
                kalshi_code = (
                    row.get("kalshi_code") or 
                    row.get("kalshiCode") or 
                    row.get("Kalshi_Code") or
                    row.get("\ufeffkalshi_code") or
                    ""
                ).strip().upper()
                
                if unabated_name and kalshi_code:
                    # Normalize name for matching (lowercase, strip)
                    normalized = unabated_name.lower().strip()
                    xref[normalized] = kalshi_code
    except FileNotFoundError:
        print(f"Warning: Team xref file not found: {path}")
    
    return xref


def parse_kalshi_ticker(ticker: str) -> Optional[Dict[str, str]]:
    """
    Parse a Kalshi ticker to extract matchup codes and team code.
    
    Format: KXNBAGAME-YYMONDD{AWAY}{HOME}-{TEAM}
    Example: KXNBAGAME-26JAN08MIACHI-MIA
    
    Returns:
        Dict with keys: matchup_codes (tuple), away_code, home_code, team_code
        or None if parsing fails
    """
    if not ticker or "-" not in ticker:
        return None
    
    parts = ticker.split("-")
    if len(parts) < 3:
        return None
    
    # Last part is team code
    team_code = parts[-1].upper()
    
    # Second-to-last part contains date + matchup codes
    # Format: YYMONDD{AWAY}{HOME} (e.g., 26JAN08MIACHI)
    matchup_part = parts[-2]
    
    # Extract matchup codes (last 6 characters should be team codes)
    if len(matchup_part) < 6:
        return None
    
    # Last 6 chars are team codes (3 for away, 3 for home)
    team_codes_part = matchup_part[-6:]
    away_code = team_codes_part[:3].upper()
    home_code = team_codes_part[3:].upper()
    
    # Create matchup tuple (sorted for consistent lookup)
    matchup_codes = (away_code, home_code)
    
    return {
        "matchup_codes": matchup_codes,
        "away_code": away_code,
        "home_code": home_code,
        "team_code": team_code
    }


def build_ticker_lookup(tickers: List[str]) -> Dict[Tuple[str, str], Dict[str, str]]:
    """
    Build lookup dict from Kalshi tickers.
    
    Structure: (away_code, home_code) -> {team_code: ticker}
    
    Returns:
        Dict mapping matchup (away_code, home_code) to dict of {team_code: ticker}
    """
    lookup = {}
    
    for ticker in tickers:
        parsed = parse_kalshi_ticker(ticker)
        if not parsed:
            continue
        
        matchup = parsed["matchup_codes"]
        team_code = parsed["team_code"]
        
        if matchup not in lookup:
            lookup[matchup] = {}
        
        lookup[matchup][team_code] = ticker
    
    return lookup


def map_unabated_to_kalshi_code(team_name: str, team_id: Optional[int], xref: Dict[str, str]) -> Optional[str]:
    """
    Map Unabated team name/ID to Kalshi code using xref CSV.
    
    Args:
        team_name: Unabated team display name
        team_id: Unabated team ID (currently not used in CSV)
        xref: Team xref dict (normalized name -> kalshi_code)
    
    Returns:
        Kalshi code (3-letter) or None if not found
    """
    if not team_name:
        return None
    
    # Normalize team name for matching
    normalized = team_name.lower().strip()
    
    # Direct lookup
    kalshi_code = xref.get(normalized)
    if kalshi_code:
        return kalshi_code
    
    # Try partial match (in case names differ slightly)
    for unabated_name, code in xref.items():
        if normalized in unabated_name or unabated_name in normalized:
            return code
    
    return None




def determine_away_home_from_kalshi(
    teams_by_id: Dict[int, str],
    fairs_by_team_id: Dict[int, float],
    xref: Dict[str, str],
    event_ticker: Optional[str],
    event_teams_raw: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Determine true away/home teams using Kalshi event ticker canonical ordering.
    
    Args:
        teams_by_id: Dict mapping Unabated team_id -> team_name
        fairs_by_team_id: Dict mapping Unabated team_id -> fair probability
        xref: Team xref dict (normalized name -> kalshi_code)
        event_ticker: Kalshi event ticker (e.g., KXNBAGAME-26JAN08TORBOS)
        event_teams_raw: Raw eventTeams dict from Unabated (for extracting rotation numbers)
    
    Returns:
        Dict with away/home team info and fairs, or None values if can't determine
    """
    if not event_ticker:
        return {
            "away_team_id": None,
            "home_team_id": None,
            "away_team_name": None,
            "home_team_name": None,
            "away_fair": None,
            "home_fair": None,
            "away_roto": None
        }
    
    # Parse Kalshi event ticker to get canonical away/home codes
    try:
        kalshi_codes = parse_event_ticker(event_ticker)
        kalshi_away_code = kalshi_codes["away_code"]
        kalshi_home_code = kalshi_codes["home_code"]
    except (ValueError, KeyError):
        return {
            "away_team_id": None,
            "home_team_id": None,
            "away_team_name": None,
            "home_team_name": None,
            "away_fair": None,
            "home_fair": None,
            "away_roto": None
        }
    
    # Map each Unabated team to its Kalshi code
    team_id_to_kalshi_code = {}
    for team_id, team_name in teams_by_id.items():
        kalshi_code = map_unabated_to_kalshi_code(team_name, team_id, xref)
        if kalshi_code:
            team_id_to_kalshi_code[team_id] = kalshi_code
    
    # Determine which Unabated team is away/home by matching Kalshi codes
    away_team_id = None
    home_team_id = None
    
    for team_id, kalshi_code in team_id_to_kalshi_code.items():
        if kalshi_code == kalshi_away_code:
            away_team_id = team_id
        elif kalshi_code == kalshi_home_code:
            home_team_id = team_id
    
    # Get team names and fairs
    away_team_name = teams_by_id.get(away_team_id) if away_team_id else None
    home_team_name = teams_by_id.get(home_team_id) if home_team_id else None
    away_fair = fairs_by_team_id.get(away_team_id) if away_team_id else None
    home_fair = fairs_by_team_id.get(home_team_id) if home_team_id else None
    
    # Extract rotation number for away team from event_teams_raw
    away_roto = None
    if away_team_id and event_teams_raw and isinstance(event_teams_raw, dict):
        for idx, team_info in event_teams_raw.items():
            if isinstance(team_info, dict):
                team_id = team_info.get("id")
                if team_id == away_team_id:
                    # Try multiple possible field names for rotation number
                    away_roto = (
                        team_info.get("rotationNumber") or
                        team_info.get("rotation") or
                        team_info.get("rotoNumber") or
                        team_info.get("roto") or
                        team_info.get("rot")
                    )
                    if away_roto is not None:
                        try:
                            away_roto = int(away_roto)
                        except (ValueError, TypeError):
                            away_roto = None
                    break
    
    return {
        "away_team_id": away_team_id,
        "home_team_id": home_team_id,
        "away_team_name": away_team_name,
        "home_team_name": home_team_name,
        "away_fair": away_fair,
        "home_fair": home_fair,
        "away_roto": away_roto,
        "kalshi_away_code": kalshi_away_code,
        "kalshi_home_code": kalshi_home_code
    }


def get_today_games_with_fairs_and_kalshi_tickers() -> List[Dict[str, Any]]:
    """
    Get today's NBA games with Unabated fair probabilities and Kalshi market tickers.
    Uses Kalshi event ticker to determine canonical away/home ordering.
    
    Returns:
        List of game dicts, each with:
        - game_date: str (YYYY-MM-DD)
        - event_start: UTC timestamp string
        - away_team_id, home_team_id: Unabated team IDs
        - away_team_name, home_team_name: str
        - away_fair, home_fair: float | None (0-1 probabilities)
        - event_ticker: Kalshi event ticker (derived from market tickers)
        - away_kalshi_ticker, home_kalshi_ticker: str | None (market tickers)
    """
    # Step A: Get today's games with fairs (keyed by team_id, not away/home)
    games = get_today_games_with_fairs()
    
    if not games:
        return []
    
    # Step B: Get Kalshi tickers
    tickers = get_all_nba_kalshi_tickers()
    
    # Step C: Build ticker lookup
    ticker_lookup = build_ticker_lookup(tickers)
    
    # Step D: Load team xref
    xref = load_team_xref()
    
    # Step E: For each game, determine away/home using Kalshi event ticker
    results = []
    
    for game in games:
        teams_by_id = game.get("teams_by_id", {})
        fairs_by_team_id = game.get("fairs_by_team_id", {})
        
        # Try to find event ticker from any available market ticker
        # We'll need to match teams first to get tickers, but we need tickers to determine away/home
        # This is a chicken-and-egg problem. Let's try a different approach:
        # 1. Build all possible matchups from teams
        # 2. Find matching tickers
        # 3. Extract event ticker from first match
        # 4. Use event ticker to determine away/home
        
        event_ticker = None
        away_ticker = None
        home_ticker = None
        
        # Get all team IDs and names
        team_ids = list(teams_by_id.keys())
        if len(team_ids) < 2:
            # Not enough teams, skip
            continue
        
        # Map teams to Kalshi codes
        team_id_to_kalshi_code = {}
        for team_id in team_ids:
            team_name = teams_by_id[team_id]
            kalshi_code = map_unabated_to_kalshi_code(team_name, team_id, xref)
            if kalshi_code:
                team_id_to_kalshi_code[team_id] = kalshi_code
        
        if len(team_id_to_kalshi_code) < 2:
            # Can't map teams to codes, skip
            continue
        
        # Find matching matchup in ticker lookup
        kalshi_codes = list(team_id_to_kalshi_code.values())
        matchup_codes = (kalshi_codes[0], kalshi_codes[1])
        matchup_data = ticker_lookup.get(matchup_codes)
        
        if not matchup_data:
            # Try swapped
            matchup_codes = (kalshi_codes[1], kalshi_codes[0])
            matchup_data = ticker_lookup.get(matchup_codes)
        
        if matchup_data:
            # Found matching tickers - extract event ticker from first ticker
            first_ticker = list(matchup_data.values())[0] if matchup_data else None
            if first_ticker:
                # Derive event ticker: remove final -TEAM suffix
                parts = first_ticker.split("-")
                if len(parts) >= 3:
                    event_ticker = "-".join(parts[:-1])
                    
                    # Parse event ticker to get canonical away/home codes
                    try:
                        parsed = parse_event_ticker(event_ticker)
                        kalshi_away_code = parsed["away_code"]
                        kalshi_home_code = parsed["home_code"]
                        
                        # Get away/home tickers based on canonical codes
                        away_ticker = matchup_data.get(kalshi_away_code)
                        home_ticker = matchup_data.get(kalshi_home_code)
                    except (ValueError, KeyError):
                        pass
        
        # Now determine true away/home using Kalshi event ticker
        away_home_info = determine_away_home_from_kalshi(
            teams_by_id, fairs_by_team_id, xref, event_ticker, game.get("event_teams_raw")
        )
        
        # Consistency check and debug for TORBOS
        if event_ticker and away_home_info.get("away_team_id") and away_home_info.get("home_team_id"):
            kalshi_away_code = away_home_info.get("kalshi_away_code")
            kalshi_home_code = away_home_info.get("kalshi_home_code")
            
            # Debug for TORBOS game
            if ("TOR" in str(kalshi_away_code) and "BOS" in str(kalshi_home_code)) or ("BOS" in str(kalshi_away_code) and "TOR" in str(kalshi_home_code)):
                print(f"\n=== DEBUG TORBOS Game ===")
                print(f"Event ticker: {event_ticker}")
                print(f"EventTeams raw: {game.get('event_teams_raw')}")
                print(f"Teams by ID: {teams_by_id}")
                print(f"Team ID -> Kalshi code mapping: {team_id_to_kalshi_code}")
                print(f"Kalshi away_code: {kalshi_away_code}, home_code: {kalshi_home_code}")
                print(f"Final away_team_name: {away_home_info.get('away_team_name')}, home_team_name: {away_home_info.get('home_team_name')}")
                print(f"Away ticker: {away_ticker}, Home ticker: {home_ticker}")
                print(f"========================\n")
            
            # Verify ticker suffixes match codes
            if away_ticker and kalshi_away_code and not away_ticker.endswith(f"-{kalshi_away_code}"):
                print(f"Warning: Event {event_ticker} - away ticker {away_ticker} doesn't end with away code {kalshi_away_code}")
            if home_ticker and kalshi_home_code and not home_ticker.endswith(f"-{kalshi_home_code}"):
                print(f"Warning: Event {event_ticker} - home ticker {home_ticker} doesn't end with home code {kalshi_home_code}")
        
        # Build result
        result = {
            "game_date": game.get("game_date"),
            "event_start": game.get("event_start"),
            "event_ticker": event_ticker,
            **away_home_info,
            "away_kalshi_ticker": away_ticker,
            "home_kalshi_ticker": home_ticker
        }
        
        results.append(result)
    
    return results


def main():
    """Main entry point."""
    results = get_today_games_with_fairs_and_kalshi_tickers()
    
    if not results:
        print("No NBA games found for today")
        return
    
    # Print table
    print(f"{'GameDate':<12} {'AwayTeam':<30} {'HomeTeam':<30} {'AwayFair':<10} {'HomeFair':<10} {'AwayKalshiTicker':<25} {'HomeKalshiTicker':<25}")
    print("-" * 142)
    
    results.sort(key=lambda x: x.get("event_start", ""))
    
    for result in results:
        away_fair_str = f"{result['away_fair']:.3f}" if result['away_fair'] is not None else "N/A"
        home_fair_str = f"{result['home_fair']:.3f}" if result['home_fair'] is not None else "N/A"
        away_ticker_str = result['away_kalshi_ticker'] or "N/A"
        home_ticker_str = result['home_kalshi_ticker'] or "N/A"
        
        print(
            f"{result['game_date']:<12} "
            f"{result['away_team_name']:<30} "
            f"{result['home_team_name']:<30} "
            f"{away_fair_str:<10} "
            f"{home_fair_str:<10} "
            f"{away_ticker_str:<25} "
            f"{home_ticker_str:<25}"
        )


if __name__ == "__main__":
    main()
