"""
Calculate maker-fee break-even probabilities for top-of-book posting scenarios.
"""

import sys
from typing import Dict, Any, Optional

from core.reusable_functions import fetch_orderbook
from utils.kalshi_api import load_creds
from pricing.fees import maker_fee_cents


def get_yes_ask_prices_and_liquidity(orderbook: Dict[str, Any]) -> tuple:
    """
    Extract best YES ask, inside ask, and their liquidity from orderbook.
    
    Best YES ask = 100 - max(NO bid prices)
    Inside ask = best_ask - 1 if best_ask >= 2, else None
    Liquidity = quantity of NO bids at the corresponding price level
    
    Args:
        orderbook: Kalshi orderbook dict with "no" bid array (format: [[price_cents, qty], ...])
    
    Returns:
        (best_yes_ask_cents, inside_yes_ask_cents, best_ask_liq, inside_ask_liq)
        Liquidity values are quantities (int) or None if no liquidity
    """
    no_bids = orderbook.get("no") or []
    
    if not no_bids or not isinstance(no_bids, list):
        return (None, None, None, None)
    
    # Find max NO bid price and its liquidity (don't assume ordering)
    max_no_bid_price = None
    max_no_bid_qty = None
    
    # Also collect all NO bids by price for liquidity lookup
    no_bids_by_price = {}
    
    for bid in no_bids:
        if isinstance(bid, list) and len(bid) >= 2:
            price = bid[0]
            qty = bid[1]
            
            # Track max price
            if max_no_bid_price is None or price > max_no_bid_price:
                max_no_bid_price = price
                max_no_bid_qty = qty
            
            # Accumulate quantities by price (in case multiple entries at same price)
            if price in no_bids_by_price:
                no_bids_by_price[price] += qty
            else:
                no_bids_by_price[price] = qty
    
    if max_no_bid_price is None:
        return (None, None, None, None)
    
    # Best YES ask = 100 - max(NO bid price)
    best_yes_ask_cents = 100 - max_no_bid_price
    
    # Liquidity at best ask = quantity of NO bids at max price
    best_ask_liq = no_bids_by_price.get(max_no_bid_price, 0) if max_no_bid_price else None
    
    # Inside ask = best_ask - 1 if best_ask >= 2
    inside_yes_ask_cents = best_yes_ask_cents - 1 if best_yes_ask_cents >= 2 else None
    
    # Liquidity at inside ask = quantity of NO bids at price = 100 - inside_ask
    inside_ask_liq = None
    if inside_yes_ask_cents is not None:
        corresponding_no_bid_price = 100 - inside_yes_ask_cents
        inside_ask_liq = no_bids_by_price.get(corresponding_no_bid_price, 0)
    
    return (best_yes_ask_cents, inside_yes_ask_cents, best_ask_liq, inside_ask_liq)


def maker_post_break_even_prob(price_cents: int) -> Optional[float]:
    """
    Calculate maker-fee break-even win probability for a posted price.
    
    Break-even formula: p_win_be = p / (1 - fee_on_win)
    Where:
    - p = price_cents / 100.0
    - fee_on_win = maker_fee_cents(price_cents, 1) / 100.0
    
    Args:
        price_cents: Posted price in cents
    
    Returns:
        Break-even probability (0.0-1.0) or None if invalid
    """
    if price_cents <= 0 or price_cents >= 100:
        return None
    
    p = price_cents / 100.0
    fee_on_win_cents = maker_fee_cents(price_cents, contracts=1)
    fee_on_win = fee_on_win_cents / 100.0
    
    # Break-even: p_win_be = p / (1 - fee_on_win)
    if fee_on_win >= 1.0:
        return None  # Invalid (would require >100% fee)
    
    p_win_be = p / (1.0 - fee_on_win)
    
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


def get_market_post_probs(market_ticker: str, api_key_id: str, private_key_pem: str) -> Dict[str, Any]:
    """
    Get top-of-book maker posting break-even probabilities and liquidity for a single market.
    
    Returns:
        Dict with: best_ask_cents, inside_ask_cents, top_prob, top_m1_prob, 
                   best_ask_liq, inside_ask_liq, error
    """
    market_ticker = market_ticker.strip().upper()
    
    # Fetch orderbook
    orderbook = fetch_orderbook(api_key_id, private_key_pem, market_ticker)
    if not orderbook:
        return {
            "best_ask_cents": None,
            "inside_ask_cents": None,
            "top_prob": None,
            "top_m1_prob": None,
            "best_ask_liq": None,
            "inside_ask_liq": None,
            "error": "No orderbook"
        }
    
    # Debug: Print ladder sizes (temporary)
    yes_levels = len(orderbook.get("yes", []))
    no_levels = len(orderbook.get("no", []))
    print(f"  {market_ticker}: Orderbook levels: yes={yes_levels} no={no_levels}")
    
    # Extract ask prices and liquidity
    best_ask, inside_ask, best_ask_liq, inside_ask_liq = get_yes_ask_prices_and_liquidity(orderbook)
    
    # Handle no liquidity case
    if best_ask is None:
        return {
            "best_ask_cents": None,
            "inside_ask_cents": None,
            "top_prob": None,
            "top_m1_prob": None,
            "best_ask_liq": None,
            "inside_ask_liq": None,
            "error": f"No NO bids; cannot derive YES ask (yes_levels={yes_levels}, no_levels={no_levels})"
        }
    
    # Calculate break-even probabilities (maker fee)
    top_prob = maker_post_break_even_prob(best_ask)
    top_m1_prob = maker_post_break_even_prob(inside_ask) if inside_ask is not None else None
    
    return {
        "best_ask_cents": best_ask,
        "inside_ask_cents": inside_ask,
        "top_prob": top_prob,
        "top_m1_prob": top_m1_prob,
        "best_ask_liq": best_ask_liq,
        "inside_ask_liq": inside_ask_liq,
        "error": None
    }


def get_top_of_book_post_probs(event_ticker: str) -> Dict[str, Any]:
    """
    Get top-of-book maker posting break-even probabilities for both teams in an event.
    
    Args:
        event_ticker: Kalshi event ticker (e.g., KXNBAGAME-26JAN08MIACHI)
    
    Returns:
        Dict with:
        - event_ticker: str
        - away_code, home_code: str
        - away_market_ticker, home_market_ticker: str
        - away_top, away_top_m1: float | None (maker-fee break-even probabilities 0-1)
        - home_top, home_top_m1: float | None
        - away_best_ask_cents, away_inside_ask_cents: int | None (debug)
        - home_best_ask_cents, home_inside_ask_cents: int | None (debug)
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
            "away_top": None,
            "away_top_m1": None,
            "home_top": None,
            "home_top_m1": None,
            "away_best_ask_cents": None,
            "away_inside_ask_cents": None,
            "home_best_ask_cents": None,
            "home_inside_ask_cents": None,
            "away_top_liq": None,
            "away_topm1_liq": None,
            "home_top_liq": None,
            "home_topm1_liq": None,
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
            "away_top": None,
            "away_top_m1": None,
            "home_top": None,
            "home_top_m1": None,
            "away_best_ask_cents": None,
            "away_inside_ask_cents": None,
            "home_best_ask_cents": None,
            "home_inside_ask_cents": None,
            "away_top_liq": None,
            "away_topm1_liq": None,
            "home_top_liq": None,
            "home_topm1_liq": None,
            "error": f"Failed to load credentials: {e}"
        }
    
    # Fetch probabilities and liquidity for both markets
    away_result = get_market_post_probs(away_market, api_key_id, private_key_pem)
    home_result = get_market_post_probs(home_market, api_key_id, private_key_pem)
    
    return {
        "event_ticker": event_ticker,
        "away_code": away_code,
        "home_code": home_code,
        "away_market_ticker": away_market,
        "home_market_ticker": home_market,
        "away_top": away_result["top_prob"],
        "away_top_m1": away_result["top_m1_prob"],
        "home_top": home_result["top_prob"],
        "home_top_m1": home_result["top_m1_prob"],
        "away_best_ask_cents": away_result["best_ask_cents"],
        "away_inside_ask_cents": away_result["inside_ask_cents"],
        "home_best_ask_cents": home_result["best_ask_cents"],
        "home_inside_ask_cents": home_result["inside_ask_cents"],
        "away_top_liq": away_result.get("best_ask_liq"),
        "away_topm1_liq": away_result.get("inside_ask_liq"),
        "home_top_liq": home_result.get("best_ask_liq"),
        "home_topm1_liq": home_result.get("inside_ask_liq"),
        "error": away_result.get("error") or home_result.get("error")
    }


def format_output(result: Dict[str, Any]) -> str:
    """
    Format result as one-line summary.
    
    Format: event_ticker | away=CODE top=0.xxxx top-1=0.xxxx | home=CODE top=0.xxxx top-1=0.xxxx
    """
    event_ticker = result.get("event_ticker", "")
    
    if result.get("error"):
        return f"{event_ticker} | Error: {result['error']}"
    
    away_code = result.get("away_code", "???")
    home_code = result.get("home_code", "???")
    
    away_top = result.get("away_top")
    away_top_m1 = result.get("away_top_m1")
    home_top = result.get("home_top")
    home_top_m1 = result.get("home_top_m1")
    
    # Format away section
    away_top_str = f"{away_top:.4f}" if away_top is not None else "N/A"
    away_top_m1_str = f"{away_top_m1:.4f}" if away_top_m1 is not None else "N/A"
    away_section = f"away={away_code} top={away_top_str} top-1={away_top_m1_str}"
    
    # Format home section
    home_top_str = f"{home_top:.4f}" if home_top is not None else "N/A"
    home_top_m1_str = f"{home_top_m1:.4f}" if home_top_m1 is not None else "N/A"
    home_section = f"home={home_code} top={home_top_str} top-1={home_top_m1_str}"
    
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
