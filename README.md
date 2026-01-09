# NBA Moneyline Value Scanner

Standalone project for scanning Kalshi NBA moneyline markets and outputting ranked value table.

## Project Structure

- `utils/` - Configuration, Kalshi API, Telegram API (copied from original)
- `pricing/` - Conversion utilities and fee calculations (copied from original)
- `core/` - Reusable functions extracted from main.py (Unabated, Kalshi, EV calculations)
- `nba_value_scanner.py` - TODO: Main scanner logic
- `nba_commands.py` - TODO: Command parsing
- `config_nba.py` - TODO: NBA-specific configuration
- `team_xref_nba.csv` - TODO: NBA team name mapping CSV
- `main.py` - TODO: Telegram bot entry point

## Setup

1. Copy credential files to root:
   - `kalshi_api_key_id.txt`
   - `kalshi_private_key.pem`

2. Create `team_xref_nba.csv` with format:
   ```
   league,unabated_name,kalshi_code
   NBA,Los Angeles Lakers,LALLAL
   NBA,Boston Celtics,BOSCEL
   ...
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Update `utils/config.py` with NBA-specific constants:
   - `UNABATED_LEAGUE_ID_NBA`
   - `KALSHI_NBA_SERIES_TICKER`

## Implementation Status

All files marked with TODO comments need implementation. See reuse plan for detailed specifications.
