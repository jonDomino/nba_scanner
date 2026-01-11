# app.py Data Flow Documentation

**Last Updated**: Based on codebase state as of latest analysis  
**Purpose**: Technical documentation of data fetching, transformation, and assembly for NBA dashboard tables

---

## 1. Executive Overview

- **Entry point**: `app.py::get_cached_dashboard()` wrapped with `@st.cache_data(ttl=30)`
- **Orchestration**: `orchestrator.py::build_all_rows()` coordinates three parallel data paths: moneylines, spreads, totals
- **Primary data sources**: Unabated API (consensus odds) and Kalshi API (orderbook prices)
- **Major cost centers**:
  - Unabated snapshot fetch: ~15-30 seconds (single HTTP request, large JSON payload)
  - Kalshi markets discovery: 12+ API calls per run (6 games × 2 market types: spreads + totals)
  - Kalshi orderbook fetches: ~150-200 API calls per run (moneyline events + spread strikes + total strikes)
  - Duplicate work: Unabated snapshot fetched **twice** per run (once in games fetch, once in orchestrator)
- **Caching**: Only Streamlit-level caching (`@st.cache_data`, 30s TTL). No module-level caching for API responses
- **Data assembly**: Three builders transform raw API data → table rows (moneylines uses event-level orderbooks, spreads/totals use strike-level orderbooks)
- **Output artifacts**: Three lists of row dicts → HTML dashboard string → Streamlit component

---

## 2. Entrypoint & Streamlit Lifecycle

### 2.1 Top-Level Execution

When `streamlit run app.py` is executed:

1. **Module import phase** (runs once per Python process):
   - Lines 22-52: Streamlit secrets are exported to `os.environ` (for Kalshi/Unabated API keys)
   - Line 54: Imports `build_all_rows` and `build_dashboard_html_all` from `orchestrator`
   - Lines 64-81: `get_cached_dashboard` function is defined with `@st.cache_data(ttl=30)` decorator

2. **First render** (runs on initial page load):
   - Line 159: `main()` is called (only if script is run directly, not in Streamlit context)
   - In Streamlit context: `main()` is executed implicitly on each rerun
   - Line 136: `get_cached_dashboard()` is called
   - Cache miss → executes `build_all_rows(debug=False)` → builds HTML → returns tuple

3. **Subsequent reruns** (user interaction, automatic refresh, or manual refresh):
   - Line 136: `get_cached_dashboard()` is called again
   - If cache hit (TTL < 30 seconds): Returns cached tuple without executing `build_all_rows`
   - If cache expired (TTL ≥ 30 seconds): Cache miss → full execution
   - Line 126-129: User clicks "Refresh Now" → `get_cached_dashboard.clear()` → cache cleared → full execution

### 2.2 Streamlit Cache Behavior

- **Cache key**: Function signature + arguments (no arguments = single cache entry per process)
- **TTL**: 30 seconds (hard-coded in `@st.cache_data(ttl=30)`)
- **Invalidation**: Manual via `get_cached_dashboard.clear()` or automatic after TTL
- **Scope**: Per-process (shared across reruns within same Streamlit process)

### 2.3 Code Path Execution Frequency

| Code Path | Execution Frequency |
|-----------|---------------------|
| Secrets export (lines 22-52) | Once per Python process |
| `get_cached_dashboard()` wrapper | Every rerun (cache hit/miss logic) |
| `build_all_rows()` | On cache miss only (TTL > 30s or manual clear) |
| All API fetches | On cache miss only |
| HTML generation | On cache miss only |

---

## 3. Current Pipeline: Step-by-Step Walkthrough

### Step 1: Streamlit Cache Check

**Function**: `app.py::get_cached_dashboard() -> Tuple[List[Dict], List[Dict], List[Dict], str, datetime]`

- **Purpose**: Check Streamlit cache for recent dashboard data
- **Inputs**: None (no arguments)
- **Outputs**: Tuple of (moneyline_rows, spread_rows, totals_rows, html_string, timestamp) or (None, None, None, None, None) on error
- **Side effects**: None (cache read/write handled by Streamlit)
- **Failure modes**: Exception in `build_all_rows()` → returns None tuple, displays error via `st.error()`, calls `st.stop()`
- **Cache behavior**: Returns cached tuple if available and TTL < 30 seconds, otherwise executes Step 2

### Step 2: Orchestrator Entry

**Function**: `orchestrator.py::build_all_rows(debug=False, use_parallel=True) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]`

- **Purpose**: Coordinate fetching and building of all three table types
- **Inputs**: 
  - `debug`: bool (default False) - controls print statements
  - `use_parallel`: bool (default True) - enables parallel execution of spreads/totals builders
- **Outputs**: Tuple of (moneyline_rows, spread_rows, totals_rows) - each is `List[Dict[str, Any]]`
- **Side effects**: Multiple API calls (see Steps 3-8)
- **Failure modes**: Returns empty lists `[], [], []` if no games found

**Execution flow**:
1. Calls `get_today_games_with_fairs_and_kalshi_tickers()` (Step 3)
2. Calls `fetch_unabated_snapshot()` (Step 4) - **DUPLICATE**: snapshot already fetched in Step 3
3. Calls `build_moneylines_rows(games, debug=False)` (Step 5)
4. Calls `build_spreads_rows_for_today(games, snapshot)` and `build_totals_rows_for_today(games, snapshot)` in parallel (Steps 6-7)

### Step 3: Games List Fetch (with Unabated snapshot #1)

**Function**: `data_build/slate.py::get_today_games_with_fairs_and_kalshi_tickers() -> List[Dict[str, Any]]`

- **Purpose**: Fetch today's NBA games with Unabated fairs and Kalshi market tickers, determine canonical away/home ordering
- **Inputs**: None
- **Outputs**: `List[Dict[str, Any]]` where each dict contains:
  - `game_date`: str (YYYY-MM-DD)
  - `event_start`: str (UTC timestamp)
  - `event_ticker`: str (e.g., "KXNBAGAME-26JAN08TORBOS")
  - `away_team_id`, `home_team_id`: int (Unabated team IDs)
  - `away_team_name`, `home_team_name`: str
  - `away_fair`, `home_fair`: float | None (0-1 probabilities)
  - `away_kalshi_ticker`, `home_kalshi_ticker`: str | None (e.g., "KXNBAGAME-26JAN08TORBOS-TOR")
  - `away_roto`: int | None (rotation number from Unabated)
- **Side effects**: 
  - **API call**: `core/reusable_functions.py::fetch_unabated_snapshot()` → HTTP GET to Unabated API
  - **API calls**: `data_build/kalshi_markets.py::get_all_nba_kalshi_tickers()` → Multiple Kalshi API calls (events + markets per event)
  - **Disk IO**: Reads `team_xref_nba.csv` via `data_build/slate.py::load_team_xref()`
- **Failure modes**: Returns `[]` if no games found, if Unabated fetch fails, or if team mapping fails

**Sub-steps**:
- 3a. `data_build/unabated_callsheet.py::get_today_games_with_fairs()` → Calls `fetch_unabated_snapshot()` internally
- 3b. `data_build/kalshi_markets.py::get_all_nba_kalshi_tickers()` → Fetches all NBA event tickers and market tickers from Kalshi
- 3c. `data_build/slate.py::build_ticker_lookup()` → Builds lookup dict: `(away_code, home_code) -> {team_code: ticker}`
- 3d. `data_build/slate.py::determine_away_home_from_kalshi()` → Maps Unabated teams to Kalshi codes, extracts rotation numbers from `event_teams_raw`

### Step 4: Unabated Snapshot Fetch #2 (DUPLICATE)

**Function**: `core/reusable_functions.py::fetch_unabated_snapshot() -> Dict[str, Any]`

- **Purpose**: Fetch Unabated game odds snapshot (used by spreads/totals builders for consensus data)
- **Inputs**: None
- **Outputs**: `Dict[str, Any]` containing Unabated API response (events, teams, consensus odds)
- **Side effects**: **API call**: HTTP GET to `{UNABATED_PROD_URL}?x-api-key={UNABATED_API_KEY}` (20s timeout)
- **Failure modes**: Raises `ValueError` if API key missing, raises `Exception` if HTTP request fails
- **Note**: This is a **DUPLICATE FETCH** - same snapshot was already fetched in Step 3a. No caching between calls.

### Step 5: Moneylines Table Build

**Function**: `orchestrator.py::build_moneylines_rows(games: List[Dict[str, Any]], debug=False) -> List[Dict[str, Any]]`

- **Purpose**: Build moneyline table rows by fetching orderbooks for each event and computing break-even probabilities
- **Inputs**: 
  - `games`: List[Dict] from Step 3
  - `debug`: bool
- **Outputs**: `List[Dict[str, Any]]` where each dict contains moneyline row fields (game metadata + Kalshi prices + EVs)
- **Side effects**: 
  - **API calls**: `data_build/top_of_book.py::get_top_of_book_post_probs(event_ticker)` for each unique event_ticker
  - Parallel execution: Uses `ThreadPoolExecutor(max_workers=min(len(event_tickers), 10))`
- **Failure modes**: Games with missing event_tickers are skipped, orderbook fetch errors stored in result dict

**Sub-steps**:
- 5a. Extract unique `event_ticker` values from games (derives from `away_kalshi_ticker` or `home_kalshi_ticker`)
- 5b. For each event_ticker: `data_build/top_of_book.py::get_top_of_book_post_probs(event_ticker)` → Fetches 2 orderbooks (away market + home market), extracts NO bids, computes break-even probs
- 5c. For each game: Combine game metadata + orderbook probs → compute EVs → build row dict
- 5d. Sort rows by `away_roto` ascending

**Key data transformation**: 
- Input: `games` list with `away_kalshi_ticker`, `home_kalshi_ticker`
- Intermediate: `event_probs` dict mapping `event_ticker -> {yes_be_top_away, yes_be_top_home, ...}`
- Output: Row dicts with `away_top_prob`, `home_top_prob`, `away_ev_top`, etc.

### Step 6: Spreads Table Build (Parallel)

**Function**: `spreads/builder.py::build_spreads_rows_for_today(games: Optional[List[Dict]], snapshot: Optional[Dict]) -> List[Dict[str, Any]]`

- **Purpose**: Build spreads table rows by discovering Kalshi spread markets, selecting closest strikes, fetching orderbooks
- **Inputs**: 
  - `games`: List[Dict] from Step 3 (pre-fetched, shared)
  - `snapshot`: Dict from Step 4 (pre-fetched, shared)
- **Outputs**: `List[Dict[str, Any]]` where each dict contains spread row fields (game metadata + strike + consensus + Kalshi prices)
- **Side effects**: 
  - **API calls**: `core/reusable_functions.py::fetch_kalshi_markets_for_event()` - 6 calls (one per game, for KXNBASPREAD series)
  - **API calls**: `core/reusable_functions.py::fetch_orderbook()` - ~10-15 calls per game (one per selected strike)
  - **Disk IO**: Reads `team_xref_nba.csv` via `spreads/builder.py::load_team_xref()`
  - **Module-level cache**: `spreads/builder.py::_orderbook_cache` (Dict[str, Dict]) - in-memory, process-scoped, no TTL
- **Failure modes**: Games with missing event_tickers skipped, games with no spread markets skipped, orderbook errors stored in row

**Sub-steps** (per game):
- 6a. Match game to Unabated event via `event_start` lookup
- 6b. `spreads/builder.py::extract_unabated_spreads(event, teams_dict)` → Extract consensus spreads by team_id
- 6c. `spreads/builder.py::discover_kalshi_spread_markets(event_ticker, away_team_name, home_team_name, xref)` → **API call**: Fetch all spread markets for event (KXNBASPREAD series)
- 6d. `spreads/builder.py::select_closest_strikes_for_team_spread()` → Filter to 2 closest strikes per canonical team
- 6e. For each selected strike: `spreads/builder.py::get_spread_orderbook_data(market_ticker, side)` → Fetches orderbook (uses `_fetch_orderbook_with_cache()`), extracts YES/NO bids, computes break-even probs
- 6f. Build row dict with strike, consensus, Kalshi prices

**Key data transformations**:
- Input: `games` list + `snapshot` dict
- Intermediate: `spread_markets` list (from Kalshi API), `spreads_by_team_id` dict (from Unabated)
- Output: Row dicts with `strike`, `unabated_spread`, `tob_effective_prob`, etc.

### Step 7: Totals Table Build (Parallel)

**Function**: `totals/builder.py::build_totals_rows_for_today(games: Optional[List[Dict]], snapshot: Optional[Dict]) -> List[Dict[str, Any]]`

- **Purpose**: Build totals table rows by discovering Kalshi totals markets, selecting closest strikes, fetching orderbooks
- **Inputs**: 
  - `games`: List[Dict] from Step 3 (pre-fetched, shared)
  - `snapshot`: Dict from Step 4 (pre-fetched, shared)
- **Outputs**: `List[Dict[str, Any]]` where each dict contains totals row fields (game metadata + strike + consensus + Kalshi prices)
- **Side effects**: 
  - **API calls**: `core/reusable_functions.py::fetch_kalshi_markets_for_event()` - 6 calls (one per game, for KXNBATOTAL series)
  - **API calls**: `core/reusable_functions.py::fetch_orderbook()` - ~10-15 calls per game (one per selected strike)
  - **Module-level cache**: Uses `spreads/builder.py::_fetch_orderbook_with_cache()` (shared cache with spreads builder)
- **Failure modes**: Games with missing event_tickers skipped, games with no totals markets skipped, orderbook errors stored in row

**Sub-steps** (per game):
- 7a. Match game to Unabated event via `event_start` lookup
- 7b. `totals/builder.py::extract_unabated_totals(event, teams_dict)` → Extract consensus total
- 7c. `totals/builder.py::discover_kalshi_totals_markets(event_ticker)` → **API call**: Fetch all totals markets for event (KXNBATOTAL series)
- 7d. `totals/builder.py::select_closest_over_strikes()` → Filter to 2 closest strikes
- 7e. For each selected strike: Fetch orderbook (uses shared `_fetch_orderbook_with_cache()`), extract YES/NO bids, compute break-even probs
- 7f. Build row dict with strike, consensus, Kalshi prices

**Key data transformations**:
- Input: `games` list + `snapshot` dict
- Intermediate: `totals_markets` list (from Kalshi API), `totals_data` dict (from Unabated)
- Output: Row dicts with `strike`, `consensus`, `over_kalshi_prob`, `under_kalshi_prob`, etc.

### Step 8: HTML Generation

**Function**: `dashboard_html.py::render_dashboard_html(moneyline_rows, spread_rows, totals_rows) -> str`

- **Purpose**: Transform table row dicts into HTML string
- **Inputs**: Three lists of row dicts from Steps 5-7
- **Outputs**: HTML string (complete HTML document)
- **Side effects**: None (pure function)
- **Failure modes**: None (always returns HTML string, may be empty if rows are empty)

**Implementation**: Delegates to `moneylines/table.py::create_html_dashboard()`

### Step 9: Cache Store & Return

**Function**: `app.py::get_cached_dashboard()` (Streamlit cache decorator)

- **Purpose**: Store result tuple in Streamlit cache, return to caller
- **Inputs**: Tuple from Step 8
- **Outputs**: Same tuple (returned to `main()`)
- **Side effects**: Streamlit cache write (managed by decorator)
- **Cache key**: Function signature (no arguments = single entry)
- **TTL**: 30 seconds

---

## 4. Data Artifacts & Schemas

### 4.1 Unabated Snapshot

**Origin**: `core/reusable_functions.py::fetch_unabated_snapshot()` → HTTP GET to Unabated API

**Canonical keys**: None (top-level dict structure)

**Schema** (key fields):
```python
{
    "events": List[Dict],  # All events across all sports
    "teams": Dict[str, Dict],  # team_id -> team metadata
    # ... other fields
}
```

**Transformations**: 
- `data_build/unabated_callsheet.py::extract_nba_games_today()` filters to NBA events only
- `data_build/unabated_callsheet.py::extract_unabated_moneylines_by_team_id()` extracts fair probabilities
- `spreads/builder.py::extract_unabated_spreads()` extracts spread consensus
- `totals/builder.py::extract_unabated_totals()` extracts totals consensus

**Downstream consumers**:
- `get_today_games_with_fairs()` (Step 3a)
- `build_spreads_rows_for_today()` (Step 6)
- `build_totals_rows_for_today()` (Step 7)

**Caching**: None (fetched twice per run - duplicate work)

### 4.2 Games List

**Origin**: `data_build/slate.py::get_today_games_with_fairs_and_kalshi_tickers()` → Combines Unabated snapshot + Kalshi tickers

**Canonical keys**: 
- `event_start` (UTC timestamp string) - used for matching Unabated events
- `event_ticker` (Kalshi event ticker string) - used for Kalshi market discovery

**Schema** (per game dict):
```python
{
    "game_date": str,  # YYYY-MM-DD
    "event_start": str,  # UTC timestamp
    "event_ticker": str,  # e.g., "KXNBAGAME-26JAN08TORBOS"
    "away_team_id": int,
    "home_team_id": int,
    "away_team_name": str,
    "home_team_name": str,
    "away_fair": float | None,  # 0-1 probability
    "home_fair": float | None,  # 0-1 probability
    "away_kalshi_ticker": str | None,  # e.g., "KXNBAGAME-26JAN08TORBOS-TOR"
    "home_kalshi_ticker": str | None,  # e.g., "KXNBAGAME-26JAN08TORBOS-BOS"
    "away_roto": int | None  # Rotation number from Unabated
}
```

**Transformations**: 
- Unabated teams (keyed by team_id) → Kalshi canonical ordering (away/home) via `determine_away_home_from_kalshi()`
- Rotation numbers extracted from `event_teams_raw` dict

**Downstream consumers**:
- `build_moneylines_rows()` (Step 5)
- `build_spreads_rows_for_today()` (Step 6)
- `build_totals_rows_for_today()` (Step 7)

**Caching**: None (recomputed on every run)

### 4.3 Kalshi Markets Discovery Results

**Origin**: 
- Spreads: `spreads/builder.py::discover_kalshi_spread_markets()` → `core/reusable_functions.py::fetch_kalshi_markets_for_event()` (KXNBASPREAD series)
- Totals: `totals/builder.py::discover_kalshi_totals_markets()` → `core/reusable_functions.py::fetch_kalshi_markets_for_event()` (KXNBATOTAL series)

**Canonical keys**: 
- `event_ticker` (converted: KXNBAGAME → KXNBASPREAD or KXNBATOTAL)
- `market_ticker` (individual market identifier)

**Schema** (per market dict - spreads):
```python
{
    "ticker": str,  # Market ticker
    "title": str,  # Market title (e.g., "LAC wins by over 6.5 points")
    "parsed_strike": float,  # Strike value (e.g., 6.5)
    "market_team_code": str  # 3-letter Kalshi code (e.g., "LAC")
}
```

**Schema** (per market dict - totals):
```python
{
    "ticker": str,  # Market ticker
    "title": str,  # Market title (e.g., "Over 221.5 points")
    "parsed_strike": float,  # Strike value (e.g., 221.5)
    "direction": str  # "over" or "under"
}
```

**Transformations**: 
- Markets filtered by title patterns and market_type
- Strikes parsed from title strings (regex-based)
- Team codes parsed from ticker strings or titles

**Downstream consumers**:
- `select_closest_strikes_for_team_spread()` (spreads)
- `select_closest_over_strikes()` (totals)
- Then orderbook fetching for selected strikes

**Caching**: None (rediscovered on every run, per game)

### 4.4 Orderbooks

**Origin**: `core/reusable_functions.py::fetch_orderbook(api_key_id, private_key_pem, market_ticker)` → Kalshi API `/markets/{market_ticker}/orderbook`

**Canonical keys**: `market_ticker` (string, normalized to uppercase)

**Schema** (Kalshi API response):
```python
{
    "yes": List[List[int, int]],  # [[price_cents, quantity], ...]
    "no": List[List[int, int]],  # [[price_cents, quantity], ...]
    # ... other fields
}
```

**Transformations**: 
- `data_build/top_of_book.py::get_yes_bid_top_and_liquidity()` extracts top YES bid
- `data_build/top_of_book.py::get_no_bid_top_and_liquidity()` extracts top NO bid
- `data_build/top_of_book.py::yes_break_even_prob()` / `no_break_even_prob()` compute fee-adjusted probabilities
- For moneylines: NO bids from opposite markets used (away price = home market NO bid)

**Downstream consumers**:
- Moneylines: `get_top_of_book_post_probs()` → `get_market_no_exposure_data()`
- Spreads: `get_spread_orderbook_data()` → `_fetch_orderbook_with_cache()`
- Totals: Uses shared `_fetch_orderbook_with_cache()` from spreads module

**Caching**: 
- Module-level: `spreads/builder.py::_orderbook_cache` (Dict[str, Dict]) - in-memory, process-scoped, no TTL
- Scope: Shared between spreads and totals builders (totals imports from spreads)
- Cache key: `market_ticker.upper()`
- Not shared with moneylines builder (uses separate code path)

### 4.5 Moneyline Rows

**Origin**: `orchestrator.py::build_moneylines_rows()` → Combines games + orderbook data

**Canonical keys**: `event_ticker` (implicit, not stored in row)

**Schema** (per row dict):
```python
{
    "game_date": str,
    "event_start": str,
    "away_roto": int | None,
    "away_team": str,
    "home_team": str,
    "away_fair": float | None,
    "home_fair": float | None,
    "event_ticker": str,
    "away_ticker": str,
    "home_ticker": str,
    "away_top_prob": float | None,  # Break-even prob at top bid
    "away_topm1_prob": float | None,  # Break-even prob at top+1c
    "home_top_prob": float | None,
    "home_topm1_prob": float | None,
    "away_top_liq": int | None,  # Liquidity at top bid
    "away_topm1_liq": int | None,
    "home_top_liq": int | None,
    "home_topm1_liq": int | None,
    "away_top_price_cents": int | None,
    "home_top_price_cents": int | None,
    "away_ev_top": float | None,  # EV = (fair - break_even) * 100
    "away_ev_topm1": float | None,
    "home_ev_top": float | None,
    "home_ev_topm1": float | None
}
```

**Transformations**: 
- Input: `games` list + `event_probs` dict
- Computation: EVs computed as `(fair - break_even_prob) * 100.0`
- Sorting: By `away_roto` ascending

**Downstream consumers**: `dashboard_html.py::render_dashboard_html()` → HTML generation

### 4.6 Spread Rows

**Origin**: `spreads/builder.py::build_spreads_rows_for_today()` → Combines games + snapshot + Kalshi markets + orderbooks

**Canonical keys**: `event_ticker` + `strike` (implicit, not stored as composite key)

**Schema** (per row dict - key fields):
```python
{
    # Game metadata (shared across strikes for same game)
    "game_date": str,
    "game_time": str,
    "away_roto": int | None,
    "away_team": str,
    "home_team": str,
    "event_ticker": str,
    # Strike-specific
    "strike": str,  # Formatted (e.g., "LAC -6.5")
    "pov_team": str,  # "away" or "home"
    "kalshi_ticker": str,
    "unabated_spread": float | None,
    "tob_effective_prob": float | None,
    "tob_liq": int | None,
    "tob_p1_effective_prob": float | None,
    # ... other fields
}
```

**Transformations**: 
- Input: `games` + `snapshot` + discovered markets + orderbooks
- Strike selection: 2 closest strikes per canonical team (favorite's perspective)
- Orderbook processing: YES/NO bids extracted, break-even probs computed

**Downstream consumers**: `dashboard_html.py::render_dashboard_html()` → HTML generation

### 4.7 Totals Rows

**Origin**: `totals/builder.py::build_totals_rows_for_today()` → Combines games + snapshot + Kalshi markets + orderbooks

**Canonical keys**: `event_ticker` + `strike` (implicit)

**Schema** (per row dict - key fields):
```python
{
    # Game metadata
    "game_date": str,
    "game_time": str,
    "away_roto": int | None,
    "away_team": str,
    "home_team": str,
    # Strike-specific
    "strike": str,  # Formatted (e.g., "Over 221.5")
    "consensus": str,  # Formatted (e.g., "221.5" or "221.5 -110")
    "over_kalshi_prob": float | None,
    "over_kalshi_liq": int | None,
    "under_kalshi_prob": float | None,
    "under_kalshi_liq": int | None,
    # ... other fields
}
```

**Transformations**: 
- Input: `games` + `snapshot` + discovered markets + orderbooks
- Strike selection: 2 closest strikes to consensus total
- Orderbook processing: YES bids → over prob, NO bids → under prob

**Downstream consumers**: `dashboard_html.py::render_dashboard_html()` → HTML generation

---

## 5. Caching Behavior (Current, Real Only)

### 5.1 Streamlit Cache (`@st.cache_data`)

**Location**: `app.py::get_cached_dashboard()` (line 65)

**Scope**: Function-level (wraps entire `build_all_rows()` + HTML generation)

**TTL**: 30 seconds (hard-coded)

**Cache key**: Function signature + arguments (no arguments = single entry)

**Invalidation**: 
- Automatic: After 30 seconds
- Manual: `get_cached_dashboard.clear()` (called on "Refresh Now" button click)

**Side effects**: None (Streamlit manages cache storage)

**Impact**: Entire pipeline (Steps 2-8) skipped on cache hit. Major performance win for rapid reruns.

### 5.2 Module-Level Orderbook Cache

**Location**: `spreads/builder.py::_orderbook_cache` (line 568)

**Scope**: Module-level dict (process-scoped, shared across function calls)

**TTL**: None (persists for process lifetime)

**Cache key**: `market_ticker.upper()` (string)

**Invalidation**: None (no expiration, no manual clearing)

**Access pattern**: 
- Write: `_fetch_orderbook_with_cache()` (line 571) - writes on cache miss
- Read: `_fetch_orderbook_with_cache()` - reads on cache hit
- Shared: Totals builder imports and uses same function (shared cache)

**Impact**: Deduplicates orderbook fetches within single run (across spreads and totals). Does NOT deduplicate with moneylines (separate code path).

**Limitations**: 
- Not shared with moneylines builder
- No TTL (stale data risk if process runs long)
- Process-scoped only (not shared across Streamlit reruns if process restarts)

### 5.3 No Other Caches

**Missing caches** (explicitly NOT implemented):
- Unabated snapshot cache (fetched twice per run - duplicate work)
- Games list cache (recomputed every run)
- Kalshi markets discovery cache (rediscovered every run, per game)
- Team xref file cache (re-read from disk every run)

---

## 6. Dependency & Call Graph

### 6.1 Module Responsibilities

| Module | Primary Responsibility |
|--------|----------------------|
| `app.py` | Streamlit app entry, cache wrapper, UI rendering |
| `orchestrator.py` | Coordinate data fetching and table building |
| `data_build/slate.py` | Games list assembly (Unabated + Kalshi ticker matching) |
| `data_build/unabated_callsheet.py` | Unabated data extraction (games, moneylines, spreads, totals) |
| `data_build/kalshi_markets.py` | Kalshi ticker discovery (all NBA events/markets) |
| `data_build/top_of_book.py` | Orderbook processing for moneylines (YES/NO bid extraction, break-even calc) |
| `spreads/builder.py` | Spreads table building (markets discovery + orderbook fetching + row assembly) |
| `totals/builder.py` | Totals table building (markets discovery + orderbook fetching + row assembly) |
| `dashboard_html.py` | HTML generation (delegates to `moneylines/table.py`) |
| `core/reusable_functions.py` | Low-level API clients (Unabated snapshot, Kalshi orderbooks, Kalshi markets) |

### 6.2 Call Graph (Simplified)

```
app.py::get_cached_dashboard()
  └─> orchestrator.py::build_all_rows()
       ├─> data_build/slate.py::get_today_games_with_fairs_and_kalshi_tickers()
       │    ├─> data_build/unabated_callsheet.py::get_today_games_with_fairs()
       │    │    └─> core/reusable_functions.py::fetch_unabated_snapshot()  [API CALL #1]
       │    ├─> data_build/kalshi_markets.py::get_all_nba_kalshi_tickers()
       │    │    └─> core/reusable_functions.py::fetch_kalshi_events()  [API CALLS]
       │    │    └─> core/reusable_functions.py::fetch_kalshi_markets_for_event()  [API CALLS]
       │    └─> data_build/slate.py::determine_away_home_from_kalshi()
       ├─> core/reusable_functions.py::fetch_unabated_snapshot()  [API CALL #2 - DUPLICATE]
       ├─> orchestrator.py::build_moneylines_rows()
       │    └─> data_build/top_of_book.py::get_top_of_book_post_probs(event_ticker)  [per event, parallel]
       │         └─> core/reusable_functions.py::fetch_orderbook()  [2 calls per event: away + home markets]
       ├─> spreads/builder.py::build_spreads_rows_for_today()  [parallel]
       │    ├─> core/reusable_functions.py::fetch_kalshi_markets_for_event()  [per game: KXNBASPREAD]
       │    └─> spreads/builder.py::get_spread_orderbook_data()
       │         └─> spreads/builder.py::_fetch_orderbook_with_cache()
       │              └─> core/reusable_functions.py::fetch_orderbook()  [per selected strike]
       └─> totals/builder.py::build_totals_rows_for_today()  [parallel]
            ├─> core/reusable_functions.py::fetch_kalshi_markets_for_event()  [per game: KXNBATOTAL]
            └─> spreads/builder.py::_fetch_orderbook_with_cache()  [shared with spreads]
                 └─> core/reusable_functions.py::fetch_orderbook()  [per selected strike]
```

### 6.3 Responsibility Bleeding

**Issues** (responsibilities not cleanly separated):

1. **Games list assembly** (`data_build/slate.py`):
   - Calls `fetch_unabated_snapshot()` directly (should receive as parameter)
   - Calls `get_all_nba_kalshi_tickers()` (expensive, should be cached or parameterized)
   - Mixes Unabated data extraction + Kalshi ticker matching + team mapping logic

2. **Orderbook caching**:
   - Lives in `spreads/builder.py` but used by `totals/builder.py` (cross-module dependency)
   - Not used by `top_of_book.py` (moneylines uses separate code path)
   - No clear ownership (should be in shared module)

3. **Unabated snapshot fetching**:
   - Called directly in multiple places (no single owner)
   - No caching (fetched twice per run)
   - Should be fetched once and passed down

4. **Team xref loading**:
   - Loaded in multiple places (`data_build/slate.py`, `spreads/builder.py`)
   - Re-read from disk every run (no caching)

---

## 7. Common Failure Points & Debugging Guidance

### 7.1 Rate Limiting

**Symptom**: HTTP 429 errors, orderbook fetches fail

**Where it occurs**: 
- `core/reusable_functions.py::fetch_orderbook()` (Kalshi API)
- `core/reusable_functions.py::fetch_kalshi_markets_for_event()` (Kalshi API)

**Current behavior**: Exceptions propagate up, stored in result dicts as error fields

**Debugging**: 
- Check orderbook fetch counts: ~150-200 calls per run
- Check parallel worker counts: Moneylines uses 10 workers, spreads/totals use sequential per-game
- No retry logic implemented (fail-fast)

**Mitigation**: Streamlit cache reduces frequency, but doesn't help within single run

### 7.2 Missing Team Mappings

**Symptom**: Games missing from output, `away_team_name`/`home_team_name` are None

**Where it occurs**: 
- `data_build/slate.py::determine_away_home_from_kalshi()` - team mapping fails
- `data_build/slate.py::map_unabated_to_kalshi_code()` - xref lookup fails

**Debugging**: 
- Check `team_xref_nba.csv` for missing teams
- Check team name normalization (case, spacing)
- `determine_away_home_from_kalshi()` returns None values on failure (games skipped downstream)

**Mitigation**: None (fail silently, game excluded from output)

### 7.3 Missing Kalshi Markets

**Symptom**: Spreads/totals rows missing for games, `discover_kalshi_*_markets()` returns empty list

**Where it occurs**: 
- `spreads/builder.py::discover_kalshi_spread_markets()` - no markets found for event
- `totals/builder.py::discover_kalshi_totals_markets()` - no markets found for event

**Debugging**: 
- Check event ticker conversion: `KXNBAGAME-*` → `KXNBASPREAD-*` or `KXNBATOTAL-*`
- Check market title parsing (regex patterns may not match all formats)
- Enable `DEBUG_SPREADS` or `DEBUG_TOTALS` flags for verbose logging

**Mitigation**: Games skipped for spreads/totals tables, moneylines still built

### 7.4 Duplicate Work on Streamlit Reruns

**Symptom**: Full pipeline executes on every rerun (even without user interaction)

**Where it occurs**: 
- Streamlit cache TTL expiration (30 seconds)
- User interaction triggers rerun (cache may be valid, but rerun still occurs)

**Debugging**: 
- Check cache hit rate: If cache hits, `build_all_rows()` is not called (check debug prints)
- Cache key: Single entry (no arguments), so all reruns share same cache

**Mitigation**: 30-second TTL reduces frequency, but doesn't eliminate duplicate work entirely

### 7.5 Schema Mismatch

**Symptom**: KeyError, AttributeError, or None values in unexpected places

**Where it occurs**: 
- Games list → builders (expected fields missing)
- Unabated snapshot → extractors (field names changed)
- Kalshi API responses → parsers (response structure changed)

**Debugging**: 
- Check field names in dict access (e.g., `game.get("away_team_name")` vs `game["away_team_name"]`)
- Check API response structure (print first event/market for inspection)
- Enable debug flags for verbose logging

**Mitigation**: Defensive coding (`.get()` with defaults) in most places, but not all

### 7.6 DEBUG / VERBOSE Flags

**Available flags**:

- `orchestrator.py::build_all_rows(debug=True)` - Prints timing and progress for orchestrator steps
- `orchestrator.py::build_moneylines_rows(debug=True)` - Prints event ticker collection and errors
- `spreads/builder.py::DEBUG_SPREADS` (module-level constant) - Prints market discovery, strike selection, orderbook fetch details
- `totals/builder.py::DEBUG_TOTALS` (module-level constant) - Prints market discovery, strike selection, orderbook fetch details

**Usage**: Set `debug=True` in `build_all_rows()` call (currently hard-coded to `False` in `app.py` line 74)

**Output**: Prints to stdout (visible in Streamlit console/logs, not in UI)

---

## 8. Appendix A: Future Integration Opportunities

**Note**: This section describes potential optimizations that are NOT currently implemented. These modules exist in the codebase but are NOT part of the `app.py` runtime flow.

### 8.1 Games Cache (`ad_hoc/games_cache.py`)

**Current state**: Standalone module, not integrated into `app.py`

**Opportunity**: Replace `data_build/slate.py::get_today_games_with_fairs_and_kalshi_tickers()` with cached version

**Potential impact**: 
- Eliminates Unabated snapshot fetch #1 (saves ~15 seconds)
- Eliminates Kalshi ticker discovery (saves ~2-5 seconds)
- Cache key: Date-based (invalidates at midnight)
- Cache storage: CSV file (`ad_hoc/games_cache.csv`)

**Integration point**: `orchestrator.py::build_all_rows()` → Replace Step 3 call with `ad_hoc/games_cache.py::get_todays_games(use_cache=True)`

**Challenges**: 
- Schema mismatch (cached games format may differ from current format)
- Rotation numbers may be missing (requires validation)
- Cache invalidation logic (date-based vs. TTL-based)

### 8.2 Fast Kalshi Data Export (`ad_hoc/kalshi_data_export.py`)

**Current state**: Standalone script, not integrated into `app.py`

**Opportunity**: Use as orderbook data provider or pre-fetch step

**Potential impact**:
- Pre-fetches all orderbooks in single parallel session (optimized for speed)
- Markets manifest cache (eliminates redundant markets discovery)
- Fail-fast retry logic (better rate limit handling)
- All strikes fetched (not just 2 closest)

**Integration options**:

1. **As provider**: Replace individual orderbook fetches with pre-fetched data from export module
   - Integration point: `orchestrator.py::build_all_rows()` → Pre-fetch all orderbooks → Pass to builders
   - Challenge: Builder signatures would need to accept pre-fetched data

2. **As pre-fetch step**: Run export module before dashboard build, load results
   - Integration point: `app.py::get_cached_dashboard()` → Load CSV from export → Build rows from CSV
   - Challenge: CSV format may not match builder expectations

3. **As shared cache**: Use export module's markets manifest cache
   - Integration point: Spreads/totals builders → Load markets from manifest cache
   - Challenge: Cache key alignment (event_ticker format consistency)

**Trade-offs**:
- Export module fetches ALL strikes (more data, slower)
- Export module has no Unabated dependency (different data shape)
- Export module optimized for batch processing (may not fit incremental updates)

---

**End of Document**
