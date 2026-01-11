# Architecture Plan: Single-Ownership Data Fetching & Cache Reuse

**Status**: Design Document  
**Purpose**: Re-architect data fetching to eliminate redundant API calls and consolidate caching under single ownership  
**Target**: Preserve all existing behavior while reducing runtime and API call volume

---

## 1. Overview

### 1.1 Current State Summary

The current `app.py` data pipeline exhibits several inefficiencies:

- **Duplicate Unabated snapshot fetches**: Snapshot fetched twice per run (~30 seconds wasted)
- **Redundant Kalshi markets discovery**: Markets rediscovered for each game on every run (12+ API calls)
- **Fragmented orderbook caching**: Multiple module-level caches that don't share state
- **No cross-run caching**: Streamlit cache only (30s TTL), no persistent caching for expensive operations

### 1.2 Target State

A single `RunContext` object owned by the orchestrator will:
- Fetch Unabated snapshot **once** per run (with cross-run TTL cache)
- Load games list **once** per run (with date-based file cache)
- Discover/restore Kalshi markets **once** per run (with short-TTL file cache)
- Provide shared orderbook access via unified provider (with process-scoped cache)
- Pass all data to builders as pure functions (builders no longer fetch independently)

### 1.3 Principles

- **Single ownership**: Orchestrator owns all data fetching
- **Builder purity**: Builders consume data, do not fetch
- **Cache consolidation**: One cache per resource type, explicitly managed
- **Backward compatibility**: All existing features preserved, builder signatures gradually migrated

---

## 2. Current Problems (Mission-Critical Only)

### 2.1 Duplicate Unabated Snapshot Fetch

**Problem**: Unabated snapshot fetched twice per run

**Location 1**: `data_build/unabated_callsheet.py::get_today_games_with_fairs()` (line 201)
- Called by: `data_build/slate.py::get_today_games_with_fairs_and_kalshi_tickers()` (Step 3a)
- Side effect: HTTP GET to Unabated API (~15 seconds)

**Location 2**: `orchestrator.py::build_all_rows()` (line 197)
- Called directly: `core/reusable_functions.py::fetch_unabated_snapshot()`
- Side effect: Same HTTP GET to Unabated API (~15 seconds)

**Impact**: ~30 seconds wasted per run, duplicate network traffic, increased API load

**Root cause**: No shared ownership - games fetcher and orchestrator both fetch snapshot independently

---

### 2.2 Redundant Kalshi Markets Discovery

**Problem**: Kalshi markets rediscovered for every game on every run

**Location**: `spreads/builder.py::build_spreads_rows_for_today()` and `totals/builder.py::build_totals_rows_for_today()`

**For spreads** (per game):
- `spreads/builder.py::discover_kalshi_spread_markets()` (line 881)
  - Calls: `core/reusable_functions.py::fetch_kalshi_markets_for_event()` (KXNBASPREAD series)
  - API call per game (6 games = 6 calls)

**For totals** (per game):
- `totals/builder.py::discover_kalshi_totals_markets()` (line 782)
  - Calls: `core/reusable_functions.py::fetch_kalshi_markets_for_event()` (KXNBATOTAL series)
  - API call per game (6 games = 6 calls)

**Total**: 12 API calls per run for markets discovery

**Impact**: ~2-5 seconds wasted per run, redundant API load, no cache reuse across runs

**Root cause**: Builders discover markets independently, no shared cache or manifest

**Note**: `data_build/kalshi_callsheet.py::fetch_kalshi_callsheet_for_slate()` exists but is NOT used by spreads/totals builders - they implement their own discovery logic

---

### 2.3 Fragmented Orderbook Caching

**Problem**: Multiple orderbook caches that don't share state

**Cache 1**: `spreads/builder.py::_orderbook_cache` (line 568)
- Type: Module-level dict `Dict[str, Dict[str, Any]]`
- Key: `market_ticker.upper()`
- Scope: Process-scoped (shared within spreads builder)
- Used by: Spreads builder + totals builder (totals imports `_fetch_orderbook_with_cache`)

**Cache 2**: `data_build/orderbook_snapshot.py::_orderbook_cache` (line 54)
- Type: Module-level dict `Dict[Tuple[str, str], OrderbookSnapshot]`
- Key: `(market_ticker.upper(), side.upper())`
- Scope: Process-scoped (shared within orderbook_snapshot module)
- Used by: Moneylines builder (via `data_build/top_of_book.py::get_top_of_book_post_probs()`)

**Impact**: 
- Same ticker fetched multiple times if accessed via different code paths
- Moneylines and spreads/totals don't share cache (separate cache keys and structures)
- No cross-run persistence (cache cleared on process restart)

**Root cause**: No unified orderbook provider - each builder/module implements its own caching

---

### 2.4 No Cross-Run Caching for Expensive Operations

**Problem**: Expensive operations (Unabated snapshot, Kalshi markets discovery) never cached across runs

**Streamlit cache**: `app.py::get_cached_dashboard()` (line 65)
- TTL: 30 seconds
- Scope: Per-process (cleared on process restart)
- Caches: Entire dashboard build result (expensive operations + HTML generation)

**Missing caches**:
- Unabated snapshot: No cache (fetched twice per run, re-fetched on every Streamlit rerun after TTL)
- Kalshi markets discovery: No cache (rediscovered on every run)
- Games list: No cache (recomputed on every run, includes expensive ticker matching)

**Impact**: 
- Rapid Streamlit reruns (user interaction) trigger full pipeline execution
- Development/testing cycles waste API quota
- No benefit from "warm" data (markets/structure rarely change within 30 seconds)

**Root cause**: Streamlit cache is too coarse-grained (entire dashboard) and too short-lived (30s TTL)

---

## 3. Target Architecture: Single-Ownership RunContext

### 3.1 RunContext Design

**Location**: New module `orchestrator_context.py` (or extend `orchestrator.py`)

**Definition**:
```python
@dataclass
class RunContext:
    """
    Single-ownership context object for one dashboard build run.
    
    All data fetching is owned by the orchestrator. Builders consume this context
    but do not fetch data independently.
    """
    # Core data (always populated)
    snapshot: Dict[str, Any]  # Unabated snapshot (cached)
    games: List[Dict[str, Any]]  # Games list with canonical keys
    
    # Kalshi markets (populated on-demand or from cache)
    markets_manifest: Optional[Dict[str, KalshiMarkets]] = None
    
    # Orderbook provider (shared access point)
    orderbook_provider: Optional[OrderbookProvider] = None
    
    # Metadata (for debugging/telemetry)
    _cache_hits: Dict[str, bool] = field(default_factory=dict)  # Track cache usage
```

**Ownership**: `orchestrator.py::build_all_rows()` creates and owns `RunContext` instance

**Lifetime**: Single run (created fresh on each `build_all_rows()` call, destroyed after use)

---

### 3.2 OrderbookProvider Design

**Location**: New module `data_build/orderbook_provider.py`

**Concurrency Strategy**: **Prefetch-then-read-only** (recommended, least risky)
- All required tickers are collected before builder execution
- Pre-fetch all orderbooks in single parallel session (ThreadPoolExecutor)
- Cache populated before builders run
- Builders operate in read-only mode (cache hits only, no concurrent writes)
- If cache miss occurs during builder execution (shouldn't happen), accept duplicate fetch (no locking needed)

**Alternative Strategy**: Thread-safe with locking (if dynamic fetching needed)
- Use `threading.Lock()` around cache read/write operations
- More complex, but allows dynamic fetching during builder execution
- Risk: Lock contention may slow down parallel builders

**Definition**:
```python
class OrderbookProvider:
    """
    Unified orderbook access point with process-scoped caching.
    
    Replaces module-level caches in spreads/builder.py and data_build/orderbook_snapshot.py.
    
    Concurrency: Designed for prefetch-then-read-only pattern.
    If used with concurrent writers, caller must ensure thread-safety.
    """
    def __init__(self, ttl_seconds: int = 0):
        self._cache: Dict[str, Dict[str, Any]] = {}  # market_ticker -> orderbook dict
        self._cache_timestamps: Dict[str, float] = {}  # market_ticker -> timestamp
        self._ttl_seconds = ttl_seconds
        # Note: No lock needed if using prefetch-then-read-only pattern
    
    def get(self, market_ticker: str, allow_cache: bool = True) -> Optional[Dict[str, Any]]:
        """
        Get orderbook for market_ticker (with caching).
        
        Thread-safety: Safe for concurrent reads. If concurrent writes expected,
        caller must synchronize or use prefetch pattern.
        
        Args:
            market_ticker: Kalshi market ticker
            allow_cache: If False, bypass cache and fetch fresh
        
        Returns:
            Orderbook dict or None if fetch fails
        """
        market_ticker = market_ticker.upper()
        
        # Check cache
        if allow_cache and market_ticker in self._cache:
            cached = self._cache[market_ticker]
            if self._ttl_seconds == 0 or (time.time() - self._cache_timestamps[market_ticker]) < self._ttl_seconds:
                return cached
        
        # Cache miss - fetch fresh (should be rare if prefetch pattern used)
        orderbook = fetch_orderbook(api_key_id, private_key_pem, market_ticker)
        if orderbook:
            self._cache[market_ticker] = orderbook
            self._cache_timestamps[market_ticker] = time.time()
        return orderbook
    
    def prefetch(self, tickers: List[str], max_workers: int = 10) -> None:
        """
        Pre-fetch all orderbooks in parallel (populates cache).
        
        This should be called before builder execution to ensure cache is warm.
        After this completes, builder calls to get() should be cache hits only.
        """
        # Parallel fetch all tickers, populate cache
        # ThreadPoolExecutor with max_workers
        pass
    
    def clear_cache(self) -> None:
        """Clear cache (for debugging)."""
        self._cache.clear()
        self._cache_timestamps.clear()
```

**Usage**: All builders call `run_context.orderbook_provider.get(ticker)` instead of direct `fetch_orderbook()` calls

**Scope**: Process-scoped (shared across all builders within single run, persists for process lifetime)

**Thread-safety**: Prefetch pattern recommended (no locking needed). If dynamic fetching required, add locking or accept occasional duplicate fetches.

---

### 3.3 Function Role Changes

**From producer to consumer**:

| Function | Current Role | New Role |
|----------|-------------|----------|
| `get_today_games_with_fairs_and_kalshi_tickers()` | Fetches snapshot internally | Accepts `snapshot` parameter (optional), uses if provided |
| `build_spreads_rows_for_today()` | Fetches snapshot if None | Accepts `RunContext`, uses `context.snapshot` |
| `build_totals_rows_for_today()` | Fetches snapshot if None | Accepts `RunContext`, uses `context.snapshot` |
| `discover_kalshi_spread_markets()` | Called by builder | Called once by orchestrator, results stored in `RunContext.markets_manifest` |
| `discover_kalshi_totals_markets()` | Called by builder | Called once by orchestrator, results stored in `RunContext.markets_manifest` |

**New producer** (orchestrator-only):

| Function | New Role |
|----------|----------|
| `build_run_context()` | Factory function: Creates `RunContext`, fetches/caches all data, returns context |
| `fetch_unabated_snapshot()` | Called once by `build_run_context()`, result cached |
| `fetch_kalshi_callsheet_for_slate()` | Called once by `build_run_context()`, result cached in manifest |

---

### 3.4 Builder Signature Migration

**Phase 1** (backward compatible):
```python
def build_spreads_rows_for_today(
    games: Optional[List[Dict[str, Any]]] = None,
    snapshot: Optional[Dict[str, Any]] = None,
    context: Optional[RunContext] = None  # NEW
) -> List[Dict[str, Any]]:
    if context:
        games = context.games
        snapshot = context.snapshot
        markets_manifest = context.markets_manifest
        orderbook_provider = context.orderbook_provider
    elif games is None:
        games = get_today_games_with_fairs_and_kalshi_tickers()
    # ... rest of logic
```

**Phase 2** (after migration complete):
```python
def build_spreads_rows_for_today(context: RunContext) -> List[Dict[str, Any]]:
    # context.games, context.snapshot, etc. always available
```

---

## 4. New Order of Operations

### 4.1 Execution Flow (Runtime Trace) - **PROPOSED/TARGET STATE**

**⚠️ NOTE**: This section describes the **target architecture**, not the current implementation. All steps below are **proposed changes** to be implemented during migration.

**Invocation**: `streamlit run app.py`

**Step 1**: Streamlit cache check (`app.py::get_cached_dashboard()`) **[UNCHANGED]**
- Cache hit (TTL < 30s): Return cached tuple, **STOP** (Steps 2-7 skipped)
- Cache miss: Continue to Step 2

**Step 2**: Create RunContext (`orchestrator.py::build_all_rows()` → `build_run_context()`) **[NEW]**
- 2a. Check Unabated snapshot cache (file-backed, TTL 30s) **[NEW]**
  - Cache hit: Load from file cache
  - Cache miss: `core/reusable_functions.py::fetch_unabated_snapshot()` → **API CALL #1**
  - Save to file cache for cross-process reuse
- 2b. Validate snapshot structure, extract teams dict **[NEW]**
- 2c. Load games list (with file cache check) **[NEW - uses games_cache.py]**
  - Check file cache: `ad_hoc/games_cache.csv` (date-based validation)
  - Cache hit: Load from CSV, convert to expected format via adapter
  - Cache miss: `data_build/slate.py::get_today_games_with_fairs_and_kalshi_tickers(snapshot=snapshot)` → Uses snapshot from 2a (no duplicate fetch)
  - **CRITICAL**: Enforce canonical `game_key = f"{event_ticker}|{event_start}"` on all games at this boundary
- 2d. Validate games structure (all games have `event_ticker`, `event_start`, `game_key`) **[NEW]**
- 2e. Load/restore Kalshi markets manifest (with file cache check) **[NEW]**
  - Check file cache: `ad_hoc/kalshi_markets_manifest_YYYYMMDD.json` (TTL 60s)
  - Cache hit: Load from JSON, restore to `KalshiMarkets` objects, key by `game_key` or `event_ticker`
  - Cache miss: `data_build/kalshi_callsheet.py::fetch_kalshi_callsheet_for_slate(games)` → **API CALLS** (N calls: one per game for spreads + one per game for totals)
  - Save to file cache for cross-process reuse
- 2f. Create `OrderbookProvider` instance (empty cache initially, thread-safe design) **[NEW]**
- 2g. Return `RunContext(snapshot, games, markets_manifest, orderbook_provider)`

**Step 3**: Build moneylines (`orchestrator.py::build_moneylines_rows(games, context=run_context)`) **[MODIFIED]**
- 3a. Extract unique `event_ticker` values from `context.games` (games already have canonical keys)
- 3b. For each event_ticker (parallel, 10 workers):
  - Call `data_build/top_of_book.py::get_top_of_book_post_probs(event_ticker, orderbook_provider=context.orderbook_provider)` **[MODIFIED - accepts provider]**
  - Internal: Fetches 2 orderbooks (away + home markets) via `context.orderbook_provider.get()` **[NEW]**
  - **API CALLS**: Only for cache misses (deduplicated by provider, thread-safe)
- 3c. Build rows, return `moneyline_rows`

**Step 4**: Build spreads (parallel with totals) **[MODIFIED]**
- 4a. `spreads/builder.py::build_spreads_rows_for_today(context=run_context)` **[MODIFIED - accepts context]**
- 4b. For each game:
  - Get markets from `context.markets_manifest[game_key]` or `context.markets_manifest[event_ticker]` **[NEW - no API call]**
  - Select closest strikes (uses Unabated consensus from `context.snapshot`)
  - For each selected strike: `context.orderbook_provider.get(market_ticker)` → **API CALLS** only for cache misses **[NEW]**
- 4c. Build rows, return `spread_rows`

**Step 5**: Build totals (parallel with spreads) **[MODIFIED]**
- 5a. `totals/builder.py::build_totals_rows_for_today(context=run_context)` **[MODIFIED - accepts context]**
- 5b. For each game:
  - Get markets from `context.markets_manifest[game_key]` or `context.markets_manifest[event_ticker]` **[NEW - no API call]**
  - Select closest strikes (uses Unabated consensus from `context.snapshot`)
  - For each selected strike: `context.orderbook_provider.get(market_ticker)` → **API CALLS** only for cache misses **[NEW]**
- 5c. Build rows, return `totals_rows`

**Step 6**: Generate HTML (`dashboard_html.py::render_dashboard_html(moneyline_rows, spread_rows, totals_rows)`) **[UNCHANGED]**
- Pure function, no API calls

**Step 7**: Store in Streamlit cache (`app.py::get_cached_dashboard()`) **[UNCHANGED]**
- Store tuple in Streamlit cache (TTL 30s)
- Return tuple to caller

---

### 4.2 Key Differences from Current Flow

| Aspect | Current | New |
|--------|---------|-----|
| Unabated snapshot fetches | 2 per run | 1 per run (with cross-run TTL cache) |
| Kalshi markets discovery | 12 calls per run (per game, per builder) | 0-1 calls per run (cached manifest, shared across builders) |
| Orderbook fetches | Fragmented (moneylines vs spreads/totals don't share) | Unified (single provider, process-scoped cache) |
| Games list computation | Every run (no cache) | File cache (date-based) |
| Builder data fetching | Builders fetch independently | Builders consume `RunContext` only |

---

## 5. Cache Strategy (Concrete and Unified)

### 5.1 Unabated Snapshot Cache

**Owner**: Module-level helper in `orchestrator_context.py` (or new `data_build/snapshot_cache.py`)

**Location**: **File-backed** (with in-memory fallback) - critical for Streamlit process restarts

**Key**: Date-based filename: `ad_hoc/unabated_snapshot_YYYYMMDD.json` + timestamp check

**TTL**: 30 seconds (file age check)

**Scope**: Cross-process (survives Streamlit process restarts, dev mode restarts)

**Invalidation**: Time-based (file age > 30s), date mismatch, manual clear via `force_refresh` flag or file deletion

**Rationale**: Streamlit dev mode and deploys restart processes frequently. File-backed cache ensures snapshot fetch (dominant latency) is reused even after process restart.

**Implementation**:
```python
_SNAPSHOT_CACHE_FILE_TTL_SECONDS = 30

def get_cached_snapshot(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Get Unabated snapshot with file-backed caching.
    
    File cache survives process restarts (critical for Streamlit dev/deploy).
    """
    if not force_refresh:
        # Try file cache first
        cache_file = get_snapshot_cache_path()  # Date-based filename
        if cache_file.exists():
            file_age = time.time() - cache_file.stat().st_mtime
            if file_age < _SNAPSHOT_CACHE_FILE_TTL_SECONDS:
                with open(cache_file, 'r') as f:
                    return json.load(f)
    
    # Cache miss - fetch fresh
    snapshot = fetch_unabated_snapshot()
    
    # Save to file cache
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, 'w') as f:
        json.dump(snapshot, f)
    
    return snapshot
```

**Debug bypass**: `build_run_context(force_refresh_snapshot=True)` or delete cache file

---

### 5.2 Games List Cache

**Owner**: `ad_hoc/games_cache.py` (existing module, integrate into RunContext creation)

**Location**: File (`ad_hoc/games_cache.csv`)

**Key**: Date (YYYY-MM-DD) - single entry per date

**TTL**: Date-based (invalidates at midnight LA time)

**Scope**: Cross-run (persists across process restarts)

**Invalidation**: Date mismatch (cached date != today), manual clear via file deletion

**Implementation**: Use existing `ad_hoc/games_cache.py::get_todays_games(use_cache=True)`

**Debug bypass**: `get_todays_games(use_cache=False)` or delete cache file

**Note**: Requires adapter function to convert cached format to expected format (if schemas differ)

---

### 5.3 Kalshi Markets Manifest Cache

**Owner**: New module `data_build/markets_manifest_cache.py` (or extend `orchestrator_context.py`)

**Location**: File (`ad_hoc/kalshi_markets_manifest_YYYYMMDD.json`)

**Key**: Date (YYYYMMDD) - single entry per date

**TTL**: 60 seconds (file age check)

**Scope**: Cross-run (persists across process restarts)

**Invalidation**: File age > 60s, date mismatch, manual clear via file deletion

**Implementation**:
```python
def load_markets_manifest_from_cache() -> Optional[Dict[str, Any]]:
    cache_path = get_markets_manifest_cache_path()  # Date-based filename
    if not cache_path.exists():
        return None
    
    file_age = time.time() - cache_path.stat().st_mtime
    if file_age > 60:
        return None
    
    with open(cache_path, 'r') as f:
        return json.load(f)

def save_markets_manifest_to_cache(manifest: Dict[str, Any]) -> None:
    # Save to JSON file
```

**Debug bypass**: Delete cache file or set TTL to 0

**Serialization**: Flat dict (not `KalshiMarkets` objects) - serialize tickers, strikes, market metadata only

---

### 5.4 Orderbook Cache

**Owner**: `OrderbookProvider` class (process-scoped instance)

**Location**: Memory (instance dict in `OrderbookProvider._cache`)

**Key**: `market_ticker.upper()` (string)

**TTL**: Configurable (default 0 = no expiration, process lifetime)

**Scope**: Per-run (shared across all builders within single run, cleared on process restart)

**Invalidation**: Process restart, manual clear via `provider.clear_cache()`, TTL expiration (if configured)

**Implementation**: See `OrderbookProvider.get()` method (Section 3.2)

**Debug bypass**: `provider.get(ticker, allow_cache=False)`

**Deduplication**: Single provider instance shared across moneylines, spreads, totals builders

---

### 5.5 Streamlit Cache (Unchanged)

**Owner**: Streamlit framework

**Location**: Streamlit-managed (process-scoped)

**Key**: Function signature (no arguments = single entry)

**TTL**: 30 seconds (hard-coded in `@st.cache_data(ttl=30)`)

**Scope**: Cross-run (within same process)

**Invalidation**: TTL expiration, manual clear via `get_cached_dashboard.clear()`

**Note**: This cache wraps entire dashboard build. New caches (snapshot, games, markets) operate at finer granularity and survive Streamlit cache expiration.

---

### 5.6 Cache Hierarchy

```
Streamlit cache (30s TTL)
  └─> RunContext creation
       ├─> Unabated snapshot cache (30s TTL, module-level)
       ├─> Games list cache (date-based, file)
       ├─> Markets manifest cache (60s TTL, file)
       └─> Orderbook provider cache (process lifetime, instance-level)
```

**Key insight**: Finer-grained caches allow reuse even when Streamlit cache expires (e.g., snapshot cache hit even if Streamlit cache miss).

---

## 6. Migration Plan (Minimal-Risk, Staged)

### Step 1: Introduce RunContext Without Behavior Change

**Goal**: Add `RunContext` structure, but builders still fetch independently (backward compatible)

**Files to modify**:
- `orchestrator.py`: Add `build_run_context()` function (fetches data, creates context)
- New file: `orchestrator_context.py` (or add to `orchestrator.py`): Define `RunContext` dataclass

**Functions to add**:
- `build_run_context(force_refresh_snapshot=False) -> RunContext`

**Functions to modify**:
- `orchestrator.py::build_all_rows()`: Call `build_run_context()`, extract `games` and `snapshot`, pass to builders (no builder changes yet)

**Migration Invariants** (not yet enforced, but prepare for Step 2):
- No invariants yet (backward compatible, builders still fetch independently)

**Validation**:
- Run `build_all_rows()` → Verify same behavior (builders still fetch snapshot if None)
- Verify no performance regression
- Verify all tables still build correctly

**Risk**: Low (no behavior change, builders still work independently)

---

### Step 2: Single Ownership of Unabated Snapshot + Canonical Key Enforcement

**Goal**: Eliminate duplicate snapshot fetch + enforce canonical game keys at boundary

**Files to modify**:
- `data_build/unabated_callsheet.py`: `get_today_games_with_fairs(snapshot: Optional[Dict] = None)`
- `data_build/slate.py`: `get_today_games_with_fairs_and_kalshi_tickers(snapshot: Optional[Dict] = None)`
- `orchestrator_context.py`: Add file-backed snapshot cache (Section 5.1), add `_enforce_canonical_game_key()` function
- `orchestrator.py`: `build_run_context()` fetches snapshot once (with file cache), passes to games fetcher, enforces canonical keys

**Functions to add**:
- `_enforce_canonical_game_key(games: List[Dict]) -> None`: Validate and set `game_key` on all games (fail fast)

**Functions to modify**:
- `get_today_games_with_fairs()`: Accept `snapshot` parameter, use if provided (no fetch if provided)
- `get_today_games_with_fairs_and_kalshi_tickers()`: Accept `snapshot` parameter, pass to `get_today_games_with_fairs()`
- `build_run_context()`: Fetch snapshot once (with file cache), pass to games fetcher, call `_enforce_canonical_game_key()` immediately after games loaded

**Migration Invariants** (enforce via assertions/logging):
- ✅ No builder calls `fetch_unabated_snapshot()` if `context.snapshot` is present
- ✅ All games have `game_key` after `_enforce_canonical_game_key()` call
- ✅ All games have both `event_ticker` and `event_start` before key enforcement

**Validation**:
- Verify `fetch_unabated_snapshot()` called exactly once per run (add logging/counters)
- Verify all games have `game_key` after loading (assert in `build_run_context()`)
- Verify games list still correct (no regression)
- Verify snapshot file cache works (second run in < 30s uses cache, survives process restart)

**Risk**: Low (backward compatible parameters, games fetcher still works if snapshot not provided). Key enforcement is strict but fails fast with clear errors.

---

### Step 3: Shared Kalshi Markets Manifest Cache

**Goal**: Discover markets once, cache manifest, share across builders

**Files to modify**:
- New file: `data_build/markets_manifest_cache.py` (or add to `orchestrator_context.py`)
- `orchestrator_context.py`: Add `markets_manifest` field to `RunContext`, load/restore in `build_run_context()`
- `spreads/builder.py`: `build_spreads_rows_for_today()` accepts `context: Optional[RunContext]`, uses `context.markets_manifest` if provided
- `totals/builder.py`: `build_totals_rows_for_today()` accepts `context: Optional[RunContext]`, uses `context.markets_manifest` if provided

**Functions to add**:
- `load_markets_manifest_from_cache() -> Optional[Dict]`
- `save_markets_manifest_to_cache(manifest: Dict) -> None`
- `build_markets_manifest(games, markets_dict) -> Dict` (serialization helper, key by both `game_key` and `event_ticker`)

**Functions to modify**:
- `build_run_context()`: Load manifest from cache or call `fetch_kalshi_callsheet_for_slate()`, store in context (key by `game_key` and `event_ticker`)
- `build_spreads_rows_for_today()`: Use `context.markets_manifest[game_key]` or `context.markets_manifest[event_ticker]` instead of calling `discover_kalshi_spread_markets()` per game
- `build_totals_rows_for_today()`: Use `context.markets_manifest[game_key]` or `context.markets_manifest[event_ticker]` instead of calling `discover_kalshi_totals_markets()` per game

**Migration Invariants** (enforce via assertions/logging):
- ✅ No builder calls `fetch_kalshi_markets_for_event()` if `context.markets_manifest` is present
- ✅ No builder calls `discover_kalshi_spread_markets()` if manifest present
- ✅ No builder calls `discover_kalshi_totals_markets()` if manifest present

**Validation**:
- Verify markets discovery API calls: **0 with warm cache, N calls with cold cache** (where N = 2 × number of games: one call per game for spreads + one per game for totals)
- **Clarification**: Cache eliminates **repetition** (discover once, reuse), not discovery entirely. Cold cache still requires N API calls.
- Verify spreads/totals tables still build correctly
- Verify manifest cache works (second run uses cache, zero discovery calls)
- Verify manifest keying works (lookup by `game_key` succeeds)

**Risk**: Medium (requires refactoring builders to use manifest instead of discovery, cache key alignment critical - mitigated by canonical key enforcement in Step 2)

---

### Step 4: Centralized Orderbook Provider

**Goal**: Unified orderbook access point, shared cache across all builders

**Concurrency Strategy**: **Prefetch-then-read-only** (recommended, least risky)
- Collect all required tickers before builder execution
- Call `provider.prefetch(tickers)` in `build_run_context()` (after markets manifest loaded)
- Builders operate in read-only mode (cache hits only)
- No locking needed (no concurrent writes)
- Alternative: Accept occasional duplicate fetches if cache miss occurs (no corruption risk)

**Files to modify**:
- New file: `data_build/orderbook_provider.py`: Define `OrderbookProvider` class (with `prefetch()` method)
- `orchestrator_context.py`: Add `orderbook_provider` field to `RunContext`, create instance in `build_run_context()`, call `prefetch()` after manifest loaded
- `data_build/top_of_book.py`: `get_top_of_book_post_probs()` accepts `orderbook_provider: Optional[OrderbookProvider]`, uses if provided
- `spreads/builder.py`: Remove `_orderbook_cache`, use `context.orderbook_provider.get()` instead of `_fetch_orderbook_with_cache()`
- `totals/builder.py`: Use `context.orderbook_provider.get()` instead of importing `_fetch_orderbook_with_cache()`
- `data_build/orderbook_snapshot.py`: Deprecate (or refactor to use provider)

**Functions to add**:
- `OrderbookProvider.__init__()`
- `OrderbookProvider.get(market_ticker, allow_cache=True) -> Optional[Dict]` (read-only after prefetch)
- `OrderbookProvider.prefetch(tickers: List[str], max_workers: int = 10) -> None` (populates cache)
- `OrderbookProvider.clear_cache() -> None` (for debugging)

**Functions to modify**:
- `build_run_context()`: After manifest loaded, collect all required tickers (moneylines + spreads + totals), call `provider.prefetch(tickers)`
- `get_top_of_book_post_probs()`: Accept `orderbook_provider` parameter, use `provider.get()` instead of direct `fetch_orderbook()`
- `get_spread_orderbook_data()`: Accept `orderbook_provider` parameter, use `provider.get()` instead of `_fetch_orderbook_with_cache()`
- Builders: Pass `context.orderbook_provider` to orderbook-fetching functions

**Migration Invariants** (enforce via assertions/logging):
- ✅ No builder calls `fetch_orderbook()` directly if `context.orderbook_provider` is present
- ✅ Prefetch called before builder execution (cache warm before builders run)
- ✅ Cache hits expected during builder execution (log cache misses as warnings)

**Validation**:
- Verify orderbook fetches deduplicated (same ticker fetched once, reused across builders)
- Verify moneylines and spreads/totals share cache (check cache hits across builders)
- Verify prefetch pattern works (all tickers fetched before builders, cache hits during builder execution)
- Verify no performance regression (cache lookups faster than API calls)

**Risk**: Medium (requires refactoring multiple modules, cache key consistency critical, but prefetch pattern reduces concurrency risk)

---

### Step 5 (Optional): Parallel Prefetch Optimization

**Goal**: Pre-fetch all required orderbooks in single parallel session (like `ad_hoc/kalshi_data_export.py`)

**Files to modify**:
- `orchestrator_context.py`: `build_run_context()` collects all required tickers, pre-fetches in parallel
- `OrderbookProvider`: Add `prefetch(tickers: List[str]) -> None` method

**Functions to add**:
- `OrderbookProvider.prefetch(tickers: List[str], max_workers: int = 10) -> None`

**Functions to modify**:
- `build_run_context()`: After markets manifest loaded, collect all required tickers (moneylines + spreads + totals), call `provider.prefetch()`

**Validation**:
- Verify all orderbooks pre-fetched before builders run (check cache hits = 100% in builders)
- Verify runtime improvement (parallel prefetch faster than sequential builder fetches)
- Verify no rate limiting issues (worker count tuned appropriately)

**Risk**: Low (optional optimization, can be disabled if issues arise)

---

## 7. Risks & Guardrails

### 7.1 Cache Key Mismatches (CRITICAL - Hardest Problem)

**Risk**: Different code paths use different keys for same resource (e.g., `event_ticker` vs `event_start` vs `{away}_{home}`)

**Problem Severity**: **HIGHEST** - This is the single biggest source of bugs in the refactor. If canonical keys aren't enforced at the boundary, manifest/provider lookups will fail silently.

**Current State**:
- Games list keyed by `event_start` (used for Unabated event matching)
- Markets manifest keyed by `event_ticker` (used for Kalshi market lookup)
- Some code paths use derived `{away_code}_{home_code}` as fallback
- No consistent canonical key across all data structures

**Examples of Failures**:
- Market manifest lookup: `manifest.get(event_start)` fails if manifest keyed by `event_ticker`
- Game-to-market join: `games[i]` doesn't match `markets[event_ticker]` if games keyed by `event_start`
- Builders fail silently: Missing markets → empty rows → no error, just missing data

**Guardrails** (MUST be implemented in Step 1):

1. **Canonical key enforcement at boundary** (non-negotiable):
   ```python
   def _enforce_canonical_game_key(games: List[Dict[str, Any]]) -> None:
       """
       Enforce canonical game_key on all games at boundary.
       
       This MUST be called immediately after games are loaded/created,
       before any downstream processing.
       """
       for game in games:
           # Require both keys
           if not game.get("event_ticker"):
               raise ValueError(f"Game missing event_ticker: {game.keys()}")
           if not game.get("event_start"):
               raise ValueError(f"Game missing event_start: {game.keys()}")
           
           # Set canonical key
           game["game_key"] = f"{game['event_ticker']}|{game['event_start']}"
   ```

2. **Validation in build_run_context()** (fail fast):
   - Call `_enforce_canonical_game_key()` immediately after games loaded
   - Assert all games have `game_key` before continuing
   - Fail with clear error message if validation fails

3. **Manifest keying strategy** (dual-key support):
   - Markets manifest stored with BOTH `game_key` and `event_ticker` as keys
   - Lookup tries `game_key` first, falls back to `event_ticker`
   - Logs warning if fallback used (indicates key mismatch)

4. **Assertions in builders** (defensive):
   - Builders assert `game_key` exists before manifest lookup
   - Raise ValueError with context if key missing (don't fail silently)

**Implementation** (must happen in Step 1, not deferred):
```python
def build_run_context(force_refresh_snapshot: bool = False) -> RunContext:
    snapshot = get_cached_snapshot(force_refresh_snapshot)
    games = load_games_list(snapshot)  # From cache or fetch
    
    # CRITICAL: Enforce canonical keys at boundary (fail fast if missing)
    _enforce_canonical_game_key(games)
    
    # Continue with validated games...
```

---

### 7.2 Streamlit Rerun Semantics

**Risk**: Streamlit reruns may clear module-level caches if process restarts

**Scenario**: User interaction triggers rerun → Process restarts → Module-level caches cleared → Full pipeline executes

**Guardrails**:
- **File caches**: Use file-based caches for expensive operations (snapshot, games, markets) - survive process restarts
- **Cache validation**: File caches validate TTL/date before use (fail gracefully if invalid)
- **Fallback behavior**: If file cache invalid, fetch fresh (no silent failures)

**Implementation**: File caches (games, markets) already designed for cross-process persistence

---

### 7.3 Stale Kalshi Markets

**Risk**: Markets manifest cache may contain stale data (new strikes appear, markets close)

**Scenario**: Markets manifest cached for 60s → New strike appears → Cache used → Strike missing from table

**Guardrails**:
- **Short TTL**: Markets manifest cache TTL = 60 seconds (balance freshness vs. performance)
- **Cache validation**: Check file age before use (reject if > 60s)
- **Manual refresh**: Provide `force_refresh` flag for development/debugging
- **Graceful degradation**: If cache invalid, fetch fresh (builders handle missing markets gracefully)

**Implementation**: 60s TTL is short enough for "realtime enough" while reducing API calls significantly

---

### 7.4 Rate Limiting

**Risk**: Parallel prefetch (Step 5) may trigger rate limits if worker count too high

**Scenario**: Prefetch 150 tickers with 10 workers → Kalshi API returns 429 errors

**Guardrails**:
- **Configurable workers**: `OrderbookProvider.prefetch(max_workers=10)` - tune based on rate limits
- **Fail-fast retries**: Minimal retry logic (1-2 attempts with small delay) - don't sleep excessively
- **Error handling**: Record errors, continue with available data (partial cache better than no cache)
- **Metrics**: Log rate limit errors, track cache hit rates

**Implementation**: Start with `max_workers=10` (same as current moneylines builder), reduce if 429s occur

---

### 7.5 Schema Mismatches

**Risk**: Cached data format differs from expected format (games cache, markets manifest)

**Scenario**: Games cache CSV format changes → Load fails → Pipeline breaks

**Guardrails**:
- **Adapter functions**: Convert cached format to expected format at boundary (don't force schema on all code)
- **Validation**: Validate cached data structure before use (check required fields)
- **Fallback**: If cache invalid, fetch fresh (no silent failures)
- **Versioning**: Include version/schema marker in cache files (future-proofing)

**Implementation**: 
- Games cache: Adapter function converts CSV rows to expected dict format
- Markets manifest: Flat dict format (JSON-serializable), restore to `KalshiMarkets` objects on load

---

## 8. Critical Success Factors

### 8.1 Canonical Game Identity Enforcement (Highest Priority)

**Why it matters**: This is the single biggest source of bugs in the refactor. If canonical keys aren't enforced at the boundary, manifest/provider lookups will fail silently (missing data, not errors).

**Success criteria**:
- ✅ All games have `game_key = f"{event_ticker}|{event_start}"` after loading
- ✅ Key enforcement happens at boundary (in `build_run_context()`, immediately after games loaded)
- ✅ Validation fails fast (raises ValueError with context if keys missing)
- ✅ Manifest keyed by both `game_key` and `event_ticker` (dual-key lookup)
- ✅ Builders assert keys exist before lookup (defensive, not silent failures)

**Implementation location**: Step 2 (must not be deferred)

---

### 8.2 Single Concurrency Strategy for Orderbooks

**Why it matters**: Multiple builders access orderbooks concurrently. Without a clear strategy, you risk cache corruption, duplicate fetches, or race conditions.

**Success criteria**:
- ✅ Prefetch-then-read-only pattern implemented (recommended, least risky)
- ✅ All tickers collected before builder execution
- ✅ `provider.prefetch()` called in `build_run_context()` (after manifest loaded)
- ✅ Builders operate in read-only mode (cache hits only, no concurrent writes)
- ✅ Alternative strategy documented if dynamic fetching needed (locking or accept duplicates)

**Implementation location**: Step 4 (prefetch pattern) or Step 5 (optional optimization)

---

## 9. Definition of Done

### 9.1 Quantitative Metrics

**Unabated snapshot fetches per run**: **1** (down from 2)
- Measurement: Count `fetch_unabated_snapshot()` calls in single `build_all_rows()` execution
- Validation: Add logging/counter, verify count = 1

**Kalshi markets discovery calls per run** (with warm cache): **0** (down from 12)
- Measurement: Count `fetch_kalshi_markets_for_event()` calls in single run (after first run with cache)
- **Clarification**: Cache eliminates **repetition** (discover once, reuse). Cold cache still requires N calls (2 × number of games: spreads + totals per game). Warm cache = 0 calls.
- Validation: Verify cache hit on second run, zero discovery calls

**Orderbook fetches per run**: **Unique tickers only** (no duplicates across builders)
- Measurement: Count unique `market_ticker` values passed to `fetch_orderbook()` in single run
- Validation: Verify same ticker never fetched twice (cache hits in providers)

**Total runtime budget target**: **< 10 seconds** (down from ~35-45 seconds)
- Measurement: Time from `build_all_rows()` start to return (with warm caches)
- Breakdown:
  - Unabated snapshot: 0s (cache hit) or ~15s (cache miss)
  - Games list: < 1s (file cache hit) or ~2s (cache miss + computation)
  - Markets discovery: 0s (cache hit) or ~2-5s (cache miss)
  - Orderbook fetches: ~2-6s (parallel, deduplicated)
  - Builders: < 1s (data assembly only, no fetching)

---

### 9.2 Qualitative Criteria

**Backward compatibility**: All existing features preserved
- All tables build correctly (moneylines, spreads, totals)
- All fields present in output (no data loss)
- UI behavior unchanged (same HTML dashboard)

**Code quality**: Clean separation of concerns
- Orchestrator owns data fetching (no builder fetches)
- Builders are pure functions (or near-pure, minimal side effects)
- Caches explicitly managed (no hidden behavior)

**Observability**: Debugging and monitoring possible
- Cache hit/miss rates trackable (logging/metrics)
- Force refresh flags available (development/debugging)
- Error handling graceful (partial data better than no data)

---

### 9.3 Validation Checklist

**Functional**:
- [ ] All three tables build successfully (moneylines, spreads, totals)
- [ ] All table rows contain expected fields (no data loss)
- [ ] HTML dashboard renders correctly (no visual regressions)
- [ ] Streamlit cache still works (30s TTL behavior preserved)

**Performance**:
- [ ] Unabated snapshot fetched once per run (with cache, zero on cache hit)
- [ ] Kalshi markets discovery: 0 calls with warm cache
- [ ] Orderbook fetches: No duplicates (same ticker fetched once)
- [ ] Runtime < 10s with warm caches

**Robustness**:
- [ ] Cache invalidation works (TTL expiration, date changes)
- [ ] Fallback behavior correct (fetch fresh if cache invalid)
- [ ] Error handling graceful (partial failures don't break entire pipeline)
- [ ] Force refresh flags work (development/debugging)

---

**End of Document**
