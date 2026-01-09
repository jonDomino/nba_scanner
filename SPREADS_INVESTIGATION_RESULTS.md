# Spreads Discovery Investigation Results

## Root Cause: Hypothesis B - CORRECT ✅

**Spreads are in a DIFFERENT series ticker (`KXNBASPREAD`), not `KXNBAGAME`.**

### Evidence:

1. **STEP 1:** Nested markets from `KXNBAGAME` series only contain 2 markets per event, both moneylines:
   - "Houston at Sacramento Winner?" (moneyline markets)

2. **STEP 2:** Spread markets exist but are in different series:
   - Found markets with titles like "Phoenix wins by over 7.5 Points"
   - These are in series like `KXMVENBASINGLEGAME` (complex multi-leg markets)

3. **STEP 3:** Discovered `KXNBASPREAD: Pro Basketball Spread` series exists

4. **STEP 4:** Confirmed `KXNBASPREAD` series contains spread markets:
   - Example event: `KXNBASPREAD-26JAN09MILLAL`
   - Contains 10 spread markets like:
     - "Milwaukee wins by over 9.5 Points?"
     - "Milwaukee wins by over 6.5 Points?"
     - "Los Angeles L wins by over 9.5 Points?"

### Series Structure:

- **KXNBAGAME**: Moneylines only (2 markets per event)
- **KXNBASPREAD**: Spread markets (multiple strikes per event, ~10 markets)
- **KXNBATOTAL**: Total points (likely separate series, not investigated)

### Event Ticker Matching:

Both series use the same date/team pattern:
- `KXNBAGAME-26JAN09MILLAL` (moneyline event)
- `KXNBASPREAD-26JAN09MILLAL` (spread event)

They can be matched by extracting the suffix (`26JAN09MILLAL`).

## Solution Required:

1. **Fetch events from `KXNBASPREAD` series** (separate API call from `KXNBAGAME`)
2. **Match spread events to moneyline events** by date/team suffix
3. **Extract spread markets from `KXNBASPREAD` events** (not from `KXNBAGAME` events)

## Unabated Spread Extraction:

STEP 5 showed that Unabated spread extraction logic needs verification:
- Some games return `None` for spreads
- Need to confirm `bt2` field contains spread data
- May need to check other bet types or market source blocks

## Minimal Change Plan:

1. Update `discover_kalshi_spread_markets()` to:
   - Accept event_ticker from `KXNBAGAME` 
   - Derive matching `KXNBASPREAD` event ticker (replace series prefix)
   - Fetch markets from `KXNBASPREAD` event instead

2. Alternative: Fetch all `KXNBASPREAD` events and match to `KXNBAGAME` events by date/teams

3. Keep all moneylines code unchanged
