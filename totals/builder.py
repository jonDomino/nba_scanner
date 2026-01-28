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
import math

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

# Pinnacle alias in Unabated snapshot for totals alt lines
PINNACLE_TOTALS_MSIDS = [7]  # ms7 = "Sharp Book Price" (alias for Pinnacle)
PINNACLE_TOTALS_OVERROUND = 1.034  # per user: sum of implied probs per (over, under) pair
STRIKE_MATCH_TOL = 1e-6


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
            print(f"    OK Parsed ticker: {ticker} -> direction={direction}, strike={strike}")
        
        return (direction, strike)
    
    # Pattern 2: Pure numeric suffix (e.g., "246" → strike=246.0, direction=None)
    match = re.match(r'^(\d+)$', suffix)
    if match:
        strike_bucket = int(match.group(1))
        
        # FIXED: Return integer total directly (no division by 10)
        strike = float(strike_bucket)
        
        if DEBUG_TOTALS:
            print(f"    OK Parsed ticker: {ticker} -> strike={strike} (no direction)")
        
        return (None, strike)
    
    return (None, None)


def american_to_prob(american_odds: int) -> Optional[float]:
    """Convert American odds to implied probability (vig-included)."""
    try:
        o = int(american_odds)
    except Exception:
        return None
    if o == 0:
        return None
    if o < 0:
        return (-o) / ((-o) + 100.0)
    return 100.0 / (o + 100.0)


def prob_to_american(p: float) -> Optional[int]:
    """Convert probability (0,1) to American odds (rounded)."""
    try:
        p = float(p)
    except Exception:
        return None
    if p <= 0.0 or p >= 1.0:
        return None
    if p >= 0.5:
        odds = - (100.0 * p) / (1.0 - p)
    else:
        odds = (100.0 * (1.0 - p)) / p
    return int(round(odds))


def canonicalize_kalshi_strike(strike: Optional[float]) -> Optional[float]:
    """
    Canonical Kalshi strike always ends in .5 (per user).
    If strike is an integer or ends with .0, add 0.5.
    """
    if strike is None:
        return None
    try:
        x = float(strike)
    except Exception:
        return None
    if abs(x - round(x)) < 1e-9:
        return float(round(x) + 0.5)
    if abs((x * 2.0) - round(x * 2.0)) < 1e-9:
        # Already on a 0.5 grid (or other half increments)
        return x
    return x


def _extract_pinnacle_totals_alt_lines_ms7(event: Dict[str, Any]) -> Dict[float, Dict[str, Any]]:
    """
    Extract totals alt lines from Unabated snapshot using ms7 blocks.

    Returns:
      dict points -> {
        "over_american": int|None,
        "under_american": int|None,
        "over_prob": float|None,
        "under_prob": float|None,
        "estimated_other_side": bool
      }
    """
    market_lines = event.get("gameOddsMarketSourcesLines", {})
    if not isinstance(market_lines, dict):
        return {}

    # Collect all ms7 blocks
    ms_keys = [k for k in market_lines.keys() if isinstance(k, str) and ":ms7:" in k]
    if not ms_keys:
        return {}

    prices_by_points: Dict[float, List[int]] = {}

    def add_price(points: Optional[float], american: Optional[int]) -> None:
        if points is None or american is None:
            return
        prices_by_points.setdefault(points, [])
        if american not in prices_by_points[points]:
            prices_by_points[points].append(american)

    main_points: Optional[float] = None

    for k in ms_keys:
        block = market_lines.get(k)
        if not isinstance(block, dict):
            continue
        bt3 = block.get("bt3")
        if not isinstance(bt3, dict):
            continue

        pts_raw = bt3.get("points") or bt3.get("total") or bt3.get("line") or bt3.get("value")
        price_raw = bt3.get("americanPrice") or bt3.get("price") or bt3.get("unabatedPrice")

        pts = None
        if pts_raw is not None:
            try:
                pts = float(str(pts_raw).strip())
            except Exception:
                pts = None

        price = None
        if price_raw is not None:
            try:
                price = int(str(price_raw).strip())
            except Exception:
                price = None

        if main_points is None and pts is not None:
            main_points = pts

        add_price(pts, price)

        # Alternate lines
        alt_lines = bt3.get("alternateLines")
        if isinstance(alt_lines, list):
            for alt in alt_lines:
                if not isinstance(alt, dict):
                    continue
                a_pts_raw = alt.get("points") or alt.get("total") or alt.get("line") or alt.get("value")
                a_price_raw = alt.get("americanPrice") or alt.get("price") or alt.get("unabatedPrice")
                a_pts = None
                if a_pts_raw is not None:
                    try:
                        a_pts = float(str(a_pts_raw).strip())
                    except Exception:
                        a_pts = None
                a_price = None
                if a_price_raw is not None:
                    try:
                        a_price = int(str(a_price_raw).strip())
                    except Exception:
                        a_price = None
                add_price(a_pts, a_price)

    if not prices_by_points:
        return {}

    # Pair over/under at each points using heuristic + overround estimation if needed
    out: Dict[float, Dict[str, Any]] = {}
    for pts, prices in prices_by_points.items():
        # Use first two unique prices
        unique_prices = prices[:2]
        estimated = False

        if len(unique_prices) == 1:
            estimated = True
            one = unique_prices[0]
            p_one = american_to_prob(one)
            other = None
            p_other = None
            if p_one is not None:
                p_other = PINNACLE_TOTALS_OVERROUND - p_one
                other = prob_to_american(p_other) if (p_other is not None and 0.0 < p_other < 1.0) else None
            unique_prices = [one, other]  # type: ignore[list-item]

        a, b = unique_prices[0], unique_prices[1]

        # Decide which is OVER vs UNDER.
        # Heuristic: for pts > main_points, OVER is cheaper (abs smaller). For pts < main_points, OVER is more expensive (abs larger).
        over_american = None
        under_american = None

        if a is None and b is None:
            continue
        if a is None:
            a, b = b, a
        if b is None:
            # Keep as over, estimate under already done above (or still missing)
            over_american = a
            under_american = b
        else:
            cheaper = a if abs(a) < abs(b) else b
            expensive = b if cheaper == a else a

            if main_points is None:
                over_american, under_american = cheaper, expensive
            else:
                if pts > main_points:
                    over_american, under_american = cheaper, expensive
                elif pts < main_points:
                    over_american, under_american = expensive, cheaper
                else:
                    over_american, under_american = cheaper, expensive

        over_prob = american_to_prob(over_american) if over_american is not None else None
        under_prob = american_to_prob(under_american) if under_american is not None else None

        out[pts] = {
            "over_american": over_american,
            "under_american": under_american,
            "over_prob": over_prob,
            "under_prob": under_prob,
            "estimated_other_side": bool(estimated),
        }

    return out


def _team_names_for_event(event: Dict[str, Any], teams: Dict[str, Any]) -> List[str]:
    """Helper for fallback event matching: extract team display names from an Unabated event."""
    out: List[str] = []
    event_teams = event.get("eventTeams", {})
    if not isinstance(event_teams, dict):
        return out
    for _, team_info in event_teams.items():
        if not isinstance(team_info, dict):
            continue
        team_id = team_info.get("id")
        if team_id is None:
            continue
        team_dict = teams.get(str(team_id)) or teams.get(team_id) or {}
        if isinstance(team_dict, dict):
            name = team_dict.get("name") or team_dict.get("teamName")
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
    return out


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
    
    # Find market source keys. Prefer configured msid, but fall back if that msid
    # is not present in this particular event payload.
    preferred_msid = getattr(config, "UNABATED_MARKET_SOURCE_ID", 49)
    fallback_msids = [preferred_msid, 70, 58, 7, 49]  # Pinnacle variants, then Sharp Book Price, then Unabated
    ms_keys = []
    for msid in fallback_msids:
        ms_token = f":ms{msid}:"
        ms_keys = [k for k in market_lines.keys() if ms_token in k]
        if ms_keys:
            break
    if not ms_keys:
        if DEBUG_TOTALS:
            print(f"    WARN No ms{msid} keys found for event")
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
        print(f"    ms{msid}_keys found: {len(ms_keys)}")
        print(f"    ms{msid}_key samples: {ms_keys[:3]}")
        
        # Print first ms49 block structure
        if ms_keys:
            first_ms49 = market_lines[ms_keys[0]]
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
    for ms49_key in ms_keys:
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
                print(f"    WARN Multiple different totals found: {unique_totals}")
                print(f"    Using first one ({all_bt3_totals[0]['total']})")
        else:
            if DEBUG_TOTALS:
                print(f"    OK All ms blocks have same total: {all_bt3_totals[0]['total']}")
        
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
            print(f"ERR Failed to load Kalshi credentials: {e}")
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
            print(f"  WARN No markets found for totals event {total_event_ticker}")
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
                print(f"  OK Parsed strike from ticker: {market_ticker} -> {strike} ({direction_from_strike or 'no direction'})")
        
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
                            print(f"  OK Parsed strike from subtitle: {subtitle} -> {strike}")
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
                            print(f"  OK Parsed strike from yes_title: {yes_title} -> {strike}")
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
                            print(f"  OK Parsed strike from no_title: {no_title} -> {strike}")
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
                            print(f"  OK Parsed strike from product_metadata: {strike}")
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
                        print(f"  OK Parsed strike from dedicated field: {strike}")
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
                        print(f"  OK Parsed strike from title: {title_raw} -> {strike}")
                except (ValueError, AttributeError):
                    pass
        
        # If strike still not found, skip this market
        if strike is None:
            if DEBUG_TOTALS:
                print(f"  WARN Could not parse strike from any source for: {market_ticker}")
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
            print(f"  WARN No Over markets found, available markets have directions: {[m.get('direction') for m in available_markets[:3]]}")
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
            print(f"ERR Failed to load Kalshi credentials: {e}")
        return []
    
    # Get Unabated snapshot for totals extraction (use provided or fetch)
    if snapshot is None:
        from core.reusable_functions import fetch_unabated_snapshot
        snapshot = fetch_unabated_snapshot()
    teams_dict = snapshot.get("teams", {})
    
    # Get today's games
    from data_build.unabated_callsheet import extract_nba_games_today
    today_events = extract_nba_games_today(snapshot)
    
    def _event_team_ids(ev: Dict[str, Any]) -> frozenset:
        ids = set()
        et = ev.get("eventTeams", {})
        if isinstance(et, dict):
            for _, info in et.items():
                if isinstance(info, dict):
                    tid = info.get("id")
                    if tid is not None:
                        try:
                            ids.add(int(tid))
                        except Exception:
                            pass
        return frozenset(ids)

    # Build event lookup by (event_start, team_ids) to avoid collisions at same start time
    events_by_key: Dict[Tuple[str, frozenset], Dict[str, Any]] = {}
    for ev in today_events:
        es = ev.get("eventStart")
        if not es:
            continue
        key = (es, _event_team_ids(ev))
        # Keep first occurrence if duplicates (should be rare)
        if key not in events_by_key:
            events_by_key[key] = ev
    
    # New schema: game-line-side level (2 rows per matched strike: OVER + UNDER)
    totals_rows: List[Dict[str, Any]] = []
    
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
                print(f"WARN Could not determine away/home teams for game")
            continue
        
        if DEBUG_TOTALS:
            print(f"\n{'='*60}")
            print(f"Game: {away_team_name} @ {home_team_name} (ROTO {away_roto})")
            print(f"  event_start: {event_start}")
        
        # Get Unabated event for totals extraction (match on eventStart + team_ids)
        away_team_id = game.get("away_team_id")
        home_team_id = game.get("home_team_id")
        unabated_event = None
        if away_team_id is not None and home_team_id is not None:
            try:
                key = (event_start, frozenset({int(away_team_id), int(home_team_id)}))
                unabated_event = events_by_key.get(key)
            except Exception:
                unabated_event = None

        # Fallback: match by eventStart + team names (slower, but safer than wrong event)
        if not unabated_event:
            target_names = {str(away_team_name).strip().lower(), str(home_team_name).strip().lower()}
            for ev in today_events:
                if ev.get("eventStart") != event_start:
                    continue
                ev_names = {n.strip().lower() for n in _team_names_for_event(ev, teams_dict)}
                if target_names.issubset(ev_names):
                    unabated_event = ev
                    break
        if not unabated_event:
            if DEBUG_TOTALS:
                print(f"  WARN Could not find Unabated event for {event_start}")
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
        
        # Extract Pinnacle (ms7 Sharp Book Price) totals alt lines for this game
        pinnacle_lines = _extract_pinnacle_totals_alt_lines_ms7(unabated_event)
        if not pinnacle_lines:
            if DEBUG_TOTALS:
                print("  WARN No Pinnacle(ms7) totals lines found for game")
            continue
        if DEBUG_TOTALS:
            pts_sorted = sorted(pinnacle_lines.keys())
            print(f"  Pinnacle(ms7) totals points: count={len(pts_sorted)} sample={pts_sorted[:10]}")
        
        # Discover Kalshi totals markets
        if not event_ticker:
            if DEBUG_TOTALS:
                print(f"  WARN No event ticker, skipping")
            continue
        
        totals_markets = discover_kalshi_totals_markets(event_ticker)
        
        if DEBUG_TOTALS:
            print(f"  Found {len(totals_markets)} totals market(s)")
            # Show first few markets for debug
            for m in totals_markets[:3]:
                print(f"    - {m.get('title')} -> strike={m.get('parsed_strike')}, direction={m.get('direction')}")
        
        if not totals_markets:
            continue
        
        # Canonicalize Kalshi strikes to always end in .5, then inner-join with Pinnacle alt points
        # (User requirement: show only where Kalshi and Pinnacle have the same strike)
        kalshi_markets_by_strike: Dict[float, Dict[str, Any]] = {}
        for m in totals_markets:
            strike_raw = m.get("parsed_strike")
            strike = canonicalize_kalshi_strike(strike_raw)
            ticker = m.get("ticker")
            if strike is None or not ticker:
                continue
            # Prefer "over" markets (Kalshi totals are typically Over markets with NO representing Under)
            direction = (m.get("direction") or "").lower()
            if direction and direction not in ["over", "total"]:
                continue
            # Keep first ticker per strike (stable)
            if strike not in kalshi_markets_by_strike:
                kalshi_markets_by_strike[strike] = m

        if not kalshi_markets_by_strike:
            continue

        # Find matches within tolerance
        pinnacle_points = sorted(pinnacle_lines.keys())
        if DEBUG_TOTALS:
            k_strikes_sorted = sorted(kalshi_markets_by_strike.keys())
            print(f"  Kalshi canonical strikes: count={len(k_strikes_sorted)} sample={k_strikes_sorted[:10]}")
        matched: List[Tuple[float, float]] = []  # (kalshi_strike, pinnacle_points)
        for k_strike in sorted(kalshi_markets_by_strike.keys()):
            best = None
            best_d = None
            for p_pts in pinnacle_points:
                d = abs(p_pts - k_strike)
                if d <= STRIKE_MATCH_TOL and (best_d is None or d < best_d):
                    best = p_pts
                    best_d = d
            if best is not None:
                matched.append((k_strike, best))

        if not matched:
            if DEBUG_TOTALS:
                print("  WARN No inner-join strikes between Kalshi and Pinnacle(ms7)")
                # show closest distances for debugging
                k_strikes_sorted = sorted(kalshi_markets_by_strike.keys())[:10]
                p_pts_sorted = pinnacle_points[:10]
                if k_strikes_sorted and p_pts_sorted:
                    approx = []
                    for ks in k_strikes_sorted:
                        dmin = min(abs(pp - ks) for pp in p_pts_sorted)
                        approx.append((ks, dmin))
                    print(f"    closest deltas sample: {approx[:10]}")
            continue
        
        # Collect all unique market tickers we need to fetch
        unique_market_tickers = set()
        for k_strike, _ in matched:
            market = kalshi_markets_by_strike.get(k_strike)
            if market and market.get("ticker"):
                unique_market_tickers.add(market["ticker"])
        
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
        
        # Emit game-line-side rows
        for k_strike, p_pts in matched:
            market = kalshi_markets_by_strike.get(k_strike)
            if not market:
                continue
            market_ticker = market.get("ticker")
            if not market_ticker:
                continue

            # Kalshi: Over exposure = YES bid; Under exposure = NO bid
            over_ob = get_spread_orderbook_data(market_ticker, "YES")
            under_ob = get_spread_orderbook_data(market_ticker, "NO")

            over_kalshi_prob = over_ob.get("tob_effective_prob")
            under_kalshi_prob = under_ob.get("tob_effective_prob")
            over_kalshi_liq = over_ob.get("tob_liq")
            under_kalshi_liq = under_ob.get("tob_liq")
            over_kalshi_price_cents = over_ob.get("tob_bid_cents")
            under_kalshi_price_cents = under_ob.get("tob_bid_cents")

            pin = pinnacle_lines.get(p_pts, {})
            over_pin_prob = pin.get("over_prob")
            under_pin_prob = pin.get("under_prob")

            # Pinnacle POV inversion (like moneylines):
            # - OVER row shows inverse of UNDER Pinnacle prob: 1 - P(UNDER)
            # - UNDER row shows inverse of OVER Pinnacle prob: 1 - P(OVER)
            over_pinnacle = (1.0 - under_pin_prob) if under_pin_prob is not None else over_pin_prob
            under_pinnacle = (1.0 - over_pin_prob) if over_pin_prob is not None else under_pin_prob

            over_ev = (over_pinnacle - over_kalshi_prob) * 100.0 if (over_pinnacle is not None and over_kalshi_prob is not None) else None
            under_ev = (under_pinnacle - under_kalshi_prob) * 100.0 if (under_pinnacle is not None and under_kalshi_prob is not None) else None

            away_code = game.get("kalshi_away_code") or ""
            home_code = game.get("kalshi_home_code") or ""
            game_code = f"{away_code}@{home_code}" if (away_code and home_code) else ""

            base = {
                "game_date": game.get("game_date"),
                "event_start": game.get("event_start"),
                "away_roto": game.get("away_roto"),
                "game": game_code,
                # Keep team names on the row for debugging/export, but the dashboard uses `game`.
                "away_team": away_team_name,
                "home_team": home_team_name,
                "market": "TOTALS",
                "line": float(k_strike),
            }

            totals_rows.append({
                **base,
                "side": "OVER",
                "kalshi_prob": over_kalshi_prob,
                "kalshi_liq": over_kalshi_liq,
                "kalshi_price_cents": over_kalshi_price_cents,
                "pinnacle_prob": over_pinnacle,
                "ev": over_ev,
                "market_ticker": market_ticker,
            })

            totals_rows.append({
                **base,
                "side": "UNDER",
                "kalshi_prob": under_kalshi_prob,
                "kalshi_liq": under_kalshi_liq,
                "kalshi_price_cents": under_kalshi_price_cents,
                "pinnacle_prob": under_pinnacle,
                "ev": under_ev,
                "market_ticker": market_ticker,
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
