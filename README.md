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

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Set up API credentials:

   **Option A: Environment Variables (Recommended)**
   ```bash
   export TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
   export TELEGRAM_CHAT_ID="your_telegram_chat_id"
   export UNABATED_API_KEY="your_unabated_api_key"
   ```

   **Option B: Create credential files (gitignored)**
   - Create `kalshi_api_key_id.txt` with your Kalshi API key ID
   - Create `kalshi_private_key.pem` with your Kalshi private key
   - Update `utils/config.py` directly with Telegram and Unabated API keys (not recommended for production)

3. The `team_xref_nba.csv` file is already included with all NBA team mappings.

## Usage

Run the main value scanner:
```bash
python nba_value_table.py
```

This will:
- Fetch today's NBA games from Unabated
- Get Kalshi orderbook data for each game
- Calculate expected value for maker-posting scenarios
- Display results in a browser dashboard with HTML table
- Show console output with detailed table

## Features

- **Today's Games**: Automatically filters to games scheduled for today (PST/PDT)
- **Value Calculation**: Seller/post-maker perspective EV calculations
- **Dashboard**: HTML dashboard with:
  - Liquidity bars (red-to-green gradient)
  - Toggle between probabilities and American odds
  - Highlighting for started games
  - Hover tooltips for liquidity and odds descriptions
- **Rotation Numbers**: Displays Unabated rotation numbers for each game
- **Game Times**: Shows game start times in PST/PDT format

## Implementation Status

All files marked with TODO comments need implementation. See reuse plan for detailed specifications.
