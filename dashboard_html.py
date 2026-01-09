"""
HTML dashboard renderer - extracted from moneylines/table.py.
This module handles all HTML generation for the dashboard.
"""

import webbrowser
import tempfile
import os
from typing import Dict, Any, List, Optional
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    USE_PYTZ = False
except ImportError:
    import pytz
    USE_PYTZ = True

# Import helper functions from moneylines.table
from moneylines.table import (
    format_game_time_pst,
    is_game_started,
    format_ev_percent,
    create_html_dashboard
)


def render_dashboard_html(
    moneyline_rows: List[Dict[str, Any]],
    spread_rows: Optional[List[Dict[str, Any]]] = None,
    totals_rows: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Render HTML dashboard from table rows (pure function).
    
    This is a pure function that returns HTML string with no side effects.
    Suitable for use in Streamlit or other frameworks.
    
    Args:
        moneyline_rows: List of moneyline row dicts
        spread_rows: Optional list of spread row dicts
        totals_rows: Optional list of totals row dicts
    
    Returns:
        HTML content as string
    """
    return create_html_dashboard(moneyline_rows, spread_rows, totals_rows)


def render_dashboard(
    moneyline_rows: List[Dict[str, Any]],
    spread_rows: Optional[List[Dict[str, Any]]] = None,
    totals_rows: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Alias for render_dashboard_html for backward compatibility.
    
    Args:
        moneyline_rows: List of moneyline row dicts
        spread_rows: Optional list of spread row dicts
        totals_rows: Optional list of totals row dicts
    
    Returns:
        HTML content as string
    """
    return render_dashboard_html(moneyline_rows, spread_rows, totals_rows)


def open_dashboard_in_browser(
    moneyline_rows: List[Dict[str, Any]],
    spread_rows: Optional[List[Dict[str, Any]]] = None,
    totals_rows: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Create HTML dashboard and open it in the default browser (local usage only).
    
    This function performs file I/O and browser opening, so it's not suitable for Streamlit.
    Use render_dashboard_html() instead for Streamlit/Cloud deployments.
    
    Args:
        moneyline_rows: List of moneyline row dicts
        spread_rows: Optional list of spread row dicts
        totals_rows: Optional list of totals row dicts
    
    Returns:
        HTML content as string (for convenience)
    """
    html_content = render_dashboard_html(moneyline_rows, spread_rows, totals_rows)
    
    # Write to temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        temp_path = f.name
    
    # Open in browser
    file_url = f"file://{temp_path}"
    webbrowser.open(file_url)
    
    print(f"\nDashboard opened in browser: {file_url}")
    
    return html_content