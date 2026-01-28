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
from concurrent.futures import ThreadPoolExecutor
import math

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

# Unabated msid for Pinnacle proxy (per user: use ms7 "Sharp Book Price" for alt lines)
PINNACLE_SPREADS_MSID = 7
PINNACLE_SPREADS_OVERROUND = 1.034  # sum of implied probs per (home, away) pair
STRIKE_MATCH_TOL = 1e-6


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
        return x
    return x


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


def _estimate_missing_juice_from_known(
    known_american: int,
    overround_target: float = PINNACLE_SPREADS_OVERROUND
) -> Optional[int]:
    """
    Estimate missing side American odds so that:
      implied_prob(known) + implied_prob(missing) ~= overround_target
    """
    p_known = american_to_prob(known_american)
    if p_known is None:
        return None
    p_missing = overround_target - p_known
    # Clamp into (0,1) to avoid invalid conversions
    p_missing = max(1e-6, min(1.0 - 1e-6, p_missing))
    return prob_to_american(p_missing)


def _extract_pinnacle_spreads_alt_lines_ms7(
    event: Dict[str, Any],
    home_team_id: Optional[int],
    away_team_id: Optional[int],
) -> Dict[float, Dict[str, Any]]:
    """
    Extract ms7 ("Sharp Book Price") alt spread lines and retain BOTH signs when present.

    Returns:
      dict magnitude -> {
        "home": { -1: american_for_-X, +1: american_for_+X },
        "away": { -1: american_for_-X, +1: american_for_+X },
      }
    """
    market_lines = event.get("gameOddsMarketSourcesLines", {})
    if not isinstance(market_lines, dict):
        return {}

    event_teams = event.get("eventTeams", {})
    if not isinstance(event_teams, dict):
        return {}

    ms_keys = [k for k in market_lines.keys() if isinstance(k, str) and f":ms{PINNACLE_SPREADS_MSID}:" in k]
    if not ms_keys:
        return {}

    # team_id -> list of (spread_line, american)
    per_team: Dict[int, List[Tuple[float, int]]] = {}

    def add(team_id: int, spread_line: Optional[float], american: Optional[int]) -> None:
        if spread_line is None or american is None:
            return
        per_team.setdefault(team_id, [])
        per_team[team_id].append((float(spread_line), int(american)))

    for k in ms_keys:
        block = market_lines.get(k)
        if not isinstance(block, dict):
            continue

        # Parse side index from key prefix (e.g., "si1:ms7:an0")
        try:
            parts = k.split(":")
            side_token = parts[0]
            if not (side_token.startswith("si") and len(side_token) > 2):
                continue
            side_idx = int(side_token[2:])
        except Exception:
            continue

        team_info = event_teams.get(str(side_idx), {})
        if not isinstance(team_info, dict):
            continue
        team_id = team_info.get("id")
        if team_id is None:
            continue

        bt2 = block.get("bt2")
        if not isinstance(bt2, dict):
            continue

        spread_raw = bt2.get("line") or bt2.get("spread") or bt2.get("value") or bt2.get("points")
        price_raw = bt2.get("americanPrice") or bt2.get("price") or bt2.get("unabatedPrice") or bt2.get("juice")

        spread = None
        if spread_raw is not None:
            try:
                spread = float(str(spread_raw).strip())
            except Exception:
                spread = None
        price = None
        if price_raw is not None:
            try:
                price = int(str(price_raw).strip())
            except Exception:
                price = None

        add(team_id, spread, price)

        alt_lines = bt2.get("alternateLines")
        if isinstance(alt_lines, list):
            for alt in alt_lines:
                if not isinstance(alt, dict):
                    continue
                a_spread_raw = alt.get("line") or alt.get("spread") or alt.get("value") or alt.get("points")
                a_price_raw = alt.get("americanPrice") or alt.get("price") or alt.get("unabatedPrice") or alt.get("juice")
                a_spread = None
                if a_spread_raw is not None:
                    try:
                        a_spread = float(str(a_spread_raw).strip())
                    except Exception:
                        a_spread = None
                a_price = None
                if a_price_raw is not None:
                    try:
                        a_price = int(str(a_price_raw).strip())
                    except Exception:
                        a_price = None
                add(team_id, a_spread, a_price)

    if home_team_id is None or away_team_id is None:
        return {}

    home_lines = per_team.get(home_team_id, [])
    away_lines = per_team.get(away_team_id, [])
    if not home_lines and not away_lines:
        return {}

    out: Dict[float, Dict[str, Any]] = {}

    def upsert(side: str, spread_line: float, american: int) -> None:
        mag = float(abs(float(spread_line)))
        sign = -1 if float(spread_line) < 0 else 1
        out.setdefault(mag, {"home": {}, "away": {}})
        # Keep first seen per (side, sign, mag) for stability
        if sign not in out[mag][side]:
            out[mag][side][sign] = int(american)

    for spread_line, american in home_lines:
        upsert("home", spread_line, american)
    for spread_line, american in away_lines:
        upsert("away", spread_line, american)

    return out


def _pair_pinnacle_spreads_by_overround(
    raw_by_mag: Dict[float, Dict[str, Any]],
    overround_target: float = PINNACLE_SPREADS_OVERROUND,
) -> Dict[float, Dict[str, Any]]:
    """
    Convert raw sign-retaining ms7 spreads into a single "best orientation" per magnitude.

    ms7 can contain BOTH +X and -X for each team. We pick the orientation that looks like a real
    spread market (one side -X, the other +X) by choosing the pairing whose implied probs sum
    closest to `overround_target`.

    Returns:
      dict magnitude -> {
        "home_line": float, "away_line": float,             # signed
        "home_american": int|None, "away_american": int|None,
        "home_prob": float|None, "away_prob": float|None,
        "estimated_other_side": bool,
        "orientation_used": str, "orientation_score": float
      }
    """
    out: Dict[float, Dict[str, Any]] = {}

    def _score_pair(am1: Optional[int], am2: Optional[int]) -> Tuple[float, bool, Optional[int], Optional[int]]:
        used_est = False
        a1 = am1
        a2 = am2
        if a1 is None and a2 is None:
            return (1e9, True, None, None)
        if a1 is None and a2 is not None:
            a1 = _estimate_missing_juice_from_known(a2, overround_target)
            used_est = True
        if a2 is None and a1 is not None:
            a2 = _estimate_missing_juice_from_known(a1, overround_target)
            used_est = True
        p1 = american_to_prob(a1) if a1 is not None else None
        p2 = american_to_prob(a2) if a2 is not None else None
        if p1 is None or p2 is None:
            return (1e9, True, a1, a2)
        s = abs((p1 + p2) - overround_target)
        if used_est:
            s += 0.01
        return (s, used_est, a1, a2)

    for mag, d in raw_by_mag.items():
        h = d.get("home") or {}
        a = d.get("away") or {}
        # Orientation 1: home -X with away +X
        score1, est1, h1, a1 = _score_pair(h.get(-1), a.get(+1))
        # Orientation 2: home +X with away -X
        score2, est2, h2, a2 = _score_pair(h.get(+1), a.get(-1))

        if score1 >= 1e8 and score2 >= 1e8:
            continue

        use1 = score1 <= score2
        if use1:
            home_line = -float(mag)
            away_line = +float(mag)
            home_am, away_am = h1, a1
            used_est = est1
            used = "home-neg/away-pos"
            sc = float(score1)
        else:
            home_line = +float(mag)
            away_line = -float(mag)
            home_am, away_am = h2, a2
            used_est = est2
            used = "home-pos/away-neg"
            sc = float(score2)

        out[float(mag)] = {
            "home_line": home_line,
            "away_line": away_line,
            "home_american": home_am,
            "away_american": away_am,
            "home_prob": american_to_prob(home_am) if home_am is not None else None,
            "away_prob": american_to_prob(away_am) if away_am is not None else None,
            "estimated_other_side": bool(used_est),
            "orientation_used": used,
            "orientation_score": sc,
        }

    return out


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
        return {}
    
    # Store spreads by team_id
    spreads_by_team_id = {}
    
    # Iterate through all ms{msid} blocks
    for ms_key in ms_keys:
        ms_block = market_lines[ms_key]
        if not isinstance(ms_block, dict):
            continue
        
        # Parse side index from key prefix (e.g., "si1:ms49:an0" -> side_idx = 1)
        try:
            parts = ms_key.split(":")
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
        bt2_line = ms_block.get("bt2")
        if bt2_line is None or not isinstance(bt2_line, dict):
            # Try other possible keys for spread
            bt2_line = ms_block.get("spread") or ms_block.get("spreadLine")
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


# Cache for orderbooks (key: market_ticker, value: orderbook dict)
_orderbook_cache: Dict[str, Dict[str, Any]] = {}


def _fetch_orderbook_with_cache(market_ticker: str, api_key_id: str, private_key_pem: str) -> Dict[str, Any]:
    """Fetch orderbook with caching to avoid duplicate API calls for same ticker."""
    market_ticker = market_ticker.strip().upper()
    
    if market_ticker in _orderbook_cache:
        return _orderbook_cache[market_ticker]
    
    orderbook = fetch_orderbook(api_key_id, private_key_pem, market_ticker)
    _orderbook_cache[market_ticker] = orderbook or {}
    return orderbook or {}


def get_spread_orderbook_data(market_ticker: str, side_to_trade: str = "YES", orderbook: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Fetch orderbook and compute TOB for a specific side (YES or NO) of a spread market.
    
    Args:
        market_ticker: Kalshi market ticker
        side_to_trade: "YES" or "NO" - which side's bids to extract
        orderbook: Optional pre-fetched orderbook (if provided, skips API call)
    
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
    # Use provided orderbook (no creds needed) or fetch (with caching, requires creds)
    if orderbook is None:
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
        orderbook = _fetch_orderbook_with_cache(market_ticker, api_key_id, private_key_pem)
    
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


def build_spreads_rows_for_today(games: Optional[List[Dict[str, Any]]] = None, snapshot: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Build spreads rows for today's NBA games.
    
    Args:
        games: Optional pre-fetched games list (if None, will fetch internally)
        snapshot: Optional pre-fetched Unabated snapshot (if None, will fetch internally)
    
    Returns:
        List of spread row dicts (2 rows per matched strike), using unified dashboard schema:
        - game_date, event_start, away_roto, game (e.g., LAL@CLE)
        - market="SPREADS"
        - side: team abbreviation (home_code or away_code)
        - line: signed spread from that team's POV (home: -X, away: +X)
        - kalshi_prob: YES(top) after fees for home row; NO(top) after fees for away row
        - kalshi_liq: liquidity at that top bid
        - kalshi_price_cents: cents at that top bid
        - pinnacle_prob: inverted opponent POV (home shows 1-away_prob, away shows 1-home_prob)
        - ev: (pinnacle_prob - kalshi_prob) * 100
        - market_ticker: Kalshi market ticker used (home POV market)
    """
    # Get today's games with all metadata (use provided or fetch)
    if games is None:
        games = get_today_games_with_fairs_and_kalshi_tickers()
    
    if not games:
        if DEBUG_SPREADS:
            print("No NBA games found for today")
        return []
    
    # Load team xref
    xref_path = config.NBA_XREF_FILE
    xref = load_team_xref(xref_path)
    
    # Load Kalshi credentials (optional: if missing, we can't build spread rows)
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        if DEBUG_SPREADS:
            print(f"❌ Failed to load Kalshi credentials: {e}")
        return []
    
    # Get Unabated snapshot for spread extraction (use provided or fetch)
    if snapshot is None:
        from core.reusable_functions import fetch_unabated_snapshot
        snapshot = fetch_unabated_snapshot()
    teams_dict = snapshot.get("teams", {})
    
    # Get today's games with spreads
    from data_build.unabated_callsheet import extract_nba_games_today
    today_events = extract_nba_games_today(snapshot)
    
    # Build events list (we will match by eventStart + team ids to avoid collisions)
    
    spread_rows = []
    
    def _find_unabated_event_for_game(event_start: str, away_team_id: Optional[int], home_team_id: Optional[int]) -> Optional[Dict[str, Any]]:
        if not event_start:
            return None
        for ev in today_events:
            if ev.get("eventStart") != event_start:
                continue
            ev_teams = ev.get("eventTeams", {})
            if not isinstance(ev_teams, dict):
                continue
            ids = set()
            for _, ti in ev_teams.items():
                if isinstance(ti, dict) and ti.get("id") is not None:
                    ids.add(ti.get("id"))
            if away_team_id in ids and home_team_id in ids:
                return ev
        return None

    for game in games:
        event_start = game.get("event_start")
        if not event_start:
            continue

        # Get away/home team names directly from game (already determined by slate)
        away_team_name = game.get("away_team_name")
        home_team_name = game.get("home_team_name")
        
        # Get event ticker (already included by moneylines module)
        event_ticker = game.get("event_ticker")
        
        if not away_team_name or not home_team_name:
            if DEBUG_SPREADS:
                print(f"⚠️ Could not determine away/home teams for game")
            continue
        
        away_team_id = game.get("away_team_id")
        home_team_id = game.get("home_team_id")

        # Find the correct Unabated event for this game
        unabated_event = _find_unabated_event_for_game(event_start, away_team_id, home_team_id)
        if not unabated_event:
            if DEBUG_SPREADS:
                print(f"⚠️ Could not find Unabated event for {event_start} (team match)")
            continue

        away_code = game.get("kalshi_away_code") or ""
        home_code = game.get("kalshi_home_code") or ""
        game_code = f"{away_code}@{home_code}" if (away_code and home_code) else ""

        # Discover Kalshi spread markets
        if not event_ticker:
            if DEBUG_SPREADS:
                print(f"  ⚠️ No event ticker, skipping")
            continue

        spread_markets = discover_kalshi_spread_markets(event_ticker, away_team_name, home_team_name, xref)
        if not spread_markets:
            continue

        # Kalshi markets grouped by canonical strike (can include either team)
        kalshi_candidates_by_strike: Dict[float, List[Dict[str, Any]]] = {}
        for m in spread_markets:
            s_raw = m.get("parsed_strike")
            s = canonicalize_kalshi_strike(s_raw)
            t = m.get("ticker")
            if s is None or not t:
                continue
            kalshi_candidates_by_strike.setdefault(s, []).append(m)

        if not kalshi_candidates_by_strike:
            continue

        # Extract Pinnacle(ms7) alt spreads (retain both signs), then pick the best +/- orientation per magnitude.
        pinnacle_raw_by_mag = _extract_pinnacle_spreads_alt_lines_ms7(unabated_event, home_team_id, away_team_id)
        if not pinnacle_raw_by_mag:
            continue
        pinnacle_paired_by_mag = _pair_pinnacle_spreads_by_overround(pinnacle_raw_by_mag, PINNACLE_SPREADS_OVERROUND)
        if not pinnacle_paired_by_mag:
            continue

        # Match strikes within tolerance: Kalshi strike (positive magnitude) to Pinnacle magnitudes
        pinnacle_mags = sorted(pinnacle_paired_by_mag.keys())
        matched: List[Tuple[float, float]] = []  # (kalshi_strike, pinnacle_mag)
        for k_strike in sorted(kalshi_candidates_by_strike.keys()):
            best = None
            best_d = None
            for p_mag in pinnacle_mags:
                d = abs(p_mag - k_strike)
                if d <= STRIKE_MATCH_TOL and (best_d is None or d < best_d):
                    best = p_mag
                    best_d = d
            if best is not None:
                matched.append((k_strike, best))

        if not matched:
            continue

        # Choose the Kalshi market ticker to use per strike, then fetch orderbooks once per ticker.
        # IMPORTANT (per user correction):
        # Kalshi "TEAM wins by over X" corresponds to TEAM -X (favorite POV) and opponent +X (via NO).
        # Therefore, we should only match a Kalshi market if Pinnacle(ms7) has that SAME team at -X
        # for this magnitude. This prevents false matches when Pinnacle has the team as +X (underdog).
        chosen_market_by_strike: Dict[float, Dict[str, Any]] = {}
        tickers: List[str] = []
        for k_strike, p_mag in matched:
            candidates = kalshi_candidates_by_strike.get(k_strike) or []
            if not candidates:
                continue
            pinp = pinnacle_paired_by_mag.get(p_mag) or {}
            if not pinp:
                continue

            # Determine which team is -X per Pinnacle at this magnitude
            home_line = float(pinp.get("home_line", 0.0))
            away_line = float(pinp.get("away_line", 0.0))

            favored_code = None
            if abs(home_line + float(k_strike)) <= STRIKE_MATCH_TOL:
                favored_code = home_code
            elif abs(away_line + float(k_strike)) <= STRIKE_MATCH_TOL:
                favored_code = away_code

            # Filter candidates to those whose TEAM matches the -X side in Pinnacle
            filtered = []
            for c in candidates:
                tc = (c.get("market_team_code") or "").strip()
                if not tc:
                    continue
                if favored_code and tc != favored_code:
                    continue
                filtered.append(c)

            if not filtered:
                # No true overlap at this magnitude (sign mismatch); skip this strike entirely.
                continue

            chosen = filtered[0]
            chosen_market_by_strike[k_strike] = chosen
            t = chosen.get("ticker")
            if t:
                tickers.append(t)

        orderbooks_by_ticker: Dict[str, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(len(tickers), 10)) as executor:
            future_to_ticker = {
                executor.submit(_fetch_orderbook_with_cache, t, api_key_id, private_key_pem): t
                for t in tickers
                if t
            }
            for fut in future_to_ticker:
                t = future_to_ticker[fut]
                try:
                    orderbooks_by_ticker[t] = fut.result() or {}
                except Exception:
                    orderbooks_by_ticker[t] = {}

        for k_strike, p_mag in matched:
            m = chosen_market_by_strike.get(k_strike) or {}
            market_ticker = m.get("ticker")
            if not market_ticker:
                continue
            ob = orderbooks_by_ticker.get(market_ticker) or {}

            # Kalshi: YES = market team "wins by over X"; NO = opponent side.
            yes_ob = get_spread_orderbook_data(market_ticker, "YES", orderbook=ob)
            no_ob = get_spread_orderbook_data(market_ticker, "NO", orderbook=ob)

            yes_prob = yes_ob.get("tob_effective_prob")
            no_prob = no_ob.get("tob_effective_prob")
            yes_liq = yes_ob.get("tob_liq")
            no_liq = no_ob.get("tob_liq")
            yes_cents = yes_ob.get("tob_bid_cents")
            no_cents = no_ob.get("tob_bid_cents")

            pinp = pinnacle_paired_by_mag.get(p_mag, {})
            home_prob = pinp.get("home_prob")
            away_prob = pinp.get("away_prob")
            home_line = float(pinp.get("home_line", -p_mag))
            away_line = float(pinp.get("away_line", +p_mag))

            market_team_code = (m.get("market_team_code") or "").strip()
            if market_team_code not in [home_code, away_code]:
                continue
            opp_code = away_code if market_team_code == home_code else home_code

            prob_by_code = {home_code: home_prob, away_code: away_prob}
            line_by_code = {home_code: home_line, away_code: away_line}

            # By construction, market_team_code is the -X side in Pinnacle at this magnitude
            fav_code = market_team_code
            dog_code = opp_code
            fav_prob = prob_by_code.get(fav_code)
            dog_prob = prob_by_code.get(dog_code)

            # Pinnacle POV inversion (like ML/TOTALS): show inverse of opponent price
            fav_pinnacle = (1.0 - dog_prob) if dog_prob is not None else fav_prob
            dog_pinnacle = (1.0 - fav_prob) if fav_prob is not None else dog_prob

            fav_ev = (fav_pinnacle - yes_prob) * 100.0 if (fav_pinnacle is not None and yes_prob is not None) else None
            dog_ev = (dog_pinnacle - no_prob) * 100.0 if (dog_pinnacle is not None and no_prob is not None) else None

            base = {
                "game_date": game.get("game_date"),
                "event_start": game.get("event_start"),
                "away_roto": game.get("away_roto"),
                "home_roto": game.get("home_roto"),
                "game": game_code,
                "market": "SPREADS",
            }

            # Favorite row (YES exposure)
            spread_rows.append({
                **base,
                "roto": game.get("home_roto") if fav_code == home_code else game.get("away_roto"),
                "side": fav_code,
                # Kalshi semantics: YES = TEAM wins by over X => TEAM -X
                "line": -float(k_strike),
                "kalshi_prob": yes_prob,
                "kalshi_liq": yes_liq,
                "kalshi_price_cents": yes_cents,
                "pinnacle_prob": fav_pinnacle,
                "ev": fav_ev,
                "market_ticker": market_ticker,
            })

            # Opponent row (NO exposure)
            spread_rows.append({
                **base,
                "roto": game.get("home_roto") if dog_code == home_code else game.get("away_roto"),
                "side": dog_code,
                # Opponent POV: +X
                "line": float(k_strike),
                "kalshi_prob": no_prob,
                "kalshi_liq": no_liq,
                "kalshi_price_cents": no_cents,
                "pinnacle_prob": dog_pinnacle,
                "ev": dog_ev,
                "market_ticker": market_ticker,
            })

        # New-format spreads rows are fully built for this game; skip legacy logic below.
        continue
        
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
        
        # Collect all unique market tickers we need to fetch
        unique_market_tickers = set()
        for market, side_to_trade_canonical in selected_strikes:
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
    
    # Sort by ROTO, time, line magnitude, then side (home first per strike)
    spread_rows.sort(key=lambda x: (
        x.get('away_roto') is None,
        x.get('away_roto') or 0,
        x.get('event_start') or "",
        x.get('line') is None,
        abs(x.get('line') or 0),
        (x.get('line') or 0) > 0,  # negative (home) first
        (x.get("side") or ""),
    ))

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
