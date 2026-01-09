# NBA Spreads Dashboard Debug Notes

## Symptoms

### Issue A: Games Missing from Spreads Table
- **Example**: LAC @ BKN game disappears from spreads table
- **Observed behavior**: System shows "selected 0 strikes" for certain games
- **Pattern**: More likely to occur when:
  - Home team is underdog
  - Market titles use abbreviated team names (e.g., "Los Angeles C" instead of "LA Clippers")
  - Title-to-team-code matching fails

### Issue B: Duplicate Consensus POV Rows
- **Example**: Both "PHI -3.5" and "ORL +3.5" appear as separate rows for the same game
- **Expected**: One canonical consensus POV per game (favorite's spread), with multiple strikes listed under that POV
- **Current**: We build rows for both away and home spreads separately, creating duplicates

---

## Root Cause Analysis

### Issue A: Why Games Are Dropped (LACBKN Example)

#### Evidence from Debug Logs
```
We fetched KXNBASPREAD-26JAN09LACBKN and got 10 markets.

Warning: Could not determine market_team_code from title: Los Angeles C wins by over 6.5 Points? (anchor_token: los angeles c)
```

#### Exact Failure Chain
1. **Market Discovery**: `discover_kalshi_spread_markets()` successfully fetches 10 spread markets for event
2. **Title Parsing Attempt**: `discover_kalshi_spread_markets()` attempts to extract team code from market title using `_build_team_name_variations()` and `team_to_kalshi_code()`
3. **Parsing Failure**: Title "Los Angeles C wins by over 6.5 Points" fails to map to team code because:
   - `anchor_team_token = "los angeles c"`
   - Team xref contains "LA Clippers" but not "Los Angeles C"
   - Name variation matching fails
   - **Result**: `market_team_code = None` for all "Los Angeles C ..." markets
4. **Market Filtering**: Markets with `market_team_code = None` are either:
   - Silently dropped, OR
   - Kept but cannot be matched to canonical team in selection step
5. **Selection Failure for LAC**: When canonical POV is LAC (away team, favorite):
   - `select_closest_strikes_for_team_spread()` filters: `[m for m in spread_markets if m.get("market_team_code") == away_code]`
   - All LAC markets have `market_team_code = None` (parsing failed)
   - **Result**: `candidate_markets = []`, function returns `[]`
   - Debug output: "Away team (LAC): selected 0 strike(s)"
6. **Selection Failure for BKN**: Similar issue if BKN markets also fail parsing OR if canonical POV logic tries BKN
   - Debug output: "Home team (BKN): selected 0 strike(s)"
7. **Game Skipped**: Both away and home selections return 0 strikes → game produces 0 spread rows → disappears from table

**Key Insight**: The issue isn't strictly "home underdog gets removed"—it's that **games where canonical POV team markets are unparseable get removed**. This correlates with "home underdog" sometimes because:
- If canonical favorite is the away team (LAC), and LAC market titles use "Los Angeles C", parsing fails
- But it could also happen to home favorites if their titles are abbreviated oddly

#### Affected Functions
- `discover_kalshi_spread_markets()` (lines ~148-259): Parses titles to extract `market_team_code`
- `_build_team_name_variations()` (lines ~338-361): Builds name variations but doesn't handle "Los Angeles C" → LAC
- `select_closest_strikes_for_team_spread()` (lines ~404-459): Filters markets by `market_team_code`, returns empty if no matches

#### Hypothesis 1 (Most Likely): Title-Based Parsing is Fragile
**Why it fails:**
- Kalshi market titles use various abbreviations: "Los Angeles C", "LA C", "Lakers", etc.
- Our `team_xref_nba.csv` contains canonical names like "LA Clippers"
- Title matching requires exhaustive aliases that are hard to maintain
- One parsing failure can drop half the available markets for a game
- **Current behavior**: Markets with `market_team_code = None` are effectively filtered out, causing selection to return 0 strikes

**Fix Direction:**
**Parse team code from market ticker, not title. Title parsing must NOT be a hard gate.**

**Source of Truth Hierarchy:**
1. **Primary**: Parse `team_code` from market ticker suffix (e.g., `-LAC6` → `LAC`)
2. **Secondary**: Parse `strike` value from title regex (e.g., "over 6.5 points" → `6.5`)
3. **Fallback**: If ticker parsing fails, try regex matching on ticker suffix pattern: `-LAC\d+` → `LAC`

**Important**: The ticker suffix digit (e.g., `6` in `LAC6`) is NOT the exact strike value. Kalshi uses discrete strike sets (3.5, 6.5, 9.5, 12.5, 15.5, 18.5, ...), and the ticker suffix may be an index into this ladder, not a direct mapping. Always extract the strike value from the title using regex.

**Ticker Format:**
- Pattern: `{series}-{date}{matchup}-{team_code}{strike_bucket}`
- Example: `KXNBASPREAD-26JAN09LACBKN-LAC6`
- Extract team code: `LAC6` → team_code = `LAC` (3 uppercase letters)
- Strike bucket: `6` (may map to 6.0, 6.5, or other values - do not use this for strike value)

**Ticker parsing benefits:**
- ✅ More reliable (ticker format is standardized)
- ✅ Doesn't depend on title variations
- ✅ Already contains canonical team codes
- ✅ Cannot fail due to title abbreviations

**Implementation:**
```python
def parse_spread_market_ticker(ticker: str) -> Tuple[Optional[str], Optional[int]]:
    """
    Parse team code and strike bucket from spread market ticker.
    
    IMPORTANT: Returns strike_bucket (e.g., 6), NOT exact strike value.
    Strike value must be parsed from title separately.
    
    Example: KXNBASPREAD-26JAN09LACBKN-LAC6 → (LAC, 6)
    """
    parts = ticker.split("-")
    if len(parts) < 3:
        return (None, None)
    
    suffix = parts[-1]  # e.g., "LAC6"
    
    # Extract team code (3 letters) and strike bucket (remaining digits)
    match = re.match(r'^([A-Z]{3})(\d+)$', suffix)
    if match:
        team_code = match.group(1)
        strike_bucket = int(match.group(2))
        return (team_code, strike_bucket)
    
    return (None, None)
```

**Must Not Skip Game Rule:**
Even if title parsing fails for some markets, we must still produce rows using the markets we can parse. For example:
- If canonical POV is LAC -4.5, we need LAC markets
- If LAC market title parsing fails but ticker parsing works, we can still proceed
- If ticker parsing also fails, try regex fallback: match ticker pattern `-LAC\d+` to extract team code
- Only skip the game if we genuinely cannot find any markets matching the canonical team code

#### Hypothesis 2: Overly Aggressive Filtering
We filter out markets where `market_team_code` is `None` before selection, which reduces our candidate pool.

**Fix Direction:**
- Keep all markets initially, mark `market_team_code=None` when ticker parsing fails
- Use ticker parsing as primary source (should rarely be None)
- At selection step, log exactly which markets are eligible and why others aren't:
  ```python
  canonical_team_code = away_code if canonical_team == "away" else home_code
  candidate_count = len([m for m in spread_markets if m.get("market_team_code") == canonical_team_code])
  print(f"  Canonical team: {canonical_team_code}")
  print(f"  Markets with market_team_code=={canonical_team_code}: {candidate_count}")
  if candidate_count == 0:
      print(f"  ⚠️ ZERO markets for canonical team - this is why game disappears")
  ```
- Only filter at selection time with clear diagnostics

#### Hypothesis 3: Team Name Alias Mismatch
"Los Angeles C" should map to LAC, but our aliases don't include this variation.

**Fix Direction:**
- Add explicit aliases: `{"Los Angeles C": "LAC", "LA C": "LAC", ...}`
- **But**: Ticker parsing is cleaner and eliminates the need for title aliases entirely

**Recommended Priority:**
1. ✅ **Implement ticker parsing** (Hypothesis 1) - most reliable
2. Add explicit title aliases as fallback (Hypothesis 3)
3. Improve filtering diagnostics (Hypothesis 2)

---

### Issue B: Why Duplicate Rows Appear

#### Evidence
Current output shows:
```
Game Date | Away | Home | Consensus | Strike      | AwayKalshi | HomeKalshi
2025-01-09| PHI  | ORL  | PHI -3 -106| PHI -3.5    | ...        | ...
2025-01-09| PHI  | ORL  | PHI -3 -106| PHI -4.5    | ...        | ...
2025-01-09| PHI  | ORL  | ORL +3 +106| ORL +3.5    | ...        | ...  ← DUPLICATE
2025-01-09| PHI  | ORL  | ORL +3 +106| ORL +4.5    | ...        | ...  ← DUPLICATE
```

**Expected:**
```
Game Date | Away | Home | Consensus | Strike      | AwayKalshi | HomeKalshi
2025-01-09| PHI  | ORL  | PHI -3 -106| PHI -3.5    | ...        | ...
2025-01-09| PHI  | ORL  | PHI -3 -106| PHI -4.5    | ...        | ...
(Only one consensus POV per game, multiple strikes listed)
```

#### Failure Chain
1. **Build Loop**: `build_spreads_rows_for_today()` processes both away and home spreads separately
2. **Away Processing** (lines ~824-916):
   - Selects 2 closest strikes for away team spread
   - Appends rows with away team's perspective (e.g., "PHI -3.5")
3. **Home Processing** (lines ~918-1010):
   - Selects 2 closest strikes for home team spread
   - Appends rows with home team's perspective (e.g., "ORL +3.5")
4. **Result**: Duplicate rows with same strikes shown from both perspectives

#### Affected Code
- `build_spreads_rows_for_today()` lines ~819-1010: Processes both `away_spread` and `home_spread` separately, appending rows for each

#### Hypothesis (Most Likely): Dual Team Processing
We're treating away and home spreads as separate entities that need separate rows, when in fact:
- Spread markets are **favorite-margin markets** (e.g., "PHI wins by over 3.5")
- The **underdog spread** is the **NO side** of the favorite's market
- We should have **one canonical POV per game** (the favorite's spread)
- Underdog exposure is expressed through side selection (YES vs NO), not duplicate rows

**The Golden Rule:**
> For spreads, treat Kalshi markets as "favorite margin > X" markets; underdog +X is always the NO side of the favorite's market.

**Canonical POV Definition (Unambiguous):**
The "canonical POV" for the spreads table means:
1. **Base Market Set**: The team whose Kalshi spread markets we will use as the base market set
   - Example: If canonical POV is PHI, we use PHI's spread markets (e.g., "PHI wins by over 3.5")
2. **Opponent Value Derivation**: The opponent's spread value is derived as the **NO side** of that same market
   - Example: ORL +3.5 is represented as NO on "PHI wins by over 3.5" market
3. **Table Representation**: One canonical POV per game, multiple strikes (multiple rows with duplicated game metadata)
   - Each row shows: consensus (canonical team's spread), strike (canonical team's strike), AwayKalshi, HomeKalshi
   - Underdog exposure is shown in the opponent's column (NO side of favorite's market)

This keeps the system consistent with the **NO-space plumbing philosophy**: we use one market per strike, read both YES and NO sides, and assign to away/home based on which team is canonical POV.

**Important**: This does NOT prevent betting the underdog. The underdog exposure is expressed via which side we trade on the favorite-margin market (YES vs NO). That's internal plumbing, not a second set of table rows.

**Fix Direction:**

1. **Select Canonical POV Team:**
   ```python
   # Determine canonical team and spread
   if away_spread is not None and away_spread < 0:
       canonical_team = "away"
       canonical_code = away_code
       canonical_spread = away_spread
       canonical_juice = away_juice
   elif home_spread is not None and home_spread < 0:
       canonical_team = "home"
       canonical_code = home_code
       canonical_spread = home_spread
       canonical_juice = home_juice
   elif away_spread is not None:
       canonical_team = "away"
       canonical_code = away_code
       canonical_spread = away_spread
       canonical_juice = away_juice
   elif home_spread is not None:
       canonical_team = "home"
       canonical_code = home_code
       canonical_spread = home_spread
       canonical_juice = home_juice
   else:
       # Skip game - no consensus spread
       continue
   ```

2. **Select Strikes for Canonical POV Only:**
   ```python
   opponent_code = home_code if canonical_team == "away" else away_code
   
   selected_strikes = select_closest_strikes_for_team_spread(
       canonical_spread,
       canonical_code,
       opponent_code,
       spread_markets,
       count=2
   )
   ```

3. **Build Rows Only for Canonical POV:**
   ```python
   for market, side_to_trade in selected_strikes:
       # Determine market and side for canonical team
       if canonical_spread < 0:
           # Canonical team is favorite: use canonical team's market, trade YES
           # ...
           side_canonical = "YES"
           side_opponent = "NO"
       else:
           # Canonical team is underdog: use opponent's market, trade NO
           # ...
           side_canonical = "NO"
           side_opponent = "YES"
       
       # Get orderbook data for both sides (same market)
       canonical_orderbook = get_spread_orderbook_data(market_ticker, side_canonical)
       opponent_orderbook = get_spread_orderbook_data(market_ticker, side_opponent)
       
       # Assign to away/home based on canonical_team
       if canonical_team == "away":
           away_kalshi_prob = canonical_orderbook.get("tob_effective_prob")
           home_kalshi_prob = opponent_orderbook.get("tob_effective_prob")
       else:
           away_kalshi_prob = opponent_orderbook.get("tob_effective_prob")
           home_kalshi_prob = canonical_orderbook.get("tob_effective_prob")
       
       # Build ONE row per strike (not one per team)
       spread_rows.append({...})
   ```

4. **Remove Separate Away/Home Processing Loops:**
   - Delete the separate away team processing loop (lines ~824-916)
   - Delete the separate home team processing loop (lines ~918-1010)
   - Replace with single canonical POV loop above

---

## Proposed Fixes (Prioritized)

### Priority 1: Parse Team Code from Market Ticker (Issue A)

**Function to Modify:**
- `discover_kalshi_spread_markets()` (lines ~148-259)

**Implementation Steps:**
1. Add `parse_spread_market_ticker()` function:
   ```python
   def parse_spread_market_ticker(ticker: str) -> Tuple[Optional[str], Optional[int]]:
       """
       Parse team code and strike bucket from spread market ticker.
       
       Example: KXNBASPREAD-26JAN09LACBKN-LAC6 → (LAC, 6)
       """
       parts = ticker.split("-")
       if len(parts) < 2:
           return (None, None)
       
       suffix = parts[-1]  # e.g., "LAC6"
       
       # Extract team code (3 letters) and strike bucket (remaining digits)
       match = re.match(r'^([A-Z]{3})(\d+)$', suffix)
       if match:
           team_code = match.group(1)
           strike_bucket = int(match.group(2))
           return (team_code, strike_bucket)
       
       return (None, None)
   ```

2. Update `discover_kalshi_spread_markets()`:
   - **Primary**: Parse `market_team_code` from ticker using `parse_spread_market_ticker()`
   - **Strike**: Parse `parsed_strike` from title using regex (always, regardless of ticker parsing success)
   - **Fallback**: If ticker parsing fails, try regex fallback on ticker suffix: match pattern `-{TEAM_CODE}\d+` where TEAM_CODE is one of away_code or home_code
   - **Must Not Skip**: Do not drop markets even if ticker parsing fails - keep them with `market_team_code=None` and log warning
   - Log warnings when fallback is used, but do not skip the market

3. Verify with LACBKN:
   - Market ticker: `KXNBASPREAD-26JAN09LACBKN-LAC6`
   - Parsed: `market_team_code = "LAC"` ✅
   - Should no longer fail to determine team code

### Priority 2: Use Canonical POV Per Game (Issue B)

**Function to Modify:**
- `build_spreads_rows_for_today()` (lines ~672-1010)

**Implementation Steps:**
1. Add canonical POV selection logic (before strike selection)
2. Replace dual away/home loops with single canonical POV loop
3. Ensure underdog exposure is represented via NO side selection, not duplicate rows

### Priority 3: Enhanced Debug Logging

**Add to `build_spreads_rows_for_today()`:**
```python
# Targeted debug for problematic games
if is_lacbkn or DEBUG_SPREADS:
    print(f"\n{'='*60}")
    print(f"[DEBUG] Game: {away_team_name} @ {home_team_name}")
    print(f"  Unabated spreads:")
    print(f"    Away: {away_spread} (juice: {away_juice})")
    print(f"    Home: {home_spread} (juice: {home_juice})")
    
    # Show canonical POV selection
    canonical_team_code = away_code if canonical_team == "away" else home_code
    print(f"  Canonical POV: {canonical_team} ({canonical_team_code}) spread={canonical_spread}")
    
    # Show market parsing results
    print(f"  Spread markets found: {len(spread_markets)}")
    canonical_market_count = len([m for m in spread_markets if m.get("market_team_code") == canonical_team_code])
    print(f"  Markets with market_team_code=={canonical_team_code}: {canonical_market_count}")
    if canonical_market_count == 0:
        print(f"  ⚠️ ZERO markets for canonical team - this is why game disappears")
    
    # Show first few markets with parsing details
    for m in spread_markets[:5]:
        ticker = m.get("ticker", "N/A")
        team_code = m.get("market_team_code", "N/A")
        strike = m.get("parsed_strike", "N/A")
        title = m.get("title", "N/A")
        print(f"    - {ticker}")
        print(f"      team_code={team_code}, strike={strike}, title={title[:50]}")
    
    # Show selection results
    print(f"  Selected {len(selected_strikes)} strike(s):")
    if len(selected_strikes) == 0:
        print(f"  ⚠️ Selection returned 0 strikes - game will be skipped")
    for market, side in selected_strikes:
        print(f"    - {market.get('ticker')} (strike={market.get('parsed_strike')}, side={side})")
```

---

## Validation Plan

### After Priority 1 Fix (Ticker Parsing):

**Expected Debug Output for LACBKN:**
```
[DEBUG] Game: LA Clippers @ Brooklyn
  Spread markets found: 10
    - KXNBASPREAD-26JAN09LACBKN-LAC6 → team=LAC, strike=6.5
    - KXNBASPREAD-26JAN09LACBKN-LAC9 → team=LAC, strike=9.5
    - KXNBASPREAD-26JAN09LACBKN-BKN6 → team=BKN, strike=6.5
    - ...
  Away team (LAC): spread=-6.5, selected 2 strike(s)
  Selected strikes:
    - KXNBASPREAD-26JAN09LACBKN-LAC6 (strike=6.5, side=YES)
    - KXNBASPREAD-26JAN09LACBKN-LAC9 (strike=9.5, side=YES)
```

**Validation Criteria:**
- ✅ No "Could not determine market_team_code" warnings for LACBKN
- ✅ All 10 markets have valid `market_team_code` (LAC or BKN)
- ✅ Strike selection returns 2+ strikes (not 0)
- ✅ LACBKN appears in spreads table

### After Priority 2 Fix (Canonical POV):

**Expected Output:**
```
Game Date | Away | Home | Consensus | Strike      | AwayKalshi | HomeKalshi
2025-01-09| PHI  | ORL  | PHI -3 -106| PHI -3.5    | 0.xxxx     | 0.yyyy
2025-01-09| PHI  | ORL  | PHI -3 -106| PHI -4.5    | 0.xxxx     | 0.yyyy
(Only PHI strikes shown, not ORL duplicates)
```

**Validation Criteria:**
- ✅ Only one consensus line per game (favorite's spread)
- ✅ Multiple strikes listed under that consensus
- ✅ Underdog exposure shown via HomeKalshi column (NO side of favorite's market)
- ✅ No duplicate rows with same strike shown from opposite perspective

### After Both Fixes:

**Complete LACBKN Row Example:**
```
Game Date | Away | Home | Consensus | Strike      | AwayKalshi | HomeKalshi
2025-01-09| LAC  | BKN  | LAC -6.5  | LAC -6.5    | 0.xxxx     | 0.yyyy
2025-01-09| LAC  | BKN  | LAC -6.5  | LAC -9.5    | 0.xxxx     | 0.yyyy
```

**Where:**
- `AwayKalshi` = YES bid on LAC-over-6.5 market (LAC covers)
- `HomeKalshi` = NO bid on LAC-over-6.5 market (BKN covers, LAC does NOT cover)

---

## Testing Checklist

- [ ] LACBKN appears in spreads table
- [ ] No "Could not determine market_team_code" warnings
- [ ] No duplicate consensus POV rows (only one per game)
- [ ] Multiple strikes per game (2 closest to canonical spread)
- [ ] Underdog exposure represented via NO side (HomeKalshi when away is favorite)
- [ ] Moneylines table remains unchanged
- [ ] Debug output shows canonical POV selection logic
- [ ] Debug output shows ticker parsing working for all markets

---

## Non-Negotiables

1. **Do not skip a game because title parsing fails.**
   - Even if title parsing fails for some markets, use ticker parsing
   - Only skip if genuinely no markets match canonical team code (with logging)

2. **`market_team_code` must come from market ticker, not title.**
   - Ticker parsing is the source of truth for team codes
   - Title parsing may still be needed for strike values (decimal precision)

3. **Strike must come from title regex, not ticker suffix digits.**
   - Ticker suffix (e.g., `6` in `LAC6`) is a bucket/index, not exact strike
   - Parse strike from title using regex: `r'over\s+([\d.]+)\s+points?'`

4. **Spreads table must output one canonical POV per game and two strikes (2 rows per game).**
   - One consensus line (favorite's spread)
   - Two strikes (2 closest to canonical spread)
   - Underdog exposure via NO side (shown in opponent's column)

5. **Moneylines table must remain untouched.**
   - All fixes confined to `nba_spreads_dashboard.py`
   - No changes to moneylines code paths

---

## Notes

- **Ticker Format Assumption**: This assumes all spread market tickers follow pattern `{series}-{date}{matchup}-{team_code}{strike_bucket}`. Validate this assumption with actual ticker samples.
- **Strike Bucket Mapping**: Strike bucket (e.g., `6`) may map to multiple strike values (e.g., 6.0, 6.5). **Do NOT use ticker suffix digits for strike value.** Always parse strike from title using regex.
- **Edge Cases**: Handle cases where ticker format varies or team codes don't match away/home codes exactly.
- **Verification**: After fixes, verify that canonical_team_code and market count are logged for problematic games (LACBKN) to confirm root cause is addressed.
