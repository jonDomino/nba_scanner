"""
NBA Totals Dashboard: Today's NBA games with Unabated totals vs Kalshi totals markets.

This module is completely separate from the moneylines and spreads dashboards and does not modify any
existing functionality. It reuses existing utilities but does not change their behavior.

For each game:
- Extract Unabated canonical total consensus
- Discover Kalshi totals markets for the event
- Select the 2 closest Over strikes to Unabated canonical total
- Emit 2 rows (one per strike) with duplicated game metadata

Canonical POV: Always "Over" (all totals markets are "Over X.Y" markets).
Under exposure is represented via NO side of the Over market.

Internal plumbing: NO-space for totals (same convention as spreads/moneylines).
User-facing: Display "price to get exposure to Over/Under X.Y".
"""

import re
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

from data_build.unabated_callsheet import get_team_name
from data_build.slate import get_today_games_with_fairs_and_kalshi_tickers
from core.reusable_functions import (
    fetch_kalshi_markets_for_event,
    load_team_xref
)
from spreads.builder import get_spread_orderbook_data, _fetch_orderbook_with_cache
from utils import config
from utils.kalshi_api import load_creds

# Debug flag
DEBUG_TOTALS = True


def parse_total_market_ticker(ticker: str) -> Tuple[Optional[str], Optional[float]]:
    """
    Parse direction and exact strike from totals market ticker.
    
    ENHANCED: Now extracts exact strike (e.g., 227.5) from ticker, not just bucket.
    
    Example patterns:
    - KXNBATOTAL-26JAN09TORBOS-OVER2275 → ("OVER", 227.5)
    - KXNBATOTAL-26JAN09TORBOS-UNDER2225 → ("UNDER", 222.5)
    - KXNBATOTAL-26JAN09TORBOS-2275 → (None, 227.5)
    
    Args:
        ticker: Market ticker string (e.g., "KXNBATOTAL-26JAN09MILLAL-OVER2215")
    
    Returns:
        Tuple of (direction, strike) where:
        - direction: "OVER" or "UNDER" or None
        - strike: Exact float strike value (e.g., 227.5) or None
    """
    if not ticker:
        return (None, None)
    
    parts = ticker.split("-")
    if len(parts) < 3:
        return (None, None)
    
    suffix = parts[-1].upper()  # e.g., "OVER2215" or "2275"
    
    # Pattern 1: OVER\d+ or UNDER\d+ (e.g., "OVER246" → direction=OVER, strike=246.0)
    match = re.match(r'^(OVER|UNDER)(\d+)$', suffix)
    if match:
        direction = match.group(1)
        strike_bucket = int(match.group(2))
        
        # FIXED: Tickers are integer totals, NOT encoded as "hundreds of cents"
        # Return strike directly as float (240 → 240.0, not 24.0)
        # Examples: 246 → 246.0, 240 → 240.0, 225 → 225.0
        strike = float(strike_bucket)
        
        if DEBUG_TOTALS:
            print(f"    ✅ Parsed ticker: {ticker} → direction={direction}, strike={strike}")
        
        return (direction, strike)
    
    # Pattern 2: Pure numeric suffix (e.g., "246" → strike=246.0, direction=None)
    match = re.match(r'^(\d+)$', suffix)
    if match:
        strike_bucket = int(match.group(1))
        
        # FIXED: Return integer total directly (no division by 10)
        strike = float(strike_bucket)
        
        if DEBUG_TOTALS:
            print(f"    ✅ Parsed ticker: {ticker} → strike={strike} (no direction)")
        
        return (None, strike)
    
    return (None, None)


def extract_unabated_totals(event: Dict[str, Any], teams: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract Unabated totals consensus from ms49.
    
    Similar structure to spreads but extracts totals (bt3 or similar) instead of spread (bt2).
    
    Returns:
        Dict with:
        - total: float (e.g., 221.5)
        - juice: int|None (American odds, e.g., -110, +105)
        or None if not found
    """
    market_lines = event.get("gameOddsMarketSourcesLines", {})
    if not isinstance(market_lines, dict):
        return None
    
    # Find ALL Unabated market source (ms49) keys
    ms49_keys = [k for k in market_lines.keys() if ":ms49:" in k]
    
    if not ms49_keys:
        if DEBUG_TOTALS:
            print(f"    ⚠️ No ms49 keys found for event")
        return None
    
    # DEBUG: Print event structure
    if DEBUG_TOTALS:
        event_teams = event.get("eventTeams", {})
        team_names = []
        if isinstance(event_teams, dict):
            for idx, team_info in event_teams.items():
                if isinstance(team_info, dict):
                    team_id = team_info.get("id")
                    if team_id:
                        team_name = get_team_name(team_id, teams)
                        team_names.append(team_name)
        
        print(f"\n  [DEBUG] Unabated Totals Extraction:")
        print(f"    Event: {event.get('eventStart')}")
        print(f"    Event teams: {team_names}")
        print(f"    ms49_keys found: {len(ms49_keys)}")
        print(f"    ms49_key samples: {ms49_keys[:3]}")
        
        # Print first ms49 block structure
        if ms49_keys:
            first_ms49 = market_lines[ms49_keys[0]]
            if isinstance(first_ms49, dict):
                print(f"    First ms49_block keys: {list(first_ms49.keys())[:10]}")
                if "bt3" in first_ms49:
                    bt3 = first_ms49["bt3"]
                    if isinstance(bt3, dict):
                        print(f"    bt3 fields: {list(bt3.keys())}")
                        print(f"    bt3 line: {bt3.get('line')}")
                        print(f"    bt3 total: {bt3.get('total')}")
                        print(f"    bt3 value: {bt3.get('value')}")
                        print(f"    bt3 points: {bt3.get('points')}")
                        print(f"    bt3 overUnder: {bt3.get('overUnder')}")
                        print(f"    bt3 americanPrice: {bt3.get('americanPrice')}")
                        print(f"    bt3 unabatedPrice: {bt3.get('unabatedPrice')}")
    
    # Collect ALL bt3 totals from all ms49 blocks (totals are game-level, but might be in any ms49 block)
    all_bt3_totals = []
    
    # Totals are typically in bt3 (bt1=moneyline, bt2=spread, bt3=total)
    # Totals are game-level (not per-team), but might appear in any ms49 block
    for ms49_key in ms49_keys:
        ms49_block = market_lines[ms49_key]
        if not isinstance(ms49_block, dict):
            continue
        
        # Try bt3 first (most likely for totals)
        bt3_line = ms49_block.get("bt3")
        if bt3_line and isinstance(bt3_line, dict):
            # Get total value
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
                except (ValueError, TypeError):
                    continue
                
                # Get juice (American odds) if available
                juice_raw = (
                    bt3_line.get("americanPrice") or
                    bt3_line.get("unabatedPrice") or
                    bt3_line.get("price") or
                    bt3_line.get("juice")
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
                
                all_bt3_totals.append({
                    "ms49_key": ms49_key,
                    "total": total,
                    "juice": juice
                })
    
    # FIXED: If multiple bt3 totals found, verify they're the same (totals are game-level)
    if all_bt3_totals:
        if DEBUG_TOTALS:
            print(f"    Found {len(all_bt3_totals)} bt3 total(s):")
            for i, bt3_data in enumerate(all_bt3_totals):
                print(f"      {i+1}. {bt3_data['ms49_key']}: total={bt3_data['total']}, juice={bt3_data['juice']}")
        
        # If multiple, check if they're the same (should be for game-level totals)
        unique_totals = set(bt3_data['total'] for bt3_data in all_bt3_totals)
        if len(unique_totals) > 1:
            if DEBUG_TOTALS:
                print(f"    ⚠️ WARNING: Multiple different totals found: {unique_totals}")
                print(f"    Using first one ({all_bt3_totals[0]['total']})")
        else:
            if DEBUG_TOTALS:
                print(f"    ✅ All ms49 blocks have same total: {all_bt3_totals[0]['total']}")
        
        # Return first bt3 total (should be same across all ms49 blocks if game-level)
        return {
            "total": all_bt3_totals[0]["total"],
            "juice": all_bt3_totals[0]["juice"]
        }
        
        # Try other possible bet types (bt4, bt5, etc.) if bt3 doesn't exist
        for bt_key in ["bt4", "bt5", "total", "overUnder"]:
            total_line = ms49_block.get(bt_key)
            if total_line and isinstance(total_line, dict):
                total_raw = (
                    total_line.get("line") or
                    total_line.get("total") or
                    total_line.get("value") or
                    total_line.get("points")
                )
                
                if total_raw is not None:
                    try:
                        if isinstance(total_raw, str):
                            total = float(total_raw.strip())
                        else:
                            total = float(total_raw)
                    except (ValueError, TypeError):
                        continue
                    
                    # Get juice if available
                    juice_raw = (
                        total_line.get("americanPrice") or
                        total_line.get("unabatedPrice") or
                        total_line.get("price") or
                        total_line.get("juice")
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
                    
                    return {
                        "total": total,
                        "juice": juice
                    }
    
    return None


def discover_kalshi_totals_markets(event_ticker: str) -> List[Dict[str, Any]]:
    """
    Discover Kalshi totals markets for an event ticker.
    
    IMPORTANT: Totals are in KXNBATOTAL series, not KXNBAGAME series.
    This function converts the KXNBAGAME event ticker to KXNBATOTAL event ticker.
    
    Parses each market title to determine:
    - parsed_strike: float strike value (e.g., 221.5)
    - direction: "over" (all markets should be Over markets)
    
    Returns:
        List of market dicts, each with:
        - ticker: market ticker
        - title: market title
        - parsed_strike: float strike value (e.g., 221.5) - REQUIRED
        - direction: "over" (canonical POV)
    """
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        if DEBUG_TOTALS:
            print(f"❌ Failed to load Kalshi credentials: {e}")
        return []
    
    # Convert KXNBAGAME event ticker to KXNBATOTAL event ticker
    # Example: KXNBAGAME-26JAN09MILLAL -> KXNBATOTAL-26JAN09MILLAL
    total_event_ticker = event_ticker.replace("KXNBAGAME-", "KXNBATOTAL-", 1)
    
    if DEBUG_TOTALS:
        print(f"  Converting event ticker: {event_ticker} -> {total_event_ticker}")
    
    # Fetch all markets for totals event (KXNBATOTAL series)
    markets = fetch_kalshi_markets_for_event(api_key_id, private_key_pem, total_event_ticker)
    
    if DEBUG_TOTALS:
        print(f"  Fetched {len(markets) if markets else 0} market(s) from {total_event_ticker}")
    
    if not markets:
        if DEBUG_TOTALS:
            print(f"  ⚠️ No markets found for totals event {total_event_ticker}")
        return []
    
    # DEBUG: Print market structure for first 2 markets
    if DEBUG_TOTALS and markets:
        print(f"\n{'='*60}")
        print(f"[DEBUG] Event metadata for {total_event_ticker}:")
        print(f"  Markets fetched: {len(markets)}")
        
        # Print first 2 markets' full structure
        for i, market in enumerate(markets[:2]):
            print(f"\n  [DEBUG] Market {i+1} structure:")
            print(f"    market_ticker: {market.get('ticker') or market.get('market_ticker')}")
            print(f"    market_title: {market.get('title') or market.get('market_title') or market.get('name')}")
            print(f"    market_subtitle: {market.get('subtitle') or market.get('market_subtitle')}")
            print(f"    market_type: {market.get('market_type') or market.get('marketType') or market.get('type')}")
            print(f"    yes_title: {market.get('yes_title') or market.get('yesTitle') or market.get('yes')}")
            print(f"    no_title: {market.get('no_title') or market.get('noTitle') or market.get('no')}")
            print(f"    product_metadata: {market.get('product_metadata') or market.get('productMetadata') or market.get('metadata')}")
            print(f"    strike: {market.get('strike') or market.get('strike_price') or market.get('strikePrice')}")
            print(f"    floor: {market.get('floor')}")
            print(f"    cap: {market.get('cap')}")
            print(f"    Top-level keys (first 20): {list(market.keys())[:20]}")
        
        # Print all market tickers
        print(f"\n  [DEBUG] All market tickers:")
        for i, market in enumerate(markets[:11]):  # Print all if <= 11, else first 11
            ticker = market.get('ticker') or market.get('market_ticker') or 'N/A'
            title = market.get('title') or market.get('market_title') or 'N/A'
            print(f"    {i+1}. {ticker}")
            print(f"       title: {title[:60]}")
    
    totals_markets = []
    
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
        
        # Check if it's a totals market by title patterns
        is_total = False
        direction = None
        
        # Pattern: "over" + number + "points" (e.g., "Over 221.5 points")
        if "over" in title_lower and "points" in title_lower:
            is_total = True
            direction = "over"
        # Pattern: "under" + number + "points" (e.g., "Under 218.5 points")
        elif "under" in title_lower and "points" in title_lower:
            is_total = True
            direction = "under"
        # Pattern: "total" + number + "points" (might be either direction)
        elif "total" in title_lower and "points" in title_lower:
            is_total = True
            # Try to infer direction from context (default to "over" if unclear)
            if "over" in title_lower:
                direction = "over"
            elif "under" in title_lower:
                direction = "under"
            else:
                direction = "over"  # Default to over
        
        # Also check market_type if available
        market_type = (
            market.get("market_type") or
            market.get("marketType") or
            market.get("type") or
            ""
        ).lower()
        
        if market_type in ["total", "over/under", "ou", "totals"]:
            is_total = True
            if not direction:
                direction = "over"  # Default to over
        
        if not is_total:
            continue
        
        # MULTI-SOURCE STRIKE PARSING (Fix A + Fix B)
        # Priority order: ticker → subtitle → yes_title/no_title → product_metadata → dedicated fields → title
        
        strike = None
        direction_from_strike = None  # Direction inferred from where strike was found
        
        # SOURCE 1: Parse strike from ticker (PRIMARY - Fix B)
        direction_from_ticker, strike_from_ticker = parse_total_market_ticker(market_ticker)
        if strike_from_ticker is not None:
            strike = strike_from_ticker
            direction_from_strike = direction_from_ticker
            if DEBUG_TOTALS:
                print(f"  ✅ Parsed strike from ticker: {market_ticker} → {strike} ({direction_from_strike or 'no direction'})")
        
        # SOURCE 2: Parse strike from subtitle (SECONDARY - Fix A)
        if strike is None:
            subtitle = market.get("subtitle") or market.get("market_subtitle") or ""
            if subtitle:
                subtitle_lower = subtitle.lower()
                # Pattern: "Over 227.5" or "Under 222.5" or "227.5"
                subtitle_match = re.search(r'(?:over|under)?\s*([\d.]+)', subtitle_lower, re.IGNORECASE)
                if subtitle_match:
                    try:
                        strike = float(subtitle_match.group(1))
                        # Infer direction from subtitle if present
                        if "over" in subtitle_lower:
                            direction_from_strike = "OVER"
                        elif "under" in subtitle_lower:
                            direction_from_strike = "UNDER"
                        if DEBUG_TOTALS:
                            print(f"  ✅ Parsed strike from subtitle: {subtitle} → {strike}")
                    except (ValueError, AttributeError):
                        pass
        
        # SOURCE 3: Parse strike from yes_title or no_title (TERTIARY - Fix A)
        if strike is None:
            yes_title = market.get("yes_title") or market.get("yesTitle") or market.get("yes") or ""
            no_title = market.get("no_title") or market.get("noTitle") or market.get("no") or ""
            
            # Try yes_title first (typically "Over X.Y")
            if yes_title:
                yes_title_lower = yes_title.lower()
                yes_match = re.search(r'(?:over|under)?\s*([\d.]+)', yes_title_lower, re.IGNORECASE)
                if yes_match:
                    try:
                        strike = float(yes_match.group(1))
                        if "over" in yes_title_lower:
                            direction_from_strike = "OVER"
                        elif "under" in yes_title_lower:
                            direction_from_strike = "UNDER"
                        if DEBUG_TOTALS:
                            print(f"  ✅ Parsed strike from yes_title: {yes_title} → {strike}")
                    except (ValueError, AttributeError):
                        pass
            
            # Try no_title if yes_title didn't work (typically "Under X.Y")
            if strike is None and no_title:
                no_title_lower = no_title.lower()
                no_match = re.search(r'(?:over|under)?\s*([\d.]+)', no_title_lower, re.IGNORECASE)
                if no_match:
                    try:
                        strike = float(no_match.group(1))
                        if "over" in no_title_lower:
                            direction_from_strike = "OVER"
                        elif "under" in no_title_lower:
                            direction_from_strike = "UNDER"
                        if DEBUG_TOTALS:
                            print(f"  ✅ Parsed strike from no_title: {no_title} → {strike}")
                    except (ValueError, AttributeError):
                        pass
        
        # SOURCE 4: Parse strike from product_metadata (QUATERNARY - Fix A)
        if strike is None:
            product_metadata = market.get("product_metadata") or market.get("productMetadata") or market.get("metadata")
            if isinstance(product_metadata, dict):
                # Try common metadata keys
                strike_candidate = (
                    product_metadata.get("strike") or
                    product_metadata.get("strike_price") or
                    product_metadata.get("strikePrice") or
                    product_metadata.get("floor") or
                    product_metadata.get("cap")
                )
                if strike_candidate is not None:
                    try:
                        strike = float(strike_candidate)
                        if DEBUG_TOTALS:
                            print(f"  ✅ Parsed strike from product_metadata: {strike}")
                    except (ValueError, TypeError):
                        pass
        
        # SOURCE 5: Parse strike from dedicated fields (QUINARY - Fix A)
        if strike is None:
            strike_candidate = (
                market.get("strike") or
                market.get("strike_price") or
                market.get("strikePrice") or
                market.get("floor")
            )
            if strike_candidate is not None:
                try:
                    strike = float(strike_candidate)
                    if DEBUG_TOTALS:
                        print(f"  ✅ Parsed strike from dedicated field: {strike}")
                except (ValueError, TypeError):
                    pass
        
        # SOURCE 6: Parse strike from title (FALLBACK - original method, but more flexible)
        if strike is None:
            # More flexible regex: don't require "points" keyword
            title_match = re.search(r'(?:over|under|total)\s+([\d.]+)', title_lower, re.IGNORECASE)
            if title_match:
                try:
                    strike = float(title_match.group(1))
                    if "over" in title_lower:
                        direction_from_strike = "OVER"
                    elif "under" in title_lower:
                        direction_from_strike = "UNDER"
                    if DEBUG_TOTALS:
                        print(f"  ✅ Parsed strike from title: {title_raw} → {strike}")
                except (ValueError, AttributeError):
                    pass
        
        # If strike still not found, skip this market
        if strike is None:
            if DEBUG_TOTALS:
                print(f"  ⚠️ Could not parse strike from any source for: {market_ticker}")
                print(f"     title: {title_raw}")
                print(f"     subtitle: {market.get('subtitle')}")
                print(f"     yes_title: {market.get('yes_title') or market.get('yesTitle')}")
                print(f"     no_title: {market.get('no_title') or market.get('noTitle')}")
            continue  # Strike is required
        
        # Use direction from strike parsing if available, otherwise use direction from market detection
        if direction_from_strike:
            direction = direction_from_strike.lower()
        elif direction_from_ticker:
            direction = direction_from_ticker.lower()
        # Otherwise keep direction from market detection (already set above)
        
        # For canonical POV, we treat all markets as "Over" markets
        # If market is "Under X.Y", we can convert it to "Over X.Y" by using NO side
        # Based on the golden rule, all markets should be "Over X.Y" markets
        # But we accept both Over and Under markets, canonical POV will be Over
        # (Under X.Y is equivalent to NOT Over X.Y, so we can represent it)
        
        # Append market (canonical POV is always Over)
        totals_markets.append({
            "ticker": market_ticker,
            "title": title_raw,
            "parsed_strike": strike,
            "direction": direction or "over",  # Default to over if unclear
        })
    
    return totals_markets


def select_closest_over_strikes(
    canonical_total: float,
    available_markets: List[Dict[str, Any]],
    count: int = 2
) -> List[Dict[str, Any]]:
    """
    Select the N closest Over strikes to canonical total.
    
    Note: For canonical POV, we only use "Over" markets. If markets are labeled as "Under",
    we can still use them but would need to adjust the perspective. For now, we assume all
    markets are "Over X.Y" markets per the golden rule.
    
    Args:
        canonical_total: Unabated canonical total (e.g., 221.5)
        available_markets: List of market dicts with "parsed_strike" key
        count: Number of strikes to select (default 2)
    
    Returns:
        List of selected market dicts, sorted by distance to canonical total
    """
    if not available_markets:
        return []
    
    # Filter to only Over markets (canonical POV)
    over_markets = [m for m in available_markets if m.get("direction", "over").lower() == "over"]
    
    # If no Over markets, we can still use Under markets but convert perspective
    # For now, let's just use Over markets
    if not over_markets:
        if DEBUG_TOTALS:
            print(f"  ⚠️ No Over markets found, available markets have directions: {[m.get('direction') for m in available_markets[:3]]}")
        # Fallback: use all markets (assume they're Over markets even if labeled differently)
        over_markets = available_markets
    
    # Calculate distance for each market
    markets_with_distance = []
    for market in over_markets:
        strike = market.get("parsed_strike")
        if strike is None:
            continue
        
        distance = abs(strike - canonical_total)
        markets_with_distance.append((distance, strike, market))
    
    if not markets_with_distance:
        return []
    
    # Sort by distance (closest first), then by strike (lower first for tie-break)
    markets_with_distance.sort(key=lambda x: (x[0], x[1]))
    
    # Select top N
    selected = [market for _, _, market in markets_with_distance[:count]]
    
    return selected


def format_total_strike_string(strike: float) -> str:
    """
    Format total strike string like "Over 221.5".
    
    Canonical POV is always Over.
    
    Args:
        strike: Strike value (e.g., 221.5)
    
    Returns:
        Formatted string like "Over 221.5"
    """
    return f"Over {strike:.1f}"


def format_total_consensus_string(
    total: float,
    juice: Optional[int] = None
) -> str:
    """
    Format consensus total string like "221.5" or "221.5 -110".
    
    Args:
        total: Unabated canonical total (e.g., 221.5)
        juice: Optional American odds (e.g., -110)
    
    Returns:
        Formatted string like "221.5" or "221.5 -110"
    """
    # Format total (remove .0 if whole number)
    if total == int(total):
        total_str = str(int(total))
    else:
        total_str = f"{total:.1f}"
    
    # Format juice if available
    if juice is not None:
        return f"{total_str} {juice:+d}"
    else:
        return total_str


def build_totals_rows_for_today(games: Optional[List[Dict[str, Any]]] = None, snapshot: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Build totals rows for today's NBA games.
    
    Args:
        games: Optional pre-fetched games list (if None, will fetch internally)
        snapshot: Optional pre-fetched Unabated snapshot (if None, will fetch internally)
    
    Returns:
        List of totals row dicts, each with:
        - All game metadata (date, time, roto, teams)
        - strike: Formatted strike string (e.g., "Over 221.5")
        - consensus: Formatted consensus string (e.g., "221.5" or "221.5 -110")
        - over_kalshi_prob: YES bid break-even prob (after fees)
        - over_kalshi_liq: YES bid liquidity
        - under_kalshi_prob: NO bid break-even prob (after fees)
        - under_kalshi_liq: NO bid liquidity
    """
    # Get today's games with all metadata (use provided or fetch)
    if games is None:
        games = get_today_games_with_fairs_and_kalshi_tickers()
    
    if not games:
        if DEBUG_TOTALS:
            print("No NBA games found for today")
        return []
    
    # Load Kalshi credentials
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        if DEBUG_TOTALS:
            print(f"❌ Failed to load Kalshi credentials: {e}")
        return []
    
    # Get Unabated snapshot for totals extraction (use provided or fetch)
    if snapshot is None:
        from core.reusable_functions import fetch_unabated_snapshot
        snapshot = fetch_unabated_snapshot()
    teams_dict = snapshot.get("teams", {})
    
    # Get today's games
    from data_build.unabated_callsheet import extract_nba_games_today
    today_events = extract_nba_games_today(snapshot)
    
    # Build event lookup by event_start
    events_by_start = {event.get("eventStart"): event for event in today_events}
    
    totals_rows = []
    
    for game in games:
        event_start = game.get("event_start")
        if not event_start:
            continue
        
        # Get away/home team names directly from game (already determined by moneylines module)
        away_team_name = game.get("away_team_name")
        home_team_name = game.get("home_team_name")
        away_roto = game.get("away_roto")
        
        # Get event ticker (already included by moneylines module)
        event_ticker = game.get("event_ticker")
        
        if not away_team_name or not home_team_name:
            if DEBUG_TOTALS:
                print(f"⚠️ Could not determine away/home teams for game")
            continue
        
        if DEBUG_TOTALS:
            print(f"\n{'='*60}")
            print(f"Game: {away_team_name} @ {home_team_name} (ROTO {away_roto})")
            print(f"  event_start: {event_start}")
        
        # Get Unabated event for totals extraction
        unabated_event = events_by_start.get(event_start)
        if not unabated_event:
            if DEBUG_TOTALS:
                print(f"  ⚠️ Could not find Unabated event for {event_start}")
            continue
        
        # DEBUG: Verify event matching
        if DEBUG_TOTALS:
            unabated_event_teams = unabated_event.get("eventTeams", {})
            unabated_team_names = []
            if isinstance(unabated_event_teams, dict):
                for idx, team_info in unabated_event_teams.items():
                    if isinstance(team_info, dict):
                        team_id = team_info.get("id")
                        if team_id:
                            team_name = get_team_name(team_id, teams_dict)
                            unabated_team_names.append(team_name)
            print(f"  [DEBUG] Matched Unabated event teams: {unabated_team_names}")
            print(f"  [DEBUG] Matched event keys: {list(unabated_event.keys())[:10]}")
        
        # Extract totals consensus
        totals_data = extract_unabated_totals(unabated_event, teams_dict)
        
        if not totals_data:
            if DEBUG_TOTALS:
                print(f"  ⚠️ Could not extract Unabated totals for game")
            continue
        
        canonical_total = totals_data.get("total")
        canonical_juice = totals_data.get("juice")
        
        if canonical_total is None:
            if DEBUG_TOTALS:
                print(f"  ⚠️ Missing consensus total - skipping game")
            continue
        
        if DEBUG_TOTALS:
            print(f"  Unabated total: {canonical_total} (juice: {canonical_juice})")
            print(f"  [DEBUG] Formatting consensus: {format_total_consensus_string(canonical_total, canonical_juice)}")
        
        # Discover Kalshi totals markets
        if not event_ticker:
            if DEBUG_TOTALS:
                print(f"  ⚠️ No event ticker, skipping")
            continue
        
        totals_markets = discover_kalshi_totals_markets(event_ticker)
        
        if DEBUG_TOTALS:
            print(f"  Found {len(totals_markets)} totals market(s)")
            # Show first few markets for debug
            for m in totals_markets[:3]:
                print(f"    - {m.get('title')} -> strike={m.get('parsed_strike')}, direction={m.get('direction')}")
        
        if not totals_markets:
            continue
        
        # Enhanced debug logging
        canonical_market_count = len([m for m in totals_markets if m.get("parsed_strike") is not None])
        if DEBUG_TOTALS:
            print(f"\n  [DEBUG] Canonical POV Selection:")
            print(f"    Unabated total: {canonical_total} (juice: {canonical_juice})")
            print(f"    Canonical POV: Over (all totals markets are Over markets)")
            print(f"    Totals markets found: {len(totals_markets)}")
            print(f"    Markets with parsed strike: {canonical_market_count}")
            if canonical_market_count == 0:
                print(f"    ⚠️ ZERO markets with parsed strike - this is why game disappears")
            # Show first few markets with parsing details
            for m in totals_markets[:5]:
                ticker = m.get("ticker", "N/A")
                strike = m.get("parsed_strike", "N/A")
                direction = m.get("direction", "N/A")
                title = m.get("title", "N/A")
                print(f"      - {ticker}")
                print(f"        strike={strike}, direction={direction}, title={title[:50]}")
        
        # Select 2 closest Over strikes (canonical POV = Over)
        selected_strikes = select_closest_over_strikes(
            canonical_total, totals_markets, count=2
        )
        
        if DEBUG_TOTALS:
            print(f"  Selected {len(selected_strikes)} strike(s) for canonical POV (Over)")
            if len(selected_strikes) == 0:
                print(f"  ⚠️ Selection returned 0 strikes - game will be skipped")
            for market in selected_strikes:
                print(f"    - {market.get('ticker')} (strike={market.get('parsed_strike')})")
        
        if not selected_strikes:
            continue
        
        # Collect all unique market tickers we need to fetch
        unique_market_tickers = set()
        for market in selected_strikes:
            market_ticker = market.get("ticker")
            if market_ticker:
                unique_market_tickers.add(market_ticker)
        
        # Pre-fetch all orderbooks in parallel (if we have multiple tickers)
        if len(unique_market_tickers) > 1:
            try:
                api_key_id, private_key_pem = load_creds()
                with ThreadPoolExecutor(max_workers=min(len(unique_market_tickers), 10)) as executor:
                    future_to_ticker = {
                        executor.submit(_fetch_orderbook_with_cache, ticker, api_key_id, private_key_pem): ticker
                        for ticker in unique_market_tickers
                    }
                    # Wait for all to complete (results are cached)
                    for future in future_to_ticker:
                        try:
                            future.result()
                        except Exception:
                            pass  # Error handling done in fetch function
            except Exception:
                pass  # Fall back to sequential fetching if parallel fails
        
        # Build rows for canonical POV only (Over perspective)
        for market in selected_strikes:
            strike_value = market.get("parsed_strike")
            if strike_value is None:
                continue
            
            market_ticker = market.get("ticker")
            if not market_ticker:
                continue
            
            # Get orderbook data for both sides (same market)
            # Over exposure = YES bid, Under exposure = NO bid
            over_orderbook_data = get_spread_orderbook_data(market_ticker, "YES")
            under_orderbook_data = get_spread_orderbook_data(market_ticker, "NO")
            
            over_kalshi_prob = over_orderbook_data.get("tob_effective_prob")
            over_kalshi_liq = over_orderbook_data.get("tob_liq")
            over_kalshi_price_cents = over_orderbook_data.get("tob_bid_cents")  # YES bid price in cents
            under_kalshi_prob = under_orderbook_data.get("tob_effective_prob")
            under_kalshi_liq = under_orderbook_data.get("tob_liq")
            under_kalshi_price_cents = under_orderbook_data.get("tob_bid_cents")  # NO bid price in cents
            
            # Format strike string (always "Over X.Y")
            strike_str = format_total_strike_string(strike_value)
            
            # Format consensus string
            consensus_str = format_total_consensus_string(canonical_total, canonical_juice)
            
            # Targeted debug
            if DEBUG_TOTALS:
                print(f"\n  [DEBUG] Totals row: {strike_str}")
                print(f"    chosen market_ticker: {market_ticker}")
                print(f"    market title: {market.get('title')}")
                print(f"    parsed strike: {strike_value}")
                print(f"    over best bid (cents): {over_orderbook_data.get('tob_bid_cents')}")
                print(f"    over best bid liq: {over_orderbook_data.get('tob_liq')}")
                print(f"    over effective prob: {over_kalshi_prob}")
                print(f"    under best bid (cents): {under_orderbook_data.get('tob_bid_cents')}")
                print(f"    under best bid liq: {under_orderbook_data.get('tob_liq')}")
                print(f"    under effective prob: {under_kalshi_prob}")
            
            totals_rows.append({
                "game_date": game.get("game_date"),
                "event_start": game.get("event_start"),
                "away_roto": game.get("away_roto"),
                "away_team": away_team_name,
                "home_team": home_team_name,
                "consensus": consensus_str,
                "strike": strike_str,
                "kalshi_ticker": market_ticker,
                "kalshi_title": market.get("title"),
                "unabated_total": canonical_total,
                "over_kalshi_prob": over_kalshi_prob,
                "over_kalshi_liq": over_kalshi_liq,
                "over_kalshi_price_cents": over_kalshi_price_cents,  # YES bid price in cents for dollar liquidity calc
                "under_kalshi_prob": under_kalshi_prob,
                "under_kalshi_liq": under_kalshi_liq,
                "under_kalshi_price_cents": under_kalshi_price_cents,  # NO bid price in cents for dollar liquidity calc
                # Placeholders for future implementation
                "over_fair": None,
                "under_fair": None,
                "over_ev": None,
                "under_ev": None,
            })
    
    return totals_rows


def print_totals_table(totals_rows: List[Dict[str, Any]]):
    """
    Print totals table in console format.
    
    Shows: GameDate, GameTime, ROTO, AwayTeam, HomeTeam, Consensus, Strike, Over Kalshi, Under Kalshi
    """
    if not totals_rows:
        print("\nNo totals rows to display")
        return
    
    # Sort by ROTO ascending (None values go last), then by game_date
    totals_rows.sort(key=lambda x: (
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
        f"{'Strike':<15} "
        f"{'OverKalshi':<12} "
        f"{'UnderKalshi':<12}"
    )
    
    print("\n" + "=" * len(header.expandtabs()))
    print("NBA TOTALS DASHBOARD")
    print("=" * len(header.expandtabs()))
    print(header)
    print("-" * len(header.expandtabs()))
    
    # Import formatting functions from main dashboard
    from moneylines.table import format_game_time_pst, is_game_started
    
    for row in totals_rows:
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
        
        # Get Over/Under Kalshi values
        over_kalshi_prob = row.get('over_kalshi_prob')
        under_kalshi_prob = row.get('under_kalshi_prob')
        
        over_kalshi_str = f"{over_kalshi_prob:.4f}" if over_kalshi_prob is not None else "N/A"
        under_kalshi_str = f"{under_kalshi_prob:.4f}" if under_kalshi_prob is not None else "N/A"
        
        print(
            f"{row['game_date']:<12} "
            f"{game_time_str:<10}{started_marker} "
            f"{away_roto_str:<6} "
            f"{row['away_team']:<30} "
            f"{row['home_team']:<30} "
            f"{consensus_str:<15} "
            f"{strike_str:<15} "
            f"{over_kalshi_str:<12} "
            f"{under_kalshi_str:<12}"
        )
    
    print("=" * len(header.expandtabs()) + "\n")


if __name__ == "__main__":
    # Test function
    rows = build_totals_rows_for_today()
    print(f"\nGenerated {len(rows)} totals row(s)")
    print_totals_table(rows)
