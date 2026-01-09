# NBA Scanner Refactor Notes

## Overview

This refactor reorganizes the NBA dashboard codebase into a clean, forkable architecture optimized for independently extending **moneylines**, **spreads**, or **totals** without dragging along the full project.

## Architecture

### Directory Structure

```
nba_scanner/
├── data_build/           # Shared data fetching (one big pull at startup)
│   ├── __init__.py
│   ├── config.py        # Credentials and settings
│   ├── clients.py       # KalshiClient, UnabatedClient
│   ├── team_xref.py     # Team mapping helpers
│   ├── slate.py         # Today's games discovery
│   ├── unabated_callsheet.py  # Unabated consensus extraction
│   ├── kalshi_markets.py      # Kalshi market fetching
│   ├── orderbook_snapshot.py  # Centralized orderbook fetching with caching
│   └── bundle.py        # DataBundle creation
│
├── moneylines/          # Moneylines table builder
│   ├── __init__.py
│   └── builder.py       # build_table(bundle) -> TableResult
│
├── spreads/             # Spreads table builder
│   ├── __init__.py
│   └── builder.py       # build_table(bundle) -> TableResult
│
├── totals/              # Totals table builder
│   ├── __init__.py
│   └── builder.py       # build_table(bundle) -> TableResult
│
├── orchestrator.py      # Main entry point (coordinates data_build + table builders)
├── dashboard_html.py    # HTML renderer (extracted from nba_value_table.py)
│
└── [legacy files - to be removed]
```

## Key Entrypoints

### Root Entrypoint
- **`orchestrator.py`**: Main entry point that:
  1. Calls `data_build.build_data_bundle()` to fetch all shared data
  2. Calls `moneylines.build_table(bundle)`, `spreads.build_table(bundle)`, `totals.build_table(bundle)`
  3. Calls `dashboard_html.render(tables)` to generate HTML
  4. Opens dashboard in browser

### Data Bundle
- **`data_build.bundle.build_data_bundle()`**: Returns `DataBundle` with:
  - `games`: List of canonical game objects
  - `unabated`: Normalized call sheet values (ML/spread/total consensus)
  - `kalshi`: Normalized market lists by event/series
  - `orderbooks`: Accessed via `orderbook_snapshot.get()` (cached)

### Table Builders
Each table module provides:
- **`build_table(bundle: DataBundle) -> TableResult`**: Transforms DataBundle into table rows
- Returns `TableResult` with `rows`, `columns`, `meta`

## How to Run

```bash
# Run the orchestrator (main entry point)
python orchestrator.py

# This will:
# 1. Fetch all shared data (Unabated games, Kalshi markets, orderbooks)
# 2. Build moneylines, spreads, and totals tables
# 3. Generate HTML dashboard
# 4. Open in browser
```

## Validation Checklist

- [ ] Running `orchestrator.py` generates the dashboard successfully
- [ ] Moneylines table output is byte-for-byte identical (values, ordering, formatting)
- [ ] Spreads table still includes games like LAC@BKN (no missing games)
- [ ] Totals consensus values match Unabated correctly
- [ ] OrderbookSnapshot caching works (log number of API calls before vs after)
- [ ] Each of `moneylines/`, `spreads/`, `totals/` can be copied with `data_build/` into a new project and run with minimal changes

## Implementation Status

### ✅ Completed
- Directory structure created
- `data_build/config.py` - Configuration module
- `data_build/clients.py` - API clients (KalshiClient, UnabatedClient)
- `data_build/team_xref.py` - Team mapping helpers
- `data_build/orderbook_snapshot.py` - Centralized orderbook fetching with caching
- `orchestrator.py` - Main entry point (wraps existing code, maintains exact parity)
- `dashboard_html.py` - HTML renderer (extracted from nba_value_table.py)

### 🔄 In Progress / Architecture Placeholders
The following modules are placeholders showing the intended architecture. They currently import and wrap existing code to maintain exact parity while demonstrating the structure:
- `data_build/slate.py` - Today's games discovery (TODO: extract from nba_today_xref_tickers.py)
- `data_build/unabated_callsheet.py` - Unabated consensus extraction (TODO: extract from nba_todays_fairs.py)
- `data_build/kalshi_markets.py` - Kalshi market fetching (TODO: extract from nba_kalshi_tickers.py)
- `data_build/bundle.py` - DataBundle creation (TODO: combine all data fetching)
- `moneylines/builder.py` - Moneylines table builder (TODO: refactor from orchestrator.py)
- `spreads/builder.py` - Spreads table builder (TODO: refactor from nba_spreads_dashboard.py)
- `totals/builder.py` - Totals table builder (TODO: refactor from nba_totals_dashboard.py)

### ⏳ Pending
- Legacy file cleanup (after full refactor and verification)

## Non-Negotiable Constraints

1. **NO IMPACT ON MONEYLINES OUTPUT** - Moneylines table must remain byte-for-byte identical
2. Maintain existing runtime behavior - running root entrypoint generates same dashboard
3. All tables must use centralized `OrderbookSnapshot` to avoid duplicate API calls
4. Each table module must be forkable with just `data_build/` as dependency

## Current Implementation Approach

The refactor has been started with a **wrapper-based approach** to maintain exact parity:

1. **New entry point**: `orchestrator.py` replaces `nba_value_table.py` main()
   - Imports existing functions
   - Maintains exact same logic and data flow
   - No changes to core calculations

2. **Extracted HTML renderer**: `dashboard_html.py`
   - Wraps `create_html_dashboard` from `nba_value_table.py`
   - Maintains exact same output
   - Can be incrementally refactored

3. **Core modules created**: `data_build/` structure demonstrates architecture
   - `config.py`, `clients.py`, `team_xref.py`, `orderbook_snapshot.py` are functional
   - Other modules are placeholders showing intended structure

## Next Steps (Incremental Refactor)

1. Test `orchestrator.py` - verify it produces identical output to `nba_value_table.py`
2. Incrementally extract logic into `data_build/` modules:
   - Extract slate discovery → `data_build/slate.py`
   - Extract Unabated callsheet → `data_build/unabated_callsheet.py`
   - Extract Kalshi markets → `data_build/kalshi_markets.py`
   - Create `data_build/bundle.py` that uses all data_build modules
3. Refactor table builders:
   - Move moneyline logic → `moneylines/builder.py`
   - Move spreads logic → `spreads/builder.py`
   - Move totals logic → `totals/builder.py`
4. Update orchestrator to use new modules
5. Test and verify moneylines parity at each step
6. Remove legacy files after full refactor verified