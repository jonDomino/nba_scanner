"""
Data bundle: in-memory structure containing all data needed for table builders.
This is the "one big pull" at startup that eliminates duplicate API calls.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from datetime import date


@dataclass
class GameInfo:
    """Canonical game information."""
    game_date: str  # YYYY-MM-DD
    event_start: str  # UTC timestamp
    away_roto: Optional[int]
    away_team_id: Optional[int]
    away_team_name: str
    home_team_id: Optional[int]
    home_team_name: str
    away_kalshi_code: Optional[str]  # 3-letter code
    home_kalshi_code: Optional[str]  # 3-letter code
    event_ticker: Optional[str]  # Kalshi event ticker


@dataclass
class UnabatedConsensus:
    """Unabated consensus data per game."""
    # Keyed by a game identifier (e.g., event_start or (away_team_id, home_team_id))
    moneylines: Dict[int, float] = field(default_factory=dict)  # team_id -> fair_prob
    spreads: Optional[Dict[str, Any]] = None  # {"spread": float, "juice": int, "team": "away"/"home"}
    totals: Optional[Dict[str, Any]] = None  # {"total": float, "juice": int}


@dataclass
class KalshiMarkets:
    """Kalshi market tickers per game."""
    # Keyed by game identifier
    moneyline_tickers: Dict[str, Dict[str, str]] = field(default_factory=dict)  # game_id -> {"away": ticker, "home": ticker}
    spread_markets: List[Dict[str, Any]] = field(default_factory=list)  # List of spread market dicts with parsed strikes
    totals_markets: List[Dict[str, Any]] = field(default_factory=list)  # List of totals market dicts with parsed strikes


@dataclass
class BundleOrderbookSnapshot:
    """Snapshot of orderbook data for a market ticker (bundle structure)."""
    market_ticker: str
    # YES side (maker bid prices)
    yes_bid_top_cents: Optional[int]
    yes_bid_top_liq: Optional[int]
    yes_be_top: Optional[float]  # Break-even prob after fees
    yes_bid_top_p1_cents: Optional[int]  # Top + 1c
    yes_bid_top_p1_liq: Optional[int]
    yes_be_top_p1: Optional[float]
    # NO side (for spreads/totals - derived from YES ask)
    no_bid_top_cents: Optional[int]
    no_bid_top_liq: Optional[int]
    no_be_top: Optional[float]
    no_bid_top_p1_cents: Optional[int]
    no_bid_top_p1_liq: Optional[int]
    no_be_top_p1: Optional[float]


@dataclass
class Bundle:
    """Complete data bundle for table builders."""
    today_date: date  # Date in LA timezone
    
    # Game list with canonical metadata
    games: List[GameInfo] = field(default_factory=list)
    
    # Unabated consensus data, keyed by game identifier
    unabated: Dict[str, UnabatedConsensus] = field(default_factory=dict)
    
    # Kalshi market discovery, keyed by event_ticker or game identifier
    kalshi_markets: Dict[str, KalshiMarkets] = field(default_factory=dict)
    
    # Orderbook snapshots, keyed by market_ticker
    orderbooks: Dict[str, BundleOrderbookSnapshot] = field(default_factory=dict)
    
    # Telemetry
    telemetry: Dict[str, int] = field(default_factory=dict)  # Call counts, etc.


def build_bundle() -> Bundle:
    """
    Build complete data bundle with one-shot API fetches.
    
    This is the main entry point that replaces multiple scattered fetches.
    
    Returns:
        Bundle with all data needed by table builders
    """
    from data_build.unabated_callsheet import fetch_unabated_slate_for_today
    from data_build.kalshi_callsheet import fetch_kalshi_callsheet_for_slate
    from data_build.orderbook_snapshot import snapshot_many
    
    # Step 1: One Unabated pull
    print("📡 Fetching Unabated slate and consensus...")
    unabated_data = fetch_unabated_slate_for_today()
    
    bundle = Bundle(today_date=unabated_data["today_date"])
    bundle.games = unabated_data["games"]
    bundle.unabated = unabated_data["unabated"]
    bundle.telemetry["unabated_calls"] = unabated_data.get("telemetry", {}).get("calls", 1)
    
    if not bundle.games:
        print("No games found for today")
        return bundle
    
    # Step 2: One Kalshi callsheet pull
    print(f"📡 Fetching Kalshi markets for {len(bundle.games)} game(s)...")
    kalshi_data = fetch_kalshi_callsheet_for_slate(bundle.games)
    bundle.kalshi_markets = kalshi_data["markets"]
    bundle.telemetry["kalshi_callsheet_calls"] = kalshi_data.get("telemetry", {}).get("calls", 1)
    
    # Step 3: Determine required tickers
    print("🔍 Determining required market tickers...")
    required_tickers = _determine_required_tickers(bundle)
    bundle.telemetry["unique_tickers_needed"] = len(required_tickers)
    
    # Step 4: One consolidated orderbook snapshot pass
    if required_tickers:
        print(f"📡 Fetching orderbooks for {len(required_tickers)} ticker(s)...")
        snapshots = snapshot_many(list(required_tickers))
        bundle.orderbooks = snapshots
        bundle.telemetry["orderbook_calls"] = snapshots.get("_telemetry", {}).get("http_requests", 0)
        # Remove telemetry from orderbooks dict
        bundle.orderbooks.pop("_telemetry", None)
    
    print(f"\n✅ Bundle built successfully:")
    print(f"   Games: {len(bundle.games)}")
    print(f"   Unique tickers: {bundle.telemetry.get('unique_tickers_needed', 0)}")
    print(f"   Unabated calls: {bundle.telemetry.get('unabated_calls', 0)}")
    print(f"   Kalshi callsheet calls: {bundle.telemetry.get('kalshi_callsheet_calls', 0)}")
    print(f"   Orderbook HTTP requests: {bundle.telemetry.get('orderbook_calls', 0)}\n")
    
    return bundle


def _determine_required_tickers(bundle: Bundle) -> set:
    """
    Determine all market tickers needed by moneylines, spreads, and totals.
    
    This uses existing selection logic to choose strikes, ensuring parity.
    
    Returns:
        Set of required market tickers
    """
    required = set()
    
    # Moneylines: always need both away and home tickers
    for game in bundle.games:
        if game.away_kalshi_code and game.home_kalshi_code and game.event_ticker:
            away_ticker = f"{game.event_ticker}-{game.away_kalshi_code}"
            home_ticker = f"{game.event_ticker}-{game.home_kalshi_code}"
            required.add(away_ticker)
            required.add(home_ticker)
    
    # Spreads: select 2 closest strikes per game using existing logic
    for game in bundle.games:
        game_id = _get_game_id(game)
        kalshi_data = bundle.kalshi_markets.get(game_id) or bundle.kalshi_markets.get(game.event_ticker or "")
        if not kalshi_data or not kalshi_data.spread_markets:
            continue
        
        # Get Unabated spread consensus for this game
        unabated_data = bundle.unabated.get(game_id) or bundle.unabated.get(game.event_ticker or "")
        if not unabated_data or not unabated_data.spreads:
            continue
        
        # Use existing selection logic from spreads/builder.py
        # Import here to avoid circular dependency
        try:
            from spreads.builder import select_closest_strikes_for_team_spread
            
            spread = unabated_data.spreads["spread"]
            if spread is None:
                continue
            
            team_code = game.away_kalshi_code if unabated_data.spreads.get("team") == "away" else game.home_kalshi_code
            opponent_code = game.home_kalshi_code if unabated_data.spreads.get("team") == "away" else game.away_kalshi_code
            
            if not team_code or not opponent_code:
                continue
            
            selected = select_closest_strikes_for_team_spread(
                spread, team_code, opponent_code, kalshi_data.spread_markets, count=2
            )
            
            for market, _ in selected:
                if market and market.get("ticker"):
                    required.add(market["ticker"])
        except Exception as e:
            # If selection fails, skip this game (degrade gracefully)
            print(f"⚠️ Error selecting spreads for {game_id}: {e}")
            continue
    
    # Totals: select 2 closest strikes per game using existing logic
    for game in bundle.games:
        game_id = _get_game_id(game)
        kalshi_data = bundle.kalshi_markets.get(game_id) or bundle.kalshi_markets.get(game.event_ticker or "")
        if not kalshi_data or not kalshi_data.totals_markets:
            continue
        
        # Get Unabated totals consensus for this game
        unabated_data = bundle.unabated.get(game_id) or bundle.unabated.get(game.event_ticker or "")
        if not unabated_data or not unabated_data.totals:
            continue
        
        # Use existing selection logic from totals/builder.py
        # Import here to avoid circular dependency
        try:
            from totals.builder import select_closest_over_strikes
            
            canonical_total = unabated_data.totals.get("total")
            if canonical_total is None:
                continue
            
            selected = select_closest_over_strikes(
                canonical_total, kalshi_data.totals_markets, count=2
            )
            
            for market in selected:
                if market and market.get("ticker"):
                    required.add(market["ticker"])
        except Exception as e:
            # If selection fails, skip this game (degrade gracefully)
            print(f"⚠️ Error selecting totals for {game_id}: {e}")
            continue
    
    return required


def _get_game_id(game: GameInfo) -> str:
    """Get a unique identifier for a game."""
    # Use event_start as primary key, fallback to team names
    if game.event_start:
        return game.event_start
    return f"{game.away_team_name}_{game.home_team_name}"