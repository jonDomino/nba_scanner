"""
Configuration module for credentials and settings.
API keys should be set via environment variables or local secrets file.
"""

import os

# Try to load from local secrets file (gitignored) if it exists
try:
    from secrets_local import (
        TELEGRAM_BOT_TOKEN as _TEL_TOKEN,
        TELEGRAM_CHAT_ID as _TEL_CHAT,
        UNABATED_API_KEY as _UNA_KEY,
    )
    _USE_LOCAL_SECRETS = True
except ImportError:
    _USE_LOCAL_SECRETS = False
    _TEL_TOKEN = None
    _TEL_CHAT = None
    _UNA_KEY = None

# Telegram credentials (environment variables or local secrets file)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or (_TEL_TOKEN if _USE_LOCAL_SECRETS else "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or (_TEL_CHAT if _USE_LOCAL_SECRETS else "")

# Kalshi API credentials (file paths - files should be gitignored)
API_KEY_ID_FILE = "kalshi_api_key_id.txt"    
PRIVATE_KEY_FILE = "kalshi_private_key.pem"   

# Unabated API (environment variable or local secrets file)
UNABATED_API_KEY = os.getenv("UNABATED_API_KEY") or (_UNA_KEY if _USE_LOCAL_SECRETS else "") 

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
