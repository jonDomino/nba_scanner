"""
NBA Spreads Dashboard: Today's NBA games with Unabated spreads vs Kalshi spread markets.

This module is completely separate from the moneylines dashboard and does not modify any
existing functionality. It reuses existing utilities but does not change their behavior.

For each game:
- Extract Unabated canonical spread for team POV
- Discover Kalshi spread markets for the event
- Select the 2 closest strikes to Unabated canonical spread
- Emit 2 rows (one per strike) with duplicated game metadata

Internal plumbing: NO-space for spreads (same convention as moneylines).
User-facing: Display "price to get exposure to Team X covering/winning by over Y".
"""

import re
from typing import Dict, Any, List, Optional, Tuple
from decimal import Decimal

from nba_todays_fairs import get_today_games_with_fairs, utc_to_la_datetime, get_team_name
from nba_today_xref_tickers import get_today_games_with_fairs_and_kalshi_tickers
from core.reusable_functions import (
    fetch_kalshi_markets_for_event,
    fetch_orderbook,
    load_team_xref,
    team_to_kalshi_code
)
from kalshi_top_of_book_probs import (
    get_yes_bid_top_and_liquidity,
    yes_break_even_prob
)
from utils import config
from utils.kalshi_api import load_creds

# Debug flag
DEBUG_SPREADS = True


def extract_unabated_spreads(event: Dict[str, Any], teams: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """
    Extract Unabated spread lines keyed by team_id from ms49.
    
    Similar structure to moneylines but extracts spread (bt2 or similar) instead of moneyline (bt1).
    
    Returns:
        Dict mapping team_id -> dict with:
        - spread: float (e.g., -2.5, +3.5)
        - juice: int|None (American odds, e.g., -107, +110)
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
    
    # Store spreads by team_id
    spreads_by_team_id = {}
    
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
        
        # Get bt2 line from this ms49 block (spread, bt1 is moneyline)
        bt2_line = ms49_block.get("bt2")
        if bt2_line is None or not isinstance(bt2_line, dict):
            # Try other possible keys for spread
            bt2_line = ms49_block.get("spread") or ms49_block.get("spreadLine")
            if not isinstance(bt2_line, dict):
                continue
        
        # Get spread value
        spread_raw = (
            bt2_line.get("line") or
            bt2_line.get("spread") or
            bt2_line.get("value") or
            bt2_line.get("points")
        )
        
        if spread_raw is None:
            continue
        
        # Convert to float safely
        try:
            if isinstance(spread_raw, str):
                spread = float(spread_raw.strip())
            else:
                spread = float(spread_raw)
        except (ValueError, TypeError):
            continue
        
        # Get juice (American odds) if available
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
    
    return spreads_by_team_id


def discover_kalshi_spread_markets(event_ticker: str) -> List[Dict[str, Any]]:
    """
    Discover Kalshi spread markets for an event ticker.
    
    IMPORTANT: Spreads are in KXNBASPREAD series, not KXNBAGAME series.
    This function converts the KXNBAGAME event ticker to KXNBASPREAD event ticker.
    
    Filters markets by checking:
    - title contains "wins by over" and "points"
    - Or market_type indicates spread
    
    Returns:
        List of market dicts, each with:
        - ticker: market ticker
        - title: market title
        - parsed_strike: float strike value (e.g., 6.5)
        - anchor_team_token: team name/code from title
    """
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        if DEBUG_SPREADS:
            print(f"❌ Failed to load Kalshi credentials: {e}")
        return []
    
    # Convert KXNBAGAME event ticker to KXNBASPREAD event ticker
    # Example: KXNBAGAME-26JAN09MILLAL -> KXNBASPREAD-26JAN09MILLAL
    spread_event_ticker = event_ticker.replace("KXNBAGAME-", "KXNBASPREAD-", 1)
    
    if DEBUG_SPREADS:
        print(f"  Converting event ticker: {event_ticker} -> {spread_event_ticker}")
    
    # Fetch all markets for spread event (KXNBASPREAD series)
    markets = fetch_kalshi_markets_for_event(api_key_id, private_key_pem, spread_event_ticker)
    
    if DEBUG_SPREADS:
        print(f"  Fetched {len(markets) if markets else 0} market(s) from {spread_event_ticker}")
    
    if not markets:
        if DEBUG_SPREADS:
            print(f"  ⚠️ No markets found for spread event {spread_event_ticker}")
        return []
    
    spread_markets = []
    
    for market in markets:
        if not isinstance(market, dict):
            continue
        
        # Get market title
        title = (
            market.get("title") or
            market.get("market_title") or
            market.get("name") or
            ""
        ).lower()
        
        # Check if it's a spread market by title patterns
        is_spread = False
        
        # Pattern: "wins by over" or "wins by" + "points"
        if ("wins by over" in title or "wins by" in title) and "points" in title:
            is_spread = True
        
        # Also check market_type if available
        market_type = (
            market.get("market_type") or
            market.get("marketType") or
            market.get("type") or
            ""
        ).lower()
        
        if market_type in ["spread", "point spread", "ps"]:
            is_spread = True
        
        if not is_spread:
            continue
        
        # Parse strike from title
        # Pattern examples:
        # "Team wins by over 6.5 points"
        # "Team wins by over 6 points"
        strike = None
        anchor_team_token = None
        
        # Extract strike (number before "points")
        strike_match = re.search(r'over\s+([\d.]+)\s+points?', title, re.IGNORECASE)
        if strike_match:
            try:
                strike = float(strike_match.group(1))
            except (ValueError, AttributeError):
                pass
        
        # Extract team name/code from title
        # Usually format: "{Team} wins by over..."
        team_match = re.match(r'^([a-z\s]+?)\s+wins\s+by', title, re.IGNORECASE)
        if team_match:
            anchor_team_token = team_match.group(1).strip()
        
        if strike is None:
            if DEBUG_SPREADS:
                print(f"⚠️ Could not parse strike from title: {title}")
            continue
        
        spread_markets.append({
            "ticker": market.get("ticker") or market.get("market_ticker"),
            "title": market.get("title") or market.get("market_title") or title,
            "parsed_strike": strike,
            "anchor_team_token": anchor_team_token
        })
    
    return spread_markets


def match_spread_markets_to_teams(
    spread_markets: List[Dict[str, Any]],
    away_team_name: str,
    home_team_name: str,
    xref: Dict[Tuple[str, str], str]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Match spread markets to away/home teams by parsing titles.
    
    Handles name variations (e.g., LA vs Los Angeles, full names vs codes).
    
    Returns:
        Dict with keys:
        - "away_markets": List of markets anchored to away team
        - "home_markets": List of markets anchored to home team
        - "unmatched_markets": List of markets that couldn't be matched
    """
    result = {
        "away_markets": [],
        "home_markets": [],
        "unmatched_markets": []
    }
    
    # Build name variations for matching
    away_variations = _build_team_name_variations(away_team_name)
    home_variations = _build_team_name_variations(home_team_name)
    
    # Also get Kalshi codes
    away_code = team_to_kalshi_code("NBA", away_team_name, xref)
    home_code = team_to_kalshi_code("NBA", home_team_name, xref)
    
    if away_code:
        away_variations.append(away_code.lower())
        away_variations.append(away_code)
    
    if home_code:
        home_variations.append(home_code.lower())
        home_variations.append(home_code)
    
    for market in spread_markets:
        anchor_token = (market.get("anchor_team_token") or "").lower().strip()
        
        if not anchor_token:
            result["unmatched_markets"].append(market)
            continue
        
        # Try to match to away team
        matched_away = any(
            var in anchor_token or anchor_token in var
            for var in away_variations
            if var
        )
        
        # Try to match to home team
        matched_home = any(
            var in anchor_token or anchor_token in var
            for var in home_variations
            if var
        )
        
        if matched_away and not matched_home:
            result["away_markets"].append(market)
        elif matched_home and not matched_away:
            result["home_markets"].append(market)
        elif matched_away and matched_home:
            # Ambiguous - prefer longer match
            if len(max(away_variations, key=len)) >= len(max(home_variations, key=len)):
                result["away_markets"].append(market)
            else:
                result["home_markets"].append(market)
        else:
            result["unmatched_markets"].append(market)
    
    return result


def _build_team_name_variations(team_name: str) -> List[str]:
    """Build variations of team name for matching."""
    variations = []
    
    if not team_name:
        return variations
    
    base = team_name.lower().strip()
    variations.append(base)
    
    # Add common variations
    if "los angeles" in base:
        variations.append(base.replace("los angeles", "la"))
        variations.append("la " + base.split()[-1])  # e.g., "la lakers"
    
    if "new york" in base:
        variations.append(base.replace("new york", "ny"))
    
    # Add last word only (e.g., "Lakers", "Celtics")
    words = base.split()
    if len(words) > 1:
        variations.append(words[-1])
    
    return variations


def determine_pov_team(
    away_spread: Optional[float],
    home_spread: Optional[float],
    away_markets: List[Dict[str, Any]],
    home_markets: List[Dict[str, Any]]
) -> Tuple[Optional[str], Optional[float]]:
    """
    Determine the POV team for strike selection.
    
    Logic:
    1. If one side has many more markets, use that
    2. Otherwise, use the team that matches Unabated favorite (negative spread)
    
    Returns:
        Tuple of (pov_team, pov_spread) where pov_team is "away" or "home"
    """
    # Count markets per side
    away_count = len(away_markets)
    home_count = len(home_markets)
    
    # If one side has significantly more markets, use that
    if away_count >= 2 * home_count:
        if DEBUG_SPREADS:
            print(f"  → POV: away (has {away_count} markets vs {home_count})")
        return ("away", away_spread)
    elif home_count >= 2 * away_count:
        if DEBUG_SPREADS:
            print(f"  → POV: home (has {home_count} markets vs {away_count})")
        return ("home", home_spread)
    
    # Otherwise, use Unabated favorite (negative spread = favorite)
    if away_spread is not None and away_spread < 0:
        if DEBUG_SPREADS:
            print(f"  → POV: away (Unabated favorite, spread={away_spread})")
        return ("away", away_spread)
    elif home_spread is not None and home_spread < 0:
        if DEBUG_SPREADS:
            print(f"  → POV: home (Unabated favorite, spread={home_spread})")
        return ("home", home_spread)
    
    # Default to away if we have a spread
    if away_spread is not None:
        if DEBUG_SPREADS:
            print(f"  → POV: away (default, spread={away_spread})")
        return ("away", away_spread)
    elif home_spread is not None:
        if DEBUG_SPREADS:
            print(f"  → POV: home (default, spread={home_spread})")
        return ("home", home_spread)
    
    return (None, None)


def select_closest_strikes(
    canonical_spread: float,
    available_markets: List[Dict[str, Any]],
    count: int = 2
) -> List[Dict[str, Any]]:
    """
    Select the N closest strikes to canonical spread.
    
    Args:
        canonical_spread: Unabated canonical spread (e.g., -2.5)
        available_markets: List of market dicts with "parsed_strike" key
        count: Number of strikes to select (default 2)
    
    Returns:
        List of selected market dicts, sorted by distance to canonical
    """
    if not available_markets:
        return []
    
    # Calculate absolute value of canonical spread for distance calculation
    S = abs(canonical_spread)
    
    # Calculate distance for each market
    markets_with_distance = []
    for market in available_markets:
        strike = market.get("parsed_strike")
        if strike is None:
            continue
        
        distance = abs(strike - S)
        markets_with_distance.append((distance, strike, market))
    
    if not markets_with_distance:
        return []
    
    # Sort by distance (closest first), then by strike (lower first for tie-break)
    markets_with_distance.sort(key=lambda x: (x[0], x[1]))
    
    # Select top N
    selected = [market for _, _, market in markets_with_distance[:count]]
    
    return selected


def get_no_bid_top_and_liquidity(orderbook: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Dict[int, int]]:
    """
    Extract top NO bid price and its liquidity from orderbook.
    
    Similar to get_yes_bid_top_and_liquidity but for NO side.
    
    Args:
        orderbook: Kalshi orderbook dict with "no" bid array (format: [[price_cents, qty], ...])
    
    Returns:
        (no_bid_top_c, no_bid_top_liq, no_bids_by_price_dict)
        - no_bid_top_c: Maximum NO bid price in cents, or None
        - no_bid_top_liq: Total liquidity (quantity) at top NO bid price, or None
        - no_bids_by_price_dict: Dict mapping price -> total quantity for all NO bid levels
    """
    no_bids = orderbook.get("no") or []
    
    if not no_bids or not isinstance(no_bids, list):
        return (None, None, {})
    
    # Find max NO bid price and accumulate quantities by price
    no_bid_top_c = None
    no_bids_by_price = {}
    
    for bid in no_bids:
        if isinstance(bid, list) and len(bid) >= 2:
            price_cents = int(bid[0])
            qty = int(bid[1])
            
            # Track max price
            if no_bid_top_c is None or price_cents > no_bid_top_c:
                no_bid_top_c = price_cents
            
            # Accumulate quantities by price (in case multiple entries at same price)
            if price_cents in no_bids_by_price:
                no_bids_by_price[price_cents] += qty
            else:
                no_bids_by_price[price_cents] = qty
    
    # Get liquidity at top price
    no_bid_top_liq = no_bids_by_price.get(no_bid_top_c, 0) if no_bid_top_c is not None else None
    
    return (no_bid_top_c, no_bid_top_liq, no_bids_by_price)


def get_spread_orderbook_data(market_ticker: str) -> Dict[str, Any]:
    """
    Fetch orderbook and compute TOB for both YES and NO sides of a spread market.
    
    For spreads, the same market has both YES (POV team covers) and NO (opponent covers) sides.
    
    Returns:
        Dict with keys:
        - tob_effective_prob: YES bid break-even probability (after fees) - for POV team
        - tob_liq: YES bid liquidity - for POV team
        - no_tob_effective_prob: NO bid break-even probability (after fees) - for opponent team
        - no_tob_liq: NO bid liquidity - for opponent team
        - tob_p1_effective_prob: YES bid+1c break-even probability (after fees)
        - tob_p1_liq: YES bid+1c liquidity (None if crossed or doesn't exist)
        - crossed: Boolean indicating if +1c would cross
    """
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception:
        return {
            "tob_effective_prob": None,
            "tob_liq": None,
            "no_tob_effective_prob": None,
            "no_tob_liq": None,
            "tob_p1_effective_prob": None,
            "tob_p1_liq": None,
            "crossed": None
        }
    
    orderbook = fetch_orderbook(api_key_id, private_key_pem, market_ticker)
    
    if not orderbook:
        return {
            "tob_effective_prob": None,
            "tob_liq": None,
            "no_tob_effective_prob": None,
            "no_tob_liq": None,
            "tob_p1_effective_prob": None,
            "tob_p1_liq": None,
            "crossed": None
        }
    
    # Get YES bid top and liquidity (for POV team)
    yes_bid_top_c, yes_bid_top_liq, yes_bids_by_price = get_yes_bid_top_and_liquidity(orderbook)
    
    # Calculate break-even probability at YES TOB (after maker fees)
    tob_effective_prob = yes_break_even_prob(yes_bid_top_c) if yes_bid_top_c is not None else None
    
    # Get NO bid top and liquidity (for opponent team)
    no_bid_top_c, no_bid_top_liq, no_bids_by_price = get_no_bid_top_and_liquidity(orderbook)
    
    # Calculate break-even probability at NO TOB (after maker fees)
    # NO bid represents betting against the POV team, so we use the NO bid price directly
    no_tob_effective_prob = yes_break_even_prob(no_bid_top_c) if no_bid_top_c is not None else None
    
    # Get YES ask top (from NO bids) for crossing check
    yes_ask_top_c = None
    if no_bid_top_c is not None:
        yes_ask_top_c = 100 - no_bid_top_c
    
    # Calculate TOB+1c for YES side
    yes_bid_top_p1_c = yes_bid_top_c + 1 if yes_bid_top_c is not None and yes_bid_top_c < 99 else None
    crossed = False
    
    if yes_bid_top_p1_c is not None and yes_ask_top_c is not None:
        if yes_bid_top_p1_c >= yes_ask_top_c:
            crossed = True
            yes_bid_top_p1_c = None
    
    # Calculate break-even probability at YES TOB+1c if valid
    tob_p1_effective_prob = None
    tob_p1_liq = None
    
    if yes_bid_top_p1_c is not None:
        tob_p1_effective_prob = yes_break_even_prob(yes_bid_top_p1_c)
        # Note: +1c is theoretical, so liquidity is None (or 0)
        tob_p1_liq = None
    
    return {
        "tob_effective_prob": tob_effective_prob,  # YES bid (POV team)
        "tob_liq": yes_bid_top_liq,
        "no_tob_effective_prob": no_tob_effective_prob,  # NO bid (opponent team)
        "no_tob_liq": no_bid_top_liq,
        "tob_p1_effective_prob": tob_p1_effective_prob,
        "tob_p1_liq": tob_p1_liq,
        "crossed": crossed
    }


def format_strike_string(
    team_code: str,
    spread: float,
    strike: float
) -> str:
    """
    Format strike string like "NOP -2.5" or "WAS +6.5".
    
    Args:
        team_code: 3-letter Kalshi code (e.g., "NOP")
        spread: Unabated canonical spread (e.g., -2.5)
        strike: Selected Kalshi strike (e.g., 6.5)
    
    Returns:
        Formatted string like "NOP -6.5" or "WAS +6.5"
    """
    # If Unabated has POV team favored (negative spread), strike is negative
    # If Unabated has POV team as dog (positive spread), strike is positive
    if spread < 0:
        return f"{team_code} -{strike}"
    else:
        return f"{team_code} +{strike}"


def format_consensus_string(
    team_code: str,
    spread: float,
    juice: Optional[int] = None
) -> str:
    """
    Format consensus spread string like "PHI -3" or "PHI -3 -107".
    
    Args:
        team_code: 3-letter Kalshi code (e.g., "PHI")
        spread: Unabated canonical spread (e.g., -3.0)
        juice: Optional American odds (e.g., -107)
    
    Returns:
        Formatted string like "PHI -3" or "PHI -3 -107"
    """
    # Format spread (remove .0 if whole number)
    if spread == int(spread):
        spread_str = f"{int(spread):+d}"  # +d includes sign
    else:
        spread_str = f"{spread:+.1f}"  # +.1f includes sign and one decimal
    
    # Format juice if available
    if juice is not None:
        return f"{team_code} {spread_str} {juice:+d}"
    else:
        return f"{team_code} {spread_str}"


def build_spreads_rows_for_today() -> List[Dict[str, Any]]:
    """
    Build spreads rows for today's NBA games.
    
    Returns:
        List of spread row dicts, each with:
        - All game metadata (date, time, roto, teams, fairs)
        - strike: Formatted strike string
        - pov_team: "away" or "home"
        - kalshi_ticker: Market ticker for this strike
        - kalshi_title: Market title
        - unabated_spread: Unabated canonical spread for POV team
        - tob_effective_prob: Top-of-book break-even prob (after fees)
        - tob_liq: Top-of-book liquidity
        - tob_p1_effective_prob: Top-of-book+1c break-even prob (after fees)
        - tob_p1_liq: Top-of-book+1c liquidity (None if theoretical)
        - crossed: Boolean if +1c would cross
    """
    # Get today's games with all metadata
    games = get_today_games_with_fairs_and_kalshi_tickers()
    
    if not games:
        if DEBUG_SPREADS:
            print("No NBA games found for today")
        return []
    
    # Load team xref
    xref_path = config.NBA_XREF_FILE
    xref = load_team_xref(xref_path)
    
    # Load Kalshi credentials
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        if DEBUG_SPREADS:
            print(f"❌ Failed to load Kalshi credentials: {e}")
        return []
    
    # Get Unabated snapshot for spread extraction
    from core.reusable_functions import fetch_unabated_snapshot
    snapshot = fetch_unabated_snapshot()
    teams_dict = snapshot.get("teams", {})
    
    # Get today's games with spreads
    from nba_todays_fairs import extract_nba_games_today
    today_events = extract_nba_games_today(snapshot)
    
    # Build event lookup by event_start
    events_by_start = {event.get("eventStart"): event for event in today_events}
    
    spread_rows = []
    
    for game in games:
        event_start = game.get("event_start")
        if not event_start:
            continue
        
        # Get Unabated event for spread extraction
        unabated_event = events_by_start.get(event_start)
        if not unabated_event:
            if DEBUG_SPREADS:
                print(f"⚠️ Could not find Unabated event for {event_start}")
            continue
        
        # Extract spreads by team_id
        spreads_by_team_id = extract_unabated_spreads(unabated_event, teams_dict)
        
        # Get away/home team names directly from game (already determined by moneylines module)
        away_team_name = game.get("away_team_name")
        home_team_name = game.get("home_team_name")
        
        # Get event ticker (already included by moneylines module)
        event_ticker = game.get("event_ticker")
        
        if not away_team_name or not home_team_name:
            if DEBUG_SPREADS:
                print(f"⚠️ Could not determine away/home teams for game")
            continue
        
        # Get team IDs by matching names to Unabated event
        away_team_id = None
        home_team_id = None
        
        event_teams = unabated_event.get("eventTeams", {})
        teams_by_id = {}
        
        # Build teams_by_id from event_teams
        if isinstance(event_teams, dict):
            for idx, team_info in event_teams.items():
                if isinstance(team_info, dict):
                    team_id = team_info.get("id")
                    team_name = get_team_name(team_id, teams_dict) if team_id else None
                    if team_id and team_name:
                        teams_by_id[team_id] = team_name
                        # Match to away/home names
                        if team_name == away_team_name:
                            away_team_id = team_id
                        elif team_name == home_team_name:
                            home_team_id = team_id
        
        # Get spreads for away/home (now returns dict with spread and juice)
        away_spread_data = spreads_by_team_id.get(away_team_id) if away_team_id else None
        home_spread_data = spreads_by_team_id.get(home_team_id) if home_team_id else None
        
        # Extract spread values (backward compatibility)
        away_spread = away_spread_data.get("spread") if isinstance(away_spread_data, dict) else (away_spread_data if away_spread_data is not None else None)
        home_spread = home_spread_data.get("spread") if isinstance(home_spread_data, dict) else (home_spread_data if home_spread_data is not None else None)
        
        # Extract juice
        away_juice = away_spread_data.get("juice") if isinstance(away_spread_data, dict) else None
        home_juice = home_spread_data.get("juice") if isinstance(home_spread_data, dict) else None
        
        if DEBUG_SPREADS:
            print(f"\n{'='*60}")
            print(f"Game: {away_team_name} @ {home_team_name}")
            print(f"  Away spread (Unabated): {away_spread} (juice: {away_juice})")
            print(f"  Home spread (Unabated): {home_spread} (juice: {home_juice})")
        
        # Discover Kalshi spread markets
        if not event_ticker:
            if DEBUG_SPREADS:
                print(f"  ⚠️ No event ticker, skipping")
            continue
        
        spread_markets = discover_kalshi_spread_markets(event_ticker)
        
        if DEBUG_SPREADS:
            print(f"  Found {len(spread_markets)} spread market(s)")
        
        if not spread_markets:
            continue
        
        # Match markets to teams
        matched = match_spread_markets_to_teams(
            spread_markets,
            away_team_name,
            home_team_name,
            xref
        )
        
        away_markets = matched["away_markets"]
        home_markets = matched["home_markets"]
        unmatched = matched["unmatched_markets"]
        
        if DEBUG_SPREADS:
            print(f"  Away markets: {len(away_markets)}")
            print(f"  Home markets: {len(home_markets)}")
            if unmatched:
                print(f"  Unmatched markets: {len(unmatched)}")
        
        # Determine POV team
        pov_team, pov_spread = determine_pov_team(
            away_spread,
            home_spread,
            away_markets,
            home_markets
        )
        
        if not pov_team or pov_spread is None:
            if DEBUG_SPREADS:
                print(f"  ⚠️ Could not determine POV team, skipping")
            continue
        
        # Get POV team code and consensus spread data
        pov_team_name = away_team_name if pov_team == "away" else home_team_name
        pov_team_code = team_to_kalshi_code("NBA", pov_team_name, xref)
        
        if not pov_team_code:
            if DEBUG_SPREADS:
                print(f"  ⚠️ Could not get Kalshi code for POV team {pov_team_name}")
            continue
        
        # Get consensus spread data for POV team
        pov_spread_data = away_spread_data if pov_team == "away" else home_spread_data
        pov_juice = away_juice if pov_team == "away" else home_juice
        
        # Select markets for POV team
        pov_markets = away_markets if pov_team == "away" else home_markets
        
        if not pov_markets:
            if DEBUG_SPREADS:
                print(f"  ⚠️ No markets for POV team ({pov_team}), skipping")
            continue
        
        # Select 2 closest strikes
        selected_markets = select_closest_strikes(pov_spread, pov_markets, count=2)
        
        if DEBUG_SPREADS:
            S = abs(pov_spread)
            print(f"  Canonical spread: {pov_spread} (abs={S})")
            print(f"  Selected {len(selected_markets)} strike(s):")
            for market in selected_markets:
                print(f"    - Strike {market['parsed_strike']}: {market['ticker']}")
        
        if not selected_markets:
            continue
        
        # Get opponent team info for finding opposite markets
        opponent_team_name = home_team_name if pov_team == "away" else away_team_name
        opponent_team_code = team_to_kalshi_code("NBA", opponent_team_name, xref)
        opponent_markets = home_markets if pov_team == "away" else away_markets
        
        # Build rows for each selected strike
        for market in selected_markets:
            market_ticker = market.get("ticker")
            if not market_ticker:
                continue
            
            strike = market.get("parsed_strike")
            if strike is None:
                continue
            
            # Get orderbook data for POV team's market (contains both YES and NO sides)
            orderbook_data = get_spread_orderbook_data(market_ticker)
            
            if DEBUG_SPREADS and market == selected_markets[0]:
                # Debug print for first strike
                print(f"  Orderbook for market ({market_ticker}):")
                print(f"    YES TOB (POV team): {orderbook_data['tob_effective_prob']} (liq: {orderbook_data['tob_liq']})")
                print(f"    NO TOB (opponent): {orderbook_data['no_tob_effective_prob']} (liq: {orderbook_data['no_tob_liq']})")
            
            # Format strike string
            strike_str = format_strike_string(pov_team_code, pov_spread, strike)
            
            # Format consensus string
            consensus_str = format_consensus_string(pov_team_code, pov_spread, pov_juice) if pov_team_code else "N/A"
            
            # Determine Away/Home Kalshi values based on pov_team
            # YES side = POV team, NO side = opponent team
            if pov_team == "away":
                # YES bid = away team (POV), NO bid = home team (opponent)
                away_kalshi_prob = orderbook_data["tob_effective_prob"]  # YES bid
                away_kalshi_liq = orderbook_data["tob_liq"]
                home_kalshi_prob = orderbook_data["no_tob_effective_prob"]  # NO bid
                home_kalshi_liq = orderbook_data["no_tob_liq"]
            else:  # pov_team == "home"
                # YES bid = home team (POV), NO bid = away team (opponent)
                away_kalshi_prob = orderbook_data["no_tob_effective_prob"]  # NO bid
                away_kalshi_liq = orderbook_data["no_tob_liq"]
                home_kalshi_prob = orderbook_data["tob_effective_prob"]  # YES bid
                home_kalshi_liq = orderbook_data["tob_liq"]
            
            # Build row (duplicate all game metadata)
            spread_rows.append({
                "game_date": game.get("game_date"),
                "event_start": game.get("event_start"),
                "away_roto": game.get("away_roto"),
                "away_team": away_team_name,
                "home_team": home_team_name,
                "consensus": consensus_str,
                "strike": strike_str,
                "pov_team": pov_team,
                "kalshi_ticker": market_ticker,
                "kalshi_title": market.get("title"),
                "unabated_spread": pov_spread,
                # Store both away and home orderbook data
                "away_kalshi_prob": away_kalshi_prob,
                "away_kalshi_liq": away_kalshi_liq,
                "home_kalshi_prob": home_kalshi_prob,
                "home_kalshi_liq": home_kalshi_liq,
                # Keep original fields for backward compatibility
                "tob_effective_prob": orderbook_data["tob_effective_prob"],
                "tob_liq": orderbook_data["tob_liq"],
                "tob_p1_effective_prob": orderbook_data["tob_p1_effective_prob"],
                "tob_p1_liq": orderbook_data["tob_p1_liq"],
                "crossed": orderbook_data["crossed"]
            })
    
    return spread_rows


def print_spreads_table(spread_rows: List[Dict[str, Any]]):
    """
    Print spreads table in console format.
    
    Shows: GameDate, GameTime, ROTO, AwayTeam, HomeTeam, Consensus, Strike, Away Kalshi, Home Kalshi
    """
    if not spread_rows:
        print("\nNo spread rows to display")
        return
    
    # Sort by ROTO ascending (None values go last), then by game_date
    spread_rows.sort(key=lambda x: (
        x.get('away_roto') is None,
        x.get('away_roto') or 0,
        x.get('game_date') or ''
    ))
    
    header = (
        f"{'GameDate':<12} "
        f"{'GameTime':<10} "
        f"{'ROTO':<6} "
        f"{'AwayTeam':<30} "
        f"{'HomeTeam':<30} "
        f"{'Consensus':<15} "
        f"{'Strike':<12} "
        f"{'AwayKalshi':<12} "
        f"{'HomeKalshi':<12}"
    )
    
    print("\n" + "=" * len(header.expandtabs()))
    print("NBA SPREADS DASHBOARD")
    print("=" * len(header.expandtabs()))
    print(header)
    print("-" * len(header.expandtabs()))
    
    # Import formatting functions from main dashboard
    from nba_value_table import format_game_time_pst, is_game_started, format_ev_percent
    
    for row in spread_rows:
        # Format game time
        event_start = row.get('event_start')
        game_time_str = format_game_time_pst(event_start)
        is_started = is_game_started(event_start)
        started_marker = " *" if is_started else ""
        
        # Format ROTO
        away_roto_str = str(row.get('away_roto', 'N/A')) if row.get('away_roto') is not None else "N/A"
        
        # Format consensus
        consensus_str = row.get('consensus', 'N/A')
        
        # Format strike
        strike_str = row.get('strike', 'N/A')
        
        # Get Away/Home Kalshi values (now stored separately)
        away_kalshi_prob = row.get('away_kalshi_prob')
        home_kalshi_prob = row.get('home_kalshi_prob')
        
        away_kalshi_str = f"{away_kalshi_prob:.4f}" if away_kalshi_prob is not None else "N/A"
        home_kalshi_str = f"{home_kalshi_prob:.4f}" if home_kalshi_prob is not None else "N/A"
        
        print(
            f"{row['game_date']:<12} "
            f"{game_time_str:<10}{started_marker} "
            f"{away_roto_str:<6} "
            f"{row['away_team']:<30} "
            f"{row['home_team']:<30} "
            f"{consensus_str:<15} "
            f"{strike_str:<12} "
            f"{away_kalshi_str:<12} "
            f"{home_kalshi_str:<12}"
        )
    
    print("=" * len(header.expandtabs()) + "\n")


if __name__ == "__main__":
    # Test function
    rows = build_spreads_rows_for_today()
    print(f"\nGenerated {len(rows)} spread row(s)")
    print_spreads_table(rows)
