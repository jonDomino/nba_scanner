"""
End-to-end NBA value scanner: today's games with Unabated fairs vs Kalshi YES exposure break-even costs.

Internal: Reads YES bids from orderbook["yes"] for maker prices (join bid queue).
Also reads NO bids to derive YES ask for crossing check.
User-facing: YES exposure (team winning) - what price to pay for win exposure.

EV Calculation (Buyer/YES Exposure Perspective):
- Kalshi top/top+1 values represent break-even win probabilities after maker fees (cost to get YES exposure)
- Unabated fair represents the true win probability (what the team is actually worth)
- EV% = (Unabated_fair - Kalshi_break_even) × 100
- Positive EV means fair win prob > break-even cost (profitable to buy YES)
- Negative EV means fair win prob < break-even cost (unprofitable to buy YES)

Queue-jump: YES bid top+1¢ only if it doesn't cross the book (yes_bid_top_p1 < yes_ask_top).
"""

import webbrowser
import tempfile
import os
from datetime import datetime
from typing import Dict, Any, List, Optional

try:
    from zoneinfo import ZoneInfo
    USE_PYTZ = False
except ImportError:
    import pytz
    USE_PYTZ = True

from data_build.slate import get_today_games_with_fairs_and_kalshi_tickers
from data_build.top_of_book import get_top_of_book_post_probs


def derive_event_ticker(market_ticker: str) -> Optional[str]:
    """
    Derive event ticker from market ticker.
    
    Example: KXNBAGAME-26JAN08MIACHI-MIA -> KXNBAGAME-26JAN08MIACHI
    """
    if not market_ticker:
        return None
    
    parts = market_ticker.split("-")
    if len(parts) < 2:
        return None
    
    # Remove last part (team code) and rejoin
    return "-".join(parts[:-1])


def compute_ev_percent(p_yes_fair: Optional[float], p_yes_be: Optional[float]) -> Optional[float]:
    """
    Compute buyer/YES exposure EV percentage: (p_yes_fair - p_yes_be) * 100
    
    This calculates EV from the perspective of buying YES (win exposure) at the Kalshi break-even price.
    Positive EV means the fair win probability is higher than the break-even cost, so buying is profitable.
    
    Example:
        Unabated fair = 0.300 (team's true win probability)
        Kalshi break-even = 0.275 (fee-adjusted cost to get win exposure)
        EV = (0.300 - 0.275) * 100 = +2.5% (paying less than it's worth = +EV)
    
    Args:
        p_yes_fair: Unabated's fair win probability (0-1)
        p_yes_be: Kalshi break-even win probability after maker fees (0-1)
    
    Returns:
        EV in percent (positive = +EV to buy YES, negative = -EV) or None if either input is None
    """
    if p_yes_fair is None or p_yes_be is None:
        return None
    
    return (p_yes_fair - p_yes_be) * 100.0


def utc_to_pst_datetime(utc_timestamp: str) -> datetime:
    """Convert UTC timestamp to Pacific timezone (PST/PDT)."""
    dt_utc = datetime.fromisoformat(utc_timestamp.replace("Z", "+00:00"))
    
    if USE_PYTZ:
        import pytz
        utc_tz = pytz.UTC
        pacific_tz = pytz.timezone("America/Los_Angeles")
        if dt_utc.tzinfo is None:
            dt_utc = utc_tz.localize(dt_utc)
        else:
            dt_utc = dt_utc.astimezone(utc_tz)
        return dt_utc.astimezone(pacific_tz)
    else:
        utc_tz = ZoneInfo("UTC")
        pacific_tz = ZoneInfo("America/Los_Angeles")
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=utc_tz)
        else:
            dt_utc = dt_utc.astimezone(utc_tz)
        return dt_utc.astimezone(pacific_tz)


def format_game_time_pst(event_start: Optional[str]) -> str:
    """Format game time as PST/PDT in hh:mm am/pm format."""
    if not event_start:
        return "N/A"
    
    try:
        dt_pst = utc_to_pst_datetime(event_start)
        # Format as hh:mm am/pm (12-hour format)
        time_str = dt_pst.strftime("%I:%M %p")
        # Remove leading zero only from single-digit hours (e.g., "09:30 AM" -> "9:30 AM")
        if time_str.startswith('0'):
            time_str = time_str[1:]
        return time_str
    except (ValueError, AttributeError):
        return "N/A"


def is_game_started(event_start: Optional[str]) -> bool:
    """Check if game has already started (current time > game time)."""
    if not event_start:
        return False
    
    try:
        game_time = utc_to_pst_datetime(event_start)
        if USE_PYTZ:
            import pytz
            now = datetime.now(pytz.timezone("America/Los_Angeles"))
        else:
            now = datetime.now(ZoneInfo("America/Los_Angeles"))
        return now >= game_time
    except (ValueError, AttributeError):
        return False


def format_ev_percent(ev_pct: Optional[float]) -> str:
    """
    Format EV percentage with one decimal and sign.
    
    Examples: +2.3%, -1.0%, N/A
    """
    if ev_pct is None:
        return "N/A"
    
    sign = "+" if ev_pct >= 0 else ""
    return f"{sign}{ev_pct:.1f}%"


def create_html_dashboard(table_rows: List[Dict[str, Any]], spread_rows: List[Dict[str, Any]] = None, totals_rows: List[Dict[str, Any]] = None) -> str:
    """
    Create HTML dashboard with dark theme matching the reference image.
    
    Includes moneylines, spreads, and totals tables.
    
    Returns:
        HTML content as string
    """
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NBA Value Dashboard</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif;
            background-color: #1a1a1a;
            color: #e0e0e0;
            padding: 20px;
            font-size: 14px;
            line-height: 1.5;
        }
        
        .dashboard-container {
            max-width: 1800px;
            margin: 0 auto;
            position: relative;
        }
        
        .header-container {
            position: relative;
            margin-bottom: 20px;
        }
        
        h1 {
            color: #ffffff;
            margin-bottom: 20px;
            font-size: 24px;
            font-weight: 600;
            text-align: center;
            border-bottom: 2px solid #333;
            padding-bottom: 15px;
        }
        
        h2 {
            color: #ffffff;
            margin-top: 40px;
            margin-bottom: 15px;
            font-size: 20px;
            font-weight: 600;
            text-align: center;
            border-bottom: 2px solid #333;
            padding-bottom: 10px;
        }
        
        .toggle-button {
            position: absolute;
            top: 0;
            right: 0;
            padding: 8px 16px;
            background-color: #2a2a2a;
            color: #e0e0e0;
            border: 1px solid #555;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
            transition: background-color 0.2s;
        }
        
        .toggle-button:hover {
            background-color: #333;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            background-color: #1f1f1f;
            border: 1px solid #333;
            border-radius: 4px;
            overflow: hidden;
        }
        
        thead {
            background-color: #2a2a2a;
            border-bottom: 2px solid #444;
        }
        
        th {
            padding: 12px 10px;
            text-align: left;
            font-weight: 600;
            color: #ffffff;
            border-right: 1px solid #333;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        th:last-child {
            border-right: none;
        }
        
        th[title] {
            cursor: help;
        }
        
        th[title]:hover::after {
            content: attr(title);
            position: absolute;
            left: 50%;
            transform: translateX(-50%);
            bottom: 100%;
            margin-bottom: 5px;
            padding: 5px 10px;
            background-color: #333;
            color: #fff;
            border: 1px solid #555;
            border-radius: 3px;
            white-space: nowrap;
            z-index: 1000;
            pointer-events: none;
            font-size: 12px;
            font-weight: normal;
            text-transform: none;
            letter-spacing: normal;
        }
        
        thead th {
            position: relative;
        }
        
        td {
            padding: 10px;
            border-right: 1px solid #2a2a2a;
            border-bottom: 1px solid #2a2a2a;
        }
        
        td:last-child {
            border-right: none;
        }
        
        tbody tr {
            transition: background-color 0.2s;
        }
        
        tbody tr:hover {
            background-color: #252525;
        }
        
        tbody tr:last-child td {
            border-bottom: none;
        }
        
        tbody tr.game-started {
            background-color: rgba(248, 113, 113, 0.15); /* Light red background */
        }
        
        tbody tr.game-started:hover {
            background-color: rgba(248, 113, 113, 0.25);
        }
        
        .text-green {
            color: #4ade80;
            font-weight: 500;
        }
        
        .text-red {
            color: #f87171;
            font-weight: 500;
        }
        
        .text-white {
            color: #ffffff;
        }
        
        .text-muted {
            color: #888;
        }
        
        .team-name {
            color: #e0e0e0;
            font-weight: 500;
        }
        
        .prob-value {
            font-family: 'Courier New', monospace;
            color: #d0d0d0;
        }
        
        .ev-positive {
            color: #4ade80;
            font-weight: 600;
            font-family: 'Courier New', monospace;
        }
        
        .ev-negative {
            color: #f87171;
            font-weight: 600;
            font-family: 'Courier New', monospace;
        }
        
        .ev-neutral {
            color: #888;
            font-family: 'Courier New', monospace;
        }
        
        .date-cell {
            color: #a0a0a0;
            font-size: 13px;
        }
        
        .kalshi-cell {
            position: relative;
            padding: 10px;
        }
        
        .kalshi-cell-content {
            position: relative;
            z-index: 2;
        }
        
        .liquidity-bar {
            position: absolute;
            top: 0;
            bottom: 0;
            left: 0;
            width: var(--liq-pct, 0%);
            min-width: 0;
            opacity: 0.5;
            z-index: 1;
            background: var(--liq-gradient, linear-gradient(to right, #f87171 0%, #4ade80 100%));
            pointer-events: none;
            transition: width 0.2s;
        }
        
        .kalshi-cell:hover {
            background-color: #252525;
            cursor: help;
        }
        
        .kalshi-cell[title]:hover::after {
            content: attr(title);
            position: absolute;
            left: 50%;
            transform: translateX(-50%);
            bottom: 100%;
            margin-bottom: 5px;
            padding: 5px 10px;
            background-color: #333;
            color: #fff;
            border: 1px solid #555;
            border-radius: 3px;
            white-space: nowrap;
            z-index: 1000;
            pointer-events: none;
            font-size: 12px;
        }
        
        .odds-cell {
            cursor: default;
        }
    </style>
    <script>
        function probToAmerican(prob) {
            if (prob === null || prob === undefined || isNaN(prob)) {
                return null;
            }
            
            // Use full precision probability (0-1), don't round first
            if (prob <= 0 || prob >= 1) {
                return null;
            }
            
            // Convert probability directly to American odds with full precision
            let americanOdds;
            if (prob >= 0.5) {
                // Favorite (negative odds)
                americanOdds = -100.0 * prob / (1.0 - prob);
            } else {
                // Underdog (positive odds)
                americanOdds = 100.0 * (1.0 - prob) / prob;
            }
            
            // Round to nearest integer
            return Math.round(americanOdds);
        }
        
        function formatAmerican(odds) {
            if (odds === null || odds === undefined || isNaN(odds)) {
                return "N/A";
            }
            // Format as integer, add + sign for positive
            return odds > 0 ? "+" + odds.toString() : odds.toString();
        }
        
        let showingProbs = true;
        
        function toggleOddsFormat() {
            showingProbs = !showingProbs;
            const button = document.getElementById('oddsToggleBtn');
            button.textContent = showingProbs ? "Change odds type" : "Change odds type";
            
            // Find all odds cells (have data-prob attribute)
            const oddsCells = document.querySelectorAll('[data-prob]');
            
            oddsCells.forEach(cell => {
                const prob = parseFloat(cell.getAttribute('data-prob'));
                
                // For Kalshi cells, update the inner content div (liquidity bars remain untouched)
                const contentDiv = cell.querySelector('.kalshi-cell-content');
                
                if (showingProbs) {
                    // Show probability - use original format stored in data-original
                    const originalText = cell.getAttribute('data-original');
                    if (originalText && originalText !== 'N/A') {
                        if (contentDiv) {
                            contentDiv.textContent = originalText;
                        } else {
                            cell.textContent = originalText;
                        }
                    } else {
                        if (contentDiv) {
                            contentDiv.textContent = "N/A";
                        } else {
                            cell.textContent = "N/A";
                        }
                    }
                } else {
                    // Show American odds
                    if (isNaN(prob) || prob === null || prob === '' || prob === 0) {
                        if (contentDiv) {
                            contentDiv.textContent = "N/A";
                        } else {
                            cell.textContent = "N/A";
                        }
                    } else {
                        const american = probToAmerican(prob);
                        const americanStr = formatAmerican(american);
                        
                        if (contentDiv) {
                            contentDiv.textContent = americanStr;
                        } else {
                            cell.textContent = americanStr;
                        }
                    }
                }
            });
        }
    </script>
</head>
<body>
    <div class="dashboard-container">
        <div class="header-container">
            <h1>NBA VALUE DASHBOARD</h1>
            <button class="toggle-button" id="oddsToggleBtn" onclick="toggleOddsFormat()">Change odds type</button>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Game Date</th>
                    <th>Game Time</th>
                    <th>ROTO</th>
                    <th>Away Team</th>
                    <th>Home Team</th>
                    <th title="Unabated consensus odds">Away Fair</th>
                    <th title="Unabated consensus odds">Home Fair</th>
                    <th title="YES break-even price (top YES bid, maker join-queue, inc fees)">Away Kalshi</th>
                    <th title="YES break-even price (top YES bid, maker join-queue, inc fees)">Home Kalshi</th>
                    <th>Away EV</th>
                    <th>Home EV</th>
                </tr>
            </thead>
            <tbody>
"""
    
    def format_liq_k(liq: Optional[int]) -> str:
        """Format liquidity in thousands (K format) - CONTRACTS."""
        if liq is None:
            return "N/A"
        if liq >= 1000:
            return f"{liq / 1000:.1f}K"
        return str(liq)
    
    def format_liq_dollars(price_cents: Optional[int], contracts: Optional[int]) -> str:
        """
        Format liquidity as dollar amount at that price level.
        
        Formula: (price_cents / 100.0) * contracts = dollar_value
        Example: 56 cents * 3000 contracts = $1,680
        
        Args:
            price_cents: Price per contract in cents (e.g., 56 for $0.56)
            contracts: Number of contracts available
        
        Returns:
            Formatted dollar string (e.g., "$1.7K", "$1,680")
        """
        if price_cents is None or contracts is None:
            return "N/A"
        
        # Calculate dollar value: (price_cents / 100.0) * contracts
        dollars = (price_cents / 100.0) * contracts
        
        # Format with appropriate scale
        if dollars >= 1000000:
            return f"${dollars / 1000000:.2f}M"
        elif dollars >= 1000:
            return f"${dollars / 1000:.1f}K"
        else:
            return f"${dollars:.0f}"
    
    def calc_dollar_liq(price_cents: Optional[int], contracts: Optional[int]) -> Optional[float]:
        """Calculate dollar liquidity: (price_cents / 100.0) * contracts."""
        if price_cents is None or contracts is None:
            return None
        return (price_cents / 100.0) * contracts
    
    def calc_liq_bar_pct(dollar_liq: Optional[float], max_dollar_liq: float) -> str:
        """Calculate liquidity bar percentage (0-100%) based on dollar amount."""
        if dollar_liq is None or max_dollar_liq == 0:
            return "0%"
        pct = min(100, (dollar_liq / max_dollar_liq) * 100)
        return f"{pct:.1f}%"
    
    def calc_liq_gradient(dollar_liq: Optional[float], max_dollar_liq: float) -> str:
        """Calculate red-to-green gradient based on dollar liquidity percentage."""
        if dollar_liq is None or max_dollar_liq == 0:
            # No liquidity = red
            return "linear-gradient(to right, #f87171 0%, #f87171 100%)"
        
        pct = min(100, (dollar_liq / max_dollar_liq) * 100)
        
        # Red (low) to green (high) gradient
        # Smooth transition: red -> orange -> yellow -> green
        # At 0% = full red, at 50% = yellow, at 100% = full green
        if pct <= 33:
            # Red to orange (0-33%)
            return "linear-gradient(to right, #f87171 0%, #fb923c 100%)"
        elif pct <= 66:
            # Orange to yellow (33-66%)
            return "linear-gradient(to right, #fb923c 0%, #fbbf24 100%)"
        else:
            # Yellow to green (66-100%)
            return "linear-gradient(to right, #fbbf24 0%, #4ade80 100%)"
    
    # Find max dollar liquidity for scaling bars (only from top columns, not +1c columns)
    max_dollar_liq = 0.0
    for row in table_rows:
        for liq_key, price_key in [('away_top_liq', 'away_top_price_cents'), ('home_top_liq', 'home_top_price_cents')]:
            dollar_liq = calc_dollar_liq(row.get(price_key), row.get(liq_key))
            if dollar_liq is not None:
                max_dollar_liq = max(max_dollar_liq, dollar_liq)
    
    # If no liquidity found, set default max to avoid division by zero
    if max_dollar_liq == 0:
        max_dollar_liq = 10000.0  # Default max for scaling ($10,000)
    
    for row in table_rows:
        # Get probability values (used for both display and data attributes)
        away_fair_val = row['away_fair']
        home_fair_val = row['home_fair']
        away_top_val = row['away_top_prob']
        home_top_val = row['home_top_prob']
        
        # Format values as probabilities (default view)
        away_fair_str = f"{away_fair_val:.3f}" if away_fair_val is not None else "N/A"
        home_fair_str = f"{home_fair_val:.3f}" if home_fair_val is not None else "N/A"
        
        away_top_str = f"{away_top_val:.4f}" if away_top_val is not None else "N/A"
        home_top_str = f"{home_top_val:.4f}" if home_top_val is not None else "N/A"
        
        # Format liquidity for tooltips in dollars (only for top columns, not +1c)
        # Calculate dollar value: (price_cents / 100.0) * contracts
        away_top_liq_str = format_liq_dollars(
            row.get('away_top_price_cents'),
            row.get('away_top_liq')
        )
        home_top_liq_str = format_liq_dollars(
            row.get('home_top_price_cents'),
            row.get('home_top_liq')
        )
        
        # Calculate dollar liquidity and bar percentages/gradients (only for top columns, not +1c)
        away_top_dollar_liq = calc_dollar_liq(row.get('away_top_price_cents'), row.get('away_top_liq'))
        home_top_dollar_liq = calc_dollar_liq(row.get('home_top_price_cents'), row.get('home_top_liq'))
        
        away_top_liq_pct = calc_liq_bar_pct(away_top_dollar_liq, max_dollar_liq)
        home_top_liq_pct = calc_liq_bar_pct(home_top_dollar_liq, max_dollar_liq)
        
        away_top_liq_gradient = calc_liq_gradient(away_top_dollar_liq, max_dollar_liq)
        home_top_liq_gradient = calc_liq_gradient(home_top_dollar_liq, max_dollar_liq)
        
        # Format EVs with color classes
        away_ev_top_val = row['away_ev_top']
        away_ev_top_str = format_ev_percent(away_ev_top_val)
        away_ev_top_class = "ev-positive" if away_ev_top_val and away_ev_top_val > 0 else ("ev-negative" if away_ev_top_val and away_ev_top_val < 0 else "ev-neutral")
        
        home_ev_top_val = row['home_ev_top']
        home_ev_top_str = format_ev_percent(home_ev_top_val)
        home_ev_top_class = "ev-positive" if home_ev_top_val and home_ev_top_val > 0 else ("ev-negative" if home_ev_top_val and home_ev_top_val < 0 else "ev-neutral")
        
        away_roto = row.get('away_roto')
        away_roto_str = str(away_roto) if away_roto is not None else "N/A"
        
        # Format game time and check if started
        event_start = row.get('event_start')
        game_time_str = format_game_time_pst(event_start)
        is_started = is_game_started(event_start)
        row_class = "game-started" if is_started else ""
        
        html_content += f"""
                <tr class="{row_class}">
                    <td class="date-cell">{row['game_date']}</td>
                    <td class="date-cell">{game_time_str}</td>
                    <td class="prob-value">{away_roto_str}</td>
                    <td class="team-name">{row['away_team']}</td>
                    <td class="team-name">{row['home_team']}</td>
                    <td class="prob-value odds-cell fair-cell" data-prob="{away_fair_val if away_fair_val is not None else ''}" data-original="{away_fair_str}">{away_fair_str}</td>
                    <td class="prob-value odds-cell fair-cell" data-prob="{home_fair_val if home_fair_val is not None else ''}" data-original="{home_fair_str}">{home_fair_str}</td>
                    <td class="kalshi-cell prob-value odds-cell" title="Liq: {away_top_liq_str}" style="--liq-pct: {away_top_liq_pct}; --liq-gradient: {away_top_liq_gradient};" data-prob="{away_top_val if away_top_val is not None else ''}" data-original="{away_top_str}">
                        <div class="kalshi-cell-content">{away_top_str}</div>
                        <div class="liquidity-bar"></div>
                    </td>
                    <td class="kalshi-cell prob-value odds-cell" title="Liq: {home_top_liq_str}" style="--liq-pct: {home_top_liq_pct}; --liq-gradient: {home_top_liq_gradient};" data-prob="{home_top_val if home_top_val is not None else ''}" data-original="{home_top_str}">
                        <div class="kalshi-cell-content">{home_top_str}</div>
                        <div class="liquidity-bar"></div>
                    </td>
                    <td class="{away_ev_top_class}">{away_ev_top_str}</td>
                    <td class="{home_ev_top_class}">{home_ev_top_str}</td>
                </tr>
"""
    
    html_content += """
            </tbody>
        </table>
"""
    
    # Add spreads table if provided
    if spread_rows:
        html_content += """
        <h2>SPREADS</h2>
        <table>
            <thead>
                <tr>
                    <th>Game Date</th>
                    <th>Game Time</th>
                    <th>ROTO</th>
                    <th>Away Team</th>
                    <th>Home Team</th>
                    <th>Consensus</th>
                    <th>Strike</th>
                    <th title="YES break-even price (top YES bid, maker join-queue, inc fees)">Away Kalshi</th>
                    <th title="YES break-even price (top YES bid, maker join-queue, inc fees)">Home Kalshi</th>
                </tr>
            </thead>
            <tbody>
"""
        
        # Find max dollar liquidity for spreads scaling (from both away and home)
        max_spread_dollar_liq = 0.0
        for row in spread_rows:
            away_dollar_liq = calc_dollar_liq(row.get('away_kalshi_price_cents'), row.get('away_kalshi_liq'))
            home_dollar_liq = calc_dollar_liq(row.get('home_kalshi_price_cents'), row.get('home_kalshi_liq'))
            if away_dollar_liq is not None:
                max_spread_dollar_liq = max(max_spread_dollar_liq, away_dollar_liq)
            if home_dollar_liq is not None:
                max_spread_dollar_liq = max(max_spread_dollar_liq, home_dollar_liq)
        
        if max_spread_dollar_liq == 0:
            max_spread_dollar_liq = 10000.0  # Default max for scaling ($10,000)
        
        # Sort spreads by ROTO
        spread_rows_sorted = sorted(spread_rows, key=lambda x: (
            x.get('away_roto') is None,
            x.get('away_roto') or 0,
            x.get('game_date') or ''
        ))
        
        for row in spread_rows_sorted:
            # Format game time and check if started
            event_start = row.get('event_start')
            game_time_str = format_game_time_pst(event_start)
            is_started = is_game_started(event_start)
            row_class = "game-started" if is_started else ""
            
            # Format ROTO
            away_roto_str = str(row.get('away_roto', 'N/A')) if row.get('away_roto') is not None else "N/A"
            
            # Format consensus
            consensus_str = row.get('consensus', 'N/A')
            
            # Format strike
            strike_str = row.get('strike', 'N/A')
            
            # Get Away/Home Kalshi values (now stored separately)
            away_kalshi_val = row.get('away_kalshi_prob')
            home_kalshi_val = row.get('home_kalshi_prob')
            away_kalshi_liq = row.get('away_kalshi_liq')
            home_kalshi_liq = row.get('home_kalshi_liq')
            
            away_kalshi_str = f"{away_kalshi_val:.4f}" if away_kalshi_val is not None else "N/A"
            home_kalshi_str = f"{home_kalshi_val:.4f}" if home_kalshi_val is not None else "N/A"
            
            # Format liquidity in dollars: (price_cents / 100.0) * contracts
            away_kalshi_liq_str = format_liq_dollars(
                row.get('away_kalshi_price_cents'),
                away_kalshi_liq
            )
            home_kalshi_liq_str = format_liq_dollars(
                row.get('home_kalshi_price_cents'),
                home_kalshi_liq
            )
            
            # Calculate dollar liquidity and bar percentages/gradients
            away_kalshi_dollar_liq = calc_dollar_liq(row.get('away_kalshi_price_cents'), away_kalshi_liq)
            home_kalshi_dollar_liq = calc_dollar_liq(row.get('home_kalshi_price_cents'), home_kalshi_liq)
            
            away_kalshi_liq_pct = calc_liq_bar_pct(away_kalshi_dollar_liq, max_spread_dollar_liq)
            home_kalshi_liq_pct = calc_liq_bar_pct(home_kalshi_dollar_liq, max_spread_dollar_liq)
            away_kalshi_liq_gradient = calc_liq_gradient(away_kalshi_dollar_liq, max_spread_dollar_liq)
            home_kalshi_liq_gradient = calc_liq_gradient(home_kalshi_dollar_liq, max_spread_dollar_liq)
            
            html_content += f"""
                <tr class="{row_class}">
                    <td class="date-cell">{row['game_date']}</td>
                    <td class="date-cell">{game_time_str}</td>
                    <td class="prob-value">{away_roto_str}</td>
                    <td class="team-name">{row['away_team']}</td>
                    <td class="team-name">{row['home_team']}</td>
                    <td class="team-name">{consensus_str}</td>
                    <td class="team-name">{strike_str}</td>
"""
            
            # Away Kalshi cell (with liquidity bar if value exists)
            if away_kalshi_val is not None:
                html_content += f"""                    <td class="kalshi-cell prob-value odds-cell" title="Liq: {away_kalshi_liq_str}" style="--liq-pct: {away_kalshi_liq_pct}; --liq-gradient: {away_kalshi_liq_gradient};" data-prob="{away_kalshi_val}" data-original="{away_kalshi_str}">
                        <div class="kalshi-cell-content">{away_kalshi_str}</div>
                        <div class="liquidity-bar"></div>
                    </td>
"""
            else:
                html_content += f"""                    <td class="prob-value odds-cell" data-prob="" data-original="{away_kalshi_str}">{away_kalshi_str}</td>
"""
            
            # Home Kalshi cell (with liquidity bar if value exists)
            if home_kalshi_val is not None:
                html_content += f"""                    <td class="kalshi-cell prob-value odds-cell" title="Liq: {home_kalshi_liq_str}" style="--liq-pct: {home_kalshi_liq_pct}; --liq-gradient: {home_kalshi_liq_gradient};" data-prob="{home_kalshi_val}" data-original="{home_kalshi_str}">
                        <div class="kalshi-cell-content">{home_kalshi_str}</div>
                        <div class="liquidity-bar"></div>
                    </td>
"""
            else:
                html_content += f"""                    <td class="prob-value odds-cell" data-prob="" data-original="{home_kalshi_str}">{home_kalshi_str}</td>
"""
            
            html_content += f"""                </tr>
"""
        
        html_content += """
            </tbody>
        </table>
"""
    
    # Add totals table if provided (after spreads, or after moneylines if no spreads)
    if totals_rows:
        html_content += """
        <h2>TOTALS</h2>
        <table>
            <thead>
                <tr>
                    <th>Game Date</th>
                    <th>Game Time</th>
                    <th>ROTO</th>
                    <th>Away Team</th>
                    <th>Home Team</th>
                    <th>Consensus</th>
                    <th>Strike</th>
                    <th title="YES break-even price (top YES bid, maker join-queue, inc fees)">Over Kalshi</th>
                    <th title="NO break-even price (top NO bid, maker join-queue, inc fees)">Under Kalshi</th>
                </tr>
            </thead>
            <tbody>
"""
        
        # Find max dollar liquidity for totals scaling (from both over and under)
        max_totals_dollar_liq = 0.0
        for row in totals_rows:
            over_dollar_liq = calc_dollar_liq(row.get('over_kalshi_price_cents'), row.get('over_kalshi_liq'))
            under_dollar_liq = calc_dollar_liq(row.get('under_kalshi_price_cents'), row.get('under_kalshi_liq'))
            if over_dollar_liq is not None:
                max_totals_dollar_liq = max(max_totals_dollar_liq, over_dollar_liq)
            if under_dollar_liq is not None:
                max_totals_dollar_liq = max(max_totals_dollar_liq, under_dollar_liq)
        
        if max_totals_dollar_liq == 0:
            max_totals_dollar_liq = 10000.0  # Default max for scaling ($10,000)
        
        # Sort totals by ROTO
        totals_rows_sorted = sorted(totals_rows, key=lambda x: (
            x.get('away_roto') is None,
            x.get('away_roto') or 0,
            x.get('game_date') or ''
        ))
        
        for row in totals_rows_sorted:
            # Format game time and check if started
            event_start = row.get('event_start')
            game_time_str = format_game_time_pst(event_start)
            is_started = is_game_started(event_start)
            row_class = "game-started" if is_started else ""
            
            # Format ROTO
            away_roto_str = str(row.get('away_roto', 'N/A')) if row.get('away_roto') is not None else "N/A"
            
            # Format consensus
            consensus_str = row.get('consensus', 'N/A')
            
            # Format strike
            strike_str = row.get('strike', 'N/A')
            
            # Get Over/Under Kalshi values
            over_kalshi_val = row.get('over_kalshi_prob')
            under_kalshi_val = row.get('under_kalshi_prob')
            over_kalshi_liq = row.get('over_kalshi_liq')
            under_kalshi_liq = row.get('under_kalshi_liq')
            
            over_kalshi_str = f"{over_kalshi_val:.4f}" if over_kalshi_val is not None else "N/A"
            under_kalshi_str = f"{under_kalshi_val:.4f}" if under_kalshi_val is not None else "N/A"
            
            # Format liquidity in dollars: (price_cents / 100.0) * contracts
            over_kalshi_liq_str = format_liq_dollars(
                row.get('over_kalshi_price_cents'),
                over_kalshi_liq
            )
            under_kalshi_liq_str = format_liq_dollars(
                row.get('under_kalshi_price_cents'),
                under_kalshi_liq
            )
            
            # Calculate dollar liquidity and bar percentages/gradients
            over_kalshi_dollar_liq = calc_dollar_liq(row.get('over_kalshi_price_cents'), over_kalshi_liq)
            under_kalshi_dollar_liq = calc_dollar_liq(row.get('under_kalshi_price_cents'), under_kalshi_liq)
            
            over_kalshi_liq_pct = calc_liq_bar_pct(over_kalshi_dollar_liq, max_totals_dollar_liq)
            under_kalshi_liq_pct = calc_liq_bar_pct(under_kalshi_dollar_liq, max_totals_dollar_liq)
            over_kalshi_liq_gradient = calc_liq_gradient(over_kalshi_dollar_liq, max_totals_dollar_liq)
            under_kalshi_liq_gradient = calc_liq_gradient(under_kalshi_dollar_liq, max_totals_dollar_liq)
            
            html_content += f"""
                <tr class="{row_class}">
                    <td class="date-cell">{row['game_date']}</td>
                    <td class="date-cell">{game_time_str}</td>
                    <td class="prob-value">{away_roto_str}</td>
                    <td class="team-name">{row['away_team']}</td>
                    <td class="team-name">{row['home_team']}</td>
                    <td class="team-name">{consensus_str}</td>
                    <td class="team-name">{strike_str}</td>
"""
            
            # Over Kalshi cell (with liquidity bar if value exists)
            if over_kalshi_val is not None:
                html_content += f"""                    <td class="kalshi-cell prob-value odds-cell" title="Over Liq: {over_kalshi_liq_str}" style="--liq-pct: {over_kalshi_liq_pct}; --liq-gradient: {over_kalshi_liq_gradient};" data-prob="{over_kalshi_val}" data-original="{over_kalshi_str}">
                        <div class="kalshi-cell-content">{over_kalshi_str}</div>
                        <div class="liquidity-bar"></div>
                    </td>
"""
            else:
                html_content += f"""                    <td class="prob-value odds-cell" data-prob="" data-original="{over_kalshi_str}">{over_kalshi_str}</td>
"""
            
            # Under Kalshi cell (with liquidity bar if value exists)
            if under_kalshi_val is not None:
                html_content += f"""                    <td class="kalshi-cell prob-value odds-cell" title="Under Liq: {under_kalshi_liq_str}" style="--liq-pct: {under_kalshi_liq_pct}; --liq-gradient: {under_kalshi_liq_gradient};" data-prob="{under_kalshi_val}" data-original="{under_kalshi_str}">
                        <div class="kalshi-cell-content">{under_kalshi_str}</div>
                        <div class="liquidity-bar"></div>
                    </td>
"""
            else:
                html_content += f"""                    <td class="prob-value odds-cell" data-prob="" data-original="{under_kalshi_str}">{under_kalshi_str}</td>
"""
            
            html_content += f"""                </tr>
"""
        
        html_content += """
            </tbody>
        </table>
"""
    
    html_content += """
    </div>
</body>
</html>
"""
    
    return html_content


def open_dashboard_in_browser(table_rows: List[Dict[str, Any]], spread_rows: List[Dict[str, Any]] = None, totals_rows: List[Dict[str, Any]] = None):
    """
    Create HTML dashboard and open it in the default browser.
    """
    html_content = create_html_dashboard(table_rows, spread_rows, totals_rows)
    
    # Create temporary HTML file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        temp_file = f.name
    
    # Open in default browser
    file_url = 'file:///' + os.path.abspath(temp_file).replace('\\', '/')
    webbrowser.open(file_url)
    
    print(f"\nDashboard opened in browser: {temp_file}")
    print("(File will remain until manually deleted)\n")


def print_dashboard(table_rows: List[Dict[str, Any]]):
    """
    Print a simplified dashboard table without tickers (console version).
    
    Shows: GameDate, AwayTeam, HomeTeam, Unabated fair odds, Kalshi odds, EVs
    """
    header = (
        f"{'GameDate':<12} "
        f"{'GameTime':<10} "
        f"{'ROTO':<6} "
        f"{'AwayTeam':<30} "
        f"{'HomeTeam':<30} "
        f"{'AwayFair':<10} "
        f"{'HomeFair':<10} "
        f"{'AwayKalshi':<12} "
        f"{'HomeKalshi':<12} "
        f"{'Away_EV':<10} "
        f"{'Home_EV':<10}"
    )
    
    print("\n" + "=" * len(header.expandtabs()))
    print("NBA VALUE DASHBOARD")
    print("=" * len(header.expandtabs()))
    print(header)
    print("-" * len(header.expandtabs()))
    
    for row in table_rows:
        # Format Unabated fair probabilities
        away_fair_str = f"{row['away_fair']:.3f}" if row['away_fair'] is not None else "N/A"
        home_fair_str = f"{row['home_fair']:.3f}" if row['home_fair'] is not None else "N/A"
        
        # Format Kalshi break-even probabilities
        away_top_str = f"{row['away_top_prob']:.4f}" if row['away_top_prob'] is not None else "N/A"
        home_top_str = f"{row['home_top_prob']:.4f}" if row['home_top_prob'] is not None else "N/A"
        
        # Format EVs
        away_ev_top_str = format_ev_percent(row['away_ev_top'])
        home_ev_top_str = format_ev_percent(row['home_ev_top'])
        
        away_roto_str = str(row.get('away_roto', 'N/A')) if row.get('away_roto') is not None else "N/A"
        event_start = row.get('event_start')
        game_time_str = format_game_time_pst(event_start)
        is_started = is_game_started(event_start)
        started_marker = " *" if is_started else ""
        
        print(
            f"{row['game_date']:<12} "
            f"{game_time_str:<10}{started_marker} "
            f"{away_roto_str:<6} "
            f"{row['away_team']:<30} "
            f"{row['home_team']:<30} "
            f"{away_fair_str:<10} "
            f"{home_fair_str:<10} "
            f"{away_top_str:<12} "
            f"{home_top_str:<12} "
            f"{away_ev_top_str:<10} "
            f"{home_ev_top_str:<10}"
        )
    
    print("=" * len(header.expandtabs()) + "\n")


def main():
    """Main entry point."""
    # Step 1: Get today's games with fairs and Kalshi tickers
    games = get_today_games_with_fairs_and_kalshi_tickers()
    
    if not games:
        print("No NBA games found for today")
        return
    
    # Collect unique event tickers (one call per event)
    event_tickers = set()
    game_to_event = {}  # Map game index to event ticker
    
    for i, game in enumerate(games):
        # Try to get event ticker from away ticker (or home if away missing)
        away_ticker = game.get("away_kalshi_ticker")
        home_ticker = game.get("home_kalshi_ticker")
        
        event_ticker = None
        if away_ticker:
            event_ticker = derive_event_ticker(away_ticker)
        elif home_ticker:
            event_ticker = derive_event_ticker(home_ticker)
        
        if event_ticker:
            event_tickers.add(event_ticker)
            game_to_event[i] = event_ticker
    
    # Step 2: Get top-of-book maker break-even probs for each event
    event_probs = {}  # event_ticker -> prob dict
    
    for event_ticker in event_tickers:
        prob_result = get_top_of_book_post_probs(event_ticker)
        event_probs[event_ticker] = prob_result
    
    # Step 3: Build final table rows
    table_rows = []
    
    for i, game in enumerate(games):
        event_ticker = game_to_event.get(i)
        prob_data = event_probs.get(event_ticker) if event_ticker else None
        
        # Get YES break-even probs and YES bid liquidity (user-facing: YES exposure, maker prices)
        yes_be_top_away = prob_data.get("yes_be_top_away") if prob_data else None
        yes_be_topm1_away = prob_data.get("yes_be_topm1_away") if prob_data else None
        yes_be_top_home = prob_data.get("yes_be_top_home") if prob_data else None
        yes_be_topm1_home = prob_data.get("yes_be_topm1_home") if prob_data else None
        
        # Internal: YES bid liquidity (from orderbook["yes"] bids, maker prices)
        yes_bid_top_liq_away = prob_data.get("yes_bid_top_liq_away") if prob_data else None
        yes_bid_top_p1_liq_away = prob_data.get("yes_bid_top_p1_liq_away") if prob_data else None
        yes_bid_top_liq_home = prob_data.get("yes_bid_top_liq_home") if prob_data else None
        yes_bid_top_p1_liq_home = prob_data.get("yes_bid_top_p1_liq_home") if prob_data else None
        
        # YES bid prices in cents (needed for dollar liquidity calculation)
        yes_bid_top_c_away = prob_data.get("yes_bid_top_c_away") if prob_data else None
        yes_bid_top_c_home = prob_data.get("yes_bid_top_c_home") if prob_data else None
        
        # Compute EVs (buyer/YES exposure perspective)
        # EV = (Unabated fair win prob - Kalshi break-even cost) * 100
        # Positive EV means fair > cost, so buying YES is profitable
        away_fair = game.get("away_fair")  # p_yes_fair_away
        home_fair = game.get("home_fair")  # p_yes_fair_home
        
        away_ev_top = compute_ev_percent(away_fair, yes_be_top_away)
        away_ev_topm1 = compute_ev_percent(away_fair, yes_be_topm1_away)
        home_ev_top = compute_ev_percent(home_fair, yes_be_top_home)
        home_ev_topm1 = compute_ev_percent(home_fair, yes_be_topm1_home)
        
        table_rows.append({
            "game_date": game.get("game_date", "N/A"),
            "event_start": game.get("event_start"),  # UTC timestamp from Unabated
            "away_roto": game.get("away_roto"),
            "away_team": game.get("away_team_name", "N/A"),
            "home_team": game.get("home_team_name", "N/A"),
            "away_fair": away_fair,
            "home_fair": home_fair,
            "event_ticker": event_ticker or "N/A",
            "away_ticker": game.get("away_kalshi_ticker") or "N/A",
            "home_ticker": game.get("home_kalshi_ticker") or "N/A",
            "away_top_prob": yes_be_top_away,  # YES break-even probability (user-facing)
            "away_topm1_prob": yes_be_topm1_away,
            "home_top_prob": yes_be_top_home,
            "home_topm1_prob": yes_be_topm1_home,
            "away_top_liq": yes_bid_top_liq_away,  # YES bid liquidity (from orderbook["yes"], maker prices)
            "away_topm1_liq": yes_bid_top_p1_liq_away,
            "home_top_liq": yes_bid_top_liq_home,
            "home_topm1_liq": yes_bid_top_p1_liq_home,
            "away_top_price_cents": yes_bid_top_c_away,  # Price in cents for dollar liquidity calc
            "home_top_price_cents": yes_bid_top_c_home,  # Price in cents for dollar liquidity calc
            "away_ev_top": away_ev_top,
            "away_ev_topm1": away_ev_topm1,
            "home_ev_top": home_ev_top,
            "home_ev_topm1": home_ev_topm1,
        })
    
    # Sort table_rows by ROTO ascending (None values go last)
    table_rows.sort(key=lambda x: (x.get('away_roto') is None, x.get('away_roto') or 0))
    
    # Step 4: Print full detailed table
    header = (
        f"{'GameDate':<12} "
        f"{'GameTime':<10} "
        f"{'ROTO':<6} "
        f"{'AwayTeam':<30} "
        f"{'HomeTeam':<30} "
        f"{'AwayFair':<10} "
        f"{'HomeFair':<10} "
        f"{'EventTicker':<25} "
        f"{'AwayTicker':<30} "
        f"{'HomeTicker':<30} "
        f"{'Away_top_prob':<13} "
        f"{'Home_top_prob':<13} "
        f"{'Away_EV_top_%':<13} "
        f"{'Home_EV_top_%':<13}"
    )
    
    print(header)
    print("-" * len(header.expandtabs()))
    
    for row in table_rows:
        # Format values
        away_fair_str = f"{row['away_fair']:.3f}" if row['away_fair'] is not None else "N/A"
        home_fair_str = f"{row['home_fair']:.3f}" if row['home_fair'] is not None else "N/A"
        
        away_top_str = f"{row['away_top_prob']:.4f}" if row['away_top_prob'] is not None else "N/A"
        home_top_str = f"{row['home_top_prob']:.4f}" if row['home_top_prob'] is not None else "N/A"
        
        away_ev_top_str = format_ev_percent(row['away_ev_top'])
        home_ev_top_str = format_ev_percent(row['home_ev_top'])
        
        away_roto_str = str(row.get('away_roto', 'N/A')) if row.get('away_roto') is not None else "N/A"
        event_start = row.get('event_start')
        game_time_str = format_game_time_pst(event_start)
        is_started = is_game_started(event_start)
        started_marker = " *" if is_started else ""
        
        print(
            f"{row['game_date']:<12} "
            f"{game_time_str:<10}{started_marker} "
            f"{away_roto_str:<6} "
            f"{row['away_team']:<30} "
            f"{row['home_team']:<30} "
            f"{away_fair_str:<10} "
            f"{home_fair_str:<10} "
            f"{row['event_ticker']:<25} "
            f"{row['away_ticker']:<30} "
            f"{row['home_ticker']:<30} "
            f"{away_top_str:<13} "
            f"{home_top_str:<13} "
            f"{away_ev_top_str:<13} "
            f"{home_ev_top_str:<13}"
        )
    
    # Step 5: Get spreads data (additive feature, zero impact on moneylines)
    spread_rows = []
    try:
        from spreads.builder import build_spreads_rows_for_today, print_spreads_table
        spread_rows = build_spreads_rows_for_today()
    except Exception as e:
        # Silently ignore errors in spreads module (don't break moneylines)
        print(f"\nNote: Spreads table unavailable ({e})\n")
    
    # Step 6: Get totals data (additive feature, zero impact on moneylines/spreads)
    totals_rows = []
    try:
        from totals.builder import build_totals_rows_for_today, print_totals_table
        totals_rows = build_totals_rows_for_today()
    except Exception as e:
        # Silently ignore errors in totals module (don't break moneylines/spreads)
        print(f"\nNote: Totals table unavailable ({e})\n")
    
    # Step 7: Open dashboard in browser window (with spreads and totals if available)
    open_dashboard_in_browser(table_rows, spread_rows if spread_rows else None, totals_rows if totals_rows else None)
    
    # Also print console version
    print_dashboard(table_rows)
    
    # Print spreads table if available
    if spread_rows:
        print_spreads_table(spread_rows)
    
    # Print totals table if available
    if totals_rows:
        print_totals_table(totals_rows)


if __name__ == "__main__":
    main()
