"""
Export today's NBA games from Unabated to DataFrame and CSV.
Optimized for speed - single snapshot fetch, single pass extraction.
"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from data_build.unabated_callsheet import (
    fetch_unabated_snapshot,
    extract_nba_games_today,
    get_team_name,
    utc_to_la_datetime,
    is_today_la
)


def american_odds_to_probability(american_odds: int) -> float:
    """Convert American odds to probability."""
    if american_odds < 0:
        return (-american_odds) / ((-american_odds) + 100.0)
    else:
        return 100.0 / (american_odds + 100.0)


def extract_all_game_data(event: Dict[str, Any], teams_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract all game data (moneylines, spreads, totals) in a single pass for speed.
    Uses Unabated data exclusively - no Kalshi dependencies.
    
    Returns dict with all fields needed for DataFrame.
    """
    event_start = event.get("eventStart")
    if not event_start:
        return None
    
    # Convert to LA time
    la_dt = utc_to_la_datetime(event_start)
    game_date = la_dt.strftime("%Y-%m-%d")
    game_time = la_dt.strftime("%H:%M")
    
    event_teams = event.get("eventTeams", {})
    if not isinstance(event_teams, dict):
        return None
    
    market_lines = event.get("gameOddsMarketSourcesLines", {})
    if not isinstance(market_lines, dict):
        return None
    
    # Find ALL ms49 keys (process once)
    ms49_keys = [k for k in market_lines.keys() if ":ms49:" in k]
    if not ms49_keys:
        return None
    
    # Extract team info and rotation numbers
    teams_by_id = {}
    roto_by_team_id = {}
    
    # DEBUG: Track team extraction
    debug_teams = []
    if "San Antonio" in str(event_teams) or "Boston" in str(event_teams) or "Spurs" in str(event_teams) or "Celtics" in str(event_teams):
        print(f"\n[DEBUG] Processing event with San Antonio/Boston teams")
        print(f"  Event start: {event_start}")
        print(f"  Event teams keys: {list(event_teams.keys())}")
    
    for idx, team_info in event_teams.items():
        if isinstance(team_info, dict):
            team_id = team_info.get("id")
            if team_id:
                team_name = get_team_name(team_id, teams_dict)
                teams_by_id[team_id] = team_name
                debug_teams.append((team_id, team_name))
                
                # Extract rotation number
                roto = (
                    team_info.get("rotationNumber") or
                    team_info.get("rotation") or
                    team_info.get("rotoNumber") or
                    team_info.get("roto") or
                    team_info.get("rot")
                )
                
                # DEBUG: Print rotation extraction for San Antonio/Boston
                if "San Antonio" in team_name or "Boston" in team_name or "Spurs" in team_name or "Celtics" in team_name:
                    print(f"  [DEBUG] Team: {team_name} (ID: {team_id})")
                    print(f"    team_info keys: {list(team_info.keys())}")
                    print(f"    rotationNumber: {team_info.get('rotationNumber')}")
                    print(f"    rotation: {team_info.get('rotation')}")
                    print(f"    rotoNumber: {team_info.get('rotoNumber')}")
                    print(f"    roto: {team_info.get('roto')}")
                    print(f"    rot: {team_info.get('rot')}")
                    print(f"    Extracted roto value: {roto}")
                    print(f"    roto type: {type(roto)}")
                
                if roto is not None:
                    try:
                        roto_int = int(roto)
                        roto_by_team_id[team_id] = roto_int
                        if "San Antonio" in team_name or "Boston" in team_name or "Spurs" in team_name or "Celtics" in team_name:
                            print(f"    ✅ Successfully stored roto: {roto_int}")
                    except (ValueError, TypeError) as e:
                        if "San Antonio" in team_name or "Boston" in team_name or "Spurs" in team_name or "Celtics" in team_name:
                            print(f"    ❌ Failed to convert roto to int: {e}")
                else:
                    if "San Antonio" in team_name or "Boston" in team_name or "Spurs" in team_name or "Celtics" in team_name:
                        print(f"    ⚠️ No rotation number found in any field")
    
    if len(teams_by_id) < 2:
        return None
    
    # Extract all market data in one pass
    moneylines_by_team_id = {}
    spreads_by_team_id = {}
    
    for ms49_key in ms49_keys:
        ms49_block = market_lines[ms49_key]
        if not isinstance(ms49_block, dict):
            continue
        
        # Parse side index
        try:
            parts = ms49_key.split(":")
            side_token = parts[0]
            if side_token.startswith("si") and len(side_token) > 2:
                side_idx = int(side_token[2:])
            else:
                continue
        except (ValueError, IndexError):
            continue
        
        # Get team_id
        team_info = event_teams.get(str(side_idx), {})
        if not isinstance(team_info, dict):
            continue
        
        team_id = team_info.get("id")
        if team_id is None:
            continue
        
        # Extract moneyline (bt1)
        bt1_line = ms49_block.get("bt1")
        if isinstance(bt1_line, dict):
            price_raw = (
                bt1_line.get("americanPrice") or
                bt1_line.get("unabatedPrice") or
                bt1_line.get("price")
            )
            if price_raw is not None:
                try:
                    if isinstance(price_raw, str):
                        american_odds = int(price_raw.strip())
                    else:
                        american_odds = int(price_raw)
                    
                    prob = american_odds_to_probability(american_odds)
                    prob = round(prob, 4)  # 4 decimal places
                    
                    moneylines_by_team_id[team_id] = {
                        "prob": prob,
                        "decimal": round(1.0 / prob, 4) if prob > 0 else None,
                        "american": american_odds
                    }
                except (ValueError, TypeError):
                    pass
        
        # Extract spread (bt2)
        bt2_line = ms49_block.get("bt2")
        if isinstance(bt2_line, dict):
            spread_raw = (
                bt2_line.get("line") or
                bt2_line.get("spread") or
                bt2_line.get("value") or
                bt2_line.get("points")
            )
            
            if spread_raw is not None:
                try:
                    if isinstance(spread_raw, str):
                        spread = float(spread_raw.strip())
                    else:
                        spread = float(spread_raw)
                    
                    juice_raw = (
                        bt2_line.get("americanPrice") or
                        bt2_line.get("unabatedPrice") or
                        bt2_line.get("price") or
                        bt2_line.get("juice")
                    )
                    
                    juice = None
                    if juice_raw is not None:
                        try:
                            if isinstance(juice_raw, str):
                                juice = int(juice_raw.strip())
                            else:
                                juice = int(juice_raw)
                        except (ValueError, TypeError):
                            pass
                    
                    spreads_by_team_id[team_id] = {
                        "spread": spread,
                        "juice": juice
                    }
                except (ValueError, TypeError):
                    pass
    
    # Extract totals (bt3) - game-level, appears in any ms49 block
    total_data = None
    for ms49_key in ms49_keys:
        ms49_block = market_lines[ms49_key]
        if not isinstance(ms49_block, dict):
            continue
        
        bt3_line = ms49_block.get("bt3")
        if isinstance(bt3_line, dict):
            total_raw = (
                bt3_line.get("line") or
                bt3_line.get("total") or
                bt3_line.get("value") or
                bt3_line.get("points") or
                bt3_line.get("overUnder")
            )
            
            if total_raw is not None:
                try:
                    if isinstance(total_raw, str):
                        total = float(total_raw.strip())
                    else:
                        total = float(total_raw)
                    
                    # Get over/under juice
                    over_juice_raw = (
                        bt3_line.get("americanPrice") or
                        bt3_line.get("unabatedPrice") or
                        bt3_line.get("price") or
                        bt3_line.get("juice")
                    )
                    
                    over_juice = None
                    if over_juice_raw is not None:
                        try:
                            if isinstance(over_juice_raw, str):
                                over_juice = int(over_juice_raw.strip())
                            else:
                                over_juice = int(over_juice_raw)
                        except (ValueError, TypeError):
                            pass
                    
                    # Under juice is typically the same as over juice (or can be calculated)
                    # For now, use same value
                    under_juice = over_juice
                    
                    total_data = {
                        "total": total,
                        "over_juice": over_juice,
                        "under_juice": under_juice
                    }
                    break  # Found total, no need to continue
                except (ValueError, TypeError):
                    continue
    
    # Determine away/home teams explicitly from event_teams["0"] and event_teams["1"]
    # event_teams["0"] = away team, event_teams["1"] = home team
    away_team_id = None
    home_team_id = None
    away_team_name = None
    home_team_name = None
    away_roto = None
    home_roto = None
    
    # Get away team from event_teams["0"]
    away_info = event_teams.get("0", {})
    if isinstance(away_info, dict):
        away_team_id = away_info.get("id")
        if away_team_id:
            away_team_name = teams_by_id.get(away_team_id)
            away_roto = roto_by_team_id.get(away_team_id)
    
    # Get home team from event_teams["1"]
    home_info = event_teams.get("1", {})
    if isinstance(home_info, dict):
        home_team_id = home_info.get("id")
        if home_team_id:
            home_team_name = teams_by_id.get(home_team_id)
            home_roto = roto_by_team_id.get(home_team_id)
    
    # DEBUG: Print away/home determination for San Antonio/Boston
    if ("San Antonio" in str(away_team_name) or "Boston" in str(away_team_name) or 
        "San Antonio" in str(home_team_name) or "Boston" in str(home_team_name) or
        "Spurs" in str(away_team_name) or "Celtics" in str(away_team_name) or
        "Spurs" in str(home_team_name) or "Celtics" in str(home_team_name)):
        print(f"\n  [DEBUG] Away/Home Determination:")
        print(f"    away_team_id: {away_team_id}, away_team_name: {away_team_name}")
        print(f"    home_team_id: {home_team_id}, home_team_name: {home_team_name}")
        print(f"    roto_by_team_id: {roto_by_team_id}")
        print(f"    away_roto (from roto_by_team_id): {roto_by_team_id.get(away_team_id) if away_team_id else None}")
        print(f"    home_roto (from roto_by_team_id): {roto_by_team_id.get(home_team_id) if home_team_id else None}")
        print(f"    Final away_roto: {away_roto}")
        print(f"    Final home_roto: {home_roto}")
    
    # Build result dict
    result = {
        "game_date": game_date,
        "game_time": game_time,
        "away_team": away_team_name,
        "home_team": home_team_name,
        "away_roto": away_roto,
        "home_roto": home_roto,
    }
    
    # Moneyline data
    away_ml = moneylines_by_team_id.get(away_team_id) if away_team_id else {}
    home_ml = moneylines_by_team_id.get(home_team_id) if home_team_id else {}
    
    result["away_ml_prob"] = away_ml.get("prob")
    result["away_ml_decimal"] = away_ml.get("decimal")
    result["away_ml_american"] = away_ml.get("american")
    result["home_ml_prob"] = home_ml.get("prob")
    result["home_ml_decimal"] = home_ml.get("decimal")
    result["home_ml_american"] = home_ml.get("american")
    
    # Spread data
    away_spread = spreads_by_team_id.get(away_team_id) if away_team_id else {}
    home_spread = spreads_by_team_id.get(home_team_id) if home_team_id else {}
    
    result["away_spread"] = away_spread.get("spread")
    result["away_spread_juice"] = away_spread.get("juice")
    result["home_spread"] = home_spread.get("spread")
    result["home_spread_juice"] = home_spread.get("juice")
    
    # Total data
    if total_data:
        result["total"] = total_data.get("total")
        result["over_juice"] = total_data.get("over_juice")
        result["under_juice"] = total_data.get("under_juice")
    else:
        result["total"] = None
        result["over_juice"] = None
        result["under_juice"] = None
    
    return result


def main():
    """Export today's NBA games to DataFrame and CSV."""
    print("Fetching Unabated snapshot...")
    snapshot = fetch_unabated_snapshot()
    teams_dict = snapshot.get("teams", {})
    
    print("Extracting today's NBA games...")
    today_games = extract_nba_games_today(snapshot)
    
    if not today_games:
        print("No NBA games found for today.")
        return
    
    print(f"Found {len(today_games)} game(s)")
    
    # Extract all game data (Unabated only)
    print("Extracting game data...")
    rows = []
    for event in today_games:
        game_data = extract_all_game_data(event, teams_dict)
        if game_data:
            rows.append(game_data)
    
    if not rows:
        print("No valid game data extracted.")
        return
    
    # Create DataFrame
    df = pd.DataFrame(rows)
    
    # Reorder columns
    column_order = [
        "game_date",
        "game_time",
        "away_team",
        "home_team",
        "away_roto",
        "home_roto",
        "away_ml_prob",
        "away_ml_decimal",
        "away_ml_american",
        "home_ml_prob",
        "home_ml_decimal",
        "home_ml_american",
        "away_spread",
        "away_spread_juice",
        "home_spread",
        "home_spread_juice",
        "total",
        "over_juice",
        "under_juice",
    ]
    
    df = df[column_order]
    
    # Print DataFrame
    print("\n" + "=" * 150)
    print("TODAY'S NBA GAMES - UNABATED CONSENSUS")
    print("=" * 150)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', 20)
    print(df.to_string(index=False))
    print("=" * 150)
    
    # Save to CSV. Add a timestamp to the filename.
    csv_filename = f"unabated_nba_games_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(f"ad_hoc/{csv_filename}", index=False)
    print(f"\n✅ Saved to: {csv_filename}")
    print(f"   Total rows: {len(df)}")


if __name__ == "__main__":
    main()
