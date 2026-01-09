# NBA Moneyline Value Scanner - MVP Implementation Plan

**Status**: MVP plan focused on offline scanning: xref CSV → orderbook → EV calculation → print table. No discovery, matching, execution, or Unabated snapshot parsing.

**MVP Scope**: Load xref CSV with odds, map to Kalshi market tickers directly, fetch orderbooks, compute two scenario EVs, print +EV markets sorted by best EV.

---

## A) Inventory of Reusable Modules/Functions (As-Is)

### 1. Kalshi API Authentication & Request Signing
- **File**: `utils/kalshi_api.py`
- **Functions**: `load_creds()`, `make_request()`, `sign_request()`
- **What it does**: RSA-PSS + SHA256 signing, credential file loading, authenticated HTTP requests
- **Why reusable**: Authentication is platform-wide, not league-specific
- **Constraints**: Credentials must be in files specified by `config.API_KEY_ID_FILE` and `config.PRIVATE_KEY_FILE`

### 2. Price Conversion Utilities
- **File**: `pricing/conversion.py`
- **Functions**: `cents_to_american(price_cents: int) -> int`, `american_to_cents(odds: int) -> int`
- **What it does**: Converts between Kalshi price cents (0-100) and American odds format
- **Why reusable**: Standard conversion formulas, league-agnostic
- **Constraints**: Returns 0 for invalid inputs (cents <= 0 or >= 100)

### 3. Fee Calculation Utilities (Source of Truth)
- **File**: `pricing/fees.py`
- **Functions**: 
  - `fee_dollars(contracts: int, price_cents: int) -> float` (line 11-18)
  - `maker_fee_cents(price_cents: int, contracts: int = 1) -> int` (line 21-35)
- **What it does**: 
  - `fee_dollars()`: Returns taker fee in dollars: `ceil(0.07 * C * P * (1-P))` where P = price_cents / 100.0
  - `maker_fee_cents()`: Returns maker fee in cents: `ceil(0.0175 * C * P * (1-P) * 100)`
- **Observed usage patterns** (main.py:1366-1367, 1571, 2668):
  - Taker: `taker_fee_dollars = fee_dollars(1, ask_price_cents)` then `taker_fee_cents = int(round(taker_fee_dollars * 100.0))`
  - Maker: `maker_fee_cents = maker_fee_cents(price_cents, 1)` (returns cents directly)
- **Constraints**: 
  - Uses `config.FEE_RATE = 0.07` for taker
  - Maker fee hardcoded to 0.0175 (1.75%)
  - Fees apply **only on winning outcomes** (confirmed by `expected_value()` usage)

### 4. Orderbook Fetching
- **File**: `core/reusable_functions.py` line 192-202
- **Function**: `fetch_orderbook(api_key_id: str, private_key_pem: str, market_ticker: str) -> Optional[Dict[str, Any]]`
- **What it does**: GET `/markets/{market_ticker}/orderbook`, returns `resp.get("orderbook", {})`
- **Structure** (copied from prior project; assumed unchanged):
  ```python
  orderbook = {
      "yes": [[price_cents, qty], ...],  # List of [price, quantity] pairs (BIDS only)
      "no": [[price_cents, qty], ...]    # List of [price, quantity] pairs (BIDS only)
  }
  ```
- **Semantics** (copied from prior project; assumed unchanged):
  - `orderbook["yes"]` and `orderbook["no"]` contain **BID arrays only** (confirmed in prior project)
  - Bids are ordered: **best bid is last element** (`yes_bids[-1][0]`)
  - Asks must be **derived from opposite-side bids** (YES ask = 100 - NO bid)

### 5. EV Calculation Function (Source of Truth)
- **File**: `core/reusable_functions.py` line 179-191
- **Function**: `expected_value(p_win: float, price_cents: int, fee_on_win_cents: float) -> float`
- **What it does**: Computes `EV = p_win * ((1 - P) - fee_on_win) - (1 - p_win) * P` where `P = price_cents / 100.0` and `fee_on_win = fee_on_win_cents / 100.0`
- **Usage**: `ev = expected_value(p_win, ask_price_cents, fee_on_win_cents)` where `fee_on_win_cents` is in cents
- **Constraints**: 
  - Requires `fee_on_win_cents` to be passed separately (in cents)
  - Fee applies only on win (confirmed by formula and usage pattern)

### 6. Unabated Snapshot Fetching (Non-MVP - For Future Use)
- **File**: `core/reusable_functions.py` line 29-42
- **Function**: `fetch_unabated_snapshot() -> Dict[str, Any]`
- **What it does**: GET Unabated API with `x-api-key` param, returns full odds snapshot JSON
- **MVP status**: **NOT USED** - MVP uses odds directly from xref CSV
- **Future use**: For automated consensus extraction (non-MVP enhancement)

### 7. Orderbook Ask Derivation
- **File**: `core/reusable_functions.py` line 209-225
- **Function**: `derive_implied_yes_asks(no_bids: List[List[int]]) -> List[Tuple[int, int]]`
- **What it does**: Derives YES asks from NO bids: `yes_ask = 100 - no_price` for each NO bid, sorts ascending
- **Pattern** (copied from prior project):
  ```python
  for no_price, no_qty in no_bids:
      yes_ask = 100 - no_price
      yes_asks.append((yes_ask, no_qty))
  yes_asks.sort(key=lambda x: x[0])  # Sort ascending (lowest ask first)
  best_yes_ask = yes_asks[0][0]  # First element after sorting
  ```
- **Why reusable**: Logic is correct for bid-only orderbooks

---

## B) Must Build New (MVP Only)

### 1. Xref CSV Structure (Manual Mapping)
- **File**: `nba_xref.csv` (new file in project root)
- **Format**:
  ```csv
  unabated_game_id,kalshi_market_ticker,team_name,unabated_odds
  {game_id},{Kalshi ticker},{Team name},{American odds}
  ```
- **Example rows**:
  ```csv
  unabated_game_id,kalshi_market_ticker,team_name,unabated_odds
  lg1:12345:KXNCAAMLGAME-25DEC20LALLAL-LALLAL,Los Angeles Lakers,-150
  lg1:12345:KXNCAAMLGAME-25DEC20LALLAL-BOSCEL,Boston Celtics,+130
  ```
- **Purpose**: Direct mapping from Unabated game ID → exact Kalshi market ticker (no discovery/matching needed)
- **MVP assumption**: User manually populates CSV with today's games and odds from Unabated

### 2. Xref CSV Loader
- **File**: `nba_scanner.py`
- **Function signature**:
  ```python
  def load_nba_xref(path: str = "nba_xref.csv") -> List[Dict[str, Any]]:
      """
      Load NBA xref CSV mapping Unabated games → Kalshi market tickers.
      
      Returns: List of dicts with keys: unabated_game_id, kalshi_market_ticker, team_name, unabated_odds
      """
  ```
- **Implementation**:
  ```python
  import csv
  from typing import List, Dict, Any
  
  def load_nba_xref(path: str = "nba_xref.csv") -> List[Dict[str, Any]]:
      xref = []
      with open(path, newline="", encoding="utf-8") as f:
          reader = csv.DictReader(f)
          for row in reader:
              xref.append({
                  "unabated_game_id": row["unabated_game_id"].strip(),
                  "kalshi_market_ticker": row["kalshi_market_ticker"].strip(),
                  "team_name": row["team_name"].strip(),
                  "unabated_odds": int(row["unabated_odds"].strip())
              })
      return xref
  ```
- **Dependencies**: Standard library `csv` module

### 3. ~~Unabated Consensus Extraction~~ (REMOVED FROM MVP)
- **MVP status**: **NOT IMPLEMENTED** - MVP uses odds directly from xref CSV
- **Future enhancement**: Extract consensus from Unabated snapshot for automated odds updates

### 4. ~~Devigging Utility~~ (REMOVED FROM MVP)
- **MVP status**: **NOT IMPLEMENTED** - MVP uses implied probability only
- **Future enhancement**: Implement devigging when both sides available (requires parsing Unabated snapshot)

### 5. Orderbook Ask Prices Helper (Canonical Implementation)
- **File**: `nba_scanner.py`
- **Function signature**:
  ```python
  def get_yes_ask_prices(orderbook: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
      """
      Get best YES ask and ask-1¢ from orderbook.
      
      Args:
          orderbook: Kalshi orderbook dict with "yes" and "no" bid arrays
      
      Returns: (best_ask_cents, inside_ask_cents | None)
      - best_ask_cents: Best YES ask (derived from NO bids) or None if no liquidity
      - inside_ask_cents: ask-1¢ if best_ask > 1, else None
      """
  ```
- **Implementation** (simplified):
  ```python
  def get_yes_ask_prices(orderbook: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
      no_bids = orderbook.get("no") or []
      
      if not no_bids:
          return (None, None)  # No liquidity
      
      # Best YES ask = 100 - best NO bid (lowest YES ask)
      best_ask_cents = min(100 - no_price for no_price, _ in no_bids)
      
      # Ask-1¢ (with tick floor)
      ask_inside_cents = best_ask_cents - 1 if best_ask_cents > 1 else None
      
      return (best_ask_cents, ask_inside_cents)
  ```
- **Rationale**: 
  - Kalshi books are bid-only per side
  - YES ask is defined as `100 - NO bid`
  - Therefore `ask-1¢` never crosses by construction (always < best ask if best ask > 1)
  - No same-side checks needed
- **Dependencies**: None (simple math)

### 6. EV Calculation (Scenario-Based)
- **File**: `nba_scanner.py`
- **Function signature**:
  ```python
  def calculate_ev_scenario(
      win_prob: float,
      price_cents: int,
      scenario: str  # "take_ask" or "post_inside_maker"
  ) -> float:
      """
      Calculate EV per contract for a scenario.
      
      Args:
          win_prob: Win probability (implied from Unabated odds)
          price_cents: Price in cents (ask for taker, ask-1¢ for maker)
          scenario: "take_ask" (taker fee) or "post_inside_maker" (maker fee)
      
      Returns:
          EV in dollars per contract
      """
  ```
- **Implementation** (corrected):
  ```python
  from core.reusable_functions import expected_value
  from pricing.fees import fee_dollars, maker_fee_cents
  
  def calculate_ev_scenario(win_prob: float, price_cents: int, scenario: str) -> float:
      if scenario == "take_ask":
          # Taker fee: convert to cents
          taker_fee_dollars = fee_dollars(1, price_cents)
          fee_on_win_cents = int(round(taker_fee_dollars * 100.0))
          return expected_value(win_prob, price_cents, fee_on_win_cents)
      
      elif scenario == "post_inside_maker":
          # Maker fee: already in cents
          fee_on_win_cents = maker_fee_cents(price_cents, 1)
          return expected_value(win_prob, price_cents, fee_on_win_cents)
      
      else:
          raise ValueError(f"Invalid scenario: {scenario}")
  ```
- **Dependencies**: `core/reusable_functions.expected_value()`, `pricing/fees.fee_dollars()`, `pricing/fees.maker_fee_cents()`

### 7. Main Scanner Function
- **File**: `nba_scanner.py`
- **Function signature**:
  ```python
  def scan_nba_moneylines(
      xref_path: str = "nba_xref.csv"
  ) -> List[Dict[str, Any]]:
      """
      Main MVP scanner: load xref, fetch orderbooks, compute EVs.
      
      Returns: List of +EV markets, each with:
      {
          "ticker": str,
          "team": str,
          "ev_if_take_best_ask": float,
          "ev_if_post_ask_minus_1c_and_get_maker_fill": Optional[float],
          "win_prob": float,
          "ask_cents": Optional[int],
          "ask_inside_cents": Optional[int],
          "ask_odds": Optional[int],  # American odds for display
          "inside_odds": Optional[int]  # American odds for display
      }
      """
  ```
- **Implementation** (simplified flow - no Unabated snapshot):
  ```python
  from core.reusable_functions import fetch_orderbook
  from utils.kalshi_api import load_creds
  from pricing.conversion import cents_to_american, american_to_cents
  
  def scan_nba_moneylines(xref_path: str = "nba_xref.csv") -> List[Dict[str, Any]]:
      # Load xref
      xref = load_nba_xref(xref_path)
      
      # Load Kalshi credentials
      api_key_id, private_key_pem = load_creds()
      
      results = []
      
      for entry in xref:
          market_ticker = entry["kalshi_market_ticker"]
          team_name = entry["team_name"]
          unabated_odds = entry["unabated_odds"]
          
          # Convert Unabated odds to implied probability (no devigging in MVP)
          win_prob = american_to_cents(unabated_odds) / 100.0
          
          # Fetch orderbook
          orderbook = fetch_orderbook(api_key_id, private_key_pem, market_ticker)
          if not orderbook:
              continue  # Skip if no orderbook
          
          # Get ask prices
          best_ask, ask_inside = get_yes_ask_prices(orderbook)
          if best_ask is None:
              continue  # Skip if no liquidity
          
          # Calculate EVs
          ev_take = calculate_ev_scenario(win_prob, best_ask, "take_ask")
          ev_inside = None
          if ask_inside is not None:
              ev_inside = calculate_ev_scenario(win_prob, ask_inside, "post_inside_maker")
          
          # Only include +EV markets
          if ev_take > 0 or (ev_inside is not None and ev_inside > 0):
              results.append({
                  "ticker": market_ticker,
                  "team": team_name,
                  "ev_if_take_best_ask": ev_take,
                  "ev_if_post_ask_minus_1c_and_get_maker_fill": ev_inside,
                  "win_prob": win_prob,
                  "ask_cents": best_ask,
                  "ask_inside_cents": ask_inside,
                  "ask_odds": cents_to_american(best_ask),
                  "inside_odds": cents_to_american(ask_inside) if ask_inside else None
              })
      
      # Sort by best EV (highest first)
      results.sort(key=lambda x: max(x["ev_if_take_best_ask"], x["ev_if_post_ask_minus_1c_and_get_maker_fill"] or -999), reverse=True)
      
      return results
  ```
- **Dependencies**: All components above
- **Note**: No Unabated snapshot fetching - uses odds directly from CSV

### 8. Table Printer
- **File**: `nba_scanner.py`
- **Function signature**:
  ```python
  def print_value_table(results: List[Dict[str, Any]]) -> None:
      """
      Print ranked table of +EV markets.
      
      Columns: Rank | Ticker | Team | EV@ask | EV@inside | Prob% | Ask | Inside
      - EV units: dollars per contract
      - Odds displayed: Kalshi price equivalents (American odds)
      """
  ```
- **Implementation**:
  ```python
  def print_value_table(results: List[Dict[str, Any]]) -> None:
      if not results:
          print("No +EV markets found.")
          return
      
      print("\nNBA Moneyline Value (Top {})".format(len(results)))
      print("-" * 100)
      print(f"{'Rank':<6} {'Ticker':<25} {'Team':<20} {'EV@ask':<10} {'EV@inside':<12} {'Prob%':<8} {'Ask':<8} {'Inside':<8}")
      print("-" * 100)
      
      for i, m in enumerate(results, 1):
          ask_str = f"{m['ask_odds']}" if m['ask_odds'] else "N/A"
          inside_str = f"{m['inside_odds']}" if m['inside_odds'] else "N/A"
          ev_inside_str = f"{m['ev_if_post_ask_minus_1c_and_get_maker_fill']:.4f}" if m['ev_if_post_ask_minus_1c_and_get_maker_fill'] is not None else "N/A"
          
          print(
              f"{i:<6} {m['ticker'][-25:]:<25} {m['team'][:20]:<20} "
              f"{m['ev_if_take_best_ask']:>+.4f}  {ev_inside_str:>12} "
              f"{m['win_prob']*100:>5.1f}%  {ask_str:>8} {inside_str:>8}"
          )
  ```
- **Presentation details**:
  - **EV units**: Dollars per contract (from `expected_value()` output)
  - **Odds displayed**: Kalshi price equivalents (converted via `cents_to_american()`)
  - **Table includes**: Rank, Ticker (last 25 chars), Team, EV@ask, EV@inside, Win Prob%, Ask odds, Inside odds

---

## C) End-to-End MVP Flow

### Step 1: Load Xref CSV
- **Function**: `load_nba_xref("nba_xref.csv")`
- **Output**: List of dicts with `unabated_game_id`, `kalshi_market_ticker`, `team_name`, `unabated_odds`

### Step 2: Iterate Xref Entries
For each entry in xref:
1. **Convert odds to probability**: `win_prob = american_to_cents(unabated_odds) / 100.0` (implied prob, no devigging)
2. **Fetch orderbook** for `kalshi_market_ticker`
3. **Compute ask prices**: `get_yes_ask_prices(orderbook)` → `(best_ask, ask_inside)`
4. **Compute EVs**: 
   - `ev_take = calculate_ev_scenario(win_prob, best_ask, "take_ask")`
   - `ev_inside = calculate_ev_scenario(win_prob, ask_inside, "post_inside_maker")` if `ask_inside` is not None
5. **Filter +EV**: Only include if `ev_take > 0` or `ev_inside > 0`

### Step 3: Sort and Print
- **Sort**: By `max(ev_take, ev_inside or -999)` descending
- **Print**: Call `print_value_table(results)`

---

## D) Orderbook Semantics (Precise Definition)

### Orderbook Structure
- **Source**: `fetch_orderbook()` returns `resp.get("orderbook", {})` (`core/reusable_functions.py` line 200)
- **Structure** (copied from prior project; assumed unchanged):
  ```python
  orderbook = {
      "yes": [[price_cents, qty], ...],  # List of [price, quantity] pairs (BIDS only)
      "no": [[price_cents, qty], ...]    # List of [price, quantity] pairs (BIDS only)
  }
  ```
- **Semantics** (copied from prior project; assumed unchanged):
  - `orderbook["yes"]` and `orderbook["no"]` contain **BID arrays only**
  - Kalshi books are bid-only per side
  - Asks are **derived from opposite-side bids**: YES ask = 100 - NO bid

### Best YES Ask Computation
- **Formula**: `best_ask_cents = min(100 - no_price for no_price, _ in no_bids)`
- **Rationale**: Best YES ask is the lowest YES ask (lowest price to buy)
- **Derivation**: For each NO bid at price `no_price`, implied YES ask is `100 - no_price`
- **Result**: Best (lowest) YES ask is `min(100 - no_price for all no_price in no_bids)`

### Ask-1¢ Computation
- **Formula**: `ask_inside_cents = best_ask_cents - 1 if best_ask_cents > 1 else None`
- **Tick floor**: Minimum price is 1¢ (if best ask is 1¢, cannot go lower)
- **Rationale**: Ask-1¢ never crosses by construction:
  - YES ask is defined as `100 - NO bid`
  - Therefore `best_ask - 1 < best_ask` always (if best_ask > 1)
  - No same-side checks needed (YES bids don't affect YES asks)
  - No implied ask re-check needed (already derived from NO bids)

---

## E) Fee Treatment (Scenario-Based)

### Fee Calculation (Concrete Example at 62¢)

**Taker fee** (using `fee_dollars()`):
- `P = 0.62`
- `raw_fee = 0.07 * 1 * 0.62 * (1 - 0.62) = 0.07 * 0.62 * 0.38 = 0.016492`
- `fee_dollars = ceil(0.016492 * 100) / 100 = ceil(1.6492) / 100 = 2 / 100 = 0.02`
- `fee_cents = 0.02 * 100 = 2`

**Maker fee** (using `maker_fee_cents()`):
- `P = 0.62`
- `raw_fee_dollars = 0.0175 * 1 * 0.62 * 0.38 = 0.004123`
- `fee_cents = ceil(0.004123 * 100) = ceil(0.4123) = 1`

**EV Calculation at 62¢** (assuming `win_prob = 0.60`):
- Taker: `EV = 0.60 * ((1 - 0.62) - 0.02) - 0.40 * 0.62 = 0.60 * 0.36 - 0.248 = 0.216 - 0.248 = -0.032`
- Maker (at 61¢): `EV = 0.60 * ((1 - 0.61) - 0.01) - 0.40 * 0.61 = 0.60 * 0.38 - 0.244 = 0.228 - 0.244 = -0.016`

### Scenario EVs (Explicitly Named)
- **`EV_if_take_best_ask`**: EV if we place BUY YES at current best ask (guaranteed taker execution, taker fee)
- **`EV_if_post_ask_minus_1c_and_get_maker_fill`**: EV if we place BUY YES at ask-1¢ and it posts as maker (conditional maker execution, maker fee)
- **MVP assumption**: We treat ask-1¢ as maker-fill scenario (even though it may execute immediately in real trading)

---

## F) Presentation Details

### EV Units
- **Units**: Dollars per contract (from `expected_value()` return value)
- **Display**: 4 decimal places (e.g., `+0.0234`)

### Odds Display
- **Source**: Kalshi price equivalents only (converted via `cents_to_american()`)
- **Format**: American odds (e.g., `-150`, `+130`)
- **Displayed**: Ask odds (Kalshi best ask) and Inside odds (Kalshi ask-1¢)
- **Not displayed**: Unabated consensus odds (only used internally for win_prob calculation)

### Table Columns
1. **Rank**: Sequential number (1, 2, 3, ...)
2. **Ticker**: Last 25 characters of Kalshi market ticker (for quick identification)
3. **Team**: Team name (truncated to 20 chars)
4. **EV@ask**: `ev_if_take_best_ask` (dollars per contract, 4 decimals)
5. **EV@inside**: `ev_if_post_ask_minus_1c_and_get_maker_fill` (dollars per contract, 4 decimals, or "N/A")
6. **Prob%**: Win probability as percentage (1 decimal, e.g., `65.2%`)
7. **Ask**: Best ask price as American odds (from `cents_to_american(best_ask)`)
8. **Inside**: Ask-1¢ price as American odds (from `cents_to_american(ask_inside)`, or "N/A")

### Filtering
- **Include only**: Markets where `ev_if_take_best_ask > 0` OR `ev_if_post_ask_minus_1c_and_get_maker_fill > 0`
- **Sort**: By `max(ev_if_take_best_ask, ev_if_post_ask_minus_1c_and_get_maker_fill or -999)` descending

---

## G) Code Corrections (Fixed Bugs)

### 1. `calculate_ev_scenario()` - Corrected Implementation
**Issue**: Must return on both branches.

**Corrected** (Section B, item 6):
```python
def calculate_ev_scenario(win_prob: float, price_cents: int, scenario: str) -> float:
    from core.reusable_functions import expected_value
    from pricing.fees import fee_dollars, maker_fee_cents
    
    if scenario == "take_ask":
        taker_fee_dollars = fee_dollars(1, price_cents)
        fee_on_win_cents = int(round(taker_fee_dollars * 100.0))
        return expected_value(win_prob, price_cents, fee_on_win_cents)
    
    elif scenario == "post_inside_maker":
        fee_on_win_cents = maker_fee_cents(price_cents, 1)
        return expected_value(win_prob, price_cents, fee_on_win_cents)
    
    else:
        raise ValueError(f"Invalid scenario: {scenario}")
```

### 2. `get_yes_ask_prices()` - Non-Crossing Logic Simplified
**Issue**: Complex crossing logic not needed for MVP.

**Corrected** (Section B, item 5):
- Simple check: `ask_inside_cents = max(1, best_ask_cents - 1)`
- Valid if `ask_inside_cents < best_ask_cents` (always true for `best_ask > 1`)
- No additional same-side bid checks needed for MVP

---

## H) Architecture (MVP - Minimal)

### File Structure
```
nba_scanner/
├── nba_scanner.py          # Single file with all MVP logic
├── nba_xref.csv            # Manual xref CSV (user-populated)
├── core/
│   └── reusable_functions.py  # Reusable functions (copied)
├── pricing/
│   ├── conversion.py          # Price conversion (copied)
│   └── fees.py                # Fee calculation (copied)
└── utils/
    ├── config.py              # Configuration (copied)
    └── kalshi_api.py          # Kalshi API (copied)
```

### Entry Point
- **File**: `nba_scanner.py`
- **Main block**:
  ```python
  if __name__ == "__main__":
      results = scan_nba_moneylines("nba_xref.csv")
      print_value_table(results)
  ```

### No Additional Components (MVP)
- ❌ No command parsing (offline scanner)
- ❌ No Telegram integration (console output only)
- ❌ No caching (fetch fresh on each run)
- ❌ No logging framework (print statements only)
- ❌ No unit tests (manual verification)
- ❌ No canonical key matching (xref CSV provides tickers directly)
- ❌ No event/series discovery (xref CSV provides tickers directly)

---

## I) Xref CSV Assumption

### CSV Format
```csv
unabated_game_id,kalshi_market_ticker,team_name,unabated_odds
lg1:12345:KXNCAAMLGAME-25DEC20LALLAL-LALLAL,Los Angeles Lakers,-150
lg1:12345:KXNCAAMLGAME-25DEC20LALLAL-BOSCEL,Boston Celtics,+130
```

### Manual Population Required
- User must manually populate CSV with today's games
- One row per team per game (both teams get separate rows)
- `unabated_game_id`: Identifier from Unabated snapshot (structure TBD from inspection)
- `kalshi_market_ticker`: Exact Kalshi market ticker (must be known/observed)
- `team_name`: Team name for display
- `unabated_odds`: American odds from Unabated (used as fallback if consensus extraction fails)

### Future Extensions (Non-MVP)
- Automated game discovery via Unabated API
- Canonical key-based matching to Kalshi events
- Automatic series/market ticker discovery
- All marked as "future work" and not in MVP scope

---

## J) ~~Missing Discovery Steps~~ (REMOVED FROM MVP)

### ~~Step 1: Inspect Unabated Snapshot Structure~~ (NOT NEEDED)
- **MVP status**: **SKIPPED** - MVP uses odds directly from xref CSV
- **Future enhancement**: Parse Unabated snapshot for automated odds updates (non-MVP)

---

## Summary: MVP Implementation Checklist

1. ✅ Create `nba_xref.csv` (manual, user-populated with odds)
2. ✅ Implement `load_nba_xref()` (Section B, item 2)
3. ✅ Implement `get_yes_ask_prices()` (Section B, item 5)
4. ✅ Implement `calculate_ev_scenario()` (Section B, item 6)
5. ✅ Implement `scan_nba_moneylines()` (Section B, item 7)
6. ✅ Implement `print_value_table()` (Section B, item 8)
7. ✅ Test with sample xref CSV (manual verification)

---

## Non-MVP Items (Future Work)

These are **NOT** in MVP scope:
- ❌ Unabated snapshot parsing (`extract_unabated_consensus()`)
- ❌ Devigging (`devig_two_side_ml()`)
- ❌ Canonical key-based matching
- ❌ Kalshi event/series discovery
- ❌ Automated game discovery
- ❌ Caching layers
- ❌ Telegram integration
- ❌ Command parsing
- ❌ Unit tests
- ❌ Logging framework

All marked as "future work" and explicitly excluded from MVP.

## MVP Simplifications

1. **No Unabated snapshot parsing**: Use odds directly from xref CSV (`unabated_odds` column)
2. **No devigging**: Use implied probability only (`american_to_cents(odds) / 100.0`)
3. **Simplified orderbook logic**: `ask_inside = best_ask - 1 if best_ask > 1 else None` (no crossing checks needed)
4. **Single file**: All logic in `nba_scanner.py`
5. **Console output only**: No Telegram, no command parsing
