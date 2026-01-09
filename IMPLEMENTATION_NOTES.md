# NBA Moneyline Value Scanner - MVP Implementation Notes

**MVP Scope**: Offline scanner that reads xref CSV, fetches orderbooks, computes EVs, prints +EV table. No discovery, matching, or execution logic.

---

## ✅ Copied Files (Ready to Use)

### `utils/`
- `config.py` - Configuration with API keys and constants
- `kalshi_api.py` - Kalshi API authentication and request signing

### `pricing/`
- `conversion.py` - Price conversion (cents ↔ American odds)
  - `cents_to_american(price_cents: int) -> int`
  - `american_to_cents(odds: int) -> int`
- `fees.py` - Fee calculations (source of truth)
  - `fee_dollars(contracts: int, price_cents: int) -> float` - Taker fee in dollars
  - `maker_fee_cents(price_cents: int, contracts: int = 1) -> int` - Maker fee in cents

### `core/`
- `reusable_functions.py` - Extracted functions from main.py:
  - `fetch_unabated_snapshot() -> Dict[str, Any]` - Fetch Unabated odds snapshot
  - `fetch_orderbook(api_key_id, private_key_pem, market_ticker) -> Optional[Dict]` - Fetch orderbook
  - `derive_implied_yes_asks(no_bids) -> List[Tuple[int, int]]` - Derive YES asks from NO bids
  - `expected_value(p_win, price_cents, fee_on_win_cents) -> float` - Calculate EV with fees

---

## 📝 MVP Implementation (Single File)

### `nba_scanner.py`
**Purpose**: Single file containing all MVP logic.

**Functions to implement**:

1. **`load_nba_xref(path: str) -> List[Dict[str, Any]]`**
   - Load CSV with columns: `unabated_game_id, kalshi_market_ticker, team_name, unabated_odds`
   - Returns list of dicts

2. **`get_yes_ask_prices(orderbook: Dict) -> Tuple[Optional[int], Optional[int]]`**
   - Get best YES ask and ask-1¢
   - Formula: `best_ask = min(100 - no_price for no_price, _ in no_bids)`
   - Formula: `ask_inside = best_ask - 1 if best_ask > 1 else None`
   - No crossing checks needed (ask-1¢ never crosses by construction)
   - Returns `(best_ask_cents, inside_ask_cents | None)`

3. **`calculate_ev_scenario(win_prob: float, price_cents: int, scenario: str) -> float`**
   - Calculate EV for "take_ask" (taker fee) or "post_inside_maker" (maker fee)
   - Uses `expected_value()` from `core/reusable_functions.py`
   - Uses `fee_dollars()` or `maker_fee_cents()` from `pricing/fees.py`
   - Returns dollars per contract

4. **`scan_nba_moneylines(xref_path: str) -> List[Dict[str, Any]]`**
   - Main orchestration: load xref, iterate entries, fetch orderbooks, compute EVs
   - **No Unabated snapshot fetching** - uses odds directly from CSV
   - Converts `unabated_odds` to implied probability: `win_prob = american_to_cents(unabated_odds) / 100.0`
   - Filters to +EV markets only
   - Sorts by best EV (max of both scenarios)
   - Returns list of market dicts

5. **`print_value_table(results: List[Dict]) -> None`**
   - Print ranked table to console
   - Columns: Rank | Ticker | Team | EV@ask | EV@inside | Prob% | Ask | Inside
   - EV units: dollars per contract (4 decimals)
   - Odds: **Kalshi price equivalents only** (American odds), not Unabated odds

**Entry point**:
```python
if __name__ == "__main__":
    results = scan_nba_moneylines("nba_xref.csv")
    print_value_table(results)
```

---

## 📋 Required Files

### `nba_xref.csv`
**Format**:
```csv
unabated_game_id,kalshi_market_ticker,team_name,unabated_odds
lg1:12345:KXNCAAMLGAME-25DEC20LALLAL-LALLAL,Los Angeles Lakers,-150
lg1:12345:KXNCAAMLGAME-25DEC20LALLAL-BOSCEL,Boston Celtics,+130
```

**Manual population**: User must populate with today's games (one row per team per game).

### Credential files
- `kalshi_api_key_id.txt` (copy from parent directory)
- `kalshi_private_key.pem` (copy from parent directory)

---

## 🔧 Implementation Steps

1. **Create `nba_xref.csv`**:
   - Manually populate with today's games and odds
   - Format: `unabated_game_id, kalshi_market_ticker, team_name, unabated_odds`
   - `unabated_odds`: American odds from Unabated (e.g., `-150`, `+130`)

2. **Implement functions in `nba_scanner.py`**:
   - Follow function signatures in PLAN.md Section B
   - Use copied utilities from `core/` and `pricing/`
   - **Do NOT** implement Unabated snapshot parsing (use CSV odds directly)

3. **Test manually**:
   - Run: `python nba_scanner.py`
   - Verify output table format and EV calculations

---

## 📊 MVP Flow

```
1. Load nba_xref.csv
   ↓
2. For each xref entry:
   - Convert unabated_odds to implied probability (no devigging)
   - Fetch orderbook for kalshi_market_ticker
   - Get ask prices: best_ask = min(100 - no_price), ask_inside = best_ask - 1
   - Calculate EVs (both scenarios)
   - Include if +EV
   ↓
3. Sort by best EV
   ↓
4. Print table
```

---

## ❌ Excluded from MVP

These are **NOT** implemented in MVP:
- Unabated snapshot parsing (`extract_unabated_consensus()`)
- Devigging (`devig_two_side_ml()`)
- Canonical key-based matching
- Kalshi event/series discovery
- Automated game discovery
- Caching layers
- Telegram integration
- Command parsing
- Unit tests
- Logging framework
- Future-proofing logic

---

## 📚 Reference

- **Plan**: See `PLAN.md` for detailed specifications
- **Reusable functions**: See `core/reusable_functions.py` for available utilities (copied from prior project)
- **Fee calculation**: See `pricing/fees.py` for fee formulas (copied from prior project)
- **Orderbook structure**: Copied from prior project; bid-only per side, asks derived from opposite-side bids
