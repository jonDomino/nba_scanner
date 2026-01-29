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
from datetime import datetime, timedelta
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
    """Format game time as PST/PDT in hh:mm am/pm format.
    
    Adds 10 minutes to Unabated's reported time (canonical adjustment).
    """
    if not event_start:
        return "N/A"
    
    try:
        dt_pst = utc_to_pst_datetime(event_start)
        # Add 10 minutes (Unabated shows time 10 minutes early)
        dt_pst = dt_pst + timedelta(minutes=10)
        # Format as hh:mm am/pm (12-hour format)
        time_str = dt_pst.strftime("%I:%M %p")
        # Remove leading zero only from single-digit hours (e.g., "09:30 AM" -> "9:30 AM")
        if time_str.startswith('0'):
            time_str = time_str[1:]
        return time_str
    except (ValueError, AttributeError):
        return "N/A"


def is_game_started(event_start: Optional[str]) -> bool:
    """Check if game has already started (current time > game time + 10 minutes).
    
    Adds 10 minutes to Unabated's reported time (canonical adjustment).
    """
    if not event_start:
        return False
    
    try:
        game_time = utc_to_pst_datetime(event_start)
        # Add 10 minutes (Unabated shows time 10 minutes early)
        game_time = game_time + timedelta(minutes=10)
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
    <title>Kalshi Value Dashboard</title>
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
        
        .table-section {
            margin-top: 40px;
            position: relative;
        }
        
        .table-header {
            position: relative;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #333;
        }
        
        h2 {
            color: #ffffff;
            margin: 0;
            font-size: 20px;
            font-weight: 600;
            text-align: center;
        }
        
        .table-toggle-button {
            position: absolute;
            top: 0;
            left: 0;
            padding: 6px 12px;
            background-color: #2a2a2a;
            color: #e0e0e0;
            border: 1px solid #555;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 500;
            transition: background-color 0.2s;
        }
        
        .table-toggle-button:hover {
            background-color: #333;
        }
        
        /* Click-to-sort headers (no visible indicator) */
        th.sortable {
            cursor: pointer;
            user-select: none;
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
        
        .table-container {
            display: block;
        }
        
        .table-container.hidden {
            display: none;
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
        
        function toggleTable(tableName) {
            const tableContainer = document.getElementById(tableName + 'Table');
            const toggleBtn = document.getElementById(tableName + 'ToggleBtn');
            
            if (tableContainer.classList.contains('hidden')) {
                tableContainer.classList.remove('hidden');
                toggleBtn.textContent = 'Hide';
            } else {
                tableContainer.classList.add('hidden');
                toggleBtn.textContent = 'Show';
            }
        }
        
        // Set default visibility: consolidated visible by default
        document.addEventListener('DOMContentLoaded', function() {
            // Consolidated: visible by default (no action needed)

            // Default: sort consolidated by EV desc
            // Column order: ... Kalshi(7), Pinnacle(8), EV(9), Liq(10)
            sortTable('consolidatedTable', 9, 'num', 'desc');
        });

        // -----------------------------
        // Sorting (EV + Liquidity)
        // -----------------------------
        const __sortState = {}; // key: `${containerId}:${colIndex}` -> boolean asc

        function __parseNumFromText(text) {
            if (!text) return null;
            let t = text.trim();
            if (!t || t === "N/A") return null;

            // Remove common formatting
            t = t.replace(/[$,%\\s,]/g, "");

            // Handle K/M suffixes (e.g., 1.7K, 2.0M)
            let mult = 1.0;
            if (/[Kk]$/.test(t)) { mult = 1_000.0; t = t.slice(0, -1); }
            if (/[Mm]$/.test(t)) { mult = 1_000_000.0; t = t.slice(0, -1); }

            const n = parseFloat(t);
            if (Number.isNaN(n)) return null;
            return n * mult;
        }

        function __getSortValue(cell, type) {
            if (!cell) return null;
            const ds = cell.getAttribute("data-sort");
            if (ds !== null && ds !== "") {
                const n = parseFloat(ds);
                return Number.isNaN(n) ? null : n;
            }
            const txt = cell.textContent || "";
            if (type === "num") return __parseNumFromText(txt);
            return (txt || "").trim().toLowerCase();
        }

        function sortTable(containerId, colIndex, type = "num", forceDir = null) {
            const container = document.getElementById(containerId);
            if (!container) return;
            const table = container.querySelector("table");
            if (!table || !table.tBodies || !table.tBodies.length) return;
            const tbody = table.tBodies[0];

            const key = `${containerId}:${colIndex}`;
            let asc = !(__sortState[key] === true);
            if (forceDir === "asc") asc = true;
            if (forceDir === "desc") asc = false;
            __sortState[key] = asc;

            // Update header classes
            const ths = table.tHead && table.tHead.rows && table.tHead.rows[0] ? table.tHead.rows[0].cells : [];
            for (let i = 0; i < ths.length; i++) {
                ths[i].classList.remove("sort-asc", "sort-desc");
            }
            if (ths[colIndex]) {
                ths[colIndex].classList.add(asc ? "sort-asc" : "sort-desc");
            }

            const rows = Array.from(tbody.rows);
            rows.sort((a, b) => {
                const va = __getSortValue(a.cells[colIndex], type);
                const vb = __getSortValue(b.cells[colIndex], type);

                const aNull = (va === null || va === undefined || va === "");
                const bNull = (vb === null || vb === undefined || vb === "");
                if (aNull && bNull) return 0;
                if (aNull) return 1;
                if (bNull) return -1;

                if (type === "num") {
                    return asc ? (va - vb) : (vb - va);
                }
                // text
                if (va < vb) return asc ? -1 : 1;
                if (va > vb) return asc ? 1 : -1;
                return 0;
            });

            // Re-attach in new order
            for (const r of rows) tbody.appendChild(r);
        }
    </script>
</head>
<body>
    <div class="dashboard-container">
        <div class="header-container">
            <h1>KALSHI VALUE DASHBOARD</h1>
            <button class="toggle-button" id="oddsToggleBtn" onclick="toggleOddsFormat()">Change odds type</button>
        </div>
        <div class="table-section">
            <div class="table-header">
                <button class="table-toggle-button" id="consolidatedToggleBtn" onclick="toggleTable('consolidated')">Hide</button>
                <h2>CONSOLIDATED</h2>
            </div>
            <div class="table-container" id="consolidatedTable">
            <table>
            <thead>
                <tr>
                    <th class="sortable" onclick="sortTable('consolidatedTable', 0, 'text')">Game Date</th>
                    <th class="sortable" onclick="sortTable('consolidatedTable', 1, 'text')">Game Time</th>
                    <th class="sortable" onclick="sortTable('consolidatedTable', 2, 'num')">ROTO</th>
                    <th class="sortable" onclick="sortTable('consolidatedTable', 3, 'text')">Game</th>
                    <th class="sortable" onclick="sortTable('consolidatedTable', 4, 'text')">Market</th>
                    <th class="sortable" onclick="sortTable('consolidatedTable', 5, 'text')">Side</th>
                    <th class="sortable" onclick="sortTable('consolidatedTable', 6, 'num')">Line</th>
                    <th class="sortable" onclick="sortTable('consolidatedTable', 7, 'num')">Kalshi</th>
                    <th class="sortable" onclick="sortTable('consolidatedTable', 8, 'num')">Pinnacle</th>
                    <th class="sortable" onclick="sortTable('consolidatedTable', 9, 'num')">EV</th>
                    <th class="sortable" onclick="sortTable('consolidatedTable', 10, 'num')">Liq</th>
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
    
    # -----------------------------
    # Consolidated (ML + TOTALS)
    # -----------------------------
    consolidated_rows: List[Dict[str, Any]] = []
    if table_rows:
        consolidated_rows.extend(table_rows)
    if spread_rows:
        consolidated_rows.extend(spread_rows)
    if totals_rows:
        consolidated_rows.extend(totals_rows)

    # Find max dollar liquidity for scaling bars
    max_dollar_liq = 0.0
    for row in consolidated_rows:
        dollar_liq = calc_dollar_liq(row.get("kalshi_price_cents"), row.get("kalshi_liq"))
        if dollar_liq is not None:
            max_dollar_liq = max(max_dollar_liq, dollar_liq)
    
    # If no liquidity found, set default max to avoid division by zero
    if max_dollar_liq == 0:
        max_dollar_liq = 10000.0  # Default max for scaling ($10,000)
    
    consolidated_rows_sorted = sorted(consolidated_rows, key=lambda x: (
        x.get('away_roto') is None,
        x.get('away_roto') or 0,
        x.get('event_start') or "",
        (x.get("market") or ""),
        (x.get("side") or ""),
        x.get('line') is None,
        x.get('line') or 0,
    ))

    for row in consolidated_rows_sorted:
        market = row.get("market") or "ML"
        side = row.get("side") or ""
        line = row.get("line")
        line_str = "" if line is None else str(line)
        game_code = row.get("game") or ""

        kalshi_val = row.get("kalshi_prob")
        pinnacle_val = row.get("pinnacle_prob")
        ev_val = row.get("ev")

        kalshi_str = f"{kalshi_val:.4f}" if kalshi_val is not None else "N/A"
        pinnacle_str = f"{pinnacle_val:.4f}" if pinnacle_val is not None else "N/A"
        ev_str = format_ev_percent(ev_val)
        ev_class = "ev-positive" if (ev_val is not None and ev_val > 0) else ("ev-negative" if (ev_val is not None and ev_val < 0) else "ev-neutral")

        liq_contracts = row.get("kalshi_liq")
        liq_price_cents = row.get("kalshi_price_cents")
        liq_str = format_liq_dollars(liq_price_cents, liq_contracts)
        dollar_liq = calc_dollar_liq(liq_price_cents, liq_contracts)
        liq_pct = calc_liq_bar_pct(dollar_liq, max_dollar_liq)
        liq_gradient = calc_liq_gradient(dollar_liq, max_dollar_liq)

        roto = row.get('roto')
        if roto is None:
            # backward-compat fallback
            roto = row.get('away_roto')
        roto_str = str(roto) if roto is not None else "N/A"

        event_start = row.get('event_start')
        game_time_str = format_game_time_pst(event_start)
        is_started = is_game_started(event_start)
        row_class = "game-started" if is_started else ""

        if is_started:
            pinnacle_str = ""
            ev_str = ""
            pinnacle_val = None
            ev_val = None

        html_content += f"""
                <tr class="{row_class}">
                    <td class="date-cell">{row.get('game_date') or ''}</td>
                    <td class="date-cell">{game_time_str}</td>
                    <td class="prob-value">{roto_str}</td>
                    <td class="team-name">{game_code}</td>
                    <td class="prob-value">{market}</td>
                    <td class="prob-value">{side}</td>
                    <td class="prob-value">{line_str}</td>
                    <td class="prob-value odds-cell" data-prob="{kalshi_val if kalshi_val is not None else ''}" data-original="{kalshi_str}">{kalshi_str}</td>
                    <td class="prob-value odds-cell fair-cell" data-prob="{pinnacle_val if pinnacle_val is not None else ''}" data-original="{pinnacle_str}">{pinnacle_str}</td>
                    <td class="{ev_class}" data-sort="{ev_val if ev_val is not None else ''}">{ev_str}</td>
                    <td class="kalshi-cell prob-value" data-sort="{dollar_liq if dollar_liq is not None else ''}" style="--liq-pct: {liq_pct}; --liq-gradient: {liq_gradient};">
                        <div class="kalshi-cell-content">{liq_str}</div>
                        <div class="liquidity-bar"></div>
                    </td>
                </tr>
"""

    html_content += """
            </tbody>
        </table>
        </div>
        </div>
        <script>
            // Auto-resize Streamlit iframe to fit full content height (no internal scrollbars).
            function __streamlitSetFrameHeight() {
                const h = Math.max(
                    document.documentElement.scrollHeight || 0,
                    document.body ? (document.body.scrollHeight || 0) : 0
                );
                try {
                    window.parent.postMessage(
                        { isStreamlitMessage: true, type: "streamlit:setFrameHeight", height: h },
                        "*"
                    );
                } catch (e) {
                    // no-op
                }
            }

            window.addEventListener("load", __streamlitSetFrameHeight);
            window.addEventListener("resize", __streamlitSetFrameHeight);

            if (window.ResizeObserver) {
                const ro = new ResizeObserver(() => __streamlitSetFrameHeight());
                ro.observe(document.documentElement);
            } else {
                setInterval(__streamlitSetFrameHeight, 1000);
            }
        </script>
"""
    
    # Individual market tables intentionally removed (everything lives in Consolidated)
    
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
    
    Shows: GameDate, GameTime, ROTO, Game, Market, Side, Line, Pinnacle, Kalshi, Liq, EV
    """
    header = (
        f"{'GameDate':<12} "
        f"{'GameTime':<10} "
        f"{'ROTO':<6} "
        f"{'GAME':<10} "
        f"{'MKT':<4} "
        f"{'SIDE':<5} "
        f"{'LINE':<6} "
        f"{'PIN':<10} "
        f"{'KALSHI':<12} "
        f"{'LIQ':<10} "
        f"{'EV':<10}"
    )
    
    print("\n" + "=" * len(header.expandtabs()))
    print("KALSHI VALUE DASHBOARD")
    print("=" * len(header.expandtabs()))
    print(header)
    print("-" * len(header.expandtabs()))
    
    for row in table_rows:
        market = row.get("market") or "ML"
        side = row.get("side") or ""
        line = row.get("line")
        line_str = "" if line is None else str(line)
        game_code = row.get("game") or ""

        pinnacle_val = row.get("pinnacle_prob")
        pinnacle_str = f"{pinnacle_val:.3f}" if pinnacle_val is not None else "N/A"

        kalshi_val = row.get("kalshi_prob")
        kalshi_str = f"{kalshi_val:.4f}" if kalshi_val is not None else "N/A"

        # Liquidity (dollars)
        liq_str = format_liq_dollars(row.get("kalshi_price_cents"), row.get("kalshi_liq"))

        ev_str = format_ev_percent(row.get("ev"))
        
        away_roto_str = str(row.get('away_roto', 'N/A')) if row.get('away_roto') is not None else "N/A"
        event_start = row.get('event_start')
        game_time_str = format_game_time_pst(event_start)
        is_started = is_game_started(event_start)
        started_marker = " *" if is_started else ""
        
        # Blank out pinnacle and EV for started games
        if is_started:
            pinnacle_str = ""
            ev_str = ""
        
        print(
            f"{row['game_date']:<12} "
            f"{game_time_str:<10}{started_marker} "
            f"{away_roto_str:<6} "
            f"{game_code:<10} "
            f"{market:<4} "
            f"{side:<5} "
            f"{line_str:<6} "
            f"{pinnacle_str:<10} "
            f"{kalshi_str:<12} "
            f"{liq_str:<10} "
            f"{ev_str:<10}"
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
        
        # Blank out fair odds and EVs for started games
        if is_started:
            away_fair_str = ""
            home_fair_str = ""
            away_ev_top_str = ""
            home_ev_top_str = ""
        
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
