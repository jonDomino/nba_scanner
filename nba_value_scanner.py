"""
TODO: NBA Moneyline Value Scanner

Main scanner logic that:
1. Loads/refreshes Unabated NBA moneyline consensus
2. Loads/refreshes Kalshi NBA moneyline events/markets
3. Matches games Unabated ↔ Kalshi using canonical keys
4. Pulls orderbooks (best bid/ask for YES)
5. Computes two EVs per market:
   - EV@ask (taker fee)
   - EV@ask-1¢ (maker fee, respecting tick floor)
6. Builds ranked table and sends via Telegram

Key functions to implement:
- extract_nba_moneyline_consensus() - Extract consensus ML from Unabated snapshot
- devig_two_side_ml() - Devig two-sided odds to true probabilities
- get_best_yes_ask_prices() - Extract best ask and ask-1¢ from orderbook
- calculate_nba_ml_ev() - Calculate EV with proper fee injection
- scan_nba_moneyline_value() - Main orchestration function
- build_value_table() - Format ranked table for Telegram
"""

# TODO: Import necessary modules
# from core.reusable_functions import ...
# from pricing.conversion import ...
# from pricing.fees import ...
# from utils.kalshi_api import load_creds

# TODO: Implement functions
