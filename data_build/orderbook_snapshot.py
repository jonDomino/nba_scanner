"""
Centralized orderbook snapshot module with caching.
All tables use this module to fetch orderbook data, avoiding duplicate API calls.
"""

from typing import Dict, Any, Optional, Literal, Tuple
from dataclasses import dataclass

from core.reusable_functions import fetch_orderbook
from data_build.top_of_book import (
    get_yes_bid_top_and_liquidity,
    yes_break_even_prob
)
from data_build.clients import KalshiClient


@dataclass
class OrderbookSnapshot:
    """Snapshot of orderbook data for a market ticker and side."""
    market_ticker: str
    side: Literal["YES", "NO"]
    best_bid_cents: Optional[int]
    best_ask_cents: Optional[int]
    best_bid_liq: Optional[int]
    best_ask_liq: Optional[int]
    effective_prob_bid: Optional[float]
    effective_prob_ask: Optional[float]
    timestamp: float
    source: str = "kalshi"


# Simple in-memory cache: (market_ticker, side) -> OrderbookSnapshot
_orderbook_cache: Dict[Tuple[str, str], OrderbookSnapshot] = {}


def get_no_bid_top_and_liquidity(orderbook: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Dict[int, int]]:
    """
    Extract top NO bid price and its liquidity from orderbook.
    
    Args:
        orderbook: Kalshi orderbook dict with "no" bid array
    
    Returns:
        (no_bid_top_c, no_bid_top_liq, no_bids_by_price_dict)
    """
    no_bids = orderbook.get("no") or []
    
    if not no_bids or not isinstance(no_bids, list):
        return (None, None, {})
    
    no_bid_top_c = None
    no_bids_by_price = {}
    
    for bid in no_bids:
        if isinstance(bid, list) and len(bid) >= 2:
            price_cents = int(bid[0])
            qty = int(bid[1])
            
            if no_bid_top_c is None or price_cents > no_bid_top_c:
                no_bid_top_c = price_cents
            
            if price_cents in no_bids_by_price:
                no_bids_by_price[price_cents] += qty
            else:
                no_bids_by_price[price_cents] = qty
    
    no_bid_top_liq = no_bids_by_price.get(no_bid_top_c, 0) if no_bid_top_c is not None else None
    
    return (no_bid_top_c, no_bid_top_liq, no_bids_by_price)


def get(
    market_ticker: str,
    side: Literal["YES", "NO"],
    client: Optional[KalshiClient] = None,
    use_cache: bool = True
) -> OrderbookSnapshot:
    """
    Get orderbook snapshot for a market ticker and side.
    
    Uses caching to avoid duplicate API calls across tables.
    
    Args:
        market_ticker: Kalshi market ticker
        side: "YES" or "NO" - which side's bids to extract
        client: Optional KalshiClient (creates new one if not provided)
        use_cache: Whether to use cached results (default True)
    
    Returns:
        OrderbookSnapshot with best bid/ask, liquidity, and effective probabilities
    """
    import time
    
    cache_key = (market_ticker.upper(), side.upper())
    
    # Check cache first
    if use_cache and cache_key in _orderbook_cache:
        return _orderbook_cache[cache_key]
    
    # Fetch orderbook
    if client is None:
        client = KalshiClient()
    
    orderbook = fetch_orderbook(client.api_key_id, client.private_key_pem, market_ticker)
    
    if not orderbook:
        # Return empty snapshot
        snapshot = OrderbookSnapshot(
            market_ticker=market_ticker,
            side=side,
            best_bid_cents=None,
            best_ask_cents=None,
            best_bid_liq=None,
            best_ask_liq=None,
            effective_prob_bid=None,
            effective_prob_ask=None,
            timestamp=time.time()
        )
        if use_cache:
            _orderbook_cache[cache_key] = snapshot
        return snapshot
    
    # Extract bid/ask based on side
    if side.upper() == "YES":
        bid_top_c, bid_top_liq, _ = get_yes_bid_top_and_liquidity(orderbook)
        # Derive ask from NO bids
        no_bid_top_c, _, _ = get_no_bid_top_and_liquidity(orderbook)
        ask_top_c = (100 - no_bid_top_c) if no_bid_top_c is not None else None
        ask_top_liq = None  # We don't track NO bid liq for YES ask
    elif side.upper() == "NO":
        bid_top_c, bid_top_liq, _ = get_no_bid_top_and_liquidity(orderbook)
        # Derive ask from YES bids
        yes_bid_top_c, _, _ = get_yes_bid_top_and_liquidity(orderbook)
        ask_top_c = (100 - yes_bid_top_c) if yes_bid_top_c is not None else None
        ask_top_liq = None  # We don't track YES bid liq for NO ask
    else:
        raise ValueError(f"Invalid side: {side} (must be YES or NO)")
    
    # Calculate effective probabilities (after maker fees)
    effective_prob_bid = yes_break_even_prob(bid_top_c) if bid_top_c is not None else None
    effective_prob_ask = yes_break_even_prob(ask_top_c) if ask_top_c is not None else None
    
    snapshot = OrderbookSnapshot(
        market_ticker=market_ticker,
        side=side,
        best_bid_cents=bid_top_c,
        best_ask_cents=ask_top_c,
        best_bid_liq=bid_top_liq,
        best_ask_liq=ask_top_liq,
        effective_prob_bid=effective_prob_bid,
        effective_prob_ask=effective_prob_ask,
        timestamp=time.time()
    )
    
    if use_cache:
        _orderbook_cache[cache_key] = snapshot
    
    return snapshot


def clear_cache():
    """Clear the orderbook cache."""
    _orderbook_cache.clear()


def get_cache_stats() -> Dict[str, int]:
    """Get cache statistics."""
    return {
        "cached_snapshots": len(_orderbook_cache),
        "unique_markets": len(set(key[0] for key in _orderbook_cache.keys()))
    }