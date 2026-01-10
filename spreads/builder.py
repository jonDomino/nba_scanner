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

from data_build.unabated_callsheet import get_today_games_with_fairs, utc_to_la_datetime, get_team_name
from data_build.slate import get_today_games_with_fairs_and_kalshi_tickers
from core.reusable_functions import (
    fetch_kalshi_markets_for_event,
    fetch_orderbook,
    load_team_xref,
    team_to_kalshi_code
)
from data_build.top_of_book import (
    get_yes_bid_top_and_liquidity,
    yes_break_even_prob
)
from utils import config
from utils.kalshi_api import load_creds

# Debug flag
DEBUG_SPREADS = True


def parse_spread_market_ticker(ticker: str) -> Tuple[Optional[str], Optional[int]]:
    """
    Parse team code and strike bucket from spread market ticker.
    
    IMPORTANT: Returns strike_bucket (e.g., 6), NOT exact strike value.
    Strike value must be parsed from title separately.
    
    Example: KXNBASPREAD-26JAN09LACBKN-LAC6 → (LAC, 6)
    
    Args:
        ticker: Market ticker string (e.g., "KXNBASPREAD-26JAN09LACBKN-LAC6")
    
    Returns:
        Tuple of (team_code, strike_bucket) where:
        - team_code: 3-letter uppercase team code (e.g., "LAC") or None
        - strike_bucket: Integer strike bucket/index (e.g., 6) or None
    """
    if not ticker:
        return (None, None)
    
    parts = ticker.split("-")
    if len(parts) < 3:
        return (None, None)
    
    suffix = parts[-1]  # e.g., "LAC6"
    
    # Extract team code (3 letters) and strike bucket (remaining digits)
    match = re.match(r'^([A-Z]{3})(\d+)$', suffix)
    if match:
        team_code = match.group(1)
        strike_bucket = int(match.group(2))
        return (team_code, strike_bucket)
    
    return (None, None)


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


def discover_kalshi_spread_markets(event_ticker: str, away_team_name: str, home_team_name: str, xref: Dict[Tuple[str, str], str]) -> List[Dict[str, Any]]:
    """
    Discover Kalshi spread markets for an event ticker and parse market team codes.
    
    IMPORTANT: Spreads are in KXNBASPREAD series, not KXNBAGAME series.
    This function converts the KXNBAGAME event ticker to KXNBASPREAD event ticker.
    
    Filters markets by checking:
    - title contains "wins by over" and "points"
    - Or market_type indicates spread
    
    Parses each market title to determine:
    - market_team_code: 3-letter Kalshi code of the team in the title
    - strike: float strike value (e.g., 6.5)
    
    Returns:
        List of market dicts, each with:
        - ticker: market ticker
        - title: market title
        - parsed_strike: float strike value (e.g., 6.5)
        - market_team_code: 3-letter Kalshi code (e.g., "LAC", "BKN")
        - anchor_team_token: team name/code from title (for debug)
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
    
    # Get team codes for fallback matching
    away_code = team_to_kalshi_code("NBA", away_team_name, xref)
    home_code = team_to_kalshi_code("NBA", home_team_name, xref)
    
    # Build name variations for fallback matching (only used if ticker parsing fails)
    away_variations = _build_team_name_variations(away_team_name)
    home_variations = _build_team_name_variations(home_team_name)
    
    for market in markets:
        if not isinstance(market, dict):
            continue
        
        # Get market ticker
        market_ticker = market.get("ticker") or market.get("market_ticker")
        if not market_ticker:
            continue
        
        # Get market title (preserve original case for parsing)
        title_raw = market.get("title") or market.get("market_title") or market.get("name") or ""
        title_lower = title_raw.lower()
        
        # Check if it's a spread market by title patterns
        is_spread = False
        
        # Pattern: "wins by over" or "wins by" + "points"
        if ("wins by over" in title_lower or "wins by" in title_lower) and "points" in title_lower:
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
        
        # PRIMARY: Parse team code from ticker
        market_team_code, strike_bucket = parse_spread_market_ticker(market_ticker)
        ticker_parse_success = market_team_code is not None
        
        # Parse strike from title (always, regardless of ticker parsing success)
        strike = None
        strike_match = re.search(r'over\s+([\d.]+)\s+points?', title_lower, re.IGNORECASE)
        if strike_match:
            try:
                strike = float(strike_match.group(1))
            except (ValueError, AttributeError):
                pass
        
        # FALLBACK: If ticker parsing failed, try title-based matching
        anchor_team_token = None
        if not ticker_parse_success:
            # Extract team name/code from title
            team_match = re.match(r'^([a-z\s]+?)\s+wins\s+by', title_lower, re.IGNORECASE)
            if team_match:
                anchor_team_token = team_match.group(1).strip()
                
                # Match anchor_team_token to away or home team to get market_team_code
                matched_away = any(
                    var in anchor_team_token or anchor_team_token in var
                    for var in away_variations
                    if var
                )
                matched_home = any(
                    var in anchor_team_token or anchor_team_token in var
                    for var in home_variations
                    if var
                )
                
                if matched_away and away_code:
                    market_team_code = away_code
                elif matched_home and home_code:
                    market_team_code = home_code
                else:
                    # Try to match directly to codes
                    if anchor_team_token and away_code and away_code.lower() in anchor_team_token:
                        market_team_code = away_code
                    elif anchor_team_token and home_code and home_code.lower() in anchor_team_token:
                        market_team_code = home_code
            
            # FALLBACK 2: Try regex fallback on ticker suffix
            if not market_team_code:
                parts = market_ticker.split("-")
                if len(parts) >= 3:
                    suffix = parts[-1]  # e.g., "LAC6"
                    # Try to match pattern -{TEAM_CODE}\d+ where TEAM_CODE is one of away_code or home_code
                    for team_code_candidate in [away_code, home_code]:
                        if team_code_candidate and suffix.startswith(team_code_candidate):
                            # Verify it's followed by digits
                            if re.match(rf'^{team_code_candidate}\d+$', suffix):
                                market_team_code = team_code_candidate
                                break
            
            # Log warning if fallback was used
            if not ticker_parse_success:
                if market_team_code:
                    if DEBUG_SPREADS:
                        print(f"⚠️ Ticker parsing failed for {market_ticker}, used fallback: {market_team_code}")
                else:
                    if DEBUG_SPREADS:
                        print(f"⚠️ Could not determine market_team_code from ticker or title: {market_ticker} (title: {title_raw[:50]})")
        
        # Must not skip markets: keep even if team code is None (strike is required though)
        if strike is None:
            if DEBUG_SPREADS:
                print(f"⚠️ Could not parse strike from title: {title_raw}")
            continue  # Strike is required, but team_code can be None
        
        # Append market (even if market_team_code is None - we'll filter at selection step)
        spread_markets.append({
            "ticker": market_ticker,
            "title": title_raw,
            "parsed_strike": strike,
            "market_team_code": market_team_code,  # May be None if all parsing fails
            "anchor_team_token": anchor_team_token
        })
    
    return spread_markets


def map_team_spread_to_market_and_side(
    team_spread: float,
    team_code: str,
    opponent_code: str,
    spread_markets: List[Dict[str, Any]]
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Map Unabated team spread to Kalshi market and side to trade.
    
    Logic:
    - If team_spread < 0 (team is favorite): use team's market, trade YES
    - If team_spread > 0 (team is underdog): use opponent's market, trade NO
      (because "underdog +X covers" = NOT(favorite wins by > X))
    
    Args:
        team_spread: Unabated spread for the team (e.g., -6.5 or +6.5)
        team_code: 3-letter Kalshi code for the team (e.g., "BKN")
        opponent_code: 3-letter Kalshi code for opponent (e.g., "LAC")
        spread_markets: List of all spread markets for the game
    
    Returns:
        Tuple of (selected_market_dict, side_to_trade)
        - selected_market_dict: Market dict with matching strike, or None
        - side_to_trade: "YES" or "NO"
    """
    abs_spread = abs(team_spread)
    
    # Determine which market to use and which side
    if team_spread < 0:
        # Favorite: use team's market, trade YES
        target_market_team_code = team_code
        side_to_trade = "YES"
    else:
        # Underdog: use opponent's market, trade NO
        target_market_team_code = opponent_code
        side_to_trade = "NO"
    
    # Find markets for target_market_team_code
    candidate_markets = [
        m for m in spread_markets
        if m.get("market_team_code") == target_market_team_code
    ]
    
    if not candidate_markets:
        return (None, side_to_trade)
    
    # Select closest strike to abs_spread
    markets_with_distance = []
    for market in candidate_markets:
        strike = market.get("parsed_strike")
        if strike is None:
            continue
        distance = abs(strike - abs_spread)
        markets_with_distance.append((distance, strike, market))
    
    if not markets_with_distance:
        return (None, side_to_trade)
    
    # Sort by distance (closest first), then by strike (lower first for tie-break)
    markets_with_distance.sort(key=lambda x: (x[0], x[1]))
    
    # Return the closest market
    selected_market = markets_with_distance[0][2]
    return (selected_market, side_to_trade)


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




def select_closest_strikes_for_team_spread(
    team_spread: float,
    team_code: str,
    opponent_code: str,
    spread_markets: List[Dict[str, Any]],
    count: int = 2
) -> List[Tuple[Dict[str, Any], str]]:
    """
    Select the N closest strikes for a team's spread, returning market + side pairs.
    
    For each selected strike, determines which market and side to use:
    - Favorite (spread < 0): use team's market, trade YES
    - Underdog (spread > 0): use opponent's market, trade NO
    
    Args:
        team_spread: Unabated spread for the team (e.g., -6.5 or +6.5)
        team_code: 3-letter Kalshi code for the team
        opponent_code: 3-letter Kalshi code for opponent
        spread_markets: List of all spread markets for the game
        count: Number of strikes to select (default 2)
    
    Returns:
        List of tuples: (selected_market_dict, side_to_trade)
        Sorted by distance to abs(team_spread)
    """
    abs_spread = abs(team_spread)
    
    # Determine which market team to use
    if team_spread < 0:
        # Favorite: use team's market, trade YES
        target_market_team_code = team_code
        side_to_trade = "YES"
    else:
        # Underdog: use opponent's market, trade NO
        target_market_team_code = opponent_code
        side_to_trade = "NO"
    
    # Find markets for target_market_team_code
    candidate_markets = [
        m for m in spread_markets
        if m.get("market_team_code") == target_market_team_code
    ]
    
    if not candidate_markets:
        return []
    
    # Calculate distance for each market
    markets_with_distance = []
    for market in candidate_markets:
        strike = market.get("parsed_strike")
        if strike is None:
            continue
        distance = abs(strike - abs_spread)
        markets_with_distance.append((distance, strike, market))
    
    if not markets_with_distance:
        return []
    
    # Sort by distance (closest first), then by strike (lower first for tie-break)
    markets_with_distance.sort(key=lambda x: (x[0], x[1]))
    
    # Select top N and pair with side_to_trade
    selected = [(markets_with_distance[i][2], side_to_trade) for i in range(min(count, len(markets_with_distance)))]
    
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


def get_spread_orderbook_data(market_ticker: str, side_to_trade: str = "YES") -> Dict[str, Any]:
    """
    Fetch orderbook and compute TOB for a specific side (YES or NO) of a spread market.
    
    Args:
        market_ticker: Kalshi market ticker
        side_to_trade: "YES" or "NO" - which side's bids to extract
    
    Returns:
        Dict with keys:
        - tob_bid_cents: Top bid price in cents
        - tob_effective_prob: Break-even probability at top bid (after fees)
        - tob_liq: Liquidity at top bid
        - tob_p1_bid_cents: Top bid+1c price (if valid and doesn't cross)
        - tob_p1_effective_prob: Break-even probability at top+1c (after fees)
        - tob_p1_liq: Always None (theoretical price)
        - crossed: Boolean indicating if +1c would cross
        - error: Error message if any
    """
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        return {
            "tob_bid_cents": None,
            "tob_effective_prob": None,
            "tob_liq": None,
            "tob_p1_bid_cents": None,
            "tob_p1_effective_prob": None,
            "tob_p1_liq": None,
            "crossed": None,
            "error": f"Failed to load credentials: {e}"
        }
    
    orderbook = fetch_orderbook(api_key_id, private_key_pem, market_ticker)
    
    if not orderbook:
        return {
            "tob_bid_cents": None,
            "tob_effective_prob": None,
            "tob_liq": None,
            "tob_p1_bid_cents": None,
            "tob_p1_effective_prob": None,
            "tob_p1_liq": None,
            "crossed": None,
            "error": "No orderbook"
        }
    
    # Extract top bid based on side
    if side_to_trade.upper() == "YES":
        bid_top_c, bid_top_liq, bids_by_price = get_yes_bid_top_and_liquidity(orderbook)
        # Get opposing side for crossing check
        no_bid_top_c, _, _ = get_no_bid_top_and_liquidity(orderbook)
        ask_top_c = (100 - no_bid_top_c) if no_bid_top_c is not None else None
    elif side_to_trade.upper() == "NO":
        bid_top_c, bid_top_liq, bids_by_price = get_no_bid_top_and_liquidity(orderbook)
        # Get opposing side for crossing check
        yes_bid_top_c, _, _ = get_yes_bid_top_and_liquidity(orderbook)
        ask_top_c = (100 - yes_bid_top_c) if yes_bid_top_c is not None else None
    else:
        return {
            "tob_bid_cents": None,
            "tob_effective_prob": None,
            "tob_liq": None,
            "tob_p1_bid_cents": None,
            "tob_p1_effective_prob": None,
            "tob_p1_liq": None,
            "crossed": None,
            "error": f"Invalid side_to_trade: {side_to_trade} (must be YES or NO)"
        }
    
    if bid_top_c is None:
        return {
            "tob_bid_cents": None,
            "tob_effective_prob": None,
            "tob_liq": None,
            "tob_p1_bid_cents": None,
            "tob_p1_effective_prob": None,
            "tob_p1_liq": None,
            "crossed": None,
            "error": f"No {side_to_trade} bids found"
        }
    
    # Calculate break-even probability at TOB (after maker fees)
    tob_effective_prob = yes_break_even_prob(bid_top_c)
    
    # Calculate TOB+1c
    bid_top_p1_c = bid_top_c + 1 if bid_top_c < 99 else None
    crossed = False
    
    # Check if +1c would cross the book
    if bid_top_p1_c is not None and ask_top_c is not None:
        if bid_top_p1_c >= ask_top_c:
            crossed = True
            bid_top_p1_c = None
    
    # Calculate break-even probability at TOB+1c if valid
    tob_p1_effective_prob = None
    if bid_top_p1_c is not None:
        tob_p1_effective_prob = yes_break_even_prob(bid_top_p1_c)
    
    return {
        "tob_bid_cents": bid_top_c,
        "tob_effective_prob": tob_effective_prob,
        "tob_liq": bid_top_liq,
        "tob_p1_bid_cents": bid_top_p1_c,
        "tob_p1_effective_prob": tob_p1_effective_prob,
        "tob_p1_liq": None,  # Theoretical price, no direct liquidity
        "crossed": crossed,
        "error": None
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
    from data_build.unabated_callsheet import extract_nba_games_today
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
        
        # Discover Kalshi spread markets (with team name parsing)
        if not event_ticker:
            if DEBUG_SPREADS:
                print(f"  ⚠️ No event ticker, skipping")
            continue
        
        spread_markets = discover_kalshi_spread_markets(event_ticker, away_team_name, home_team_name, xref)
        
        if DEBUG_SPREADS:
            print(f"  Found {len(spread_markets)} spread market(s)")
            # Show first few markets for debug
            for m in spread_markets[:3]:
                print(f"    - {m.get('title')} -> market_team={m.get('market_team_code')}, strike={m.get('parsed_strike')}")
        
        if not spread_markets:
            continue
        
        # Get team codes
        away_code = team_to_kalshi_code("NBA", away_team_name, xref)
        home_code = team_to_kalshi_code("NBA", home_team_name, xref)
        
        if not away_code or not home_code:
            if DEBUG_SPREADS:
                print(f"  ⚠️ Could not get team codes (away={away_code}, home={home_code})")
            continue
        
        # Check if this is LAC @ BKN for targeted debug
        is_lacbkn = (away_code == "LAC" and home_code == "BKN") or (away_code == "BKN" and home_code == "LAC")
        
        # CANONICAL POV SELECTION: Choose one team's perspective per game
        # Logic: ALWAYS use favorite's spread (negative spread) as canonical POV
        # Underdog exposure is represented via NO side of favorite's market
        # 
        # IMPORTANT: If one team is underdog (positive spread), the other MUST be favorite (negative spread)
        # If we can't find a favorite from Unabated data, infer it from the underdog
        canonical_team = None
        canonical_code = None
        canonical_spread = None
        canonical_juice = None
        
        # Priority 1: Use favorite (negative spread) if explicitly available
        if away_spread is not None and away_spread < 0:
            # Away team is favorite
            canonical_team = "away"
            canonical_code = away_code
            canonical_spread = away_spread
            canonical_juice = away_juice
        elif home_spread is not None and home_spread < 0:
            # Home team is favorite
            canonical_team = "home"
            canonical_code = home_code
            canonical_spread = home_spread
            canonical_juice = home_juice
        # Priority 2: If one team is underdog (positive), the other is implicitly the favorite
        elif away_spread is not None and away_spread > 0 and home_spread is not None:
            # Away is underdog, so home must be favorite (even if home_spread is None or positive)
            # Use home as canonical, but we'll need to infer home spread from away spread
            canonical_team = "home"
            canonical_code = home_code
            # Infer home spread: if away is +X, home is approximately -X
            canonical_spread = -away_spread if home_spread is None else home_spread
            canonical_juice = home_juice
        elif home_spread is not None and home_spread > 0 and away_spread is not None:
            # Home is underdog, so away must be favorite (even if away_spread is None or positive)
            # Use away as canonical, but we'll need to infer away spread from home spread
            canonical_team = "away"
            canonical_code = away_code
            # Infer away spread: if home is +X, away is approximately -X
            canonical_spread = -home_spread if away_spread is None else away_spread
            canonical_juice = away_juice
        # Priority 3: Fallback to whichever spread is available
        elif away_spread is not None:
            canonical_team = "away"
            canonical_code = away_code
            canonical_spread = away_spread
            canonical_juice = away_juice
        elif home_spread is not None:
            canonical_team = "home"
            canonical_code = home_code
            canonical_spread = home_spread
            canonical_juice = home_juice
        else:
            # No consensus spread available
            if DEBUG_SPREADS:
                print(f"  ⚠️ Missing consensus spread - skipping game")
            continue
        
        # Get opponent info for canonical POV
        opponent_code = home_code if canonical_team == "away" else away_code
        
        # Enhanced debug logging
        if is_lacbkn or DEBUG_SPREADS:
            canonical_market_count = len([m for m in spread_markets if m.get("market_team_code") == canonical_code])
            opponent_market_count = len([m for m in spread_markets if m.get("market_team_code") == opponent_code])
            print(f"\n  [DEBUG] Canonical POV Selection:")
            print(f"    Away spread: {away_spread} (juice: {away_juice})")
            print(f"    Home spread: {home_spread} (juice: {home_juice})")
            print(f"    Canonical POV: {canonical_team} ({canonical_code}) spread={canonical_spread}")
            print(f"    Opponent: {opponent_code}")
            print(f"    Spread markets found: {len(spread_markets)}")
            print(f"    Markets with market_team_code=={canonical_code}: {canonical_market_count}")
            print(f"    Markets with market_team_code=={opponent_code}: {opponent_market_count}")
            if canonical_market_count == 0 and opponent_market_count == 0:
                print(f"    ⚠️ ZERO markets for both teams - this is why game disappears")
            # Show first few markets with parsing details
            for m in spread_markets[:5]:
                ticker = m.get("ticker", "N/A")
                team_code = m.get("market_team_code", "N/A")
                strike = m.get("parsed_strike", "N/A")
                title = m.get("title", "N/A")
                print(f"      - {ticker}")
                print(f"        team_code={team_code}, strike={strike}, title={title[:50]}")
        
        # Select 2 closest strikes for canonical POV only
        # Note: If canonical_spread > 0 (underdog), select_closest_strikes_for_team_spread will
        # look for opponent's markets (favorite's markets) since underdog uses opponent's market
        selected_strikes = select_closest_strikes_for_team_spread(
            canonical_spread, canonical_code, opponent_code, spread_markets, count=2
        )
        
        if DEBUG_SPREADS:
            print(f"  Selected {len(selected_strikes)} strike(s) for canonical POV ({canonical_code})")
            if len(selected_strikes) == 0:
                print(f"  ⚠️ Selection returned 0 strikes - game will be skipped")
                # Additional debug: show what markets were available
                if canonical_spread < 0:
                    # Favorite: should have canonical_code markets
                    available = [m for m in spread_markets if m.get("market_team_code") == canonical_code]
                    print(f"    Expected markets for {canonical_code} (favorite): {len(available)}")
                else:
                    # Underdog: should have opponent_code markets
                    available = [m for m in spread_markets if m.get("market_team_code") == opponent_code]
                    print(f"    Expected markets for {opponent_code} (favorite, for underdog {canonical_code}): {len(available)}")
            for market, side in selected_strikes:
                print(f"    - {market.get('ticker')} (strike={market.get('parsed_strike')}, side={side})")
        
        if not selected_strikes:
            continue
        
        # Build rows for canonical POV only (not for both away and home)
        for market, side_to_trade_canonical in selected_strikes:
            strike_value = market.get("parsed_strike")
            if strike_value is None:
                continue
            
            market_ticker = market.get("ticker")
            if not market_ticker:
                continue
            
            # Determine market and side for canonical POV team
            # Logic: if canonical_spread < 0 (favorite), use canonical team's market, trade YES
            #        if canonical_spread > 0 (underdog), use opponent's market (favorite), trade NO
            if canonical_spread < 0:
                # Canonical team is favorite: use canonical team's market, trade YES
                # Verify market is for canonical team
                if market.get("market_team_code") != canonical_code:
                    # Find correct market for canonical team at this strike
                    correct_markets = [m for m in spread_markets if m.get("market_team_code") == canonical_code and abs(m.get("parsed_strike", 0) - strike_value) < 0.1]
                    if correct_markets:
                        market = correct_markets[0]
                        market_ticker = market.get("ticker")
                side_canonical = "YES"
                side_opponent = "NO"  # Opposite side of same market
            else:
                # Canonical team is underdog: use opponent's market (favorite), trade NO
                # Verify market is for opponent team
                if market.get("market_team_code") != opponent_code:
                    # Find correct market for opponent team at this strike
                    correct_markets = [m for m in spread_markets if m.get("market_team_code") == opponent_code and abs(m.get("parsed_strike", 0) - strike_value) < 0.1]
                    if correct_markets:
                        market = correct_markets[0]
                        market_ticker = market.get("ticker")
                side_canonical = "NO"
                side_opponent = "YES"  # Opposite side of same market
            
            # Get orderbook data for both sides (same market)
            canonical_orderbook_data = get_spread_orderbook_data(market_ticker, side_canonical)
            opponent_orderbook_data = get_spread_orderbook_data(market_ticker, side_opponent)
            
            # Assign to away/home based on canonical_team
            if canonical_team == "away":
                away_kalshi_prob = canonical_orderbook_data.get("tob_effective_prob")
                away_kalshi_liq = canonical_orderbook_data.get("tob_liq")
                away_kalshi_price_cents = canonical_orderbook_data.get("tob_bid_cents")
                home_kalshi_prob = opponent_orderbook_data.get("tob_effective_prob")
                home_kalshi_liq = opponent_orderbook_data.get("tob_liq")
                home_kalshi_price_cents = opponent_orderbook_data.get("tob_bid_cents")
            else:
                away_kalshi_prob = opponent_orderbook_data.get("tob_effective_prob")
                away_kalshi_liq = opponent_orderbook_data.get("tob_liq")
                away_kalshi_price_cents = opponent_orderbook_data.get("tob_bid_cents")
                home_kalshi_prob = canonical_orderbook_data.get("tob_effective_prob")
                home_kalshi_liq = canonical_orderbook_data.get("tob_liq")
                home_kalshi_price_cents = canonical_orderbook_data.get("tob_bid_cents")
            
            # Format strike string (canonical team's perspective)
            if canonical_spread < 0:
                strike_str = f"{canonical_code} -{strike_value}"
            else:
                strike_str = f"{canonical_code} +{strike_value}"
            
            # Format consensus string (use canonical team's spread/juice)
            consensus_str = format_consensus_string(canonical_code, canonical_spread, canonical_juice)
            
            # Targeted debug for LACBKN
            if is_lacbkn and DEBUG_SPREADS:
                print(f"\n  [LACBKN DEBUG] Canonical POV spread row: {strike_str}")
                print(f"    desired strike label: {strike_str}")
                print(f"    chosen market_ticker: {market_ticker}")
                print(f"    market title: {market.get('title')}")
                print(f"    parsed market_team_code: {market.get('market_team_code')}")
                print(f"    side_canonical ({canonical_code}): {side_canonical}")
                print(f"    side_opponent ({opponent_code}): {side_opponent}")
                print(f"    canonical best bid (cents): {canonical_orderbook_data.get('tob_bid_cents')}")
                print(f"    canonical best bid liq: {canonical_orderbook_data.get('tob_liq')}")
                print(f"    canonical effective prob: {canonical_orderbook_data.get('tob_effective_prob')}")
                print(f"    opponent best bid (cents): {opponent_orderbook_data.get('tob_bid_cents')}")
                print(f"    opponent best bid liq: {opponent_orderbook_data.get('tob_liq')}")
                print(f"    opponent effective prob: {opponent_orderbook_data.get('tob_effective_prob')}")
            
            spread_rows.append({
                "game_date": game.get("game_date"),
                "event_start": game.get("event_start"),
                "away_roto": game.get("away_roto"),
                "away_team": away_team_name,
                "home_team": home_team_name,
                "consensus": consensus_str,
                "strike": strike_str,
                "kalshi_ticker": market_ticker,
                "kalshi_title": market.get("title"),
                "unabated_spread": canonical_spread,
                "away_kalshi_prob": away_kalshi_prob,
                "away_kalshi_liq": away_kalshi_liq,
                "away_kalshi_price_cents": away_kalshi_price_cents,  # Price in cents for dollar liquidity calc
                "home_kalshi_prob": home_kalshi_prob,
                "home_kalshi_liq": home_kalshi_liq,
                "home_kalshi_price_cents": home_kalshi_price_cents,  # Price in cents for dollar liquidity calc
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
    from moneylines.table import format_game_time_pst, is_game_started, format_ev_percent
    
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
