"""
Quick script to print Unabated consensus moneylines for today's NBA games.
"""

from typing import Dict, Any, Tuple
from data_build.unabated_callsheet import (
    fetch_unabated_snapshot,
    extract_nba_games_today,
    get_team_name,
    utc_to_la_datetime
)


def american_odds_to_probability(american_odds: int) -> float:
    """
    Convert American odds to probability with full precision.
    
    Args:
        american_odds: American odds (e.g., -150, +130)
    
    Returns:
        Probability as float (0.0-1.0) with full precision
    """
    if american_odds < 0:
        # Favorite: p = (-odds) / ((-odds) + 100)
        p = (-american_odds) / ((-american_odds) + 100.0)
    else:
        # Underdog: p = 100 / (odds + 100)
        p = 100.0 / (american_odds + 100.0)
    
    return p


def extract_unabated_moneylines_with_american_odds(
    event: Dict[str, Any],
    teams: Dict[str, Any]
) -> Dict[int, Tuple[float, float, int]]:
    """
    Extract Unabated moneyline prices with American odds keyed by team_id.
    
    Also extracts the Unabated-provided probability (which may be truncated) and
    calculates a higher-precision probability from American odds.
    
    Returns:
        Dict mapping team_id -> (unabated_prob, calculated_prob, american_odds)
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
    
    # Store prices by team_id: (prob, american_odds)
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
        
        # Get American price
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
                american_odds = int(price_raw.strip())
            else:
                american_odds = int(price_raw)
        except (ValueError, TypeError):
            continue
        
        # Calculate high-precision probability from American odds directly
        calculated_prob = american_odds_to_probability(american_odds)
        
        # Also get Unabated's provided probability (may be truncated/rounded)
        # They might provide it in a different field, but typically we'd calculate from odds
        # For now, we'll use the calculated one as both since Unabated doesn't separately provide prob
        # But we can show the difference if they round through cents
        unabated_prob = calculated_prob  # Default to calculated, but this could be replaced if Unabated provides separate prob
        
        # Check if there's a direct probability field (unlikely, but worth checking)
        if "probability" in bt1_line or "prob" in bt1_line:
            prob_raw = bt1_line.get("probability") or bt1_line.get("prob")
            if prob_raw is not None:
                try:
                    if isinstance(prob_raw, (int, float)):
                        unabated_prob = float(prob_raw)
                    elif isinstance(prob_raw, str):
                        unabated_prob = float(prob_raw.strip())
                except (ValueError, TypeError):
                    pass
        
        prices_by_team_id[team_id] = (unabated_prob, calculated_prob, american_odds)
    
    return prices_by_team_id


def main():
    """Print today's Unabated consensus moneylines."""
    print("=" * 100)
    print("UNABATED CONSENSUS MONEYLINES - TODAY'S NBA GAMES")
    print("=" * 100)
    print()
    
    # Fetch snapshot
    snapshot = fetch_unabated_snapshot()
    teams_dict = snapshot.get("teams", {})
    
    # Get today's games
    today_games = extract_nba_games_today(snapshot)
    
    if not today_games:
        print("No NBA games found for today.")
        return
    
    print(f"Found {len(today_games)} game(s) today:\n")
    
    # Process each game
    for i, event in enumerate(today_games, 1):
        event_start = event.get("eventStart", "N/A")
        la_dt = utc_to_la_datetime(event_start)
        game_time = la_dt.strftime("%Y-%m-%d %H:%M %Z")
        
        # Extract moneylines with American odds
        moneylines_with_odds = extract_unabated_moneylines_with_american_odds(event, teams_dict)
        
        # Get team names
        event_teams = event.get("eventTeams", {})
        team_names = {}
        if isinstance(event_teams, dict):
            for idx, team_info in event_teams.items():
                if isinstance(team_info, dict):
                    team_id = team_info.get("id")
                    if team_id:
                        team_name = get_team_name(team_id, teams_dict)
                        team_names[team_id] = team_name
        
        print(f"Game {i}: {game_time}")
        print("-" * 120)
        print(f"  {'Team':30} {'Team ID':>8} {'Calc Prob %':>12} {'Calc Decimal':>13} {'Am Odds':>9} {'Diff (calc-unabated)':>20}")
        print("-" * 120)
        
        if not moneylines_with_odds:
            print("  ⚠️ No moneyline consensus found")
        else:
            # Print each team's moneyline
            for team_id, (unabated_prob, calculated_prob, american_odds) in sorted(moneylines_with_odds.items()):
                team_name = team_names.get(team_id, f"Team {team_id}")
                calc_prob_pct = calculated_prob * 100
                # Format American odds with + sign for positive
                odds_str = f"+{american_odds}" if american_odds > 0 else str(american_odds)
                
                # Calculate difference (though they should be the same unless Unabated provides separate prob)
                diff = calculated_prob - unabated_prob
                diff_str = f"{diff:+.6f}" if abs(diff) > 1e-9 else "0.000000"
                
                print(f"  {team_name:30} {team_id:>8} {calc_prob_pct:>11.6f}% {calculated_prob:>12.8f} {odds_str:>9} {diff_str:>20}")
        
        print()
    
    print("=" * 100)


if __name__ == "__main__":
    main()
