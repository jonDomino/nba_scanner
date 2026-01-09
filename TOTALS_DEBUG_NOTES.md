# Totals Table Debug Notes

## Symptoms

**Observed behavior:**
- Totals table returns 0 markets for every game
- Event ticker conversion succeeds: `KXNBAGAME-26JAN09TORBOS` → `KXNBATOTAL-26JAN09TORBOS`
- Market fetching succeeds: "Fetched 11 market(s) from KXNBATOTAL-26JAN09TORBOS"
- Strike parsing fails for every market: `⚠️ Could not parse strike from title: Toronto at Boston: Total Points`
- Result: "Found 0 totals market(s)" for every game

**Root failure chain:**
1. Market detection passes (title contains "total" + "points")
2. Strike parsing from title fails (title has no numeric value)
3. Markets are filtered out because `strike is None` (strike is required)
4. Empty list returned → 0 totals markets → no rows in table

## Root Cause Hypotheses (Ranked by Probability)

### Hypothesis 1: Strike is NOT in `market['title']` — it's in structured fields (HIGHEST PROBABILITY)

**Evidence:**
- Title shown in logs: "Toronto at Boston: Total Points" (constant, no number)
- This matches the pattern we see in spread markets where the title is descriptive but the strike lives elsewhere
- Kalshi API typically stores strike information in structured fields for multi-strike products

**Likely locations for strike:**
- `market['subtitle']` — often contains the strike (e.g., "Over 227.5")
- `market['yes_title']` / `market['no_title']` — outcome labels (e.g., "Over 227.5", "Under 227.5")
- `market['product_metadata']` — often includes strike/floor/cap (dict with numeric values)
- `market['strike']` / `market['strike_price']` / `market['floor']` — dedicated strike fields
- `market['rules_primary']` / `market['rules_secondary']` — may contain strike as text

**Fix priority:** HIGHEST — This is almost certainly the issue based on the title pattern.

---

### Hypothesis 2: Strike is encoded in market ticker suffix (HIGH PROBABILITY)

**Evidence:**
- Spreads module successfully parses team code and strike bucket from ticker suffix (e.g., `KXNBASPREAD-26JAN09LACBKN-LAC6` → team=LAC, bucket=6)
- Totals markets likely follow similar pattern: `KXNBATOTAL-26JAN09TORBOS-OVER2275` → direction=OVER, strike=227.5
- Title is generic because strike varies per market; ticker uniquely identifies the strike

**Likely ticker format:**
- Pattern: `KXNBATOTAL-{DATE}{TEAMS}-{DIRECTION}{STRIKE}`
- Example: `KXNBATOTAL-26JAN09TORBOS-OVER2275` → Over 227.5
- Example: `KXNBATOTAL-26JAN09TORBOS-UNDER2225` → Under 222.5
- Or numeric suffix: `KXNBATOTAL-26JAN09TORBOS-2275` → strike=227.5, direction inferred from market type

**Fix priority:** HIGH — Should be implemented alongside Hypothesis 1.

---

### Hypothesis 3: Our "totals market detection" filter is too strict (MEDIUM PROBABILITY)

**Evidence:**
- We check for "over"/"under"/"total" + "points" in title
- Title is "Toronto at Boston: Total Points" (contains "total" + "points") — so detection passes
- But if title format varies (e.g., "Total Points: Over/Under" in subtitle), we might miss markets

**Possible issues:**
- Title doesn't contain "over"/"under" keywords (they might be in subtitle or yes_title/no_title)
- Market type check might not match Kalshi's actual `market_type` values
- We might be filtering valid totals markets before we even try to parse strike

**Fix priority:** MEDIUM — Investigate after fixing strike parsing.

---

### Hypothesis 4: Wrong series ticker or event structure (LOW PROBABILITY)

**Evidence:**
- We successfully fetch 11 markets from `KXNBATOTAL-26JAN09TORBOS`, so series exists
- But these 11 markets might not be different strikes — could be:
  - Hourly/period variants (Q1, Q2, Q3, Q4, Full Game)
  - Alternative products (player props, team totals)
  - All markets might be duplicates or alt representations

**Possible issues:**
- Series ticker might be `KXNBATOTALS` (plural) or `KXNBATOT` or `KXNBATOTALPTS`
- Event might not be structured the same way as spreads (different market count/format)

**Fix priority:** LOW — Only investigate if Hypotheses 1-3 don't resolve it.

---

## Where to Look in Code

### File: `nba_totals_dashboard.py`

**Function: `discover_kalshi_totals_markets(event_ticker: str)`** (Lines 192-332)
- **Location of failure:** Lines 292-305
- **Current logic:**
  1. Line 247: Gets `title_raw` from `market.get("title")`
  2. Lines 254-286: Filters markets by checking title for "over"/"under"/"total" + "points"
  3. Line 294: Parses strike from title using regex: `r'(?:over|under|total)\s+([\d.]+)\s+points?'`
  4. Lines 302-305: **FAILS HERE** — if `strike is None`, logs warning and `continue` (skips market)

**Function: `parse_total_market_ticker(ticker: str)`** (Lines 40-72)
- **Current logic:** Tries to parse direction and strike_bucket from ticker suffix
- **Issue:** Only extracts bucket (e.g., 2275), not exact strike (227.5), and is marked as "optional"

**Function: `select_closest_over_strikes(...)`** (Lines 335-369)
- **Not the problem:** This function works correctly — it's never reached because `discover_kalshi_totals_markets` returns empty list

---

## Mandatory Debug Prints to Add

Add these prints in `discover_kalshi_totals_markets()` function, right after fetching markets and before filtering:

```python
# Add after line 228 (after fetching markets)
if DEBUG_TOTALS and markets:
    print(f"\n{'='*60}")
    print(f"[DEBUG] Event metadata for {total_event_ticker}:")
    # Try to get event object (if available in response)
    # If not available, print what we can infer
    print(f"  Markets fetched: {len(markets)}")
    
    # Print first 2 markets' full structure
    for i, market in enumerate(markets[:2]):
        print(f"\n  [DEBUG] Market {i+1} structure:")
        print(f"    market_ticker: {market.get('ticker') or market.get('market_ticker')}")
        print(f"    market_title: {market.get('title') or market.get('market_title') or market.get('name')}")
        print(f"    market_subtitle: {market.get('subtitle') or market.get('market_subtitle')}")
        print(f"    market_type: {market.get('market_type') or market.get('marketType') or market.get('type')}")
        print(f"    yes_title: {market.get('yes_title') or market.get('yesTitle') or market.get('yes')}")
        print(f"    no_title: {market.get('no_title') or market.get('noTitle') or market.get('no')}")
        print(f"    product_metadata: {market.get('product_metadata') or market.get('productMetadata') or market.get('metadata')}")
        print(f"    strike: {market.get('strike') or market.get('strike_price') or market.get('strikePrice')}")
        print(f"    floor: {market.get('floor')}")
        print(f"    cap: {market.get('cap')}")
        print(f"    Top-level keys: {list(market.keys())[:20]}")  # First 20 keys
```

Also add a print to show all market tickers:

```python
# Add after line 228
if DEBUG_TOTALS and markets:
    print(f"\n  [DEBUG] All market tickers:")
    for i, market in enumerate(markets[:11]):  # Print all 11
        ticker = market.get('ticker') or market.get('market_ticker') or 'N/A'
        title = market.get('title') or market.get('market_title') or 'N/A'
        print(f"    {i+1}. {ticker}")
        print(f"       title: {title[:60]}")
```

---

## Concrete Fix Options

### Fix A: Parse strike from structured fields (PREFERRED)

**Implementation:**
1. **Primary source:** Check `market['subtitle']` first
   - Pattern: "Over 227.5" or "Under 222.5" or "227.5"
   - Regex: `r'(?:over|under)?\s*([\d.]+)'` (more flexible)

2. **Secondary source:** Check `market['yes_title']` / `market['no_title']`
   - YES title likely: "Over 227.5" or "Over 227½"
   - NO title likely: "Under 227.5" or "Under 227½"
   - Parse strike from whichever one exists

3. **Tertiary source:** Check `market['product_metadata']`
   - Usually a dict with keys like `strike`, `floor`, `cap`
   - Extract `strike` if it exists

4. **Fallback:** Check dedicated fields
   - `market['strike']`, `market['strike_price']`, `market['floor']`

5. **Last resort:** Title parsing (current method, but make it more flexible)
   - Don't require "points" keyword — just look for number after "over"/"under"/"total"

**Pros:**
- Uses structured data (more reliable)
- Handles variations in title format
- Follows Kalshi API patterns (similar to how spreads work)

**Cons:**
- Requires understanding Kalshi API response structure
- May need multiple fallbacks if field names vary

**Code location:** Replace lines 292-305 in `discover_kalshi_totals_markets()`

---

### Fix B: Parse strike from market ticker suffix (RECOMMENDED ALONGSIDE FIX A)

**Implementation:**
1. **Enhance `parse_total_market_ticker()`** to extract exact strike from ticker:
   ```python
   # Pattern 1: KXNBATOTAL-26JAN09TORBOS-OVER2275 → (OVER, 227.5)
   # Pattern 2: KXNBATOTAL-26JAN09TORBOS-2275 → (None, 227.5)
   # Pattern 3: KXNBATOTAL-26JAN09TORBOS-OVER227 → (OVER, 227.0)
   ```
2. **Extract numeric suffix and convert to float:**
   - Parse last token after final `-`
   - Extract digits (might be `2275` → `227.5`, or `227` → `227.0`)
   - Determine half-points by checking if last digit is 0 or 5

3. **Use ticker strike as PRIMARY source** (before subtitle/title)

**Pros:**
- Ticker is always present and structured
- No dependency on title/subtitle format
- Similar to spreads parsing logic (proven to work)

**Cons:**
- Must determine ticker format convention (might vary)
- Need to handle edge cases (whole numbers vs half-points)

**Code location:** Update `parse_total_market_ticker()` (lines 40-72) and use it as primary source in `discover_kalshi_totals_markets()`

---

### Fix C: Relax market detection filter (LOWER PRIORITY)

**Implementation:**
1. **Don't filter by title keywords** — instead, filter by:
   - `market_type` if available
   - Event series (if we can confirm we're in `KXNBATOTAL` series)
   - Ticker prefix pattern

2. **Accept all markets from `KXNBATOTAL` event** — then filter later based on whether we can extract a strike

**Pros:**
- Won't miss markets due to title format variations
- Simpler filtering logic

**Cons:**
- Might include non-totals markets (hourly, props, etc.)
- Still need to parse strike somehow

**Code location:** Modify lines 250-287 in `discover_kalshi_totals_markets()`

---

## Recommended Fix Path

**Phase 1: Investigation (Do this first)**
1. Add debug prints (see "Mandatory Debug Prints" section)
2. Run on one game (TORBOS) and capture full output
3. Identify which fields contain the strike value

**Phase 2: Implementation (Priority order)**
1. **Implement Fix B** (ticker parsing) — high confidence this will work based on spreads pattern
2. **Implement Fix A** (structured fields) — backup if ticker parsing insufficient
3. **Implement Fix C** (relax filter) — only if needed after 1-2

**Phase 3: Validation**
1. Verify we extract strikes for all (or most) of 11 markets
2. Confirm we select 2 closest strikes to Unabated total
3. Ensure totals rows appear in dashboard table

---

## Validation Plan

### Step 1: Verify Strike Extraction

**For TORBOS game:**
- **Input:** Unabated total = 227.0 (example)
- **Expected output:**
  ```
  [DEBUG] Market 1 structure:
    market_ticker: KXNBATOTAL-26JAN09TORBOS-OVER2275
    market_subtitle: Over 227.5
    parsed_strike: 227.5  ✅
  [DEBUG] Market 2 structure:
    market_ticker: KXNBATOTAL-26JAN09TORBOS-UNDER2225
    market_subtitle: Under 222.5
    parsed_strike: 222.5  ✅
  ...
  Found 11 totals market(s)  ✅ (was 0 before)
  ```

**Success criteria:**
- At least 8-10 of 11 markets have `parsed_strike` populated
- Strikes are reasonable NBA totals (e.g., 200-250 range)
- No duplicate strikes (unless intentional)

---

### Step 2: Verify Strike Selection

**For TORBOS game with Unabated total = 227.0:**
- **Expected output:**
  ```
  [DEBUG] Canonical POV Selection:
    Unabated total: 227.0
    Canonical POV: Over (all totals markets are Over markets)
    Totals markets found: 11
    Markets with parsed strike: 11
    Selected 2 strike(s) for canonical POV (Over):
      - KXNBATOTAL-26JAN09TORBOS-OVER2275 (strike=227.5, distance=0.5)
      - KXNBATOTAL-26JAN09TORBOS-OVER2285 (strike=228.5, distance=1.5)
  ```

**Success criteria:**
- Exactly 2 strikes selected
- Selected strikes are closest to Unabated total (227.0)
- Both strikes are "Over" markets (canonical POV)

---

### Step 3: Verify Dashboard Output

**Expected console output:**
```
NBA TOTALS DASHBOARD
==================================================================
GameDate      GameTime   ROTO  AwayTeam                    HomeTeam                    Consensus      Strike         OverKalshi    UnderKalshi
------------------------------------------------------------------
2026-01-09    7:00 pm    701   Toronto Raptors             Boston Celtics              227.0         Over 227.5     0.5234        0.4766
2026-01-09    7:00 pm    701   Toronto Raptors             Boston Celtics              227.0         Over 228.5     0.5100        0.4900
```

**Success criteria:**
- Totals table appears after spreads table (or after moneylines if no spreads)
- 2 rows per game (2 selected strikes)
- Over Kalshi and Under Kalshi populated with probabilities
- Consensus column shows Unabated total
- Strike column shows "Over X.Y" format

---

### Step 4: HTML Dashboard Verification

**Expected HTML output:**
- TOTALS section appears with header `<h2>TOTALS</h2>`
- Table structure matches moneylines/spreads (same styling)
- Liquidity bars visible on Over Kalshi and Under Kalshi columns
- Odds toggle button switches Over/Under Kalshi between probabilities and American odds
- Game time formatting and "game-started" highlighting work correctly

---

## Implementation Checklist

- [ ] Add debug prints to `discover_kalshi_totals_markets()` (see "Mandatory Debug Prints")
- [ ] Run on TORBOS game and capture output
- [ ] Identify strike field location (ticker / subtitle / yes_title / metadata)
- [ ] Implement ticker parsing enhancement (`parse_total_market_ticker()`)
- [ ] Implement structured field parsing (subtitle / yes_title / metadata)
- [ ] Update strike parsing logic in `discover_kalshi_totals_markets()` (replace lines 292-305)
- [ ] Remove or relax title-based strike parsing requirement
- [ ] Test on multiple games (at least 3)
- [ ] Verify totals rows appear in console dashboard
- [ ] Verify totals rows appear in HTML dashboard
- [ ] Remove temporary debug prints (or guard behind `DEBUG_TOTALS` flag)

---

## Expected Outcome After Fixes

**Before:**
```
Converting event ticker: KXNBAGAME-26JAN09TORBOS -> KXNBATOTAL-26JAN09TORBOS
Fetched 11 market(s) from KXNBATOTAL-26JAN09TORBOS
⚠️ Could not parse strike from title: Toronto at Boston: Total Points
⚠️ Could not parse strike from title: Toronto at Boston: Total Points
... (repeated for all 11 markets)
Found 0 totals market(s)
```

**After:**
```
Converting event ticker: KXNBAGAME-26JAN09TORBOS -> KXNBATOTAL-26JAN09TORBOS
Fetched 11 market(s) from KXNBATOTAL-26JAN09TORBOS
[DEBUG] Market 1: ticker=KXNBATOTAL-26JAN09TORBOS-OVER2275, subtitle=Over 227.5, parsed_strike=227.5 ✅
[DEBUG] Market 2: ticker=KXNBATOTAL-26JAN09TORBOS-UNDER2225, subtitle=Under 222.5, parsed_strike=222.5 ✅
... (strikes parsed for 11/11 markets)
Found 11 totals market(s)
Selected 2 strike(s) for canonical POV (Over):
  - KXNBATOTAL-26JAN09TORBOS-OVER2275 (strike=227.5)
  - KXNBATOTAL-26JAN09TORBOS-OVER2285 (strike=228.5)
```

---

## Notes

- **Non-negotiable constraint:** Do not modify moneylines or spreads code — all fixes confined to `nba_totals_dashboard.py`
- **Debug flag:** Use `DEBUG_TOTALS = True` for verbose logging during investigation
- **Similarity to spreads:** Spreads module successfully parses strikes from title because spread titles contain numbers (e.g., "Milwaukee wins by over 6.5 Points"). Totals titles are generic, so we must use structured fields or ticker.
