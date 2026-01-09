"""
Calculate maker-fee break-even probabilities for YES exposure (team winning) scenarios.

Internal: Reads YES bids from orderbook["yes"] for maker prices (join bid queue).
Also reads NO bids to derive YES ask for crossing check.
User-facing: YES exposure (what price to pay for win exposure).

Queue-jump: YES bid top+1¢ is simply top+1 (simple increment, no API lookup needed).
"""

import sys
from typing import Dict, Any, Optional, Tuple

from core.reusable_functions import fetch_orderbook
from utils.kalshi_api import load_creds
from pricing.fees import maker_fee_cents


def get_yes_bid_top_and_liquidity(orderbook: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Dict[int, int]]:
    """
    Extract top YES bid price and its liquidity from orderbook.
    
    Internal function: Reads orderbook["yes"] bids (maker prices for YES exposure).
    
    Args:
        orderbook: Kalshi orderbook dict with "yes" bid array (format: [[price_cents, qty], ...])
    
    Returns:
        (yes_bid_top_c, yes_bid_top_liq, yes_bids_by_price_dict)
        - yes_bid_top_c: Maximum YES bid price in cents, or None
        - yes_bid_top_liq: Total liquidity (quantity) at top YES bid price, or None
        - yes_bids_by_price_dict: Dict mapping price -> total quantity for all YES bid levels
    """
    yes_bids = orderbook.get("yes") or []
    
    if not yes_bids or not isinstance(yes_bids, list):
        return (None, None, {})
    
    # Find max YES bid price and accumulate quantities by price
    yes_bid_top_c = None
    yes_bids_by_price = {}
    
    for bid in yes_bids:
        if isinstance(bid, list) and len(bid) >= 2:
            price_cents = int(bid[0])
            qty = int(bid[1])
            
            # Track max price
            if yes_bid_top_c is None or price_cents > yes_bid_top_c:
                yes_bid_top_c = price_cents
            
            # Accumulate quantities by price (in case multiple entries at same price)
            if price_cents in yes_bids_by_price:
                yes_bids_by_price[price_cents] += qty
            else:
                yes_bids_by_price[price_cents] = qty
    
    # Get liquidity at top price
    yes_bid_top_liq = yes_bids_by_price.get(yes_bid_top_c, 0) if yes_bid_top_c is not None else None
    
    return (yes_bid_top_c, yes_bid_top_liq, yes_bids_by_price)


def yes_break_even_prob(yes_px_cents: int) -> Optional[float]:
    """
    Calculate maker-fee break-even win probability for YES exposure at a given YES price.
    
    This answers: "What win probability do I need to break even if I pay this YES price as maker?"
    
    Maker fee formula (for C contracts): fees_total = ceil(0.0175 * C * P * (1-P) * 100) cents
    Per-contract fee: fee_per_contract = fees_total / C
    
    After-fee price per contract: after_fee = yes_px_cents + fee_per_contract
    
    Break-even formula: p_win_be = after_fee / 100.0
    
    We use C=1000 contracts for fee calculation (standard Kalshi sizing).
    
    Args:
        yes_px_cents: YES price in cents (what you pay for win exposure)
    
    Returns:
        Break-even win probability (0.0-1.0) or None if invalid
    """
    if yes_px_cents <= 0 or yes_px_cents >= 100:
        return None
    
    # Use 1000 contracts for fee calculation (matches Kalshi UI)
    C = 1000
    P = yes_px_cents / 100.0
    
    # Calculate total fee for C contracts, rounded up
    fee_total_cents = maker_fee_cents(yes_px_cents, contracts=C)
    
    # Per-contract fee in cents
    fee_per_contract_cents = fee_total_cents / C
    
    # After-fee price per contract (in cents)
    after_fee_cents = yes_px_cents + fee_per_contract_cents
    
    # Convert to probability (0-1 range)
    p_win_be = after_fee_cents / 100.0
    
    # Clamp to valid probability range
    if p_win_be < 0.0:
        return 0.0
    if p_win_be > 1.0:
        return 1.0
    
    return p_win_be


def parse_event_ticker(event_ticker: str) -> Dict[str, str]:
    """
    Parse event ticker to extract away/home team codes.
    
    Format: KXNBAGAME-YYMONDD{AWAY}{HOME}
    Example: KXNBAGAME-26JAN08MIACHI → away=MIA, home=CHI
    
    Args:
        event_ticker: Event ticker string
    
    Returns:
        Dict with keys: away_code, home_code
        Raises ValueError if parsing fails
    """
    event_ticker = event_ticker.strip().upper()
    
    if "-" not in event_ticker:
        raise ValueError("Event ticker must contain at least one dash")
    
    # Get matchup part (after last dash)
    matchup_part = event_ticker.split("-")[-1]
    
    # Last 6 chars should be team codes
    if len(matchup_part) < 6:
        raise ValueError(f"Event ticker matchup part too short: {matchup_part}")
    
    team_codes_part = matchup_part[-6:]
    
    # Check if last 6 chars are letters
    if not team_codes_part.isalpha():
        raise ValueError(f"Last 6 characters must be letters (team codes): {team_codes_part}")
    
    away_code = team_codes_part[:3].upper()
    home_code = team_codes_part[3:].upper()
    
    return {
        "away_code": away_code,
        "home_code": home_code
    }


def get_market_yes_exposure_data(market_ticker: str, api_key_id: str, private_key_pem: str) -> Dict[str, Any]:
    """
    Get YES exposure data for a single market (team winning).
    
    Internal: Reads YES bids from orderbook["yes"] for maker prices.
    User-facing: Returns YES bid prices and break-even probabilities.
    
    Queue-jump: YES bid top+1¢ is simply top+1 (no API lookup needed, just increment).
    
    Returns:
        Dict with:
        - yes_bid_top_c, yes_bid_top_p1_c: YES bid prices (internal, maker)
        - yes_be_top, yes_be_topm1: Break-even win probabilities (user-facing, fee-adjusted)
        - yes_bid_top_liq: Liquidity from YES bids at top
        - yes_bid_top_p1_liq: Always None (theoretical price, not in book)
        - error: Error message if any
    """
    market_ticker = market_ticker.strip().upper()
    
    # Fetch orderbook (read YES bids for maker prices)
    orderbook = fetch_orderbook(api_key_id, private_key_pem, market_ticker)
    if not orderbook:
        return {
            "yes_bid_top_c": None,
            "yes_bid_top_p1_c": None,
            "yes_be_top": None,
            "yes_be_topm1": None,
            "yes_bid_top_liq": None,
            "yes_bid_top_p1_liq": None,
            "error": "No orderbook"
        }
    
    # Extract YES bid top price and liquidity (maker prices)
    yes_bid_top_c, yes_bid_top_liq, yes_bids_by_price = get_yes_bid_top_and_liquidity(orderbook)
    
    # Handle no YES bids case
    if yes_bid_top_c is None:
        return {
            "yes_bid_top_c": None,
            "yes_bid_top_p1_c": None,
            "yes_be_top": None,
            "yes_be_topm1": None,
            "yes_bid_top_liq": None,
            "yes_bid_top_p1_liq": None,
            "error": "No YES bids found"
        }
    
    # Compute queue-jump YES bid: top + 1¢ (simple increment, no API lookup needed)
    # This is a theoretical price for queue-jump, not necessarily in the book
    yes_bid_top_p1_c = yes_bid_top_c + 1 if yes_bid_top_c < 99 else None
    
    # No liquidity lookup for +1c level (it's a theoretical price, not in the book)
    yes_bid_top_p1_liq = None
    
    # Calculate break-even win probabilities for YES bid prices (maker fee, using 1000 contracts)
    yes_be_top = yes_break_even_prob(yes_bid_top_c)
    yes_be_topm1 = yes_break_even_prob(yes_bid_top_p1_c) if yes_bid_top_p1_c is not None else None
    
    return {
        "yes_bid_top_c": yes_bid_top_c,
        "yes_bid_top_p1_c": yes_bid_top_p1_c,
        "yes_be_top": yes_be_top,
        "yes_be_topm1": yes_be_topm1,
        "yes_bid_top_liq": yes_bid_top_liq,
        "yes_bid_top_p1_liq": yes_bid_top_p1_liq,
        "error": None
    }


def get_top_of_book_post_probs(event_ticker: str) -> Dict[str, Any]:
    """
    Get YES exposure data for both teams in an event.
    
    Internal: Reads YES bids from orderbook["yes"] for maker prices.
    User-facing: Returns YES break-even probabilities and prices.
    
    Args:
        event_ticker: Kalshi event ticker (e.g., KXNBAGAME-26JAN08MIACHI)
    
    Returns:
        Dict with:
        - event_ticker, away_code, home_code: str
        - away_market_ticker, home_market_ticker: str
        - yes_be_top_away, yes_be_topm1_away: float | None (break-even win probabilities 0-1)
        - yes_be_top_home, yes_be_topm1_home: float | None
        - yes_bid_top_c_away, yes_bid_top_p1_c_away: int | None (YES bid prices in cents, maker)
        - yes_bid_top_c_home, yes_bid_top_p1_c_home: int | None
        - yes_bid_top_liq_away, yes_bid_top_p1_liq_away: int | None (liquidity from YES bids)
        - yes_bid_top_liq_home, yes_bid_top_p1_liq_home: int | None
        - error: str | None
    """
    # Normalize event ticker
    event_ticker = event_ticker.strip().upper()
    
    # Parse event ticker to get team codes
    try:
        team_codes = parse_event_ticker(event_ticker)
        away_code = team_codes["away_code"]
        home_code = team_codes["home_code"]
    except ValueError as e:
        return {
            "event_ticker": event_ticker,
            "away_code": None,
            "home_code": None,
            "away_market_ticker": None,
            "home_market_ticker": None,
            "yes_be_top_away": None,
            "yes_be_topm1_away": None,
            "yes_be_top_home": None,
            "yes_be_topm1_home": None,
            "yes_bid_top_c_away": None,
            "yes_bid_top_p1_c_away": None,
            "yes_bid_top_c_home": None,
            "yes_bid_top_p1_c_home": None,
            "yes_bid_top_liq_away": None,
            "yes_bid_top_p1_liq_away": None,
            "yes_bid_top_liq_home": None,
            "yes_bid_top_p1_liq_home": None,
            "error": str(e)
        }
    
    # Build market tickers
    away_market = f"{event_ticker}-{away_code}"
    home_market = f"{event_ticker}-{home_code}"
    
    # Load Kalshi credentials
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        return {
            "event_ticker": event_ticker,
            "away_code": away_code,
            "home_code": home_code,
            "away_market_ticker": away_market,
            "home_market_ticker": home_market,
            "yes_be_top_away": None,
            "yes_be_topm1_away": None,
            "yes_be_top_home": None,
            "yes_be_topm1_home": None,
            "yes_bid_top_c_away": None,
            "yes_bid_top_p1_c_away": None,
            "yes_bid_top_c_home": None,
            "yes_bid_top_p1_c_home": None,
            "yes_bid_top_liq_away": None,
            "yes_bid_top_p1_liq_away": None,
            "yes_bid_top_liq_home": None,
            "yes_bid_top_p1_liq_home": None,
            "error": f"Failed to load credentials: {e}"
        }
    
    # Fetch YES exposure data for both markets (reads YES bids for maker prices)
    away_result = get_market_yes_exposure_data(away_market, api_key_id, private_key_pem)
    home_result = get_market_yes_exposure_data(home_market, api_key_id, private_key_pem)
    
    return {
        "event_ticker": event_ticker,
        "away_code": away_code,
        "home_code": home_code,
        "away_market_ticker": away_market,
        "home_market_ticker": home_market,
        "yes_be_top_away": away_result["yes_be_top"],
        "yes_be_topm1_away": away_result["yes_be_topm1"],
        "yes_be_top_home": home_result["yes_be_top"],
        "yes_be_topm1_home": home_result["yes_be_topm1"],
        "yes_bid_top_c_away": away_result["yes_bid_top_c"],
        "yes_bid_top_p1_c_away": away_result["yes_bid_top_p1_c"],
        "yes_bid_top_c_home": home_result["yes_bid_top_c"],
        "yes_bid_top_p1_c_home": home_result["yes_bid_top_p1_c"],
        "yes_bid_top_liq_away": away_result["yes_bid_top_liq"],
        "yes_bid_top_p1_liq_away": away_result["yes_bid_top_p1_liq"],
        "yes_bid_top_liq_home": home_result["yes_bid_top_liq"],
        "yes_bid_top_p1_liq_home": home_result["yes_bid_top_p1_liq"],
        "error": away_result.get("error") or home_result.get("error")
    }


def format_output(result: Dict[str, Any]) -> str:
    """
    Format result as one-line summary (YES exposure break-even probabilities).
    
    Format: event_ticker | away=CODE top=0.xxxx top+1=0.xxxx | home=CODE top=0.xxxx top+1=0.xxxx
    """
    event_ticker = result.get("event_ticker", "")
    
    if result.get("error"):
        return f"{event_ticker} | Error: {result['error']}"
    
    away_code = result.get("away_code", "???")
    home_code = result.get("home_code", "???")
    
    yes_be_top_away = result.get("yes_be_top_away")
    yes_be_topm1_away = result.get("yes_be_topm1_away")
    yes_be_top_home = result.get("yes_be_top_home")
    yes_be_topm1_home = result.get("yes_be_topm1_home")
    
    # Format away section
    away_top_str = f"{yes_be_top_away:.4f}" if yes_be_top_away is not None else "N/A"
    away_top_m1_str = f"{yes_be_topm1_away:.4f}" if yes_be_topm1_away is not None else "N/A"
    away_section = f"away={away_code} top={away_top_str} top+1={away_top_m1_str}"
    
    # Format home section
    home_top_str = f"{yes_be_top_home:.4f}" if yes_be_top_home is not None else "N/A"
    home_top_m1_str = f"{yes_be_topm1_home:.4f}" if yes_be_topm1_home is not None else "N/A"
    home_section = f"home={home_code} top={home_top_str} top+1={home_top_m1_str}"
    
    return f"{event_ticker} | {away_section} | {home_section}"


def main(event_ticker: str = None):
    """
    CLI entry point.
    
    Args:
        event_ticker: Optional event ticker string. If not provided, reads from sys.argv[1]
    """
    if event_ticker is None:
        if len(sys.argv) < 2:
            print("Usage: python kalshi_top_of_book_probs.py <event_ticker>")
            print("Example: python kalshi_top_of_book_probs.py KXNBAGAME-26JAN08MIACHI")
            sys.exit(1)
        event_ticker = sys.argv[1]
    
    result = get_top_of_book_post_probs(event_ticker)
    
    # Check for parse errors
    if result.get("error") and (result.get("away_code") is None or result.get("home_code") is None):
        print(f"Error: {result['error']}")
        sys.exit(1)
    
    print(format_output(result))


if __name__ == "__main__":
    main("KXNBAGAME-26JAN08MIACHI")
