# Totals Consensus Debug Notes

## Symptoms

**Observed behavior:**
- Totals dashboard "Consensus" column shows incorrect values
- Example: NOP@WAS (ROTO 561) showing `227 +100` when Unabated consensus is `~242`
- Kalshi totals tickers are parsed incorrectly: `...-240` → `24.0` (should be `240.0`), `...-230` → `23.0` (should be `230.0`), `...-225` → `22.5` (should be `225.0`)
- Unabated total printed in debug logs is often the same value (227.0) across many games, suggesting it's being reused or not properly matched per game

**Root failure chain:**
1. **Issue A:** Kalshi ticker parsing divides by 10 incorrectly (240 → 24.0 instead of 240.0)
2. **Issue B:** Unabated totals extraction returns first bt3 found across ALL ms49 blocks, potentially:
   - Returning the same total for all games (if function returns early)
   - Matching wrong game's total
   - Using wrong field (e.g., team total, first-half total, fallback default)
3. **Issue C:** Consensus formatting may be incorrect (though less likely the primary issue)

## Root Cause Hypotheses (Ranked)

### Hypothesis 1: Kalshi ticker parsing divides by 10 incorrectly (CONFIRMED - HIGHEST PROBABILITY)

**Evidence:**
- User observation: tickers like `...-240`, `...-230`, `...-225` are parsed as `24.0`, `23.0`, `22.5`
- Current code in `parse_total_market_ticker()` (lines 74-107) divides by 10 for ALL cases
- User states: "In this dataset, tickers appear to be integer totals" (NOT encoded as "hundreds of cents")

**Current broken logic:**
```python
# Line 74-79: Divides by 10 for all cases
if strike_bucket % 10 == 5:
    strike = strike_bucket / 10.0  # 225 → 22.5 (WRONG)
elif strike_bucket % 10 == 0:
    strike = strike_bucket / 10.0  # 240 → 24.0 (WRONG)
```

**Expected behavior:**
- `240` → `240.0` (integer total)
- `230` → `230.0` (integer total)
- `225` → `225.0` (integer total, not half-point)
- `246` → `246.0` (integer total)

**Fix priority:** HIGHEST — This is a clear bug and must be fixed first.

---

### Hypothesis 2: Unabated totals extraction returns first bt3 across all ms49 blocks (HIGH PROBABILITY)

**Evidence:**
- `extract_unabated_totals()` (lines 114-230) iterates through ALL ms49 blocks but returns on FIRST bt3 found
- Totals are game-level (not per-team like spreads/moneylines), so there should be ONE total per game
- But if multiple ms49 blocks exist (one per team), the function returns the first one it finds, which might be:
  - The same total for all games (if it's always finding the first game's total)
  - Or correctly matched per game (if each event has its own ms49 blocks)

**Current logic issue:**
- Lines 143-185: Tries bt3 first, returns immediately if found
- Lines 187-228: Falls back to bt4, bt5, etc. if bt3 not found
- **Problem:** If multiple ms49 blocks exist in the same event, it returns the FIRST bt3 found, which might be from the wrong side/team

**Comparison to spreads:**
- Spreads extraction (`extract_unabated_spreads`) correctly iterates ALL ms49 blocks and extracts per-team data
- Totals should extract game-level total (not per-team), but might need to check ALL ms49 blocks to find the correct one

**Fix priority:** HIGH — This could explain why all games show the same total.

---

### Hypothesis 3: Unabated totals are not in bt3 field (MEDIUM PROBABILITY)

**Evidence:**
- Current code tries bt3 first (most likely), then falls back to bt4, bt5, "total", "overUnder"
- But Unabated API structure might store totals differently:
  - Not in bt3 (might be in different bet type)
  - Not per-team (might be in a single ms49 block without si{index} prefix)
  - Might be in a different field name (e.g., "gameTotal", "fullGameTotal" vs "total")

**Possible issues:**
- Wrong bet type key (bt3 might be wrong)
- Wrong field name within bt3 (e.g., using "line" when it should be "gameTotal")
- Totals might be in a separate ms49 block without si{index} prefix (global game-level data)

**Fix priority:** MEDIUM — Only investigate after fixing Hypothesis 1 and checking actual Unabated structure.

---

### Hypothesis 4: Game matching is incorrect (LOW PROBABILITY)

**Evidence:**
- Function is called per-game in `build_totals_rows_for_today()` (line 671)
- Events are matched by `event_start` (line 664: `events_by_start.get(event_start)`)
- This matching should be correct since it's the same logic used for spreads/moneylines

**Possible issues:**
- Multiple events with same `event_start` (unlikely)
- Event lookup returns wrong event (shouldn't happen if `event_start` is unique)

**Fix priority:** LOW — Only investigate if other hypotheses don't resolve it.

---

## Where to Look in Code

### File: `nba_totals_dashboard.py`

**Function: `parse_total_market_ticker(ticker: str)`** (Lines 37-111)
- **Location of failure:** Lines 74-107
- **Current logic:** Divides all strike buckets by 10, assuming "hundreds of cents" encoding
- **Issue:** Tickers are integer totals, not encoded as hundreds of cents
- **Fix:** Remove division by 10, return integer totals directly (240 → 240.0, not 24.0)

**Function: `extract_unabated_totals(event: Dict[str, Any], teams: Dict[str, Any])`** (Lines 114-230)
- **Location of potential failure:** Lines 143-185 (returns first bt3 found)
- **Current logic:** Iterates through ALL ms49 blocks, returns FIRST bt3 found
- **Issue:** If multiple ms49 blocks exist (one per team), it returns first one, which might not be game-level total
- **Fix:** Need to verify if totals are per-team or game-level. If game-level, should find single total; if per-team, need to aggregate or pick correctly.

**Function: `build_totals_rows_for_today()`** (Lines 614-815)
- **Location of call:** Line 671 (`totals_data = extract_unabated_totals(unabated_event, teams_dict)`)
- **Current logic:** Matches events by `event_start`, calls `extract_unabated_totals` per game
- **Issue:** Should be correct, but needs verification that correct event is matched

---

## Mandatory Debug Prints to Add

Add these prints in `extract_unabated_totals()` function, right after finding ms49_keys:

```python
# Add after line 131 (after finding ms49_keys)
if DEBUG_TOTALS:
    print(f"\n  [DEBUG] Unabated Totals Extraction:")
    print(f"    Event: {event.get('eventStart')} (matched by event_start)")
    print(f"    ms49_keys found: {len(ms49_keys)}")
    print(f"    ms49_key samples: {ms49_keys[:3]}")
    
    # Check for NOP@WAS specifically
    event_teams = event.get("eventTeams", {})
    if isinstance(event_teams, dict):
        team_names = []
        for idx, team_info in event_teams.items():
            if isinstance(team_info, dict):
                team_id = team_info.get("id")
                if team_id:
                    team_name = get_team_name(team_id, teams)
                    team_names.append(team_name)
        print(f"    Event teams: {team_names}")
    
    # Print first ms49 block structure
    if ms49_keys:
        first_ms49 = market_lines[ms49_keys[0]]
        print(f"    First ms49_block keys: {list(first_ms49.keys())[:10]}")
        if "bt3" in first_ms49:
            bt3 = first_ms49["bt3"]
            if isinstance(bt3, dict):
                print(f"    bt3 fields: {list(bt3.keys())}")
                print(f"    bt3 line: {bt3.get('line')}")
                print(f"    bt3 total: {bt3.get('total')}")
                print(f"    bt3 value: {bt3.get('value')}")
                print(f"    bt3 points: {bt3.get('points')}")
```

Add these prints in `parse_total_market_ticker()` to verify parsing:

```python
# Add after parsing (before return), with DEBUG_TOTALS guard
if DEBUG_TOTALS:
    test_cases = ["246", "243", "240", "237", "225", "220", "205"]
    for test_suffix in test_cases:
        test_ticker = f"KXNBATOTAL-26JAN09OKCMEM-{test_suffix}"
        dir_result, strike_result = parse_total_market_ticker(test_ticker)
        expected = float(test_suffix)
        status = "✅" if abs(strike_result - expected) < 0.1 else "❌"
        print(f"    {status} {test_ticker} → {strike_result} (expected {expected})")
```

Add these prints in `build_totals_rows_for_today()` to verify per-game extraction:

```python
# Add right before line 671 (before extracting totals)
if DEBUG_TOTALS:
    away_team_name = game.get("away_team_name")
    home_team_name = game.get("home_team_name")
    away_roto = game.get("away_roto")
    print(f"\n  [DEBUG] Extracting totals for: {away_team_name} @ {home_team_name} (ROTO {away_roto})")
    print(f"    event_start: {event_start}")
    print(f"    matched unabated_event keys: {list(unabated_event.keys())[:10]}")
```

---

## Concrete Fix Options

### Fix A: Correct Kalshi ticker parsing (REQUIRED - Do this first)

**Implementation:**
1. **Remove division by 10** for integer totals
2. **Return strike directly** as float (240 → 240.0)
3. **Only divide by 10** if we can confirm ticker format uses "hundreds of cents" encoding (which we can't)

**Code change:**
```python
# In parse_total_market_ticker(), replace lines 69-91:
if match:
    direction = match.group(1)
    strike_bucket = int(match.group(2))
    
    # FIXED: Tickers are integer totals, NOT encoded as hundreds of cents
    # Return strike directly as float (240 → 240.0, not 24.0)
    strike = float(strike_bucket)
    
    return (direction, strike)

# Similarly for Pattern 2 (pure numeric), replace lines 96-107:
match = re.match(r'^(\d+)$', suffix)
if match:
    strike_bucket = int(match.group(1))
    
    # FIXED: Return integer total directly
    strike = float(strike_bucket)
    
    return (None, strike)
```

**Validation:**
- Test with tickers: `...-246`, `...-243`, `...-240`, `...-237`, `...-225`, `...-220`, `...-205`
- Expected: `246.0`, `243.0`, `240.0`, `237.0`, `225.0`, `220.0`, `205.0`
- All should parse correctly without division

**Pros:**
- Simple fix (remove incorrect division)
- Matches user's observation ("integer totals")
- No impact on other functionality

**Cons:**
- If some tickers ARE encoded differently in future, might break
- But user explicitly states: "In this dataset, tickers appear to be integer totals"

---

### Fix B: Fix Unabated totals extraction to handle per-game correctly (REQUIRED)

**Implementation:**
1. **Verify totals structure** — Are totals per-team or game-level?
   - If game-level: Should find single total (may need to check all ms49 blocks)
   - If per-team: Should aggregate or pick correctly (but totals shouldn't be per-team)

2. **Add debug logging** to see which ms49 block contains totals
   - Print all ms49 blocks checked
   - Print which one returned the total
   - Verify it's not reusing the same total across games

3. **Ensure per-game matching** — Verify `unabated_event` is correct per game
   - Print event teams to confirm matching
   - Verify `event_start` matching is correct

4. **Handle edge cases:**
   - If no bt3 found, try other bet types (bt4, bt5, etc.) — already done
   - If multiple ms49 blocks have bt3, check if they're different values or same
   - If same value, that's fine (totals are game-level)

**Code change:**
```python
# In extract_unabated_totals(), add debug and fix early return:
# Collect ALL bt3 values from all ms49 blocks
all_bt3_totals = []

for ms49_key in ms49_keys:
    ms49_block = market_lines[ms49_key]
    if not isinstance(ms49_block, dict):
        continue
    
    bt3_line = ms49_block.get("bt3")
    if bt3_line and isinstance(bt3_line, dict):
        # Extract total (same logic as before)
        total_raw = (...)
        if total_raw is not None:
            try:
                total = float(total_raw.strip() if isinstance(total_raw, str) else total_raw)
                juice = (...)
                all_bt3_totals.append({
                    "ms49_key": ms49_key,
                    "total": total,
                    "juice": juice
                })
            except (ValueError, TypeError):
                continue

# FIXED: If multiple bt3 totals found, verify they're the same (totals are game-level)
if all_bt3_totals:
    if DEBUG_TOTALS:
        print(f"    Found {len(all_bt3_totals)} bt3 total(s)")
        for i, bt3_data in enumerate(all_bt3_totals):
            print(f"      {i+1}. {bt3_data['ms49_key']}: total={bt3_data['total']}, juice={bt3_data['juice']}")
    
    # If multiple, check if they're the same (should be for game-level totals)
    unique_totals = set(bt3_data['total'] for bt3_data in all_bt3_totals)
    if len(unique_totals) > 1:
        if DEBUG_TOTALS:
            print(f"    ⚠️ WARNING: Multiple different totals found: {unique_totals}")
        # Use first one for now (may need to aggregate or pick correctly)
    
    # Return first bt3 total (should be same across all ms49 blocks if game-level)
    return all_bt3_totals[0]
```

**Alternative approach (if totals are per-team):**
- If totals ARE per-team (unlikely), aggregate them (average or use one team's total)
- But totals should be game-level, not per-team

**Validation:**
- For NOP@WAS, verify extracted total is ~242 (not 227)
- Verify different games have different totals (not all 227.0)
- Verify correct game is matched (check event teams in debug output)

---

### Fix C: Ensure consensus formatting is per-game (LOWER PRIORITY)

**Implementation:**
1. Verify `format_total_consensus_string()` is called per-game (should be fine)
2. Verify `canonical_total` and `canonical_juice` are per-game values (not reused)
3. Add debug print to show consensus per game

**Code change:**
```python
# In build_totals_rows_for_today(), add debug before formatting:
if DEBUG_TOTALS:
    print(f"    [DEBUG] Formatting consensus for {away_team_name} @ {home_team_name}:")
    print(f"      canonical_total: {canonical_total}")
    print(f"      canonical_juice: {canonical_juice}")
    print(f"      formatted: {format_total_consensus_string(canonical_total, canonical_juice)}")
```

**Validation:**
- Verify consensus string is different per game
- Verify format matches expected (e.g., "242" or "242 -110")

---

## Recommended Fix Path

**Phase 1: Fix ticker parsing (Do this first - highest confidence)**
1. Fix `parse_total_market_ticker()` to return integer totals directly (no division by 10)
2. Add debug prints to verify parsing for test cases (246, 243, 240, etc.)
3. Test on one game to verify strikes parse correctly

**Phase 2: Fix Unabated extraction (Primary goal)**
1. Add comprehensive debug prints to `extract_unabated_totals()`
2. Run on NOP@WAS game and capture output
3. Verify:
   - Correct event is matched (check event teams)
   - Correct ms49 block is checked
   - Correct bt3 field is extracted
   - Total value is ~242 (not 227)
4. Fix extraction logic if needed (handle multiple ms49 blocks correctly)

**Phase 3: Validation**
1. Verify NOP@WAS consensus shows ~242 (not 227)
2. Verify different games have different totals
3. Verify strikes selected are reasonable around true total
4. Verify moneylines output is unchanged

---

## Validation Plan

### Step 1: Verify Ticker Parsing

**For test cases:**
```
Test ticker: KXNBATOTAL-26JAN09OKCMEM-246 → Expected: 246.0 ✅ (was 24.6 ❌)
Test ticker: KXNBATOTAL-26JAN09OKCMEM-243 → Expected: 243.0 ✅ (was 24.3 ❌)
Test ticker: KXNBATOTAL-26JAN09OKCMEM-240 → Expected: 240.0 ✅ (was 24.0 ❌)
Test ticker: KXNBATOTAL-26JAN09OKCMEM-237 → Expected: 237.0 ✅ (was 23.7 ❌)
Test ticker: KXNBATOTAL-26JAN09OKCMEM-225 → Expected: 225.0 ✅ (was 22.5 ❌)
Test ticker: KXNBATOTAL-26JAN09OKCMEM-220 → Expected: 220.0 ✅ (was 22.0 ❌)
Test ticker: KXNBATOTAL-26JAN09OKCMEM-205 → Expected: 205.0 ✅ (was 20.5 ❌)
```

**Success criteria:**
- All test cases parse correctly (no division by 10)
- Kalshi strikes selected are reasonable (e.g., 243, 246 for game with ~242 total)

---

### Step 2: Verify Unabated Extraction

**For NOP@WAS (ROTO 561):**
```
[DEBUG] Extracting totals for: New Orleans Pelicans @ Washington Wizards (ROTO 561)
  event_start: 2026-01-10T00:00:00Z
  matched unabated_event teams: ['New Orleans Pelicans', 'Washington Wizards']
  ms49_keys found: 2
  ms49_key samples: ['si1:ms49:an0', 'si0:ms49:an0']
  First ms49_block keys: ['bt1', 'bt2', 'bt3']
  bt3 fields: ['line', 'americanPrice', 'unabatedPrice']
  bt3 line: 242.0  ✅ (not 227.0)
  chosen_total: 242.0
```

**Success criteria:**
- Extracted total is ~242 (not 227)
- Different games show different totals (verify at least 3 games)
- Correct event is matched (check team names in debug)

---

### Step 3: Verify Consensus Column

**Expected output for NOP@WAS:**
```
GameDate      GameTime   ROTO  AwayTeam                    HomeTeam                    Consensus      Strike         OverKalshi    UnderKalshi
2026-01-09    4:00 pm    561   New Orleans Pelicans         Washington Wizards         242            Over 242.0     0.5234        0.4766
2026-01-09    4:00 pm    561   New Orleans Pelicans         Washington Wizards         242            Over 243.0     0.5100        0.4900
```

**Success criteria:**
- Consensus shows `242` (not `227` or `227 +100`)
- Different games have different consensus values
- Strikes selected are close to consensus (e.g., 242, 243 for game with 242 total)

---

### Step 4: Verify No Impact on Moneylines

**Before/After comparison:**
- Run `python nba_value_table.py` before fix
- Capture moneylines table output
- Apply fixes
- Run again and verify moneylines table is identical
- Verify no changes to:
  - `nba_todays_fairs.py` (not modified)
  - `nba_value_table.py` moneylines rendering (not modified)
  - `kalshi_top_of_book_probs.py` (not modified)

**Success criteria:**
- Moneylines table output is byte-for-byte identical
- No errors or warnings in moneylines module
- All existing functionality works as before

---

## Implementation Checklist

- [ ] Fix `parse_total_market_ticker()` to return integer totals directly (remove division by 10)
- [ ] Add debug prints to verify ticker parsing for test cases
- [ ] Add comprehensive debug prints to `extract_unabated_totals()`
- [ ] Fix Unabated extraction logic if needed (handle multiple ms49 blocks correctly)
- [ ] Add debug prints to verify per-game matching in `build_totals_rows_for_today()`
- [ ] Test on NOP@WAS game and verify consensus is ~242
- [ ] Test on at least 3 different games and verify different totals
- [ ] Verify strikes selected are reasonable around true total
- [ ] Verify moneylines output is unchanged
- [ ] Remove temporary debug prints (or guard behind `DEBUG_TOTALS` flag)

---

## No Impact on Moneylines Check

**Why changes cannot affect moneylines:**
1. **Separate module:** All fixes are in `nba_totals_dashboard.py`, which is completely separate from moneylines code
2. **No shared functions modified:**
   - `nba_todays_fairs.py` — Not modified (moneylines uses this for game metadata)
   - `nba_value_table.py` — Only totals table rendering modified, moneylines rendering unchanged
   - `kalshi_top_of_book_probs.py` — Not modified
   - `core/reusable_functions.py` — Not modified
3. **Additive changes only:**
   - Totals table is added to dashboard (doesn't modify existing moneylines table)
   - Totals module uses its own functions (doesn't modify shared functions)
4. **Different data paths:**
   - Moneylines: Uses `extract_unabated_moneylines_by_team_id()` → `get_top_of_book_post_probs()`
   - Totals: Uses `extract_unabated_totals()` → `discover_kalshi_totals_markets()` → `get_spread_orderbook_data()`
   - No shared code paths between moneylines and totals

**Validation method:**
- Compare moneylines table output before/after fixes
- Verify no changes to moneylines HTML rendering
- Verify no errors in moneylines module

---

## Expected Outcome After Fixes

**Before:**
```
Kalshi ticker: KXNBATOTAL-26JAN09OKCMEM-240 → parsed_strike: 24.0 ❌
Unabated total: 227.0 (same for all games) ❌
Consensus: 227 (incorrect) ❌
```

**After:**
```
Kalshi ticker: KXNBATOTAL-26JAN09OKCMEM-240 → parsed_strike: 240.0 ✅
Unabated total: 242.0 (per-game, different values) ✅
Consensus: 242 (correct for NOP@WAS) ✅
Selected strikes: 242.0, 243.0 (close to consensus) ✅
```

---

## Notes

- **Non-negotiable constraint:** Do not modify moneylines or spreads code — all fixes confined to `nba_totals_dashboard.py`
- **Debug flag:** Use `DEBUG_TOTALS = True` for verbose logging during investigation
- **Ticker format:** User explicitly states "In this dataset, tickers appear to be integer totals" — do NOT divide by 10
- **Per-game extraction:** Verify `extract_unabated_totals()` is called per-game and matches correct event (should be correct via `event_start` matching, but verify)
