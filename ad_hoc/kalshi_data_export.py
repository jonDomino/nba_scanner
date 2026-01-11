"""
Export comprehensive Kalshi orderbook data for today's NBA games.

Fastest "all strikes" export - NO Unabated dependency.

This module collects all Kalshi data needed for moneylines, spreads, and totals tables:
- Moneyline markets (away/home)
- Spread markets (ALL strikes per game)
- Totals markets (ALL strikes per game)

For each market, extracts:
- Top bid price (cents)
- Top bid liquidity (contracts)
- Top bid+1c price (if available)
- Break-even probabilities (after maker fees)
- Dollar liquidity calculations

Optimized for speed with:
- Games cache (avoids repeated Unabated snapshot fetches)
- Markets manifest cache (avoids repeated Kalshi callsheet discovery)
- Parallel orderbook fetching with fail-fast retries
- Dynamic worker sizing based on ticker count
"""

import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Set, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import json

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from data_build.bundle import GameInfo, KalshiMarkets
from data_build.kalshi_callsheet import fetch_kalshi_callsheet_for_slate
from ad_hoc.games_cache import get_todays_games, get_today_date_str
from data_build.unabated_callsheet import utc_to_la_datetime
from data_build.top_of_book import (
    get_yes_bid_top_and_liquidity,
    get_no_bid_top_and_liquidity,
    yes_break_even_prob,
    no_break_even_prob
)
from core.reusable_functions import fetch_orderbook
from utils.kalshi_api import load_creds

# Configuration
DEBUG = False
VERBOSE_PROGRESS = True
MARKETS_MANIFEST_TTL_SECONDS = 60  # Cache markets manifest for 60 seconds

# Cache file locations
MARKETS_MANIFEST_DIR = project_root / "ad_hoc"


def get_markets_manifest_cache_path() -> Path:
    """Get path to markets manifest cache file."""
    today_str = get_today_date_str().replace("-", "")
    return MARKETS_MANIFEST_DIR / f"kalshi_markets_manifest_{today_str}.json"


def load_markets_manifest_from_cache() -> Optional[Dict[str, Any]]:
    """
    Load markets manifest from cache if it exists and is fresh (< TTL).
    
    Returns:
        Markets manifest dict if cache is valid, None otherwise
    """
    cache_path = get_markets_manifest_cache_path()
    
    if not cache_path.exists():
        return None
    
    try:
        # Check file age
        file_age = time.time() - cache_path.stat().st_mtime
        if file_age > MARKETS_MANIFEST_TTL_SECONDS:
            if DEBUG:
                print(f"  Markets manifest cache expired ({file_age:.1f}s > {MARKETS_MANIFEST_TTL_SECONDS}s)")
            return None
        
        with open(cache_path, 'r') as f:
            manifest = json.load(f)
        
        if DEBUG:
            print(f"  ✅ Loaded markets manifest from cache (age: {file_age:.1f}s)")
        return manifest
        
    except Exception as e:
        if DEBUG:
            print(f"  ⚠️ Error reading markets manifest cache: {e}")
        return None


def save_markets_manifest_to_cache(manifest: Dict[str, Any]) -> None:
    """Save markets manifest to cache."""
    cache_path = get_markets_manifest_cache_path()
    
    try:
        with open(cache_path, 'w') as f:
            json.dump(manifest, f, indent=2)
        if DEBUG:
            print(f"  ✅ Saved markets manifest to cache: {cache_path}")
    except Exception as e:
        if DEBUG:
            print(f"  ⚠️ Error saving markets manifest cache: {e}")


def build_markets_manifest(games: List[GameInfo], markets_dict: Dict[str, KalshiMarkets]) -> Dict[str, Any]:
    """
    Build JSON-serializable markets manifest from games and markets_dict.
    
    Returns:
        Dict with event_ticker -> markets data
    """
    manifest = {}
    
    for game in games:
        event_ticker = game.event_ticker
        if not event_ticker:
            continue
        
        # Find markets for this game
        kalshi_markets = markets_dict.get(event_ticker) or markets_dict.get(game.event_start)
        if not kalshi_markets:
            continue
        
        # Extract serializable data
        manifest[event_ticker] = {
            "moneyline_tickers": kalshi_markets.moneyline_tickers,
            "spread_markets": [
                {
                    "ticker": m.get("ticker"),
                    "parsed_strike": m.get("parsed_strike"),
                    "market_team_code": m.get("market_team_code")
                }
                for m in kalshi_markets.spread_markets
                if m.get("ticker")
            ],
            "totals_markets": [
                {
                    "ticker": m.get("ticker"),
                    "parsed_strike": m.get("parsed_strike"),
                    "direction": m.get("direction")
                }
                for m in kalshi_markets.totals_markets
                if m.get("ticker")
            ]
        }
    
    return manifest


def restore_markets_from_manifest(manifest: Dict[str, Any], event_ticker: str) -> Optional[KalshiMarkets]:
    """
    Restore KalshiMarkets object from cached manifest.
    
    Returns:
        KalshiMarkets object or None
    """
    if event_ticker not in manifest:
        return None
    
    data = manifest[event_ticker]
    
    # Reconstruct KalshiMarkets
    return KalshiMarkets(
        moneyline_tickers=data.get("moneyline_tickers", {}),
        spread_markets=data.get("spread_markets", []),
        totals_markets=data.get("totals_markets", [])
    )


def get_todays_games_and_markets(use_cache: bool = True):
    """
    Get today's NBA games and Kalshi markets.
    
    Uses games cache and markets manifest cache to minimize API calls.
    
    Returns:
        Tuple of (games_dict_list, markets_dict)
        - games_dict_list: List of game dicts (from cache or fresh fetch)
        - markets_dict: Dict of KalshiMarkets keyed by game_id or event_ticker
    """
    cache_start = time.time()
    
    # Get games as dicts (using cache if available)
    games_dict = get_todays_games(use_cache=use_cache)
    
    cache_time = time.time() - cache_start
    
    if not games_dict:
        return [], {}
    
    # Try to load markets manifest from cache
    manifest = load_markets_manifest_from_cache() if use_cache else None
    
    if manifest:
        # Restore markets_dict from cache
        markets_dict = {}
        for game_dict in games_dict:
            event_ticker = game_dict.get("event_ticker")
            if event_ticker and event_ticker in manifest:
                # Use event_ticker and event_start as keys (same as fetch_kalshi_callsheet_for_slate does)
                game_id = game_dict.get("event_start") or f"{game_dict.get('away_team_name')}_{game_dict.get('home_team_name')}"
                kalshi_markets = restore_markets_from_manifest(manifest, event_ticker)
                if kalshi_markets:
                    markets_dict[game_id] = kalshi_markets
                    markets_dict[event_ticker] = kalshi_markets
        
        if markets_dict:
            if DEBUG:
                print(f"📡 Markets restored from cache in {time.time() - cache_start:.2f}s")
            return games_dict, markets_dict
    
    # Cache miss - need to discover markets
    if DEBUG:
        print(f"📡 Markets manifest cache miss, discovering markets...")
    
    # Extract team codes from cached tickers to avoid re-parsing
    for game_dict in games_dict:
        away_ticker = game_dict.get("away_kalshi_ticker")
        home_ticker = game_dict.get("home_kalshi_ticker")
        
        if away_ticker and "-" in away_ticker:
            game_dict["away_kalshi_code"] = away_ticker.split("-")[-1]
        if home_ticker and "-" in home_ticker:
            game_dict["home_kalshi_code"] = home_ticker.split("-")[-1]
    
    # Convert dict games to GameInfo objects for callsheet
    games = []
    for game_dict in games_dict:
        game = GameInfo(
            game_date=game_dict.get("game_date", ""),
            event_start=game_dict.get("event_start", ""),
            away_roto=None,
            away_team_id=game_dict.get("away_team_id"),
            away_team_name=game_dict.get("away_team_name", ""),
            home_team_id=game_dict.get("home_team_id"),
            home_team_name=game_dict.get("home_team_name", ""),
            away_kalshi_code=game_dict.get("away_kalshi_code"),
            home_kalshi_code=game_dict.get("home_kalshi_code"),
            event_ticker=game_dict.get("event_ticker")
        )
        games.append(game)
    
    # Discover all Kalshi markets (spreads and totals - this still needs API calls)
    callsheet_start = time.time()
    callsheet_result = fetch_kalshi_callsheet_for_slate(games)
    callsheet_time = time.time() - callsheet_start
    markets_dict = callsheet_result.get("markets", {})
    
    if DEBUG:
        print(f"  Markets discovered in {callsheet_time:.2f}s")
    
    # Save to cache
    if markets_dict:
        manifest = build_markets_manifest(games, markets_dict)
        save_markets_manifest_to_cache(manifest)
    
    return games_dict, markets_dict


def fetch_orderbook_with_retry(
    market_ticker: str,
    api_key_id: str,
    private_key_pem: str,
    max_retries: int = 1,
    base_delay: float = 0.1
) -> Optional[Dict[str, Any]]:
    """
    Fetch orderbook with fail-fast retry logic.
    
    Args:
        market_ticker: Market ticker to fetch
        api_key_id: Kalshi API key ID
        private_key_pem: Kalshi private key PEM
        max_retries: Maximum retry attempts (default: 1 for fail-fast)
        base_delay: Base delay in seconds for retries (default: 0.1s, minimal)
    
    Returns:
        Orderbook dict or None if all retries failed
    """
    import random
    
    for attempt in range(max_retries + 1):
        try:
            orderbook = fetch_orderbook(api_key_id, private_key_pem, market_ticker)
            return orderbook
        except Exception as e:
            error_str = str(e).lower()
            
            # Check if it's a 429 error
            if "429" in error_str or "too many requests" in error_str:
                if attempt < max_retries:
                    # Minimal delay with small jitter (fail-fast mode)
                    delay = base_delay + random.uniform(0, 0.05)
                    if DEBUG:
                        print(f"  ⚠️ Rate limited for {market_ticker}, retrying in {delay:.2f}s...")
                    time.sleep(delay)
                    continue
                else:
                    if DEBUG:
                        print(f"  ❌ Rate limited for {market_ticker}, max retries exceeded")
                    return None
            else:
                # Non-429 error, don't retry
                if DEBUG:
                    print(f"  ❌ Error fetching {market_ticker}: {e}")
                return None
    
    return None


def extract_orderbook_data_for_both_sides(
    market_ticker: str,
    api_key_id: str,
    private_key_pem: str
) -> Dict[str, Dict[str, Any]]:
    """
    Extract comprehensive orderbook data for BOTH YES and NO sides from one orderbook fetch.
    
    Args:
        market_ticker: Kalshi market ticker
        api_key_id: Kalshi API key ID
        private_key_pem: Kalshi private key PEM
    
    Returns:
        Dict with keys "YES" and "NO", each containing:
        - bid_top_cents: Top bid price in cents
        - bid_top_liq: Top bid liquidity (contracts)
        - bid_top_p1_cents: Top bid+1c price (if available)
        - break_even_prob: Break-even probability after maker fees
        - break_even_prob_p1: Break-even probability at bid+1c
        - bid_top_dollar_liq: Dollar liquidity at top bid
        - error: Error message if fetch failed
    """
    result = {
        "YES": {
            "bid_top_cents": None,
            "bid_top_liq": None,
            "bid_top_p1_cents": None,
            "break_even_prob": None,
            "break_even_prob_p1": None,
            "bid_top_dollar_liq": None,
            "error": None
        },
        "NO": {
            "bid_top_cents": None,
            "bid_top_liq": None,
            "bid_top_p1_cents": None,
            "break_even_prob": None,
            "break_even_prob_p1": None,
            "bid_top_dollar_liq": None,
            "error": None
        }
    }
    
    try:
        # Fetch orderbook with fail-fast retry
        orderbook = fetch_orderbook_with_retry(market_ticker, api_key_id, private_key_pem, max_retries=1)
        
        if not orderbook:
            result["YES"]["error"] = "No orderbook returned"
            result["NO"]["error"] = "No orderbook returned"
            return result
        
        # Extract YES side data
        yes_bid_top_c, yes_bid_top_liq, _ = get_yes_bid_top_and_liquidity(orderbook)
        no_bid_top_c, no_bid_top_liq, _ = get_no_bid_top_and_liquidity(orderbook)
        
        # Calculate ask prices (opposite side bid) - heuristic for crossing check
        yes_ask_top_c = (100 - no_bid_top_c) if no_bid_top_c is not None else None
        no_ask_top_c = (100 - yes_bid_top_c) if yes_bid_top_c is not None else None
        
        # Process YES side
        if yes_bid_top_c is not None:
            yes_bid_p1_c = yes_bid_top_c + 1 if yes_bid_top_c < 99 else None
            if yes_bid_p1_c is not None and yes_ask_top_c is not None:
                if yes_bid_p1_c >= yes_ask_top_c:
                    yes_bid_p1_c = None  # Would cross
            
            yes_be = yes_break_even_prob(yes_bid_top_c)
            yes_be_p1 = yes_break_even_prob(yes_bid_p1_c) if yes_bid_p1_c is not None else None
            yes_dollar_liq = (yes_bid_top_liq * yes_bid_top_c / 100.0) if yes_bid_top_liq is not None else None
            
            result["YES"].update({
                "bid_top_cents": yes_bid_top_c,
                "bid_top_liq": yes_bid_top_liq,
                "bid_top_p1_cents": yes_bid_p1_c,
                "break_even_prob": yes_be,
                "break_even_prob_p1": yes_be_p1,
                "bid_top_dollar_liq": yes_dollar_liq
            })
        else:
            result["YES"]["error"] = "No YES bid found"
        
        # Process NO side
        if no_bid_top_c is not None:
            no_bid_p1_c = no_bid_top_c + 1 if no_bid_top_c < 99 else None
            if no_bid_p1_c is not None and no_ask_top_c is not None:
                if no_bid_p1_c >= no_ask_top_c:
                    no_bid_p1_c = None  # Would cross
            
            no_be = no_break_even_prob(no_bid_top_c)
            no_be_p1 = no_break_even_prob(no_bid_p1_c) if no_bid_p1_c is not None else None
            no_dollar_liq = (no_bid_top_liq * no_bid_top_c / 100.0) if no_bid_top_liq is not None else None
            
            result["NO"].update({
                "bid_top_cents": no_bid_top_c,
                "bid_top_liq": no_bid_top_liq,
                "bid_top_p1_cents": no_bid_p1_c,
                "break_even_prob": no_be,
                "break_even_prob_p1": no_be_p1,
                "bid_top_dollar_liq": no_dollar_liq
            })
        else:
            result["NO"]["error"] = "No NO bid found"
        
    except Exception as e:
        result["YES"]["error"] = str(e)
        result["NO"]["error"] = str(e)
    
    return result


def collect_all_kalshi_data() -> pd.DataFrame:
    """
    Collect all Kalshi orderbook data for today's NBA games.
    
    Fastest mode: NO Unabated dependency, ALL strikes, cached discovery.
    
    Optimizations:
    - Uses games cache and markets manifest cache
    - Fetches each unique market ticker only once
    - Extracts both YES and NO data from single orderbook response
    - Fail-fast retry logic (no long sleeps)
    - Dynamic worker sizing based on ticker count
    
    Returns:
        DataFrame with comprehensive Kalshi data for all markets
    """
    total_start = time.time()
    
    # Step 1: Load games and discover Kalshi markets (with caching)
    step1_start = time.time()
    print("📊 Step 1: Loading games and discovering Kalshi markets...")
    games, markets_dict = get_todays_games_and_markets(use_cache=True)
    step1_time = time.time() - step1_start
    print(f"✅ Step 1 completed in {step1_time:.2f}s")
    
    if not games:
        print("❌ No NBA games found for today")
        return pd.DataFrame()
    
    if not DEBUG:
        print(f"   Found {len(games)} game(s)")
    
    if not markets_dict:
        print("❌ No Kalshi markets found")
        return pd.DataFrame()
    
    if DEBUG:
        print(f"   Slate games: {len(games)}")
        print(f"   markets_dict keys: {len(markets_dict)} (2 per game is normal - game_id + event_ticker)")
    
    def _get_markets_for_game(game_info: Dict[str, Any], markets_dict: Dict[str, Any]) -> Optional[KalshiMarkets]:
        """
        Get KalshiMarkets for a game using preference order:
        1. event_ticker
        2. event_start (game_id)
        3. fallback "{away}_{home}"
        """
        event_ticker = game_info.get("event_ticker")
        game_id = game_info.get("event_start") or f"{game_info.get('away_team_name')}_{game_info.get('home_team_name')}"
        fallback_key = f"{game_info.get('away_team_name')}_{game_info.get('home_team_name')}"
        
        return markets_dict.get(event_ticker) or markets_dict.get(game_id) or markets_dict.get(fallback_key)
    
    # Canonicalize: process exactly one markets object per game
    selected_games = []
    for game_info in games:
        kalshi_markets = _get_markets_for_game(game_info, markets_dict)
        if kalshi_markets:
            selected_games.append((game_info, kalshi_markets))
    
    if not DEBUG:
        print(f"   Using markets for: {len(selected_games)} game(s)")
    
    if not selected_games:
        print("❌ No games matched to markets_dict")
        return pd.DataFrame()
    
    # Step 2: Collect market tickers with metadata (ALL strikes, no filtering)
    step2_start = time.time()
    if DEBUG:
        print(f"\n📊 Step 2: Building market metadata (ALL strikes, deduplication)...")
    
    # Simplified structure: market_ticker -> (game_info, market_type, strike)
    # We'll create YES/NO rows during DataFrame building
    market_metadata = {}  # Dict[str, Tuple] - market_ticker -> (game_info, market_type, strike)
    
    for game_info, kalshi_markets in selected_games:
        event_ticker = game_info.get("event_ticker")
        if not event_ticker:
            continue  # Skip if no event_ticker
        
        # Moneyline markets - always include both
        moneyline_tickers = kalshi_markets.moneyline_tickers
        if moneyline_tickers.get("away"):
            ticker = moneyline_tickers["away"]
            if ticker not in market_metadata:
                market_metadata[ticker] = (game_info, "moneyline", None)
        
        if moneyline_tickers.get("home"):
            ticker = moneyline_tickers["home"]
            if ticker not in market_metadata:
                market_metadata[ticker] = (game_info, "moneyline", None)
        
        # Spread markets - ALL strikes
        for spread_market in kalshi_markets.spread_markets:
            ticker = spread_market.get("ticker")
            strike = spread_market.get("parsed_strike")
            if ticker and ticker not in market_metadata:
                market_metadata[ticker] = (game_info, "spread", strike)
        
        # Totals markets - ALL strikes
        for total_market in kalshi_markets.totals_markets:
            ticker = total_market.get("ticker")
            strike = total_market.get("parsed_strike")
            if ticker and ticker not in market_metadata:
                market_metadata[ticker] = (game_info, "total", strike)
        
        if DEBUG:
            print(f"  [DEBUG] {event_ticker}: spreads={len(kalshi_markets.spread_markets)}, totals={len(kalshi_markets.totals_markets)}")
    
    step2_time = time.time() - step2_start
    unique_tickers = len(market_metadata)
    print(f"✅ Step 2 completed in {step2_time:.2f}s")
    print(f"   Collected {unique_tickers} unique market ticker(s)")
    print(f"   Expected rows: ~{unique_tickers * 2} (2 sides per ticker)")
    
    if unique_tickers == 0:
        print("❌ No market tickers collected")
        return pd.DataFrame()
    
    # Step 3: Fetch orderbooks in parallel
    step3_start = time.time()
    
    # Use 10 parallel workers
    max_workers = 10
    print(f"\n📊 Step 3: Fetching orderbooks for {unique_tickers} unique market ticker(s)...")
    print(f"   Using {max_workers} parallel workers")
    
    # Load credentials once
    try:
        api_key_id, private_key_pem = load_creds()
    except Exception as e:
        print(f"❌ Failed to load Kalshi credentials: {e}")
        return pd.DataFrame()
    
    # Helper function to build rows from game_info and orderbook data
    def build_rows(game_info, market_type, market_ticker, strike, orderbook_data):
        """Build both YES and NO rows from orderbook data."""
        rows = []
        event_start = game_info.get("event_start")
        game_date = game_info.get("game_date")
        game_time = None
        
        if event_start and not game_date:
            try:
                la_dt = utc_to_la_datetime(event_start)
                game_date = la_dt.strftime("%Y-%m-%d")
                game_time = la_dt.strftime("%H:%M")
            except Exception:
                pass
        elif event_start:
            try:
                la_dt = utc_to_la_datetime(event_start)
                game_time = la_dt.strftime("%H:%M")
            except Exception:
                pass
        
        # Build YES row
        yes_data = orderbook_data.get("YES", {})
        rows.append({
            "game_date": game_date,
            "game_time": game_time,
            "away_team": game_info.get("away_team_name"),
            "home_team": game_info.get("home_team_name"),
            "away_roto": None,
            "home_roto": None,
            "event_ticker": game_info.get("event_ticker"),
            "market_type": market_type,
            "market_ticker": market_ticker,
            "side": "YES",
            "strike": strike,
            "bid_top_cents": yes_data.get("bid_top_cents"),
            "bid_top_liq": yes_data.get("bid_top_liq"),
            "bid_top_p1_cents": yes_data.get("bid_top_p1_cents"),
            "bid_top_p1_liq": None,
            "break_even_prob": yes_data.get("break_even_prob"),
            "break_even_prob_p1": yes_data.get("break_even_prob_p1"),
            "bid_top_dollar_liq": yes_data.get("bid_top_dollar_liq"),
            "bid_top_p1_dollar_liq": None,
            "error": yes_data.get("error")
        })
        
        # Build NO row
        no_data = orderbook_data.get("NO", {})
        rows.append({
            "game_date": game_date,
            "game_time": game_time,
            "away_team": game_info.get("away_team_name"),
            "home_team": game_info.get("home_team_name"),
            "away_roto": None,
            "home_roto": None,
            "event_ticker": game_info.get("event_ticker"),
            "market_type": market_type,
            "market_ticker": market_ticker,
            "side": "NO",
            "strike": strike,
            "bid_top_cents": no_data.get("bid_top_cents"),
            "bid_top_liq": no_data.get("bid_top_liq"),
            "bid_top_p1_cents": no_data.get("bid_top_p1_cents"),
            "bid_top_p1_liq": None,
            "break_even_prob": no_data.get("break_even_prob"),
            "break_even_prob_p1": no_data.get("break_even_prob_p1"),
            "bid_top_dollar_liq": no_data.get("bid_top_dollar_liq"),
            "bid_top_p1_dollar_liq": None,
            "error": no_data.get("error")
        })
        
        return rows
    
    # Fetch all unique orderbooks in parallel
    rows = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit one task per unique market ticker
        future_to_ticker = {
            executor.submit(
                extract_orderbook_data_for_both_sides,
                market_ticker,
                api_key_id,
                private_key_pem
            ): market_ticker
            for market_ticker in market_metadata.keys()
        }
        
        # Collect results as they complete
        completed = 0
        progress_interval = 25 if VERBOSE_PROGRESS else 1000  # Only show progress every N
        
        for future in as_completed(future_to_ticker):
            completed += 1
            if VERBOSE_PROGRESS and completed % progress_interval == 0:
                print(f"  Progress: {completed}/{unique_tickers}")
            
            market_ticker = future_to_ticker[future]
            game_info, market_type, strike = market_metadata[market_ticker]
            
            try:
                orderbook_data = future.result()
                ticker_rows = build_rows(game_info, market_type, market_ticker, strike, orderbook_data)
                rows.extend(ticker_rows)
                
            except Exception as e:
                if DEBUG:
                    print(f"  ⚠️ Error processing {market_ticker}: {e}")
                # Add error rows for both sides
                error_data = {"YES": {"error": str(e)}, "NO": {"error": str(e)}}
                ticker_rows = build_rows(game_info, market_type, market_ticker, strike, error_data)
                rows.extend(ticker_rows)
    
    step3_time = time.time() - step3_start
    print(f"✅ Step 3 completed in {step3_time:.2f}s")
    print(f"   Collected data for {len(rows)} rows from {unique_tickers} unique markets")
    
    # Step 4: Build DataFrame
    step4_start = time.time()
    if DEBUG:
        print(f"\n📊 Step 4: Building DataFrame...")
    df = pd.DataFrame(rows)
    step4_time = time.time() - step4_start
    
    # Sort by game_date, game_time, market_type, strike, side
    if not df.empty:
        df = df.sort_values(
            by=["game_date", "game_time", "market_type", "strike", "side"],
            na_position="last"
        ).reset_index(drop=True)
    
    if DEBUG:
        print(f"✅ Step 4 completed in {step4_time:.2f}s")
    
    # Store timing data
    if not hasattr(collect_all_kalshi_data, '_last_timing'):
        collect_all_kalshi_data._last_timing = {}
    collect_all_kalshi_data._last_timing = {
        'step1': step1_time,
        'step2': step2_time,
        'step3': step3_time,
        'step4': step4_time
    }
    
    return df


def main():
    """Main function to collect and export Kalshi data."""
    start_time = datetime.now()
    
    print("=" * 60)
    print("KALSHI DATA EXPORT (Fastest Mode - All Strikes)")
    print("=" * 60)
    
    df = collect_all_kalshi_data()
    
    if df.empty:
        print("\n❌ No data collected")
        return
    
    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total rows: {len(df)}")
    print(f"\nBy market type:")
    print(df["market_type"].value_counts())
    print(f"\nBy side:")
    print(df["side"].value_counts())
    print(f"\nGames: {df['event_ticker'].nunique()}")
    print(f"Markets: {df['market_ticker'].nunique()}")
    
    # Check for errors
    error_count = df["error"].notna().sum()
    if error_count > 0:
        print(f"\n⚠️ Errors: {error_count} rows with errors")
        if DEBUG:
            print(df[df["error"].notna()][["market_ticker", "side", "error"]].head(10))
    
    # Save to CSV
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = project_root / "ad_hoc" / f"kalshi_data_{timestamp}.csv"
    df.to_csv(csv_path, index=False)
    print(f"\n✅ Saved to: {csv_path}")
    
    # Print sample
    if DEBUG:
        print("\n" + "=" * 60)
        print("SAMPLE DATA (first 10 rows)")
        print("=" * 60)
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', None)
        pd.set_option('display.max_colwidth', 30)
        print(df.head(10).to_string())
    
    elapsed = (datetime.now() - start_time).total_seconds()
    
    # Print timing summary
    if hasattr(collect_all_kalshi_data, '_last_timing'):
        timing = collect_all_kalshi_data._last_timing
        print("\n" + "=" * 60)
        print("TIMING SUMMARY")
        print("=" * 60)
        print(f"Step 1 - Load games & discover markets:     {timing['step1']:.2f}s")
        print(f"Step 2 - Build market metadata:               {timing['step2']:.2f}s")
        print(f"Step 3 - Fetch orderbooks (parallel):         {timing['step3']:.2f}s")
        print(f"Step 4 - Build DataFrame:                     {timing['step4']:.2f}s")
        print("-" * 60)
        
        # Calculate overhead
        overhead = elapsed - (timing['step1'] + timing['step2'] + timing['step3'] + timing['step4'])
        print(f"Total time:                                  {elapsed:.2f}s")
        if overhead > 0.1:
            print(f"Other overhead (summary/printing/etc):        {overhead:.2f}s")
    else:
        print(f"\n⏱️ Total time: {elapsed:.2f} seconds")


if __name__ == "__main__":
    main()
