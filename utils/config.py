"""
Configuration module for credentials and settings.
API keys should be set via environment variables or .env file.
"""

import os

# Telegram credentials (set via environment variables)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Kalshi API credentials (file paths - files should be gitignored)
API_KEY_ID_FILE = "kalshi_api_key_id.txt"    
PRIVATE_KEY_FILE = "kalshi_private_key.pem"   

# Unabated API (set via environment variable)
UNABATED_API_KEY = os.getenv("UNABATED_API_KEY", "") 

# Trading constants
MAX_BUDGET_DOLLARS = 50.0
FEE_RATE = 0.07

# League-scoped constants (CBB)
LEAGUE = "CBB"
UNABATED_LEAGUE_ID = 4
KALSHI_SERIES_TICKER = "KXNCAAMBGAME"
TEAM_XREF_FILE = "team_xref_cbb.csv"

# NBA-specific constants
UNABATED_LEAGUE_ID_NBA = 3  # NBA league ID from Unabated API
NBA_XREF_FILE = "team_xref_nba.csv"

# API endpoints
UNABATED_PROD_URL = "https://partner-api.unabated.com/api/markets/gameOdds"
KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
