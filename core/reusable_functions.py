"""
Reusable functions extracted from main.py for NBA moneyline scanner.
These functions are league-agnostic and can be reused as-is or with minimal modification.
"""

import re
import csv
import time
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List

import requests
try:
    from zoneinfo import ZoneInfo
    USE_PYTZ = False
except ImportError:
    # Python < 3.9 fallback - use pytz
    import pytz
    USE_PYTZ = True

from utils import config
from utils.kalshi_api import make_request
from pricing.fees import fee_dollars, maker_fee_cents


# ============================================================================
# Constants
# ============================================================================

MONTHS = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05", "JUN": "06",
    "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12"
}


# ============================================================================
# Unabated Integration
# ============================================================================

def fetch_unabated_snapshot() -> Dict[str, Any]:
    """
    Fetch Unabated game odds snapshot.
    """
    if not config.UNABATED_API_KEY:
        raise ValueError("Unabated API key not configured")
    
    url = f"{config.UNABATED_PROD_URL}?x-api-key={config.UNABATED_API_KEY}"
    
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise Exception(f"Failed to fetch Unabated snapshot: {e}")


# ============================================================================
# Date and Key Building
# ============================================================================

def unabated_event_to_kalshi_date(event_start: str) -> str:
    """
    Convert Unabated UTC eventStart to Kalshi local date (US/Eastern).
    
    Kalshi uses US Eastern local dates in tickers, while Unabated uses UTC.
    For evening games (6-9 PM ET), this can cross the UTC midnight boundary,
    causing date mismatches if we use UTC directly.
    
    Example:
        Unabated: 2025-12-16T00:00:00Z (midnight UTC = 7 PM ET on Dec 15)
        Kalshi: 25DEC15 (Dec 15 local)
        Returns: "20251215"
    """
    if USE_PYTZ:
        import pytz
        utc = pytz.UTC
        eastern = pytz.timezone("US/Eastern")
    else:
        utc = ZoneInfo("UTC")
        eastern = ZoneInfo("US/Eastern")
    
    dt_utc = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=utc)
    else:
        dt_utc = dt_utc.astimezone(utc)
    
    dt_local = dt_utc.astimezone(eastern)
    return dt_local.strftime("%Y%m%d")


def build_canonical_key(league: str, event_start: str, team_a: str, team_b: str) -> str:
    """
    Build canonical game key: {LEAGUE}_{YYYYMMDD}_{TEAM_A}_{TEAM_B}
    Teams are sorted alphabetically.
    Uses US Eastern local date (not UTC) to match Kalshi ticker dates.
    """
    date_str = unabated_event_to_kalshi_date(event_start)
    teams_sorted = sorted([team_a.upper(), team_b.upper()])
    return f"{league}_{date_str}_{teams_sorted[0]}_{teams_sorted[1]}"


# ============================================================================
# Kalshi Event Matching
# ============================================================================

def parse_kalshi_event_ticker(event_ticker: str) -> Optional[Tuple[str, str]]:
    """
    Parse Kalshi event ticker to extract date (YYYYMMDD) and team codes.
    Example: "KXNCAAMBGAME-25DEC14CHSLCHI" -> ("20251214", "CHSLCHI")
    Returns (date_str, team_codes_str) or None if parsing fails.
    """
    try:
        if "-" not in event_ticker:
            return None
        
        token = event_ticker.split("-")[1]  # e.g., "25DEC14VANNJ"
        
        if len(token) < 7:
            return None
        
        yy = token[0:2]
        mmm = token[2:5].upper()
        dd = token[5:7]
        rest = token[7:]  # Team codes
        
        if mmm not in MONTHS:
            return None
        
        yyyy = "20" + yy
        mm = MONTHS[mmm]
        yyyymmdd = f"{yyyy}{mm}{dd}"
        
        return (yyyymmdd, rest)
    except Exception:
        return None


def fetch_kalshi_events(api_key_id: str, private_key_pem: str, series_ticker: str) -> List[Dict[str, Any]]:
    """
    Fetch all open Kalshi events for a given series (handles pagination).
    
    Args:
        api_key_id: Kalshi API key ID
        private_key_pem: Kalshi private key PEM
        series_ticker: Series ticker (e.g., "KXNBAGAME" for NBA)
    
    Returns:
        List of event dicts (all pages combined)
    """
    path = "/events"
    all_events = []
    cursor = None
    
    while True:
        params = {
            "series_ticker": series_ticker,
            "status": "open",
            "with_nested_markets": "true"
        }
        
        if cursor:
            params["cursor"] = cursor
        
        resp = make_request(api_key_id, private_key_pem, "GET", path, params)
        events = resp.get("events", [])
        all_events.extend(events)
        
        # Check for next page
        cursor = resp.get("cursor") or resp.get("next_cursor")
        if not cursor:
            break
    
    return all_events


def fetch_kalshi_markets_for_event(
    api_key_id: str,
    private_key_pem: str,
    event_ticker: str
) -> List[Dict[str, Any]]:
    """
    Fetch all markets for a Kalshi event (including totals, moneylines, etc.).
    
    Args:
        api_key_id: Kalshi API key ID
        private_key_pem: Kalshi private key PEM
        event_ticker: Event ticker (e.g., "KXNBAGAME-25DEC15LALLAL")
    
    Returns:
        List of market dicts for the event
    """
    path = "/markets"
    params = {
        "event_ticker": event_ticker,
        "status": "open"
    }
    
    resp = make_request(api_key_id, private_key_pem, "GET", path, params)
    return resp.get("markets", [])


def fetch_orderbook(api_key_id: str, private_key_pem: str, market_ticker: str) -> Optional[Dict[str, Any]]:
    """
    Fetch Kalshi orderbook for a market.
    """
    path = f"/markets/{market_ticker}/orderbook"
    
    try:
        resp = make_request(api_key_id, private_key_pem, "GET", path)
        return resp.get("orderbook", {})
    except Exception:
        return None


# ============================================================================
# Orderbook Utilities
# ============================================================================

def derive_implied_yes_asks(no_bids: List[List[int]]) -> List[Tuple[int, int]]:
    """
    Derive implied YES asks from NO bids.
    Returns list of (price_cents, qty) sorted by price ascending.
    """
    if not no_bids:
        return []
    
    # Best bid is last element
    implied_asks = []
    for no_price, no_qty in no_bids:
        yes_ask = 100 - no_price
        implied_asks.append((yes_ask, no_qty))
    
    # Sort by price ascending (lowest first)
    implied_asks.sort(key=lambda x: x[0])
    return implied_asks


def derive_implied_no_asks(yes_bids: List[List[int]]) -> List[Tuple[int, int]]:
    """
    Derive implied NO asks from YES bids.
    Returns list of (price_cents, qty) sorted by price ascending.
    """
    if not yes_bids:
        return []
    
    # Best bid is last element
    implied_asks = []
    for yes_price, yes_qty in yes_bids:
        no_ask = 100 - yes_price
        implied_asks.append((no_ask, yes_qty))
    
    # Sort by price ascending (lowest first)
    implied_asks.sort(key=lambda x: x[0])
    return implied_asks


# ============================================================================
# Team Name Normalization
# ============================================================================

def load_team_xref(path: str) -> Dict[Tuple[str, str], str]:
    """
    Load team name cross-reference CSV.
    Returns dict mapping (league, unabated_name_lower) -> kalshi_code.
    """
    xref = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                league_key = row["league"].strip().upper()
                unabated_name = row["unabated_name"].strip().lower()
                kalshi_code = row["kalshi_code"].strip().upper()
                xref[(league_key, unabated_name)] = kalshi_code
    except FileNotFoundError:
        print(f"❌ Team xref file not found: {path}")
    return xref


def team_to_kalshi_code(league: str, team_raw: str, team_xref: Dict[Tuple[str, str], str]) -> Optional[str]:
    """
    Look up Kalshi code for a team name.
    Returns None if not found.
    
    Args:
        league: League name (e.g., "NBA")
        team_raw: Team name from Unabated
        team_xref: Team xref dictionary
    """
    key = (league.upper(), team_raw.strip().lower())
    return team_xref.get(key)


# ============================================================================
# EV Calculation
# ============================================================================

def expected_value(p_win: float, price_cents: int, fee_on_win_cents: float) -> float:
    """
    Calculate expected value per contract with fees only on winning outcomes.
    
    Args:
        p_win: Win probability (0.0 to 1.0)
        price_cents: Price paid/posted in cents
        fee_on_win_cents: Fee in cents (only applies on win)
    
    Returns:
        Expected value in dollars per contract
    """
    P = price_cents / 100.0
    fee_on_win = fee_on_win_cents / 100.0
    
    # EV = p_win * ((1 - P) - fee_on_win) - (1 - p_win) * P
    ev = p_win * ((1.0 - P) - fee_on_win) - (1.0 - p_win) * P
    return ev


# ============================================================================
# Event Matching
# ============================================================================

def match_kalshi_event(
    canonical_key: str,
    team_codes: Tuple[str, str],
    events_cache: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Match a Kalshi event by canonical key and team codes using cached events.
    
    Args:
        canonical_key: Canonical key (e.g., "NBA_20251215_LALLAL_MIAMI")
        team_codes: Tuple of (team_a, team_b) codes
        events_cache: Dictionary mapping event_ticker -> event dict
    
    Returns:
        Event dict if found, None otherwise
    """
    # Parse canonical key
    parts = canonical_key.split("_")
    if len(parts) != 4:
        return None
    
    league, date_str, team_a, team_b = parts
    
    # Search through cached events
    for event_ticker, event in events_cache.items():
        parsed = parse_kalshi_event_ticker(event_ticker)
        
        if not parsed:
            continue
        
        event_date, event_team_codes = parsed
        
        # Check date match
        if event_date != date_str:
            continue
        
        # Check if both team codes appear in the ticker suffix
        # Note: This assumes team codes are substrings in the ticker suffix.
        code_a, code_b = team_codes
        if code_a in event_team_codes and code_b in event_team_codes:
            return event
    
    return None
