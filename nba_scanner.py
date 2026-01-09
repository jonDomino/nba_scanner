"""
NBA Moneyline Value Scanner - MVP

Fetches today's NBA games from Unabated, extracts consensus moneyline odds,
maps to Kalshi markets via xref CSV, and computes expected value.
"""

import csv
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List

try:
    from zoneinfo import ZoneInfo
    USE_PYTZ = False
except ImportError:
    import pytz
    USE_PYTZ = True

from core.reusable_functions import (
    fetch_unabated_snapshot,
    fetch_orderbook,
    expected_value
)
from utils.kalshi_api import load_creds
from utils import config
from pricing.conversion import cents_to_american, american_to_cents
from pricing.fees import fee_dollars, maker_fee_cents


# ============================================================================
# Timezone Utilities
# ============================================================================

def get_la_timezone():
    """Get America/Los_Angeles timezone."""
    if USE_PYTZ:
        import pytz
        return pytz.timezone("America/Los_Angeles")
    else:
        return ZoneInfo("America/Los_Angeles")


def utc_to_la_datetime(utc_timestamp: str) -> datetime:
    """
    Convert UTC timestamp string to America/Los_Angeles datetime.
    
    Args:
        utc_timestamp: ISO format UTC timestamp (e.g., "2025-12-15T19:00:00Z")
    
    Returns:
        datetime object in LA timezone
    """
    # Parse UTC timestamp
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
    """
    Check if UTC timestamp corresponds to "today" in America/Los_Angeles timezone.
    
    Args:
        utc_timestamp: ISO format UTC timestamp
    
    Returns:
        True if the date in LA timezone is today
    """
    la_dt = utc_to_la_datetime(utc_timestamp)
    
    # Get today's date in LA timezone
    if USE_PYTZ:
        import pytz
        la_today = datetime.now(pytz.timezone("America/Los_Angeles")).date()
    else:
        la_today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    
    return la_dt.date() == la_today


# ============================================================================
# Unabated Snapshot Parsing
# ============================================================================

def extract_nba_games_today(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Filter Unabated snapshot to NBA games that start "today" in LA timezone.
    
    Args:
        snapshot: Full Unabated API response
    
    Returns:
        List of NBA game dicts with today's date in LA
    """
    games = []
    
    # Handle different possible snapshot structures
    # Common patterns: snapshot["games"], snapshot["data"]["games"], snapshot as list
    game_list = snapshot.get("games", [])
    if not game_list:
        game_list = snapshot.get("data", {}).get("games", [])
    if not game_list and isinstance(snapshot, list):
        game_list = snapshot
    
    for game in game_list:
        # Filter by league (NBA)
        league_id = game.get("leagueId") or game.get("league_id") or game.get("league", {}).get("id")
        if league_id != config.UNABATED_LEAGUE_ID_NBA:
            continue
        
        # Get event start time (UTC)
        event_start = game.get("eventStart") or game.get("event_start") or game.get("startTime") or game.get("start_time")
        if not event_start:
            continue
        
        # Check if game is today in LA timezone
        if not is_today_la(event_start):
            continue
        
        games.append(game)
    
    return games


def extract_consensus_moneyline(game: Dict[str, Any]) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    Extract consensus moneyline odds for both teams from Unabated game data.
    
    Args:
        game: Unabated game dict
    
    Returns:
        Dict mapping team name/id to moneyline odds dict:
        {
            "team_a": {"name": str, "odds": int},  # American odds
            "team_b": {"name": str, "odds": int}
        }
        Returns None if moneyline not found
    """
    # Look for markets array
    markets = game.get("markets", []) or game.get("market", [])
    
    # Find moneyline market
    ml_market = None
    for market in markets:
        market_type = market.get("marketType") or market.get("market_type") or market.get("type")
        if market_type in ["MONEYLINE", "moneyline", "ML", "ml"]:
            ml_market = market
            break
    
    if not ml_market:
        return None
    
    # Extract consensus odds
    # Common patterns: consensusOdds, consensus, lines, outcomes
    outcomes = ml_market.get("outcomes", []) or ml_market.get("lines", [])
    if len(outcomes) < 2:
        return None
    
    # Get team names and consensus odds
    result = {}
    for outcome in outcomes[:2]:  # First two outcomes should be the teams
        team_name = outcome.get("name") or outcome.get("team") or outcome.get("teamName") or outcome.get("team_name")
        consensus_odds = outcome.get("consensusOdds") or outcome.get("consensus") or outcome.get("consensusOddsAmerican") or outcome.get("americanOdds")
        
        # Try alternate paths
        if consensus_odds is None:
            consensus = outcome.get("consensus", {})
            if isinstance(consensus, dict):
                consensus_odds = consensus.get("americanOdds") or consensus.get("odds") or consensus.get("american")
        
        if team_name and consensus_odds is not None:
            # Ensure odds is integer
            try:
                odds_int = int(consensus_odds)
                result[team_name] = {"name": team_name, "odds": odds_int}
            except (ValueError, TypeError):
                continue
    
    if len(result) < 2:
        return None
    
    # Return as team_a and team_b (order doesn't matter for our use case)
    team_keys = list(result.keys())
    return {
        "team_a": result[team_keys[0]],
        "team_b": result[team_keys[1]]
    }


# ============================================================================
# Xref CSV Loading
# ============================================================================

def load_nba_xref(path: str = None) -> Dict[str, Dict[str, Any]]:
    """
    Load NBA xref CSV mapping Unabated game/team → Kalshi market ticker.
    
    Expected CSV format:
    unabated_game_id,team_name_unabated,kalshi_market_ticker
    
    Where unabated_game_id and team_name_unabated together identify a unique team in a game.
    
    Args:
        path: Path to xref CSV (defaults to config.NBA_XREF_FILE)
    
    Returns:
        Dict mapping (game_id, team_name) -> {"kalshi_ticker": str, ...}
    """
    if path is None:
        path = config.NBA_XREF_FILE
    
    xref = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                game_id = row.get("unabated_game_id", "").strip()
                team_name = row.get("team_name_unabated", "").strip()
                kalshi_ticker = row.get("kalshi_market_ticker", "").strip()
                
                if game_id and team_name and kalshi_ticker:
                    key = (game_id, team_name.lower())  # Case-insensitive team matching
                    xref[key] = {
                        "kalshi_ticker": kalshi_ticker,
                        "team_name": team_name  # Preserve original case
                    }
    except FileNotFoundError:
        print(f"⚠️  Xref file not found: {path}")
    except Exception as e:
        print(f"❌ Error loading xref: {e}")
    
    return xref


def get_game_id_from_unabated(game: Dict[str, Any]) -> Optional[str]:
    """
    Extract stable game ID from Unabated game dict.
    
    Tries multiple possible field names.
    """
    return (
        game.get("id") or
        game.get("gameId") or
        game.get("game_id") or
        game.get("eventId") or
        game.get("event_id")
    )


# ============================================================================
# Orderbook Utilities
# ============================================================================

def get_yes_ask_prices(orderbook: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """
    Get best YES ask and ask-1¢ from orderbook.
    
    Formula: best_yes_ask = 100 - max(no_bids.price)
    (Do not assume ordering; explicitly find max)
    
    Args:
        orderbook: Kalshi orderbook dict with "yes" and "no" bid arrays
    
    Returns:
        (best_ask_cents, inside_ask_cents | None)
        - best_ask_cents: Best YES ask (derived from NO bids) or None if no liquidity
        - inside_ask_cents: ask-1¢ if best_ask >= 2, else None
    """
    no_bids = orderbook.get("no") or []
    
    if not no_bids:
        return (None, None)  # No liquidity
    
    # Find max NO bid price (best NO bid = highest price)
    # Note: orderbook["no"] contains [price_cents, qty] pairs
    max_no_bid_price = None
    for bid in no_bids:
        if isinstance(bid, list) and len(bid) >= 1:
            price = bid[0]
            if max_no_bid_price is None or price > max_no_bid_price:
                max_no_bid_price = price
    
    if max_no_bid_price is None:
        return (None, None)
    
    # Best YES ask = 100 - max(NO bid price)
    best_ask_cents = 100 - max_no_bid_price
    
    # Ask-1¢ (with tick floor)
    ask_inside_cents = best_ask_cents - 1 if best_ask_cents >= 2 else None
    
    return (best_ask_cents, ask_inside_cents)


# ============================================================================
# EV Calculation
# ============================================================================

def calculate_ev_scenario(win_prob: float, price_cents: int, scenario: str) -> float:
    """
    Calculate EV per contract for a scenario.
    
    Args:
        win_prob: Win probability (implied from Unabated odds)
        price_cents: Price in cents (ask for taker, ask-1¢ for maker)
        scenario: "take_ask" (taker fee) or "post_inside_maker" (maker fee)
    
    Returns:
        EV in dollars per contract
    """
    if scenario == "take_ask":
        # Taker fee: convert to cents
        taker_fee_dollars = fee_dollars(1, price_cents)
        fee_on_win_cents = int(round(taker_fee_dollars * 100.0))
        return expected_value(win_prob, price_cents, fee_on_win_cents)
    
    elif scenario == "post_inside_maker":
        # Maker fee: already in cents
        fee_on_win_cents = maker_fee_cents(price_cents, 1)
        return expected_value(win_prob, price_cents, fee_on_win_cents)
    
    else:
        raise ValueError(f"Invalid scenario: {scenario}")


# ============================================================================
# Main Scanner
# ============================================================================

def scan_nba_moneylines(xref_path: str = None) -> List[Dict[str, Any]]:
    """
    Main MVP scanner: fetch Unabated snapshot, filter to today's NBA games,
    extract consensus ML odds, map to Kalshi via xref, fetch orderbooks, compute EVs.
    
    Args:
        xref_path: Path to xref CSV (defaults to config.NBA_XREF_FILE)
    
    Returns:
        List of +EV markets, each with:
        {
            "ticker": str,
            "team": str,
            "ev_if_take_best_ask": float,
            "ev_if_post_ask_minus_1c_and_get_maker_fill": Optional[float],
            "win_prob": float,
            "ask_cents": Optional[int],
            "ask_inside_cents": Optional[int],
            "ask_odds": Optional[int],  # American odds for display
            "inside_odds": Optional[int]  # American odds for display
        }
    """
    # Load xref
    xref = load_nba_xref(xref_path)
    if not xref:
        print("⚠️  No xref entries loaded. Please create nba_xref.csv")
        return []
    
    # Fetch Unabated snapshot
    print("📡 Fetching Unabated snapshot...")
    try:
        snapshot = fetch_unabated_snapshot()
    except Exception as e:
        print(f"❌ Failed to fetch Unabated snapshot: {e}")
        return []
    
    # Filter to today's NBA games
    print("🔍 Filtering to today's NBA games...")
    today_games = extract_nba_games_today(snapshot)
    print(f"   Found {len(today_games)} NBA game(s) today")
    
    if not today_games:
        print("⚠️  No NBA games found for today")
        return []
    
    # Load Kalshi credentials
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        print(f"❌ Failed to load Kalshi credentials: {e}")
        return []
    
    results = []
    
    # Process each game
    for game in today_games:
        game_id = get_game_id_from_unabated(game)
        if not game_id:
            continue
        
        # Extract consensus moneyline
        ml_data = extract_consensus_moneyline(game)
        if not ml_data:
            continue
        
        # Process both teams
        for team_key in ["team_a", "team_b"]:
            team_data = ml_data[team_key]
            team_name = team_data["name"]
            consensus_odds = team_data["odds"]
            
            # Look up Kalshi ticker in xref
            xref_key = (game_id, team_name.lower())
            xref_entry = xref.get(xref_key)
            
            if not xref_entry:
                # Try alternate matching (maybe team name differs slightly)
                # Skip for now - xref must match exactly
                continue
            
            kalshi_ticker = xref_entry["kalshi_ticker"]
            
            # Convert Unabated odds to implied probability (no devigging in MVP)
            win_prob = american_to_cents(consensus_odds) / 100.0
            
            # Fetch orderbook
            orderbook = fetch_orderbook(api_key_id, private_key_pem, kalshi_ticker)
            if not orderbook:
                continue  # Skip if no orderbook
            
            # Get ask prices
            best_ask, ask_inside = get_yes_ask_prices(orderbook)
            if best_ask is None:
                continue  # Skip if no liquidity
            
            # Calculate EVs
            ev_take = calculate_ev_scenario(win_prob, best_ask, "take_ask")
            ev_inside = None
            if ask_inside is not None:
                ev_inside = calculate_ev_scenario(win_prob, ask_inside, "post_inside_maker")
            
            # Only include +EV markets
            if ev_take > 0 or (ev_inside is not None and ev_inside > 0):
                results.append({
                    "ticker": kalshi_ticker,
                    "team": xref_entry["team_name"],
                    "ev_if_take_best_ask": ev_take,
                    "ev_if_post_ask_minus_1c_and_get_maker_fill": ev_inside,
                    "win_prob": win_prob,
                    "ask_cents": best_ask,
                    "ask_inside_cents": ask_inside,
                    "ask_odds": cents_to_american(best_ask),
                    "inside_odds": cents_to_american(ask_inside) if ask_inside else None
                })
    
    # Sort by best EV (highest first)
    results.sort(
        key=lambda x: max(
            x["ev_if_take_best_ask"],
            x["ev_if_post_ask_minus_1c_and_get_maker_fill"] or -999
        ),
        reverse=True
    )
    
    return results


# ============================================================================
# Table Printing
# ============================================================================

def print_value_table(results: List[Dict[str, Any]]) -> None:
    """
    Print ranked table of +EV markets.
    
    Columns: Rank | Ticker | Team | EV@ask | EV@inside | Prob% | Ask | Inside
    - EV units: dollars per contract
    - Odds displayed: Kalshi price equivalents (American odds)
    """
    if not results:
        print("\n⚠️  No +EV markets found.")
        return
    
    print(f"\n{'='*100}")
    print(f"NBA Moneyline Value (Top {len(results)} +EV Markets)")
    print(f"{'='*100}")
    print(f"{'Rank':<6} {'Ticker':<30} {'Team':<25} {'EV@ask':<12} {'EV@inside':<12} {'Prob%':<8} {'Ask':<10} {'Inside':<10}")
    print(f"{'-'*100}")
    
    for i, m in enumerate(results, 1):
        ask_str = f"{m['ask_odds']}" if m['ask_odds'] else "N/A"
        inside_str = f"{m['inside_odds']}" if m['inside_odds'] else "N/A"
        ev_inside_str = f"{m['ev_if_post_ask_minus_1c_and_get_maker_fill']:.4f}" if m['ev_if_post_ask_minus_1c_and_get_maker_fill'] is not None else "N/A"
        
        # Truncate long tickers/teams for display
        ticker_display = m['ticker'][-30:] if len(m['ticker']) > 30 else m['ticker']
        team_display = m['team'][:25] if len(m['team']) <= 25 else m['team'][:22] + "..."
        
        print(
            f"{i:<6} {ticker_display:<30} {team_display:<25} "
            f"{m['ev_if_take_best_ask']:>+.4f}    {ev_inside_str:>12} "
            f"{m['win_prob']*100:>5.1f}%  {ask_str:>10} {inside_str:>10}"
        )
    
    print(f"{'='*100}")


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    results = scan_nba_moneylines()
    print_value_table(results)
