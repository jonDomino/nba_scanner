# NBA Totals Table Implementation Plan

## Overview
Implement a Totals table following the same architectural pattern as the Spreads table:
- **Canonical POV per game**: One perspective (Over), multiple strikes (2 rows per game)
- **Ticker parsing**: Primary source for strike extraction (if applicable)
- **Title parsing**: Extract strike values from market titles
- **Same-market plumbing**: Both Over and Under Kalshi values come from the same "Over X.Y" market (YES/NO sides)

## Key Similarities to Spreads
- Same canonical POV approach: One Over perspective per game, Under shown as NO side
- Same ticker parsing strategy: Extract strike from ticker suffix if possible, fallback to title
- Same orderbook extraction: Maker bid prices (YES bid top, NO bid top from same market)
- Same row structure: 2 rows per game (2 closest strikes), duplicated game metadata

## Key Differences from Spreads
- **Perspective:** Over/Under (instead of Away/Home)
- **Unabated Data:** Total points consensus value (single number, e.g., 221.5)
- **Kalshi Markets:** Totals markets (likely in `KXNBATOTAL` series)
- **Canonical POV:** Always "Over" perspective (all totals markets are "Over X.Y" markets)
- **Strike Selection:** 2 closest "Over" strikes to Unabated total

## The Golden Rule for Totals
> For totals, treat Kalshi markets as "Over X.Y" markets; Under exposure is always the NO side of the Over market.

This means:
- All totals markets are "Over X.Y" markets (e.g., "Over 221.5 points")
- Over exposure = YES bid on Over market
- Under exposure = NO bid on Over market (same market)
- One canonical POV = Over perspective
- Multiple strikes = multiple Over markets, each showing Over (YES) and Under (NO) sides

## Implementation Steps

### Step 1: Extract Unabated Totals Consensus
**Module:** `nba_totals_dashboard.py` (new module, parallel to spreads)

**Function:** `extract_unabated_totals(event: Dict[str, Any], teams: Dict[str, Any]) -> Optional[float]`

**Logic:**
- Similar to `extract_unabated_spreads()` structure
- Look for totals bet type in `gameOddsMarketSourcesLines` ms49 blocks
- Likely `bt3` or similar (investigate actual Unabated structure)
- Extract the total value (e.g., 221.5) as a single float
- This represents the consensus total points line

**Return:**
- Single float value (the total line) or None if not found

**Debug:**
- Print ms49 block keys to identify which `btX` contains totals
- Print extracted total value for each game

### Step 2: Discover Kalshi Totals Markets
**Function:** `discover_kalshi_totals_markets(event_ticker: str) -> List[Dict[str, Any]]`

**Logic:**
- **Series Conversion:** Convert `KXNBAGAME` event ticker → `KXNBATOTAL` event ticker
  - Example: `KXNBAGAME-26JAN09MILLAL` → `KXNBATOTAL-26JAN09MILLAL`
- **Fetch Markets:** Use `fetch_kalshi_markets_for_event()` for `KXNBATOTAL` event
- **Filter Markets:** Check title patterns:
  - "Over" + number + "points" (e.g., "Over 221.5 points")
  - All totals markets should be "Over X.Y" markets (Under is represented as NO side)

**Parsing Strategy (same as spreads):**
1. **PRIMARY - Ticker Parsing (if applicable):**
   - Parse strike bucket from ticker suffix (e.g., `KXNBATOTAL-26JAN09MILLAL-OVER2215` → strike bucket)
   - Note: May not be reliable, so this is optional if ticker format doesn't contain strike

2. **STRIKE - Title Parsing (always):**
   - Extract strike from title using regex: `r'over\s+([\d.]+)\s+points?'`
   - This is the source of truth for strike value
   - Always parse strike from title, regardless of ticker parsing success

3. **FALLBACK:** If ticker parsing fails and title parsing fails, log warning but keep market (with strike=None, will be filtered later)

**Important:** 
- **Do not skip markets** if title parsing fails - keep them with strike=None
- Only skip if strike cannot be parsed from title (strike is required)
- All markets should be "Over X.Y" format (verify this assumption)

**Return:**
- List of market dicts, each with:
  - `ticker`: market ticker
  - `title`: market title
  - `parsed_strike`: float strike value (e.g., 221.5) - **required**
  - `strike_bucket`: optional bucket from ticker (for debug, not used for strike value)

**Debug:**
- Print count of totals markets found
- Print first few markets with parsed strikes
- Show ticker parsing results (if applicable)

### Step 3: Select Closest Strikes (Canonical POV = Over)
**Function:** `select_closest_over_strikes(canonical_total: float, available_markets: List[Dict], count: int = 2) -> List[Dict]`

**Logic:**
- Similar to `select_closest_strikes_for_team_spread()` but simpler
- All markets are "Over X.Y" markets (canonical POV = Over)
- Calculate distance: `abs(strike - canonical_total)` for each market
- Sort by distance (closest first), then by strike (lower first for tie-break)
- Select top N markets (default 2)

**Note:** 
- All selected strikes will be "Over" strikes
- Under exposure is shown via NO side of each Over market

**Return:**
- List of selected market dicts (up to count=2)

**Debug:**
- Print canonical total from Unabated
- Print all candidate strikes with distances
- Print selected 2 strikes and why

### Step 4: Build Totals Rows (Canonical POV = Over Only)
**Function:** `build_totals_rows_for_today() -> List[Dict[str, Any]]`

**Logic:**
- **Reuse game metadata:** Use `get_today_games_with_fairs_and_kalshi_tickers()` to get games
- **For each game:**
  1. Get Unabated event and extract total consensus value
  2. Get event_ticker from game
  3. Discover Kalshi totals markets for that event
  4. **Canonical POV:** Always "Over" (all markets are Over markets)
  5. **Select 2 closest Over strikes** using `select_closest_over_strikes()`
  6. **For each selected strike:**
     - Fetch orderbook for that Over market
     - Get YES bid data (Over exposure) using `get_spread_orderbook_data(market_ticker, "YES")`
     - Get NO bid data (Under exposure) using `get_spread_orderbook_data(market_ticker, "NO")`
     - Build row with:
       - All game metadata (duplicated per strike)
       - `strike`: Formatted as "Over 221.5"
       - `consensus`: Formatted as "221.5" (from Unabated)
       - `over_kalshi_prob`: YES bid break-even prob (after fees)
       - `over_kalshi_liq`: YES bid liquidity
       - `under_kalshi_prob`: NO bid break-even prob (after fees)
       - `under_kalshi_liq`: NO bid liquidity
       - `over_fair`: Placeholder "N/A" for now (Unabated totals probability)
       - `under_fair`: Placeholder "N/A" for now
       - `over_ev`: Placeholder "N/A" for now
       - `under_ev`: Placeholder "N/A" for now

**Important:**
- **Only build rows for Over perspective** (canonical POV)
- **Under exposure shown via NO side** (same market as Over)
- **2 rows per game** (2 closest strikes)
- **No duplicate rows** from Under perspective (similar to spreads fix)

**Return:**
- List of totals row dicts (2 rows per game, one per selected Over strike)

### Step 5: Format Strike and Consensus Strings
**Function:** `format_total_strike_string(strike: float) -> str`

**Logic:**
- Format as "Over 221.5" (canonical POV is always Over)
- Show strike to 1 decimal place

**Function:** `format_total_consensus_string(total: float, juice: Optional[int] = None) -> str`

**Logic:**
- Format as "221.5" or "221.5 -110" (if juice available)
- Similar to spreads consensus formatting

### Step 6: Integrate into Dashboard
**Module:** `nba_value_table.py`

**Changes:**
1. **HTML Dashboard:**
   - Add totals table after spreads table (or after moneylines if no spreads)
   - Add `<h2>TOTALS</h2>` header
   - **Column structure:**
     - Game Date | Game Time | ROTO | Away Team | Home Team | Consensus | Strike | Over Fair | Under Fair | Over Kalshi | Under Kalshi | Over EV | Under EV
   - **Styling:** Match spreads table styling (same headers, colors, fonts)
   - **Liquidity bars:**
     - Over Kalshi column: Horizontal red-to-green gradient bar showing `over_kalshi_liq`
     - Under Kalshi column: Horizontal red-to-green gradient bar showing `under_kalshi_liq`
     - Hover tooltip: Show liquidity rounded to Ks (thousands)
   - **Odds toggle:** Both Over Kalshi and Under Kalshi switch between probabilities and American odds
   - **Placeholder values:**
     - Over Fair / Under Fair: Show "N/A" for now
     - Over EV / Under EV: Show "N/A" for now
   - **Only show TOB** (not +1c) - same as spreads

2. **Console Output:**
   - Add `print_totals_table()` function similar to `print_spreads_table()`
   - Print after spreads table (or after moneylines if no spreads)
   - Same column structure as HTML (without liquidity bars)

3. **Main Function:**
   - Fetch totals rows using `build_totals_rows_for_today()`
   - Pass to `create_html_dashboard()` as `totals_rows` parameter
   - Print console totals table after spreads

### Step 7: Orderbook Logic (Reuse from Spreads)
**Function:** `get_total_orderbook_data(market_ticker: str, side_to_trade: str = "YES") -> Dict[str, Any]`

**Logic:**
- **Reuse `get_spread_orderbook_data()`** - same logic works for totals
- `side_to_trade = "YES"` for Over exposure
- `side_to_trade = "NO"` for Under exposure
- Returns: `tob_bid_cents`, `tob_effective_prob`, `tob_liq`, etc.

**Note:** Totals markets work identically to spreads - YES bid = maker price to join queue for Over, NO bid = maker price for Under

### Step 8: Enhanced Debug Logging
Add behind `DEBUG_TOTALS = True` flag (similar to spreads):

**For each game:**
```
[DEBUG] Game: MIL @ LAL
  Unabated total: 221.5
  Canonical POV: Over (all totals markets are Over markets)
  Totals markets found: 10
  Markets with parsed strike: 10
  Selected 2 strike(s) for canonical POV (Over):
    - KXNBATOTAL-26JAN09MILLAL-OVER2215 (strike=221.5)
    - KXNBATOTAL-26JAN09MILLAL-OVER2225 (strike=222.5)
```

**For problematic games:**
- Show canonical total
- Show market count
- Show parsed strikes
- Show selection results
- If selection returns 0 strikes, explain why (e.g., "ZERO markets with parsed strike")

## Data Structure Per Row

```python
{
    # Game metadata (duplicated per strike)
    "game_date": "2026-01-09",
    "event_start": "2026-01-10T03:00:00Z",
    "away_roto": 701,
    "away_team": "Milwaukee Bucks",
    "home_team": "Los Angeles Lakers",
    
    # Totals-specific
    "strike": "Over 221.5",  # Always "Over X.Y" format
    "consensus": "221.5",  # or "221.5 -110" if juice available
    "kalshi_ticker": "KXNBATOTAL-26JAN09MILLAL-OVER2215",
    "kalshi_title": "Over 221.5 points scored?",
    "unabated_total": 221.5,  # Original Unabated consensus total
    
    # Pricing (both sides from same market)
    "over_kalshi_prob": 0.5234,  # YES bid break-even prob (Over exposure)
    "over_kalshi_liq": 5000,  # YES bid liquidity
    "under_kalshi_prob": 0.4766,  # NO bid break-even prob (Under exposure)
    "under_kalshi_liq": 3500,  # NO bid liquidity
    
    # Placeholders (for future implementation)
    "over_fair": None,  # Probability total goes over strike (from Unabated)
    "under_fair": None,  # Probability total goes under strike (from Unabated)
    "over_ev": None,  # EV for betting Over
    "under_ev": None,  # EV for betting Under
}
```

## Column Display Logic

### Consensus Column:
- Shows Unabated consensus total (e.g., "221.5" or "221.5 -110")
- Similar to spreads Consensus column

### Strike Column:
- Always shows "Over X.Y" format (canonical POV is Over)

### Over Kalshi Column:
- Always populated (canonical POV is Over)
- Shows `over_kalshi_prob` (YES bid break-even prob)
- Liquidity bar showing `over_kalshi_liq`
- Hover tooltip: "Over liquidity: XK" (rounded to thousands)

### Under Kalshi Column:
- Always populated (from NO side of same Over market)
- Shows `under_kalshi_prob` (NO bid break-even prob)
- Liquidity bar showing `under_kalshi_liq`
- Hover tooltip: "Under liquidity: XK" (rounded to thousands)

### Over/Under Fair Columns:
- Show "N/A" as placeholder until Unabated totals probability calculation is implemented
- Future: Extract probability that total goes over/under strike from Unabated

### Over/Under EV Columns:
- Show "N/A" as placeholder until EV calculation is implemented
- Future: `over_ev = (over_fair - over_kalshi_prob) * 100` if betting Over
- Future: `under_ev = (under_fair - under_kalshi_prob) * 100` if betting Under

## Implementation Priority

### Priority 1: Basic Totals Table (No EV, Placeholder Fairs)
1. Extract Unabated totals consensus (single float value)
2. Discover Kalshi totals markets (parse strike from title)
3. Select 2 closest Over strikes
4. Build rows with Over/Under Kalshi from same market (YES/NO sides)
5. Display in dashboard (with "N/A" for Fair and EV columns)

### Priority 2: Enhanced Parsing (if needed)
1. Implement ticker parsing for totals markets (if ticker format contains strike)
2. Add fallback logic if title parsing fails

### Priority 3: Unabated Totals Probabilities (Future)
1. Extract probability that total goes over/under strike from Unabated
2. Populate Over Fair and Under Fair columns
3. Calculate and display EV columns

## Non-Negotiables

1. **Do not skip a game because title parsing fails.**
   - Even if title parsing fails for some markets, use ticker parsing (if applicable)
   - Only skip if genuinely no markets match canonical total (with logging)

2. **Strike must come from title regex, not ticker suffix digits.**
   - Parse strike from title using regex: `r'over\s+([\d.]+)\s+points?'`
   - Ticker suffix (if exists) is a bucket/index, not exact strike

3. **Totals table must output one canonical POV per game and two strikes (2 rows per game).**
   - One consensus line (Unabated total)
   - Two strikes (2 closest Over strikes)
   - Under exposure via NO side (shown in Under Kalshi column)

4. **Both Over and Under Kalshi come from same market (YES and NO sides).**
   - Over Kalshi = YES bid on Over market
   - Under Kalshi = NO bid on same Over market

5. **Moneylines and Spreads tables must remain untouched.**
   - All fixes confined to `nba_totals_dashboard.py` (new module)
   - No changes to existing code paths

## Potential Issues to Investigate

1. **Series Ticker Verification:**
   - Confirm `KXNBATOTAL` is the correct series (investigate similar to spreads)
   - Verify event ticker format matches `KXNBAGAME` pattern

2. **Unabated Totals Location:**
   - May be `bt3`, `bt4`, or different key
   - May require parsing different structure than spreads
   - May need to extract juice if available

3. **Market Title Parsing:**
   - Titles might be "Over 221.5 points" or "Total points: Over 221.5"
   - Need robust regex to extract strike
   - Verify all markets are "Over X.Y" format (not "Under X.Y" markets)

4. **Ticker Format (if applicable):**
   - May be `KXNBATOTAL-26JAN09MILLAL-OVER2215` or similar
   - Strike bucket might be embedded in ticker
   - Do not use ticker bucket for strike value (use title only)

## Dependencies

### Reuse Existing Functions (No Modifications Needed):
- `fetch_kalshi_markets_for_event()` - already handles any event ticker
- `fetch_orderbook()` - already handles any market ticker
- `get_yes_bid_top_and_liquidity()` - already handles any orderbook
- `get_no_bid_top_and_liquidity()` - already handles any orderbook
- `yes_break_even_prob()` - already handles any YES/NO price
- `get_spread_orderbook_data()` - **reuse this function** for totals (same logic)
- `get_today_games_with_fairs_and_kalshi_tickers()` - already provides game metadata
- `format_game_time_pst()`, `is_game_started()` - already available

### New Functions to Create (in `nba_totals_dashboard.py`):
- `extract_unabated_totals()` - extract total consensus from Unabated
- `discover_kalshi_totals_markets()` - discover totals markets from Kalshi (ticker + title parsing)
- `select_closest_over_strikes()` - select 2 closest Over strikes to canonical total
- `format_total_strike_string()` - format strike display ("Over X.Y")
- `format_total_consensus_string()` - format consensus display ("221.5" or "221.5 -110")
- `build_totals_rows_for_today()` - main function to build all rows (canonical POV = Over only)
- `print_totals_table()` - console output function

## Zero Impact Guarantee

- All existing moneylines and spreads code remains untouched
- Totals module is completely separate (`nba_totals_dashboard.py`)
- Only additive changes to `nba_value_table.py` main() function:
  - Call `build_totals_rows_for_today()`
  - Pass `totals_rows` to `create_html_dashboard()`
  - Call `print_totals_table()`
- Dashboard HTML generation extended but doesn't modify existing table generation
- JavaScript odds toggle extended to handle totals table (same logic as spreads)

## Validation Plan

### After Implementation:

**Expected Output for a Game:**
```
Game Date | Game Time | ROTO | Away | Home | Consensus | Strike      | Over Fair | Under Fair | Over Kalshi | Under Kalshi | Over EV | Under EV
2026-01-09| 7:00 pm   | 701  | MIL  | LAL  | 221.5     | Over 221.5  | N/A       | N/A        | 0.5234      | 0.4766       | N/A     | N/A
2026-01-09| 7:00 pm   | 701  | MIL  | LAL  | 221.5     | Over 222.5  | N/A       | N/A        | 0.5100      | 0.4900       | N/A     | N/A
(2 rows per game, both showing Over strikes)
```

**Validation Criteria:**
- ✅ All games appear in totals table (no missing games due to parsing failures)
- ✅ One consensus line per game (Unabated total)
- ✅ Two strikes per game (2 closest Over strikes)
- ✅ Both Over Kalshi and Under Kalshi populated (from same market, YES/NO sides)
- ✅ No duplicate rows (only Over perspective, not Under perspective)
- ✅ Liquidity bars shown for both Over and Under Kalshi columns
- ✅ Odds toggle works for totals table
- ✅ Moneylines and spreads tables remain unchanged
